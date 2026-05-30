"""FastAPI lifespan bootstrapping."""

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

import call_center_db
from agent_tools import set_agent_checkpointer
from event_hub import kitchen_events
from settings import langgraph_checkpoint_db_path
from workflow import get_workflow, set_workflow_checkpointer

logger = logging.getLogger(__name__)

_checkpointer_cm: Any | None = None
_checkpointer: Any | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _checkpointer_cm, _checkpointer

    kitchen_events.bind_loop()
    call_center_db.bootstrap_db()
    _checkpointer_cm = AsyncSqliteSaver.from_conn_string(
        str(langgraph_checkpoint_db_path())
    )
    _checkpointer = await _checkpointer_cm.__aenter__()
    await _checkpointer.setup()
    set_workflow_checkpointer(_checkpointer)
    set_agent_checkpointer(_checkpointer)

    with call_center_db.db_session() as conn:
        get_workflow().retriever.ensure_index(conn)
        logger.info(
            "Call center bootstrapped: db=%s checkpoint_db=%s products=%s "
            "orders=%s embedded_chunks=%s",
            call_center_db.db_path(),
            langgraph_checkpoint_db_path(),
            len(call_center_db.list_available_products(conn)),
            len(call_center_db.list_orders(conn)),
            len(call_center_db.list_embedded_chunks(conn)),
        )
    try:
        yield
    finally:
        set_workflow_checkpointer(None)
        set_agent_checkpointer(None)
        if _checkpointer_cm is not None:
            await _checkpointer_cm.__aexit__(None, None, None)
        _checkpointer_cm = None
        _checkpointer = None
