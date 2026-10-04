"""Tests de seguridad: el proyecto no puede operar con dinero real ni tocar APIs de trading."""
import ast
import re
from pathlib import Path

import pytest

from config import ROOT, Settings

NETWORK_MODULES = {"requests", "urllib", "urllib3", "http", "socket", "httpx", "aiohttp", "websocket", "websockets", "openai"}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    return mods


@pytest.mark.parametrize("path", sorted((ROOT / "simulation").glob("*.py")), ids=lambda p: p.name)
def test_simulation_modules_have_no_network_access(path):
    """El módulo de ejecución simulada está aislado de la red (y de los datos externos)."""
    mods = imported_modules(path)
    assert not (mods & NETWORK_MODULES), f"{path.name} importa módulos de red: {mods & NETWORK_MODULES}"


def test_broker_and_portfolio_do_not_depend_on_the_data_layer():
    for name in ("broker.py", "portfolio.py", "risk.py"):
        source = (ROOT / "simulation" / name).read_text(encoding="utf-8")
        assert "data_provider" not in source, name


def test_data_layer_only_reads_public_klines():
    from market import data_provider as dp
    assert dp.ALLOWED_PATHS == ("/api/v3/klines",)
    source = (ROOT / "market" / "data_provider.py").read_text(encoding="utf-8")
    assert not re.search(r"\.(post|put|delete)\(", source)             # solo GET
    assert not re.search(r"(api[_-]?key|secret|signature|X-MBX-APIKEY)", source, re.IGNORECASE)


def test_no_exchange_credentials_anywhere_in_config():
    fields = {f.lower() for f in Settings.__dataclass_fields__}
    assert not any(("exchange" in f or "secret" in f or "binance" in f) for f in fields)
    env_lines = [l for l in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
                 if l.strip() and not l.lstrip().startswith("#")]
    assert not any(re.search(r"BINANCE|SECRET|EXCHANGE", l, re.IGNORECASE) for l in env_lines)


def test_the_only_agent_action_tool_is_the_simulated_order():
    from agent.tools import TOOL_SCHEMAS
    names = [t["function"]["name"] for t in TOOL_SCHEMAS]
    assert "execute_virtual_order" in names
    assert not any(w in n for n in names for w in ("real", "withdraw", "transfer", "exchange", "api_key"))


def test_system_prompt_states_it_is_a_simulation():
    from agent.prompts import build_system_prompt
    prompt = build_system_prompt(Settings())
    assert "SIMULADO" in prompt and "NUNCA inventes" in prompt and "no son recomendaciones financieras".lower() in prompt.lower()
