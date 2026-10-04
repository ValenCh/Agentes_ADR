import pytest
from pydantic import ValidationError

from agent.grounding import check_grounding
from agent.schemas import TradeDecision

VALID = dict(action="BUY", asset="BTC/USDT", amount=0.01, confidence=0.7,
             reason="Motivo suficientemente largo para pasar la validación.", cited_values={"price": 60000.0})


def test_valid_decision_is_accepted_and_asset_normalized():
    d = TradeDecision.model_validate(VALID)
    assert d.action.value == "BUY" and d.asset == "BTCUSDT"


def test_hold_with_zero_amount_needs_no_cited_values():
    d = TradeDecision.model_validate({**VALID, "action": "HOLD", "amount": 0, "cited_values": {}})
    assert d.action.value == "HOLD"


@pytest.mark.parametrize("change", [
    {"action": "SHORT"},                        # acción inexistente
    {"action": "buy now!"},
    {"amount": -0.5},                           # cantidad negativa
    {"amount": float("nan")},
    {"confidence": 1.5},                        # confianza fuera de rango
    {"confidence": -0.1},
    {"reason": "corto"},                        # motivo demasiado breve
    {"amount": 0},                              # BUY sin cantidad
    {"cited_values": {}},                       # BUY sin cifras citadas
    {"invented_field": 123},                    # campos no permitidos
    {"amount": "mucho"},                        # tipo incorrecto
])
def test_invalid_decisions_are_rejected(change):
    with pytest.raises(ValidationError):
        TradeDecision.model_validate({**VALID, **change})


def test_hold_with_amount_is_rejected():
    with pytest.raises(ValidationError, match="HOLD"):
        TradeDecision.model_validate({**VALID, "action": "HOLD", "amount": 0.01})


def test_missing_required_fields_are_rejected():
    with pytest.raises(ValidationError):
        TradeDecision.model_validate({"action": "HOLD"})


# ---------------------------------------------------------------- grounding
FACTS = {"price": 60_000.0, "rsi14": 31.4, "btc": 0.05}


def test_grounding_accepts_values_with_reasonable_rounding():
    assert check_grounding({"price": 60_100.0, "rsi14": 31.0, "btc": 0.0501}, FACTS) == []


def test_grounding_rejects_invented_price():
    problems = check_grounding({"price": 68_000.0}, FACTS)
    assert problems and "price" in problems[0] and "60000.0" in problems[0]


def test_grounding_rejects_values_never_obtained_from_tools():
    problems = check_grounding({"ema50": 59_000.0}, FACTS)
    assert problems and "no lo obtuviste" in problems[0]


def test_grounding_ignores_unknown_keys():
    assert check_grounding({"macd": 12.0}, FACTS) == []
