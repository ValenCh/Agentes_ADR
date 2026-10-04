"""Tests del bucle del agente con un LLM guionado (sin red)."""
import dataclasses

import pytest

from agent.agent import TradingAgent
from simulation.portfolio import Portfolio
from tests.helpers import ScriptedLLM, order, reads, resp, tc


def run(ctx, turns, cfg=None):
    llm = ScriptedLLM(turns)
    outcome = TradingAgent(llm, cfg or ctx.settings).run_cycle(ctx, 1, "2025-01-01 00:00 UTC")
    return outcome, llm


def test_agent_consults_tools_then_executes_a_valid_buy(make_ctx):
    ctx = make_ctx()
    price = ctx.provider.latest_price
    out, llm = run(ctx, [reads(), resp(order("BUY", 0.01, cited={"price": price}))])
    assert out.status == "EXECUTED"
    assert out.fill.executed and out.verdict.status == "APPROVED"
    assert ctx.portfolio.btc == pytest.approx(0.01)
    assert ctx.portfolio.cash_usd < 10_000
    assert [t["tool"] for t in ctx.tool_trace] == [
        "get_market_data", "calculate_indicators", "get_portfolio", "calculate_risk", "execute_virtual_order"]
    assert llm.calls == 2 and out.llm_calls == 2


def test_agent_never_sees_prices_in_its_prompt(make_ctx):
    ctx = make_ctx()
    price = ctx.provider.latest_price
    _, llm = run(ctx, [resp(order("HOLD"))])
    text = " ".join(m["content"] for m in llm.seen[0])
    assert f"{price:.2f}" not in text and f"{price:,.2f}" not in text
    assert str(int(price)) not in text          # el precio solo llega por herramientas


def test_hold_is_recorded_without_touching_portfolio(make_ctx):
    ctx = make_ctx()
    before = ctx.portfolio.to_dict()
    out, _ = run(ctx, [reads(), resp(order("HOLD"))])
    assert out.status == "HOLD" and out.fill is None
    assert ctx.portfolio.to_dict() == before


def test_invalid_decision_is_rejected_then_agent_corrects(make_ctx):
    ctx = make_ctx()
    out, llm = run(ctx, [reads(), resp(order("BUY", -1, confidence=5, cited={"price": 1})), resp(order("HOLD"))])
    assert out.status == "HOLD" and llm.calls == 3
    invalid = [t for t in ctx.tool_trace if t["tool"] == "execute_virtual_order"][0]["result"]
    assert invalid["status"] == "INVALID" and invalid["errors"]
    assert ctx.portfolio.btc == 0


def test_hallucinated_price_is_rejected_by_grounding_check(make_ctx):
    ctx = make_ctx()
    fake = ctx.provider.latest_price * 1.10
    out, _ = run(ctx, [reads(), resp(order("BUY", 0.01, cited={"price": fake})), resp(order("HOLD"))])
    result = [t for t in ctx.tool_trace if t["tool"] == "execute_virtual_order"][0]["result"]
    assert result["status"] == "UNGROUNDED"
    assert out.status == "HOLD" and ctx.portfolio.btc == 0          # no se ejecutó nada


def test_operating_without_consulting_tools_is_blocked(make_ctx):
    ctx = make_ctx()
    out, _ = run(ctx, [resp(order("BUY", 0.01, cited={"price": ctx.provider.latest_price})), resp(order("HOLD"))])
    first = ctx.tool_trace[0]["result"]
    assert first["status"] == "NOT_CONSULTED"
    assert ctx.portfolio.btc == 0 and out.status == "HOLD"


def test_buy_without_funds_is_rejected_by_risk_and_can_be_corrected(make_ctx):
    ctx = make_ctx(portfolio=Portfolio.new(5.0))
    price = ctx.provider.latest_price
    out, _ = run(ctx, [reads(), resp(order("BUY", 0.01, cited={"price": price})), resp(order("HOLD"))])
    first = [t for t in ctx.tool_trace if t["tool"] == "execute_virtual_order"][0]["result"]
    assert first["status"] == "REJECTED_BY_RISK" and "Saldo insuficiente" in first["risk"]["notes"][0]
    assert out.status == "HOLD" and ctx.portfolio.cash_usd == 5.0


def test_two_rejected_attempts_end_the_cycle_as_rejected(make_ctx):
    ctx = make_ctx(portfolio=Portfolio.new(5.0))
    price = ctx.provider.latest_price
    bad = order("BUY", 0.01, cited={"price": price})
    out, llm = run(ctx, [reads(), resp(bad), resp(order("BUY", 0.01, cited={"price": price}))])
    assert out.status == "REJECTED" and out.fill is None
    assert out.verdict.status == "REJECTED"
    assert ctx.finished and llm.calls == 3


def test_selling_without_position_is_rejected(make_ctx):
    ctx = make_ctx()
    price = ctx.provider.latest_price
    out, _ = run(ctx, [reads(), resp(order("SELL", 0.01, cited={"price": price})), resp(order("HOLD"))])
    first = [t for t in ctx.tool_trace if t["tool"] == "execute_virtual_order"][0]["result"]
    assert first["status"] == "REJECTED_BY_RISK"
    assert "No hay posición" in first["risk"]["notes"][0]


def test_order_exceeding_limits_is_adjusted_not_blindly_executed(make_ctx):
    ctx = make_ctx()
    price = ctx.provider.latest_price
    out, _ = run(ctx, [reads(), resp(order("BUY", 0.09, cited={"price": price}))])   # ~5.400 USD > 25 % de 10.000
    assert out.status == "EXECUTED" and out.verdict.status == "ADJUSTED"
    assert out.fill.amount < 0.09
    assert out.fill.notional <= 2_500 + 1e-6


def test_json_in_plain_text_is_accepted_as_fallback(make_ctx):
    ctx = make_ctx()
    text = ('```json\n{"action": "HOLD", "asset": "BTCUSDT", "amount": 0, "confidence": 0.4, '
            '"reason": "Sin señal clara, se mantiene la posición actual."}\n```')
    out, _ = run(ctx, [reads(), resp(content=text)])
    assert out.status == "HOLD"


def test_agent_that_never_decides_fails_safe(make_ctx, settings):
    cfg = dataclasses.replace(settings, max_agent_steps=3)
    ctx = make_ctx(cfg=cfg)
    out, llm = run(ctx, [resp(content="no sé"), resp(content="sigo pensando"), resp(content="...")], cfg)
    assert out.status == "FAILED" and out.decision is None and out.fill is None
    assert llm.calls == 3
    assert ctx.portfolio.btc == 0


def test_llm_exception_fails_safe_without_crashing(make_ctx):
    ctx = make_ctx()
    out, _ = run(ctx, [RuntimeError("API caída")])
    assert out.status == "FAILED" and "API caída" in out.error
    assert ctx.portfolio.cash_usd == 10_000


def test_unknown_tool_and_bad_json_arguments_are_reported_to_the_agent(make_ctx):
    ctx = make_ctx()
    bad = tc("hackear_exchange")
    bad_json = tc("get_portfolio", {"_invalid_json": "{no es json"})
    out, _ = run(ctx, [resp(bad, bad_json), resp(order("HOLD"))])
    assert "Herramienta desconocida" in ctx.tool_trace[0]["result"]["error"]
    assert "JSON" in ctx.tool_trace[1]["result"]["error"]
    assert out.status == "HOLD"


def test_trade_history_lets_the_agent_observe_previous_results(make_ctx, db):
    ctx = make_ctx()
    db.add_decision("t", dict(step=1, candle_time="x", price=ctx.provider.latest_price * 0.98, source="agent",
                              action="BUY", amount=0.01, confidence=0.6, reason="compra previa de prueba",
                              risk_status="APPROVED", executed=True))
    out, _ = run(ctx, [resp(tc("get_trade_history", {"limit": 3})), resp(order("HOLD"))])
    entry = ctx.tool_trace[0]["result"]["previous_decisions_newest_first"][0]
    assert entry["action"] == "BUY" and entry["price_change_since_then_pct"] == pytest.approx(2.04, abs=0.05)
