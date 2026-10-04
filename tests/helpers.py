"""Utilidades de test: un LLM guionado (sin red) y atajos para armar llamadas a herramientas."""
from __future__ import annotations

import json
import uuid

from agent.llm import LLMResponse, ToolCall


class ScriptedLLM:
    """LLM falso que responde con turnos preparados de antemano. No usa red ni IA."""

    model_name = "scripted-test-llm"

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = 0
        self.seen: list[list[dict]] = []

    def chat(self, messages, tools):
        self.calls += 1
        self.seen.append([dict(m) for m in messages])
        if not self.turns:
            raise AssertionError("El agente hizo más llamadas al LLM de las previstas en el guion.")
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn(messages) if callable(turn) else turn


def tc(name: str, arguments: dict | None = None) -> ToolCall:
    return ToolCall(id=f"call_{uuid.uuid4().hex[:8]}", name=name, arguments=arguments or {},
                    raw_arguments=json.dumps(arguments or {}))


def resp(*calls: ToolCall, content: str | None = None) -> LLMResponse:
    return LLMResponse(content=content, tool_calls=list(calls))


def reads() -> LLMResponse:
    """Un turno donde el agente consulta las cuatro herramientas de lectura principales."""
    return resp(tc("get_market_data"), tc("calculate_indicators"), tc("get_portfolio"), tc("calculate_risk"))


def order(action="HOLD", amount=0.0, confidence=0.6, reason="Motivo de prueba lo bastante largo.",
          cited=None, asset="BTCUSDT", **extra) -> ToolCall:
    args = {"action": action, "asset": asset, "amount": amount, "confidence": confidence, "reason": reason}
    if cited is not None:
        args["cited_values"] = cited
    args.update(extra)
    return tc("execute_virtual_order", args)
