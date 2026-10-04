"""El agente: bucle explícito percibir -> razonar -> usar herramientas -> actuar.

No hay framework: el ciclo es este `for`. En cada vuelta el LLM recibe la
conversación, pide herramientas, el sistema las ejecuta y le devuelve los
resultados (OBSERVACIÓN), y el modelo vuelve a razonar. El ciclo termina cuando
el agente llama a `execute_virtual_order` con una decisión aceptada, o cuando
se agotan los pasos (en cuyo caso el sistema opta por HOLD, la opción segura).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from agent.llm import LLMClient
from agent.prompts import build_cycle_message, build_system_prompt
from agent.schemas import TradeDecision
from agent.tools import ToolBox, ToolContext
from config import Settings
from simulation.broker import Fill
from simulation.risk import RiskVerdict

log = logging.getLogger("agent")


@dataclass
class AgentOutcome:
    status: str                      # EXECUTED | HOLD | REJECTED | FAILED
    decision: TradeDecision | None
    verdict: RiskVerdict | None
    fill: Fill | None
    llm_calls: int
    error: str | None = None


def _extract_json(text: str | None) -> dict | None:
    """Plan B: algunos modelos responden con JSON en texto en vez de llamar a la herramienta."""
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


class TradingAgent:
    def __init__(self, llm: LLMClient, settings: Settings):
        self.llm = llm
        self.settings = settings
        self.system_prompt = build_system_prompt(settings)

    def run_cycle(self, ctx: ToolContext, cycle_number: int, candle_time: str) -> AgentOutcome:
        toolbox = ToolBox(ctx)
        messages: list[dict] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": build_cycle_message(cycle_number, candle_time)},
        ]
        llm_calls, error = 0, None

        for _ in range(self.settings.max_agent_steps):
            try:
                resp = self.llm.chat(messages, toolbox.schemas())
            except Exception as exc:  # noqa: BLE001 - un fallo del LLM nunca debe romper la simulación
                error = f"{type(exc).__name__}: {exc}"
                log.error("Fallo al consultar el LLM: %s", error)
                break
            llm_calls += 1

            if resp.tool_calls:
                messages.append(resp.assistant_message())
                for tc in resp.tool_calls:
                    result = toolbox.call(tc.name, tc.arguments)
                    messages.append({
                        "role": "tool", "tool_call_id": tc.id,
                        "content": json.dumps(result, ensure_ascii=False, default=str),
                    })
                    if ctx.finished:
                        break
            else:
                data = _extract_json(resp.content)
                messages.append({"role": "assistant", "content": resp.content or ""})
                if data and "action" in data:
                    result = toolbox.call("execute_virtual_order", data)
                    messages.append({"role": "user", "content":
                                     "Resultado de execute_virtual_order: " + json.dumps(result, ensure_ascii=False, default=str)})
                else:
                    messages.append({"role": "user", "content":
                                     "Debes usar las herramientas y terminar llamando a execute_virtual_order."})
            if ctx.finished:
                break

        return self._outcome(ctx, llm_calls, error)

    @staticmethod
    def _outcome(ctx: ToolContext, llm_calls: int, error: str | None) -> AgentOutcome:
        if ctx.final_status in ("EXECUTED", "HOLD") and ctx.decision is not None:
            return AgentOutcome(ctx.final_status, ctx.decision, ctx.verdict, ctx.fill, llm_calls, error)
        if ctx.final_status == "REJECTED" and ctx.decision is not None:
            return AgentOutcome("REJECTED", ctx.decision, ctx.verdict, None, llm_calls, error)
        reason = error or ("; ".join(ctx.last_errors) if ctx.last_errors else "El agente no llegó a una decisión válida.")
        log.warning("Ciclo sin decisión válida -> HOLD de seguridad (%s)", reason)
        return AgentOutcome("FAILED", None, None, None, llm_calls, reason)
