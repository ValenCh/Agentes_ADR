"""Tests de integración: runner, escenarios, historial y ejecución simulada de punta a punta."""
import dataclasses

import pytest

from market.data_provider import ReplayProvider
from simulation.runner import EndOfData, create_session
from storage.database import Database
from tests.helpers import ScriptedLLM, order, resp


def make(settings, **kw):
    return create_session(settings, db=Database(settings.db_path), **kw)


def test_backtest_keeps_portfolio_invariants_and_records_every_step(settings):
    s = make(settings, mode="backtest", llm_kind="mock", start_index=100)
    s.run(120)
    rows = s.db.list_decisions(s.run_id)
    assert len(rows) == 120
    for r in rows:
        pf = r["portfolio_after"]
        assert pf["cash_usd"] >= -1e-6 and pf["btc"] >= -1e-9
        assert pf["btc"] <= settings.max_btc_position + 1e-9
        assert r["equity_after"] == pytest.approx(pf["cash_usd"] + pf["btc"] * r["price"], abs=0.02)
    summ = s.summary()
    assert summ["skipped_cycles"] > 0 and summ["agent_cycles"] < 120     # el filtro ahorra llamadas
    assert summ["llm_calls"] < 2 * 120
    assert rows[0]["source"] != "skipped"                                # el primer ciclo siempre consulta al agente


def test_scenario_dip_executes_simulated_orders_and_updates_portfolio(settings):
    s = make(settings, mode="scenario", scenario="dip", llm_kind="mock")
    res = s.run(3)
    assert len(res) == 3 and all(r["source"] == "agent" for r in res)
    first = res[0]
    assert first["action"] == "BUY" and first["executed"]
    assert first["portfolio_before"]["btc"] == 0
    assert first["portfolio_after"]["btc"] > 0
    assert first["portfolio_after"]["cash_usd"] < first["portfolio_before"]["cash_usd"]
    assert s.portfolio.btc == pytest.approx(s.db.list_decisions(s.run_id)[-1]["portfolio_after"]["btc"])
    assert first["tool_trace"] and first["llm_calls"] == 2


def test_history_has_everything_needed_to_explain_a_decision(settings):
    s = make(settings, mode="scenario", scenario="rally", llm_kind="mock")
    s.run(1)
    row = s.db.list_decisions(s.run_id)[0]
    for key in ("timestamp", "candle_time", "price", "action", "amount", "reason", "risk_status",
                "portfolio_before", "portfolio_after", "tool_trace", "cited_values", "confidence"):
        assert row[key] is not None, key
    assert isinstance(row["portfolio_before"], dict) and isinstance(row["tool_trace"], list)
    assert row["reason"].startswith("[MOCK")


def test_stop_loss_sells_automatically_even_if_the_agent_only_holds(settings):
    llm = ScriptedLLM([resp(order("HOLD")) for _ in range(12)])
    s = make(settings, mode="scenario", scenario="crash", llm=llm)
    rows = s.run(6)
    guard = [r for r in rows if r["source"] == "risk_guard"]
    assert len(guard) == 1
    g = guard[0]
    assert g["action"] == "SELL" and g["executed"] and g["llm_calls"] == 0
    assert "STOP-LOSS" in g["reason"]
    assert g["portfolio_after"]["btc"] == 0
    assert g["realized_pnl"] < 0                      # se vendió con pérdida limitada por el stop


def test_agent_failure_becomes_a_safe_hold_and_the_session_continues(settings):
    llm = ScriptedLLM([RuntimeError("sin conexión"), resp(order("HOLD"))])
    s = make(settings, mode="scenario", scenario="sideways", llm=llm)
    rows = s.run(2)
    assert rows[0]["source"] == "agent_failed" and rows[0]["action"] == "HOLD" and not rows[0]["executed"]
    assert rows[1]["source"] == "agent" and rows[1]["action"] == "HOLD"
    assert s.summary()["agent_failures"] == 1


def test_wake_always_consults_the_agent_every_cycle(settings):
    s = make(settings, mode="backtest", llm_kind="mock", wake="always", start_index=100)
    s.run(5)
    assert s.summary()["agent_cycles"] == 5 and s.summary()["skipped_cycles"] == 0


def test_summary_compares_against_buy_and_hold(settings):
    s = make(settings, mode="scenario", scenario="dip", llm_kind="mock")
    s.run(3)
    summ = s.summary()
    expected = (s.provider.latest_price / s.first_price - 1) * 100
    assert summ["buy_and_hold_return_pct"] == pytest.approx(round(expected, 2))
    assert summ["equity_usd"] == pytest.approx(s.portfolio.equity(s.provider.latest_price), abs=0.01)


def test_end_of_data_is_signalled(settings, candles):
    s = make(settings, mode="backtest", llm_kind="mock", start_index=100)
    s.provider = ReplayProvider(candles.head(102), 100)
    s.step(); s.step()
    assert not s.can_step
    with pytest.raises(EndOfData):
        s.step()


def test_scenarios_always_use_the_sample_dataset_even_if_data_path_changes(settings):
    cfg = dataclasses.replace(settings, data_path="data/no_existe.csv")
    s = make(cfg, mode="scenario", scenario="dip", llm_kind="mock")
    assert len(s.run(1)) == 1


def test_unknown_scenario_and_mode_raise_clear_errors(settings):
    with pytest.raises(ValueError, match="Escenario desconocido"):
        make(settings, mode="scenario", scenario="nope", llm_kind="mock")
    with pytest.raises(ValueError, match="Modo desconocido"):
        make(settings, mode="otro", llm_kind="mock")


def test_missing_api_key_gives_actionable_error(settings):
    from agent.llm import LLMConfigError
    with pytest.raises(LLMConfigError, match="LLM_API_KEY"):
        make(dataclasses.replace(settings, llm_api_key=""), mode="scenario", scenario="dip", llm_kind="api")
