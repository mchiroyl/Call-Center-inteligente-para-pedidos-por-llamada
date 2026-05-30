"""Pydantic request bodies shared by routers."""

from typing import Literal

from pydantic import BaseModel, Field


class StockAdjustBody(BaseModel):
    action: Literal["increment", "decrement", "zero"]
    amount: int = Field(default=1, ge=1, le=999)


class OrderStatusBody(BaseModel):
    status: Literal[
        "nuevo",
        "en_preparacion",
        "listo",
        "en_camino",
        "entregado",
        "cancelado",
    ]


class PaymentStatusBody(BaseModel):
    payment_status: Literal["pending_cash", "paid"]


class LoginBody(BaseModel):
    username: str
    password: str


class ResetDemoBody(BaseModel):
    confirm: bool = False

