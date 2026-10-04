"""Esquemas Pydantic: el "contrato" entre el LLM y el sistema.

Si el modelo devuelve algo que no cumple este esquema (acción inexistente,
cantidad negativa, confianza fuera de 0-1, HOLD con cantidad, motivo vacío...),
el sistema lo RECHAZA y le devuelve el error al agente para que corrija.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Cifras que el agente puede citar como evidencia y que el sistema verifica
# contra lo que devolvieron las herramientas (control de alucinaciones).
CITABLE_KEYS = (
    "price", "rsi14", "ema20", "ema50", "sma20", "bb_upper", "bb_lower", "percent_b",
    "cash_usd", "btc", "equity_usd", "unrealized_pnl_pct", "max_buy_btc", "max_sell_btc",
)


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class TradeDecision(BaseModel):
    """Decisión estructurada que produce el agente."""

    model_config = ConfigDict(extra="forbid")

    action: Action
    asset: str = Field(description="Par a operar, por ejemplo BTCUSDT")
    amount: float = Field(ge=0, allow_inf_nan=False, description="Cantidad en BTC (0 si HOLD)")
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    reason: str = Field(min_length=20, max_length=1200)
    cited_values: dict[str, float] = Field(default_factory=dict)

    @field_validator("asset")
    @classmethod
    def _normalize_asset(cls, v: str) -> str:
        return v.upper().replace("/", "").replace("-", "").strip()

    @model_validator(mode="after")
    def _consistency(self) -> "TradeDecision":
        if self.action == Action.HOLD and self.amount != 0:
            raise ValueError("Con HOLD, amount debe ser 0.")
        if self.action in (Action.BUY, Action.SELL):
            if self.amount <= 0:
                raise ValueError("Con BUY/SELL, amount debe ser mayor que 0 (en BTC).")
            if not self.cited_values:
                raise ValueError(
                    "Con BUY/SELL debes incluir cited_values con las cifras que usaste "
                    f"(claves posibles: {', '.join(CITABLE_KEYS)})."
                )
        return self


def format_validation_errors(exc) -> list[str]:
    """Convierte un ValidationError de Pydantic en mensajes cortos y legibles."""
    out = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "decision"
        out.append(f"{loc}: {err.get('msg')}")
    return out
