"""
Stateful workflow for the intelligent call-center order flow.

This module contributes the course requirements around:
- stateful workflows
- structured outputs
- retrieval-augmented generation support
- durable persistence of conversation state
"""

from __future__ import annotations

import os
import re
import asyncio
import logging
from copy import deepcopy
from typing import Any, Literal, TypedDict

from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from ai_errors import ai_error_user_message, classify_ai_error
import call_center_db
from rag import SemanticRetriever

PHONE_CAPTURE_RE = re.compile(r"(?:\+?502[\s-]*)?(\d{4}[\s-]?\d{4})")
NAME_CAPTURE_RE = re.compile(
    r"(?:mi nombre es|soy|me llamo)\s+([a-záéíóúñ][a-záéíóúñ\s]{1,60})",
    re.IGNORECASE,
)
NON_WORD_RE = re.compile(r"[^a-z0-9\s]")
NUMBER_WORDS = {
    "un": 1,
    "uno": 1,
    "una": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
}
ORDER_INTENT_TOKENS = (
    "quiero",
    "necesito",
    "agregame",
    "agrega",
    "agregar",
    "dame",
    "deme",
    "ponme",
    "mande",
    "mandeme",
    "paseme",
    "pedido",
)
TOTAL_QUESTION_TOKENS = (
    "total",
    "cuanto llevo",
    "cuanto es",
    "cuanto seria",
    "cuanto tengo",
    "que tengo en mi pedido",
    "que tengo en el pedido",
    "que agregaste",
    "lo agregaste al pedido",
    "que hiciste con lo que te dije",
    "mirame el total",
    "resumen del pedido",
)
NO_NEED_ANYTHING_TOKENS = (
    "no necesito nada",
    "ya no necesito nada",
    "eso es todo gracias",
    "nada mas gracias",
    "nada mas",
    "ya no quiero nada",
)


def _normalized_text(value: str) -> str:
    lowered = value.lower()
    replacements = str.maketrans(
        {
            "á": "a",
            "é": "e",
            "í": "i",
            "ó": "o",
            "ú": "u",
            "ü": "u",
            "ñ": "n",
        }
    )
    clean = lowered.translate(replacements)
    clean = clean.replace("-", " ")
    clean = NON_WORD_RE.sub(" ", clean)
    return " ".join(clean.split())


def _chat_model() -> ChatOpenAI:
    timeout_s = float(os.getenv("OPENAI_CHAT_TIMEOUT_SECONDS", "10"))
    return ChatOpenAI(
        model="gpt-5.2",
        temperature=0,
        request_timeout=timeout_s,
        max_retries=1,
    )


logger = logging.getLogger(__name__)


class ExtractedItem(BaseModel):
    requested_name: str = Field(description="Nombre del producto mencionado por el cliente.")
    product_id: str | None = Field(
        default=None,
        description="ID canonico del catalogo. Debe salir del menu provisto o quedar null si no es seguro.",
    )
    quantity: int = Field(default=1, ge=1, le=20)
    operation: Literal["add", "remove", "set"] = "add"


class StructuredTurnOutput(BaseModel):
    intent: Literal[
        "greeting",
        "new_order",
        "add_item",
        "remove_item",
        "provide_address",
        "provide_payment",
        "provide_customer_data",
        "confirm_order",
        "cancel_order",
        "ask_question",
        "unknown",
    ] = "unknown"
    customer_name: str | None = None
    customer_phone: str | None = None
    delivery_type: Literal["delivery", "pickup"] | None = None
    address: str | None = None
    payment_method: Literal["efectivo", "tarjeta", "transferencia", "paypal"] | None = None
    notes: str | None = None
    items: list[ExtractedItem] = Field(default_factory=list)
    wants_confirmation: bool = False
    asks_question: bool = False
    menu_category_requested: (
        Literal["hamburguesas", "pizzas", "tacos", "bebidas", "acompanamientos"] | None
    ) = None
    clarification_needed: str | None = None


class TurnState(TypedDict, total=False):
    session_id: str
    transcript: str
    session: dict[str, Any]
    draft: dict[str, Any]
    retrieval_hits: list[dict[str, Any]]
    extraction: dict[str, Any]
    response_mode: str
    response_text: str
    recommended_action: str
    agent_prompt: str
    finalized_order: dict[str, Any] | None
    workflow_state_name: str
    provider_error_kind: str | None
    end_call: bool
    call_end_reason: str | None


class CallCenterWorkflow:
    def __init__(self) -> None:
        self.retriever = SemanticRetriever()
        self.structured_llm = None
        if os.getenv("OPENAI_API_KEY"):
            self.structured_llm = _chat_model().with_structured_output(StructuredTurnOutput)
        graph = StateGraph(TurnState)
        graph.add_node("load_session", self.load_session)
        graph.add_node("retrieve_context", self.retrieve_context)
        graph.add_node("extract_structured_turn", self.extract_structured_turn)
        graph.add_node("merge_and_persist", self.merge_and_persist)
        graph.set_entry_point("load_session")
        graph.add_edge("load_session", "retrieve_context")
        graph.add_edge("retrieve_context", "extract_structured_turn")
        graph.add_edge("extract_structured_turn", "merge_and_persist")
        graph.add_edge("merge_and_persist", END)
        self.graph = graph.compile(checkpointer=_workflow_checkpointer)

    def load_session(self, state: TurnState) -> TurnState:
        with call_center_db.db_session() as conn:
            session = call_center_db.get_or_create_session(conn, state["session_id"])
            draft = call_center_db.get_draft(conn, state["session_id"])
            call_center_db.log_workflow_event(
                conn,
                state["session_id"],
                "load_session",
                {"transcript": state["transcript"]},
            )
        return {
            "session": session,
            "draft": draft,
        }

    def retrieve_context(self, state: TurnState) -> TurnState:
        query = state["transcript"]
        draft_summary = call_center_db.draft_summary_text(state["draft"])
        retrieval_query = f"{query}\nContexto actual: {draft_summary}"
        with call_center_db.db_session() as conn:
            hits = self.retriever.search(conn, retrieval_query, k=4)
            provider_error_kind = self.retriever.last_error_kind
            call_center_db.log_workflow_event(
                conn,
                state["session_id"],
                "retrieve_context",
                {
                    "query": retrieval_query,
                    "hits": hits,
                    "provider_error_kind": provider_error_kind,
                },
            )
        return {"retrieval_hits": hits, "provider_error_kind": provider_error_kind}

    async def extract_structured_turn(self, state: TurnState) -> TurnState:
        transcript = state["transcript"].strip()
        if not transcript:
            return {"extraction": StructuredTurnOutput(intent="unknown").model_dump()}

        retrieval_block = "\n".join(
            f"- [{hit['source']}] {hit['title']}: {hit['content']}"
            for hit in state.get("retrieval_hits", [])
        )
        with call_center_db.db_session() as conn:
            menu_catalog = call_center_db.menu_catalog_for_prompt(conn)

        prompt = (
            "Extrae informacion estructurada de una llamada de pedidos de restaurante.\n"
            "Reglas:\n"
            "- No inventes productos.\n"
            "- Solo usa product_id existentes del catalogo.\n"
            "- Si no estas segura del producto, deja product_id en null.\n"
            "- Si el cliente confirma el pedido, usa intent=confirm_order y wants_confirmation=true.\n"
            "- Si el cliente pregunta por horarios, cobertura, politicas, menu, precios, disponibilidad o recomendaciones, usa asks_question=true.\n"
            "- Si el cliente pregunta por una categoria concreta del menu, llena menu_category_requested con una de estas opciones: hamburguesas, pizzas, tacos, bebidas o acompanamientos.\n"
            "- Si el cliente dice que va a recoger o pasar a recoger, usa delivery_type=pickup.\n"
            "- Los metodos de pago validos son efectivo, tarjeta, transferencia y paypal.\n"
            "- Debes responder unicamente con la estructura solicitada.\n\n"
            f"Catalogo:\n{menu_catalog}\n\n"
            f"Contexto recuperado:\n{retrieval_block or '- sin contexto extra'}\n\n"
            f"Pedido actual:\n{call_center_db.draft_summary_text(state['draft'])}\n\n"
            f"Transcripcion del cliente:\n{transcript}"
        )

        provider_error_kind = state.get("provider_error_kind")
        try:
            if self.structured_llm is None:
                raise RuntimeError("OPENAI_API_KEY missing")
            timeout_s = float(os.getenv("OPENAI_CHAT_TIMEOUT_SECONDS", "10"))
            extraction = await asyncio.wait_for(
                self.structured_llm.ainvoke(prompt),
                timeout=max(timeout_s + 2.0, 6.0),
            )
            result = extraction.model_dump()
        except Exception as exc:
            provider_error_kind = provider_error_kind or classify_ai_error(exc)
            logger.warning(
                "extract_structured_turn fallback due to AI error (%s): %s",
                provider_error_kind,
                exc,
            )
            result = self._fallback_extract(transcript)

        with call_center_db.db_session() as conn:
            call_center_db.log_workflow_event(
                conn,
                state["session_id"],
                "extract_structured_turn",
                {"extraction": result},
            )
        return {"extraction": result, "provider_error_kind": provider_error_kind}

    def merge_and_persist(self, state: TurnState) -> TurnState:
        extraction = state["extraction"]
        provider_error_kind = state.get("provider_error_kind")
        draft = deepcopy(state["draft"])
        session = state["session"]
        transcript = state["transcript"]

        if extraction.get("customer_name"):
            draft["customer_name"] = extraction["customer_name"]
        if extraction.get("customer_phone"):
            draft["customer_phone"] = extraction["customer_phone"]
        elif session.get("customer_phone") and not draft.get("customer_phone"):
            draft["customer_phone"] = session["customer_phone"]
        if extraction.get("delivery_type"):
            draft["delivery_type"] = extraction["delivery_type"]
            if extraction["delivery_type"] == "pickup":
                draft["address"] = call_center_db.PICKUP_ADDRESS_TEXT
            elif draft.get("address") == call_center_db.PICKUP_ADDRESS_TEXT:
                draft["address"] = None
        if extraction.get("address"):
            draft["address"] = extraction["address"]
        if extraction.get("payment_method"):
            draft["payment_method"] = extraction["payment_method"]
        if extraction.get("notes"):
            draft["notes"] = extraction["notes"]

        with call_center_db.db_session() as conn:
            catalog = call_center_db.product_catalog_map(conn)
            extraction = self._post_process_extraction(transcript, extraction, catalog)
            self._merge_items(draft, extraction.get("items", []), catalog)
            draft["last_intent"] = extraction.get("intent", "unknown")
            draft["last_retrieval_hits"] = state.get("retrieval_hits", [])
            draft["last_updated_at"] = call_center_db.utc_now_iso()
            draft["missing_fields"] = self._missing_fields(draft)
            draft["ready_for_confirmation"] = not draft["missing_fields"] and bool(draft["items"])
            response_mode = "template"
            finalized_order = None
            end_call = False
            call_end_reason = None

            wants_confirmation = bool(extraction.get("wants_confirmation")) or self._looks_like_confirmation(transcript)
            asks_question = bool(extraction.get("asks_question")) or extraction.get("intent") == "ask_question"
            cancel_order = extraction.get("intent") == "cancel_order"
            wants_call_end = self._looks_like_call_end(transcript)

            if cancel_order:
                draft = call_center_db.empty_draft()
                response_text = "Con mucho gusto. Gracias por llamar, que tenga un excelente dia."
                workflow_state_name = "call_completed"
                end_call = True
                call_end_reason = "customer_no_longer_needs_assistance"
            elif wants_call_end and (not draft.get("items") or draft["ready_for_confirmation"]):
                response_text = "Con gusto. Gracias por llamar, quedo a la orden. Que tenga excelente dia."
                workflow_state_name = "call_completed"
                end_call = True
                call_end_reason = "customer_requested_call_end"
            elif wants_confirmation and draft["ready_for_confirmation"]:
                finalization = call_center_db.finalize_order_from_draft(conn, state["session_id"])
                if finalization["ok"]:
                    finalized_order = finalization["order"]
                    draft = call_center_db.empty_draft()
                    response_text = (
                        f"Pedido confirmado con codigo {finalized_order['id'][:8].upper()}. "
                        f"Total Q{finalized_order['total_cents'] / 100:.2f}. "
                        "Lo estamos enviando al modulo operativo. "
                        "Muchas gracias por su pedido. Que tenga excelente dia."
                    )
                    workflow_state_name = "order_confirmed"
                    end_call = True
                    call_end_reason = "order_confirmed"
                else:
                    response_text = finalization["error"]
                    workflow_state_name = "needs_completion"
            elif asks_question and extraction.get("menu_category_requested"):
                response_text = self._build_category_response(
                    conn,
                    str(extraction["menu_category_requested"]),
                )
                workflow_state_name = "answering_question"
            elif asks_question:
                if provider_error_kind in {"quota_exceeded", "rate_limited", "auth_error"}:
                    response_mode = "template"
                    response_text = ai_error_user_message(provider_error_kind)
                    workflow_state_name = "provider_error"
                else:
                    response_mode = "agent"
                    response_text = ""
                    workflow_state_name = "answering_question"
            else:
                response_text, workflow_state_name = self._build_template_response(draft, extraction)

            call_center_db.save_draft(conn, state["session_id"], draft)
            call_center_db.update_session(
                conn,
                state["session_id"],
                customer_name=draft.get("customer_name"),
                customer_phone=draft.get("customer_phone"),
                current_state=workflow_state_name,
                latest_transcript=transcript,
            )
            call_center_db.log_workflow_event(
                conn,
                state["session_id"],
                "merge_and_persist",
                {
                    "draft": draft,
                    "response_mode": response_mode,
                    "workflow_state_name": workflow_state_name,
                    "finalized_order_id": finalized_order["id"] if finalized_order else None,
                    "end_call": end_call,
                },
            )

        return {
            "draft": draft,
            "response_mode": response_mode,
            "response_text": response_text,
            "recommended_action": self._recommended_action(draft, extraction, workflow_state_name),
            "agent_prompt": self._build_agent_prompt(
                transcript=transcript,
                draft=draft,
                retrieval_hits=state.get("retrieval_hits", []),
                workflow_state_name=workflow_state_name,
            ),
            "finalized_order": finalized_order,
            "workflow_state_name": workflow_state_name,
            "end_call": end_call,
            "call_end_reason": call_end_reason,
        }

    async def run_turn(self, session_id: str, transcript: str) -> TurnState:
        return await self.graph.ainvoke(
            {"session_id": session_id, "transcript": transcript},
            config={"configurable": {"thread_id": session_id}},
        )

    def _fallback_extract(self, transcript: str) -> dict[str, Any]:
        text = _normalized_text(transcript)
        intent = "unknown"
        if any(word in text for word in ("hola", "buenas", "buen dia")):
            intent = "greeting"
        if any(word in text for word in ("quiero", "deseo", "ordenar", "pedido", "necesito")):
            intent = "new_order"
        if any(word in text for word in ("confirm", "mandelo", "mande", "esta bien")):
            intent = "confirm_order"
        if any(phrase in text for phrase in NO_NEED_ANYTHING_TOKENS):
            intent = "cancel_order"
        asks_question = any(
            phrase in text
            for phrase in (
                "que tienen",
                "que tienes",
                "que venden",
                "menu",
                "precio",
                "cuanto cuesta",
                "que me recomienda",
                "recomienda",
                "disponible",
            )
        )
        if any(phrase in text for phrase in TOTAL_QUESTION_TOKENS):
            asks_question = True
        menu_category_requested = call_center_db.normalize_menu_category(text)
        if menu_category_requested and any(token in text for token in ("que", "cual", "precio", "tienes", "tienen", "vende")):
            asks_question = True
        if "domicilio" in text:
            delivery_type = "delivery"
        elif any(phrase in text for phrase in ("recoger", "pickup", "pasar a recoger", "voy a recoger", "pasare a recoger")):
            delivery_type = "pickup"
        else:
            delivery_type = None
        if "tarjeta" in text:
            payment_method = "tarjeta"
        elif "efectivo" in text:
            payment_method = "efectivo"
        elif "paypal" in text or "pay pal" in text:
            payment_method = "paypal"
        elif "transferencia" in text:
            payment_method = "transferencia"
        else:
            payment_method = None
        phone_match = PHONE_CAPTURE_RE.search(transcript)
        phone_value = None
        if phone_match:
            phone_value = re.sub(r"\D", "", phone_match.group(1))
        name_match = NAME_CAPTURE_RE.search(transcript)
        customer_name = None
        if name_match:
            customer_name = " ".join(name_match.group(1).split()).strip(" .,")
        fallback = StructuredTurnOutput(
            intent="ask_question" if asks_question and intent != "cancel_order" else intent,
            customer_name=customer_name,
            customer_phone=phone_value,
            asks_question=asks_question,
            menu_category_requested=menu_category_requested,
            delivery_type=delivery_type,
            payment_method=payment_method,
        ).model_dump()
        fallback["items"] = []
        return fallback

    def _extract_requested_quantity(self, text: str) -> int:
        digit_match = re.search(r"\b(\d{1,2})\b", text)
        if digit_match:
            return max(1, min(20, int(digit_match.group(1))))
        for token, value in NUMBER_WORDS.items():
            if re.search(rf"\b{token}\b", text):
                return value
        return 1

    def _infer_items_from_transcript(
        self,
        transcript: str,
        catalog: dict[str, dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], str | None]:
        text = _normalized_text(transcript)
        quantity = self._extract_requested_quantity(text)
        wants_order = any(token in text for token in ORDER_INTENT_TOKENS) or bool(re.search(r"\b\d+\b", text))
        if not wants_order:
            return [], None

        inferred: list[dict[str, Any]] = []
        clarification_needed: str | None = None

        def add_item(product_id: str, requested_name: str) -> None:
            inferred.append(
                {
                    "requested_name": requested_name,
                    "product_id": product_id,
                    "quantity": quantity,
                    "operation": "add",
                }
            )

        if any(phrase in text for phrase in ("agua pura", "aguas puras")):
            add_item("agua-botella", "Agua pura")
        elif re.search(r"\bagua\b|\baguas\b", text) and "bebida" not in text:
            add_item("agua-botella", "Agua pura")

        if any(phrase in text for phrase in ("coca cola", "coca", "refresco de cola", "gaseosa coca", "gaseosa cola")):
            add_item("refresco-cola", "Refresco de cola")

        if any(phrase in text for phrase in ("hamburguesa doble", "hamburguesas dobles", "doble bacon", "dobles bacon")):
            add_item("burger-doble-bacon", "Hamburguesa doble bacon")
        elif any(phrase in text for phrase in ("hamburguesa clasica", "hamburguesas clasicas", "clasica")):
            add_item("burger-clasica", "Hamburguesa clasica")
        elif re.search(r"\bhamburguesa\b|\bhamburguesas\b", text):
            burger_count = sum(1 for item in catalog.values() if item["category"] == "hamburguesas")
            if burger_count > 1:
                clarification_needed = (
                    "Con gusto. Tengo hamburguesa clasica y hamburguesa doble bacon. "
                    "Cual de las dos desea?"
                )

        if any(phrase in text for phrase in ("pizza pepperoni", "pepperoni")):
            add_item("pizza-pepperoni-mediana", "Pizza pepperoni mediana")
        elif any(phrase in text for phrase in ("pizza vegetariana", "vegetariana")):
            add_item("pizza-vegetariana-familiar", "Pizza vegetariana familiar")

        if any(phrase in text for phrase in ("tacos de pollo", "tacos pollo", "taco de pollo")):
            add_item("tacos-pollo-orden", "Orden de tacos de pollo")

        if any(phrase in text for phrase in ("papas grandes", "papas fritas", "papas")):
            add_item("papas-grandes", "Papas fritas grandes")

        unique: dict[str, dict[str, Any]] = {}
        for item in inferred:
            unique[item["product_id"]] = item
        return list(unique.values()), clarification_needed

    def _post_process_extraction(
        self,
        transcript: str,
        extraction: dict[str, Any],
        catalog: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        result = deepcopy(extraction)
        text = _normalized_text(transcript)

        if any(phrase in text for phrase in NO_NEED_ANYTHING_TOKENS):
            result["intent"] = "cancel_order"
            result["asks_question"] = False

        if any(phrase in text for phrase in TOTAL_QUESTION_TOKENS):
            result["intent"] = "ask_question"
            result["asks_question"] = True

        is_question_turn = bool(result.get("asks_question")) or result.get("intent") == "ask_question"
        existing_items = result.get("items") or []
        has_resolved_items = any(item.get("product_id") for item in existing_items)
        inferred_items, clarification_needed = self._infer_items_from_transcript(transcript, catalog)
        if not has_resolved_items and inferred_items:
            result["items"] = inferred_items
            if result.get("intent") in {"unknown", "greeting", "new_order"}:
                result["intent"] = "add_item"

        if (
            clarification_needed
            and not is_question_turn
            and not result.get("clarification_needed")
            and not has_resolved_items
        ):
            result["clarification_needed"] = clarification_needed

        return result

    def _merge_items(
        self,
        draft: dict[str, Any],
        extracted_items: list[dict[str, Any]],
        catalog: dict[str, dict[str, Any]],
    ) -> None:
        by_id = {item["product_id"]: item for item in draft["items"]}
        for raw_item in extracted_items:
            product_id = raw_item.get("product_id")
            if not product_id or product_id not in catalog:
                continue
            quantity = max(1, int(raw_item.get("quantity") or 1))
            operation = raw_item.get("operation") or "add"
            current = by_id.get(product_id)
            if not current:
                current = {
                    "product_id": product_id,
                    "name": catalog[product_id]["name"],
                    "quantity": 0,
                    "unit_price_cents": catalog[product_id]["price_cents"],
                }
                by_id[product_id] = current
            if operation == "remove":
                current["quantity"] = max(0, current["quantity"] - quantity)
            elif operation == "set":
                current["quantity"] = quantity
            else:
                current["quantity"] += quantity

        draft["items"] = [
            item
            for item in by_id.values()
            if item["quantity"] > 0
        ]
        draft["items"].sort(key=lambda item: item["name"])

    def _missing_fields(self, draft: dict[str, Any]) -> list[str]:
        missing = []
        if not draft["items"]:
            missing.append("items")
        if not draft.get("customer_name"):
            missing.append("customer_name")
        if not draft.get("customer_phone"):
            missing.append("customer_phone")
        if not draft.get("delivery_type"):
            missing.append("delivery_type")
        if draft.get("delivery_type") == "delivery" and not draft.get("address"):
            missing.append("address")
        if not draft.get("payment_method"):
            missing.append("payment_method")
        return missing

    def _build_template_response(
        self,
        draft: dict[str, Any],
        extraction: dict[str, Any],
    ) -> tuple[str, str]:
        if extraction.get("clarification_needed"):
            return (str(extraction["clarification_needed"]), "clarify")
        if "items" in draft["missing_fields"]:
            menu_guidance = "Tenemos hamburguesas, pizzas, tacos, bebidas y acompanamientos."
            return (
                f"Con gusto. {menu_guidance} Puede decirme su pedido, preguntar por precios o pedirme una recomendacion.",
                "collect_items",
            )
        if "customer_name" in draft["missing_fields"]:
            return (
                "Perfecto. Antes de confirmar, necesito el nombre del cliente.",
                "collect_customer_name",
            )
        if "customer_phone" in draft["missing_fields"]:
            return (
                "Perfecto. Ahora necesito un numero de telefono de contacto.",
                "collect_customer_phone",
            )
        if "delivery_type" in draft["missing_fields"]:
            return (
                f"Llevo registrado {call_center_db.draft_summary_text(draft)} "
                "Desea entrega a domicilio o pasara a recoger?",
                "collect_delivery_type",
            )
        if "address" in draft["missing_fields"]:
            return (
                "Perfecto. Para entrega a domicilio necesito la direccion completa.",
                "collect_address",
            )
        if "payment_method" in draft["missing_fields"]:
            return (
                "Perfecto. Que metodo de pago prefiere: efectivo, tarjeta, transferencia o PayPal?",
                "collect_payment_method",
            )
        return (
            f"Resumen del pedido: {call_center_db.draft_summary_text(draft)} "
            "Si todo esta correcto, por favor confirme su pedido.",
            "await_confirmation",
        )

    def _build_category_response(self, conn: Any, category: str) -> str:
        items = call_center_db.browse_menu(conn, category=category, limit=6)
        labels = {
            "bebidas": "bebidas",
            "pizzas": "pizzas",
            "hamburguesas": "hamburguesas",
            "tacos": "tacos",
            "acompanamientos": "acompanamientos",
        }
        friendly = labels.get(category, category)
        if not items:
            return (
                f"En este momento no tengo {friendly} disponibles. "
                "Si desea, puedo ofrecerle otra categoria del menu."
            )

        parts = [f"{item['name']} a Q{item['price_cents'] / 100:.2f}" for item in items]
        if len(parts) == 1:
            listing = parts[0]
        else:
            listing = ", ".join(parts[:-1]) + f" y {parts[-1]}"
        return (
            f"De {friendly} tengo disponible {listing}. "
            "Si desea, puede pedirme una de esas opciones o preguntar por otra categoria."
        )

    def _recommended_action(
        self,
        draft: dict[str, Any],
        extraction: dict[str, Any],
        workflow_state_name: str,
    ) -> str:
        if workflow_state_name == "order_confirmed":
            return "Informar que la orden ya fue registrada y despedirse."
        if workflow_state_name == "answering_question":
            return "Responder la duda del cliente usando retrieval y luego regresar al flujo del pedido."
        if extraction.get("clarification_needed"):
            return "Pedir una aclaracion puntual."
        if draft["missing_fields"]:
            return f"Solicitar el siguiente dato faltante: {draft['missing_fields'][0]}"
        return "Presentar resumen corto y pedir confirmacion final."

    def _build_agent_prompt(
        self,
        *,
        transcript: str,
        draft: dict[str, Any],
        retrieval_hits: list[dict[str, Any]],
        workflow_state_name: str,
    ) -> str:
        retrieval_block = "\n".join(
            f"- [{hit['source']}] {hit['title']}: {hit['content']}"
            for hit in retrieval_hits
        ) or "- Sin hallazgos relevantes"
        return (
            "Contexto del workflow de call center:\n"
            f"- Estado actual: {workflow_state_name}\n"
            f"- Ultima transcripcion: {transcript}\n"
            f"- Borrador actual: {call_center_db.draft_summary_text(draft)}\n"
            f"- Campos faltantes: {', '.join(draft['missing_fields']) or 'ninguno'}\n"
            f"- Contexto recuperado:\n{retrieval_block}\n\n"
            "Responde en espanol de Guatemala, muy breve, con tono profesional y cordial. "
            "Si necesitas contestar dudas del cliente sobre menu, horarios, zonas o pago, "
            "puedes usar las herramientas disponibles. "
            "Si la duda es sobre que lleva el pedido, que se agrego o cual es el total, "
            "usa get_current_order_snapshot antes de responder."
        )

    def _looks_like_confirmation(self, transcript: str) -> bool:
        text = transcript.lower()
        normalized = (
            text.replace(",", " ")
            .replace(".", " ")
            .replace("?", " ")
            .replace("!", " ")
        )
        patterns = (
            "confirmo",
            "confirmar",
            "confirmado",
            "si correcto",
            "si esta correcto",
            "esta correcto",
            "esta bien",
            "de acuerdo",
            "ok",
            "dale",
            "listo",
            "mande el pedido",
            "confirmo el pedido",
            "si confirmo",
            "si esta bien",
        )
        return any(pattern in normalized for pattern in patterns)

    def _looks_like_call_end(self, transcript: str) -> bool:
        text = transcript.lower()
        normalized = (
            text.replace(",", " ")
            .replace(".", " ")
            .replace("?", " ")
            .replace("!", " ")
        )
        patterns = (
            "gracias eso es todo",
            "eso es todo",
            "nada mas",
            "no necesito nada mas",
            "ya no",
            "hasta luego",
            "adios",
            "me despido",
            "listo gracias",
            "ok gracias",
        )
        return any(pattern in normalized for pattern in patterns)


_workflow_singleton: CallCenterWorkflow | None = None
_workflow_checkpointer: Any | None = None


def set_workflow_checkpointer(checkpointer: Any | None) -> None:
    global _workflow_checkpointer, _workflow_singleton
    _workflow_checkpointer = checkpointer
    _workflow_singleton = None


def get_workflow() -> CallCenterWorkflow:
    global _workflow_singleton
    if _workflow_singleton is None:
        _workflow_singleton = CallCenterWorkflow()
    return _workflow_singleton
