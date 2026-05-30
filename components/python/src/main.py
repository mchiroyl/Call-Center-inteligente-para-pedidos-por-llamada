"""Backend entrypoint."""

import logging

import uvicorn

from app_factory import create_app

logging.basicConfig(level=logging.INFO)

app = create_app()


if __name__ == "__main__":
    uvicorn.run("main:app", port=8000, reload=False)
