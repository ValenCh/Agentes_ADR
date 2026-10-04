import json
from types import SimpleNamespace

import pandas as pd
import pytest

from agent.llm import LLMConfigError, MockPolicyLLM, OpenAICompatClient, build_llm, parse_chat_completion
from config import Settings
from market.data_provider import (
    DataUnavailable, LiveProvider, ReplayProvider, SecurityError, assert_public_data_url, fetch_klines)
from storage.database import Database


# ------------------------------------------------------------------ datos
def test_replay_never_shows_future_candles(candles):
    p = ReplayProvider(candles, 100)
    seen_last = p.get_candles(50)["time"].iloc[-1]
    assert seen_last == candles["time"].iloc[100]
    assert len(p.get_candles(1000)) == 101                      # solo pasado, nada posterior
    assert p.advance(3) and p.cursor == 103
    assert p.get_candles(5)["time"].iloc[-1] == candles["time"].iloc[103]
    assert p.latest_price == candles["close"].iloc[103]


def test_replay_stops_at_the_end(candles):
    p = ReplayProvider(candles, len(candles) - 2)
    assert p.advance() and not p.can_advance() and not p.advance()


def test_replay_rejects_invalid_start(candles):
    with pytest.raises(DataUnavailable):
        ReplayProvider(candles, 5)


def _fake_rows(n=5):
    return [[1_700_000_000_000 + i * 3_600_000, "100", "101", "99", "100.5", "12.3", 0, 0, 0, 0, 0, 0] for i in range(n)]


def test_fetch_klines_parses_public_response():
    calls = []

    def fake_get(url, params, timeout):
        calls.append((url, params))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: _fake_rows())

    df = fetch_klines("BTCUSDT", "1h", 5, http_get=fake_get)
    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume"]
    assert len(df) == 5 and df["close"].iloc[0] == 100.5
    assert calls[0][0].endswith("/api/v3/klines")
    assert "signature" not in calls[0][1] and "apiKey" not in calls[0][1]      # nada de firmas ni claves


def test_fetch_klines_tries_the_next_base_url_on_failure():
    hosts = []

    def flaky(url, params, timeout):
        hosts.append(url)
        if len(hosts) == 1:
            raise ConnectionError("caído")
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: _fake_rows())

    assert len(fetch_klines(http_get=flaky)) == 5 and len(hosts) == 2


def test_fetch_klines_raises_when_every_source_fails():
    def broken(url, params, timeout):
        raise TimeoutError("sin red")

    with pytest.raises(DataUnavailable):
        fetch_klines(http_get=broken)


def test_live_provider_falls_back_to_local_data(candles):
    def failing_fetcher(*a, **k):
        raise DataUnavailable("sin red")

    live = LiveProvider("BTCUSDT", "1h", 200, ReplayProvider(candles, 300), fetcher=failing_fetcher)
    assert live.using_fallback and live.source_name == "csv-fallback"
    assert live.latest_price == candles["close"].iloc[300]
    assert live.advance() and live.can_advance()


def test_live_provider_uses_api_data_when_available(candles):
    df = candles.head(100)
    live = LiveProvider("BTCUSDT", "1h", 200, ReplayProvider(candles, 300), fetcher=lambda *a, **k: df)
    assert not live.using_fallback and live.latest_price == df["close"].iloc[-1]


@pytest.mark.parametrize("url", [
    "https://api.binance.com/api/v3/order",             # endpoint de TRADING
    "https://api.binance.com/api/v3/account",           # cuenta
    "https://api.binance.com/sapi/v1/capital/withdraw/apply",
    "http://api.binance.com/api/v3/klines",             # sin HTTPS
    "https://evil.example.com/api/v3/klines",           # otro host
])
def test_only_public_market_data_urls_are_allowed(url):
    with pytest.raises(SecurityError):
        assert_public_data_url(url)


def test_public_klines_url_is_allowed():
    assert_public_data_url("https://data-api.binance.vision/api/v3/klines") is None


# ---------------------------------------------------------------- almacenamiento
def test_database_roundtrip_of_json_columns_and_filters(tmp_path):
    db = Database(tmp_path / "x.db")
    db.create_run("r1", "backtest", None, "BTCUSDT", 10_000, "m")
    base = dict(candle_time="t", price=1.0, action="HOLD", amount=0.0, executed=False)
    db.add_decision("r1", dict(step=1, source="agent", portfolio_before={"cash": 1}, tool_trace=[{"tool": "x"}], **base))
    db.add_decision("r1", dict(step=2, source="skipped", **base))
    db.add_decision("r1", dict(step=3, source="risk_guard", risk_notes=["a"], **base))
    rows = db.list_decisions("r1")
    assert [r["step"] for r in rows] == [1, 2, 3]
    assert rows[0]["portfolio_before"] == {"cash": 1} and rows[0]["tool_trace"] == [{"tool": "x"}]
    assert [r["step"] for r in db.list_decisions("r1", include_skipped=False)] == [1, 3]
    assert [r["step"] for r in db.list_decisions("r1", limit=2, newest_first=True)] == [3, 2]
    assert db.get_run("r1")["mode"] == "backtest" and db.list_runs()[0]["run_id"] == "r1"


# -------------------------------------------------------------------------- LLM
def _fake_completion(tool_calls=None, content=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                           usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))


def _fake_tool_call(name, arguments, id="abc"):
    return SimpleNamespace(id=id, function=SimpleNamespace(name=name, arguments=arguments))


def test_parse_chat_completion_handles_tool_calls_and_bad_json():
    r = parse_chat_completion(_fake_completion([
        _fake_tool_call("get_portfolio", "{}"),
        _fake_tool_call("execute_virtual_order", '{"action": "HOLD"}', id=""),
        _fake_tool_call("execute_virtual_order", "{roto"),
    ]))
    assert [c.name for c in r.tool_calls] == ["get_portfolio", "execute_virtual_order", "execute_virtual_order"]
    assert r.tool_calls[1].arguments == {"action": "HOLD"} and r.tool_calls[1].id      # id generado si falta
    assert "_invalid_json" in r.tool_calls[2].arguments
    msg = r.assistant_message()
    assert msg["role"] == "assistant" and len(msg["tool_calls"]) == 3
    assert r.usage == {"prompt_tokens": 10, "completion_tokens": 5}


def test_openai_client_passes_tools_and_optional_temperature():
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return _fake_completion(content="hola")

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    OpenAICompatClient("k", "http://x", "m", temperature=0.2, client=fake).chat([{"role": "user", "content": "x"}], [{"t": 1}])
    assert captured["model"] == "m" and captured["tools"] == [{"t": 1}] and captured["temperature"] == 0.2
    captured.clear()
    OpenAICompatClient("k", "http://x", "m", temperature=None, client=fake).chat([], [])
    assert captured["model"] == "m" and "temperature" not in captured


def test_build_llm_requires_key_for_real_model_but_not_for_mock():
    with pytest.raises(LLMConfigError):
        build_llm(Settings(llm_api_key=""), "api")
    assert isinstance(build_llm(Settings(), "mock"), MockPolicyLLM)


def test_mock_llm_first_asks_for_tools_then_decides():
    llm = MockPolicyLLM()
    first = llm.chat([{"role": "user", "content": "hola"}], [])
    assert {c.name for c in first.tool_calls} >= {"get_market_data", "calculate_indicators", "get_portfolio"}
    msgs = [{"role": "user", "content": "hola"}, first.assistant_message()]
    payloads = {"calculate_indicators": {"rsi14": 20.0, "percent_b": 0.0, "price": 100.0},
                "get_portfolio": {"btc": 0.0, "unrealized_pnl_pct": 0.0},
                "calculate_risk": {"max_buy_btc": 0.04}}
    for c in first.tool_calls:
        msgs.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(payloads.get(c.name, {}))})
    second = llm.chat(msgs, [])
    assert second.tool_calls[0].name == "execute_virtual_order"
    assert second.tool_calls[0].arguments["action"] == "BUY"


def test_models_command_lists_and_filters_models(monkeypatch, capsys):
    import dataclasses
    import openai
    import main

    class FakeModels:
        def list(self):
            return [SimpleNamespace(id="models/gemini-3.8-flash"), SimpleNamespace(id="models/gemini-3.8-pro"),
                    SimpleNamespace(id="models/text-embedding-9")]

    monkeypatch.setattr(openai, "OpenAI", lambda **kw: SimpleNamespace(models=FakeModels()))
    cfg = dataclasses.replace(Settings(), llm_api_key="k")
    args = main.build_parser().parse_args(["models"])
    assert main.cmd_models(args, cfg) == 0
    out = capsys.readouterr().out
    listed = [l.strip() for l in out.splitlines() if l.startswith("  ")]
    assert listed == ["gemini-3.8-flash"]          # filtra por "flash" y quita el prefijo "models/"
    assert main.cmd_models(args, Settings(llm_api_key="")) == 1


def test_default_model_is_not_the_retired_one():
    assert "2.5" not in Settings().llm_model
