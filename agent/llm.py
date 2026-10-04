"""Clientes de modelo de lenguaje.

- OpenAICompatClient: habla con CUALQUIER endpoint compatible con la API de
  chat de OpenAI (Gemini, Groq, OpenRouter, Ollama, OpenAI...). Solo cambian
  LLM_BASE_URL, LLM_API_KEY y LLM_MODEL en el archivo .env.
- MockPolicyLLM: NO ES IA. Es un sustituto determinista para desarrollo
  offline y tests: recorre el mismo bucle de herramientas, pero decide con una
  regla fija. Sirve para probar el sistema, nunca para demostrar razonamiento.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from config import Settings


class LLMConfigError(RuntimeError):
    """Falta configuración del modelo (p. ej. la API key)."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    raw_arguments: str = ""


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict | None = None

    def assistant_message(self) -> dict:
        """El mensaje del asistente en formato OpenAI (para seguir la conversación)."""
        msg: dict = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.raw_arguments or json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in self.tool_calls
            ]
        return msg


class LLMClient(Protocol):
    model_name: str

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMResponse: ...


# --------------------------------------------------------------------------
# Cliente real
# --------------------------------------------------------------------------
def parse_chat_completion(resp) -> LLMResponse:
    """Convierte la respuesta del SDK en un LLMResponse (función pura, testeable)."""
    msg = resp.choices[0].message
    calls: list[ToolCall] = []
    for tc in getattr(msg, "tool_calls", None) or []:
        raw = tc.function.arguments or ""
        try:
            args = json.loads(raw) if raw else {}
            if not isinstance(args, dict):
                args = {"_invalid_json": raw}
        except json.JSONDecodeError:
            args = {"_invalid_json": raw}
        calls.append(ToolCall(id=tc.id or f"call_{uuid.uuid4().hex[:10]}", name=tc.function.name,
                              arguments=args, raw_arguments=raw))
    usage = None
    if getattr(resp, "usage", None) is not None:
        usage = {"prompt_tokens": getattr(resp.usage, "prompt_tokens", None),
                 "completion_tokens": getattr(resp.usage, "completion_tokens", None)}
    return LLMResponse(content=getattr(msg, "content", None), tool_calls=calls, usage=usage)


class OpenAICompatClient:
    def __init__(self, api_key: str, base_url: str, model: str, temperature: float | None = 0.2, client=None):
        self.model_name = model
        self.temperature = temperature
        if client is None:
            from openai import OpenAI  # import diferido: el mock no necesita el paquete

            client = OpenAI(api_key=api_key, base_url=base_url, timeout=60.0, max_retries=2)
        self._client = client

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        kwargs = dict(model=self.model_name, messages=messages, tools=tools, tool_choice="auto")
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        return parse_chat_completion(self._client.chat.completions.create(**kwargs))


# --------------------------------------------------------------------------
# Sustituto sin IA (solo desarrollo / tests)
# --------------------------------------------------------------------------
READ_TOOLS = ("get_market_data", "calculate_indicators", "get_portfolio", "calculate_risk", "get_trade_history")


class MockPolicyLLM:
    model_name = "MOCK (sin IA)"

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        names: dict[str, str] = {}
        results: dict[str, dict] = {}
        for m in messages:
            if m["role"] == "assistant":
                for tc in m.get("tool_calls") or []:
                    names[tc["id"]] = tc["function"]["name"]
            elif m["role"] == "tool":
                try:
                    results[names.get(m["tool_call_id"], "?")] = json.loads(m["content"])
                except json.JSONDecodeError:
                    pass

        n = len(messages)
        if "calculate_indicators" not in results:
            return LLMResponse(None, [ToolCall(f"mock_{n}_{i}", name, {}) for i, name in enumerate(READ_TOOLS)])

        prev = results.get("execute_virtual_order", {})
        ind, pf, risk = results["calculate_indicators"], results["get_portfolio"], results["calculate_risk"]
        rsi, pb, btc = ind["rsi14"], ind["percent_b"], pf["btc"]
        cited = {"price": ind["price"], "rsi14": rsi, "percent_b": pb}

        if prev.get("status") not in (None, "HOLD_RECORDED", "EXECUTED"):
            action, amount, why = "HOLD", 0.0, "la orden anterior fue rechazada; se opta por no operar."
        elif btc > 0 and (rsi >= 68 or pb >= 1.0 or pf["unrealized_pnl_pct"] <= -3.0):
            action, amount, why = "SELL", pf["btc"], "RSI alto / precio en banda superior / pérdida abierta: se cierra la posición."
        elif rsi <= 35 and risk["max_buy_btc"] > 0:
            action, amount, why = "BUY", round(risk["max_buy_btc"] * 0.5, 6), "RSI bajo: se compra la mitad del máximo permitido."
        else:
            action, amount, why = "HOLD", 0.0, "sin señal clara."
        args = {
            "action": action, "asset": "BTCUSDT", "amount": amount,
            "confidence": 0.5,
            "reason": f"[MOCK sin IA] RSI14={rsi} y %B={pb}: {why}",
            "cited_values": cited,
        }
        return LLMResponse(None, [ToolCall(f"mock_{n}_x", "execute_virtual_order", args)])


def build_llm(settings: Settings, kind: str | None = None) -> LLMClient:
    kind = (kind or settings.llm_provider).lower()
    if kind == "mock":
        return MockPolicyLLM()
    if not settings.llm_api_key:
        raise LLMConfigError(
            "Falta LLM_API_KEY. Copia .env.example a .env y completa tu clave "
            "(o ejecuta con --llm mock para probar sin IA)."
        )
    return OpenAICompatClient(settings.llm_api_key, settings.llm_base_url, settings.llm_model,
                              settings.llm_temperature)
