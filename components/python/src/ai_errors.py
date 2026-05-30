"""Helpers to classify provider/API errors for user-facing fallbacks."""

from __future__ import annotations


def classify_ai_error(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}".lower()

    if any(
        token in text
        for token in (
            "insufficient_quota",
            "quota",
            "billing",
            "credit balance",
            "exceeded your current quota",
        )
    ):
        return "quota_exceeded"

    if any(
        token in text
        for token in (
            "rate limit",
            "ratelimit",
            "too many requests",
        )
    ):
        return "rate_limited"

    if any(
        token in text
        for token in (
            "invalid api key",
            "authentication",
            "unauthorized",
            "forbidden",
            "permission",
        )
    ):
        return "auth_error"

    if any(
        token in text
        for token in (
            "timeout",
            "timed out",
            "connection",
            "network",
            "dns",
            "ssl",
            "connecterror",
            "apiconnectionerror",
        )
    ):
        return "network_timeout"

    return "unknown"


def ai_error_user_message(kind: str) -> str:
    if kind == "quota_exceeded":
        return (
            "No puedo responder con IA ahora porque la cuota de la API se agotó "
            "(100% consumida o sin saldo). Recargue tokens/saldo y vuelva a intentar."
        )
    if kind == "rate_limited":
        return (
            "La API de IA alcanzó el límite de solicitudes por minuto. "
            "Espere unos segundos y vuelva a intentar."
        )
    if kind == "auth_error":
        return (
            "La API de IA rechazó las credenciales. Verifique OPENAI_API_KEY "
            "y permisos del proyecto."
        )
    if kind == "network_timeout":
        return (
            "La API de IA no respondió a tiempo por red/timeout. "
            "Intente nuevamente en unos segundos."
        )
    return "Ocurrió un error con el proveedor de IA. Intente nuevamente."

