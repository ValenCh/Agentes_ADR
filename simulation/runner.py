"""Runner: el ciclo de control que rodea al agente (percibir -> decidir -> actuar -> registrar).

Orden de un paso:
 1. Avanza el mercado una vela (o pide la más reciente si es en vivo).
 2. Stop-loss automático (código): si la posición pierde demasiado, vende sin consultar al LLM.
 3. Filtro de eventos (código): decide si vale la pena "despertar" al agente.
    No decide QUÉ hacer; solo CUÁNDO consultar al modelo, para ahorrar llamadas.
 4. El agente razona con herramientas y decide; el sistema valida y ejecuta (simulado).
 5. Se registra todo en SQLite.
"""
from __future__ import annotations

import logging
from datetime import datetime

from agent.agent import AgentOutcome, TradingAgent
from agent.llm import LLMClient, build_llm
from agent.tools import ToolContext
from config import Settings
from market.data_provider import DataUnavailable, LiveProvider, ReplayProvider, load_csv
from market.indicators import MIN_CANDLES, latest_snapshot
from simulation.broker import SimulatedBroker
from simulation.portfolio import Portfolio
from simulation.risk import RiskManager
from simulation.scenarios import Scenario, build_portfolio, load_scenarios
from storage.database import Database

log = logging.getLogger("runner")

HEARTBEAT_STEPS = 24        # aunque no pase nada, consultar al agente cada N velas
PNL_WAKE_PCT = 3.0          # posición con ganancia/pérdida abierta >= 3 %


class EndOfData(Exception):
    """Se acabaron las velas del dataset."""


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _zone(rsi: float) -> str:
    return "low" if rsi <= 35 else "high" if rsi >= 65 else "mid"


class TradingSession:
    def __init__(self, settings: Settings, provider, llm: LLMClient, db: Database, portfolio: Portfolio,
                 mode: str, run_id: str, wake: str = "events", stride: int = 1,
                 scenario: Scenario | None = None):
        self.settings, self.provider, self.llm, self.db = settings, provider, llm, db
        self.portfolio = portfolio
        self.mode, self.run_id, self.wake, self.stride = mode, run_id, wake, stride
        self.scenario = scenario
        self.risk = RiskManager(settings)
        self.broker = SimulatedBroker(settings.fee_rate)
        self.agent = TradingAgent(llm, settings)
        self.step_no = 0
        self.first_price: float | None = None
        self.history: list[dict] = []
        self.equity_curve: list[dict] = []
        self._started = False
        self._prev_snapshot: dict | None = None
        self._steps_since_wake = 0

    # ------------------------------------------------------------------ control
    @property
    def can_step(self) -> bool:
        return (not self._started) or self.provider.can_advance(self.stride)

    def run(self, n: int) -> list[dict]:
        out = []
        for _ in range(n):
            if not self.can_step:
                break
            out.append(self.step())
        return out

    # ------------------------------------------------------------------ un paso
    def step(self) -> dict:
        if self._started:
            if not self.provider.advance(self.stride):
                raise EndOfData("No quedan velas en el dataset.")
        self._started = True
        self.step_no += 1

        price = self.provider.latest_price
        candle_time = self.provider.current_time.strftime("%Y-%m-%d %H:%M UTC")
        if self.first_price is None:
            self.first_price = price
        snap = latest_snapshot(self.provider.get_candles(self.settings.candles_window))
        before = self.portfolio.to_dict(price)
        log.info("---- Ciclo %d | vela %s | precio %s ----", self.step_no, candle_time, f"{price:,.2f}")

        base = dict(step=self.step_no, candle_time=candle_time, price=price, portfolio_before=before)

        # 1) Stop-loss automático (código, sin LLM)
        if self.risk.stop_loss_triggered(self.portfolio, price):
            record = self._stop_loss(base, price)
        else:
            wake_reason = self._wake_reason(snap, price)
            self._prev_snapshot = snap
            if wake_reason is None:
                self._steps_since_wake += 1
                record = self._skipped(base, price)
            else:
                self._steps_since_wake = 0
                log.info("Se consulta al agente. Motivo: %s", wake_reason)
                record = self._consult_agent(base, price, candle_time, wake_reason)

        record["portfolio_after"] = self.portfolio.to_dict(price)
        record["equity_after"] = record["portfolio_after"]["equity_usd"]
        self.db.add_decision(self.run_id, record)
        record["indicators"] = snap
        self.history.append(record)
        self.equity_curve.append({"step": self.step_no, "candle_time": candle_time, "price": price,
                                  "equity": record["equity_after"]})
        return record

    # ----------------------------------------------------------- ramas del paso
    def _stop_loss(self, base: dict, price: float) -> dict:
        sl = self.risk.stop_loss_price(self.portfolio)
        log.warning("STOP-LOSS: precio %s <= %s. Venta automática (sin consultar al agente).",
                    f"{price:,.2f}", f"{sl:,.2f}")
        fill = self.broker.execute(self.portfolio, "SELL", self.portfolio.btc, price)
        log.info("Orden simulada ejecutada: %s", fill.message)
        return dict(**base, source="risk_guard", wake_reason="stop-loss", action="SELL", amount=fill.amount,
                    confidence=None,
                    reason=f"STOP-LOSS automático: el precio {price:,.2f} cayó por debajo de {sl:,.2f} "
                           f"({self.settings.stop_loss_pct:.0%} bajo el precio medio de entrada). Decisión del sistema, no del agente.",
                    risk_status="FORCED", risk_notes=["Stop-loss aplicado por el gestor de riesgo."],
                    executed=fill.executed, fill_price=fill.price, fee=fill.fee, realized_pnl=fill.realized_pnl,
                    cited_values={}, tool_trace=[], llm_calls=0)

    def _skipped(self, base: dict, price: float) -> dict:
        log.info("Sin evento relevante: no se consulta al modelo (HOLD sin coste).")
        return dict(**base, source="skipped", wake_reason=None, action="HOLD", amount=0.0, confidence=None,
                    reason="Sin evento relevante: no se consultó al agente (ahorro de llamadas al modelo).",
                    risk_status=None, risk_notes=[], executed=False, cited_values={}, tool_trace=[], llm_calls=0)

    def _consult_agent(self, base: dict, price: float, candle_time: str, wake_reason: str) -> dict:
        ctx = ToolContext(settings=self.settings, provider=self.provider, portfolio=self.portfolio,
                          risk=self.risk, broker=self.broker, db=self.db, run_id=self.run_id, step=self.step_no)
        outcome: AgentOutcome = self.agent.run_cycle(ctx, self.step_no, candle_time)
        common = dict(**base, wake_reason=wake_reason, tool_trace=ctx.tool_trace, llm_calls=outcome.llm_calls)
        if outcome.status == "FAILED":
            return dict(**common, source="agent_failed", action="HOLD", amount=0.0, confidence=None,
                        reason=f"HOLD de seguridad: el agente no produjo una decisión válida ({outcome.error}).",
                        risk_status=None, risk_notes=[], executed=False, cited_values={})
        d, v, f = outcome.decision, outcome.verdict, outcome.fill
        return dict(**common, source="agent", action=d.action.value,
                    amount=f.amount if f else d.amount, confidence=d.confidence, reason=d.reason,
                    risk_status=v.status if v else None, risk_notes=v.notes if v else [],
                    executed=bool(f and f.executed), fill_price=f.price if f else None,
                    fee=f.fee if f else None, realized_pnl=f.realized_pnl if f else None,
                    cited_values=d.cited_values)

    # ------------------------------------------------------------- filtro eventos
    def _wake_reason(self, snap: dict, price: float) -> str | None:
        if self.wake == "always":
            return "modo always: se consulta en cada ciclo"
        if self.step_no == 1:
            return "primer ciclo"
        reasons: list[str] = []
        prev = self._prev_snapshot
        if prev:
            z, zp = _zone(snap["rsi14"]), _zone(prev["rsi14"])
            if z != zp and z != "mid":
                reasons.append(f"RSI entra en zona {'baja' if z == 'low' else 'alta'} ({snap['rsi14']})")
            if _sign(snap["price_vs_ema20_pct"]) != _sign(prev["price_vs_ema20_pct"]):
                reasons.append("el precio cruza la EMA20")
            if _sign(snap["ema20_vs_ema50_pct"]) != _sign(prev["ema20_vs_ema50_pct"]):
                reasons.append("cruce entre EMA20 y EMA50")
            outside = lambda pb: pb <= 0 or pb >= 1  # noqa: E731
            if outside(snap["percent_b"]) and not outside(prev["percent_b"]):
                reasons.append("el precio sale de las Bandas de Bollinger")
        if (self.portfolio.btc > 0 and abs(self.portfolio.unrealized_pnl_pct(price)) >= PNL_WAKE_PCT
                and self._steps_since_wake >= 3):
            reasons.append(f"posición con resultado abierto de {self.portfolio.unrealized_pnl_pct(price):+.1f}%")
        if self._steps_since_wake >= HEARTBEAT_STEPS:
            reasons.append(f"control periódico (cada {HEARTBEAT_STEPS} ciclos)")
        return "; ".join(reasons) if reasons else None

    # ------------------------------------------------------------------- resumen
    def summary(self) -> dict:
        price = self.provider.latest_price
        pf = self.portfolio
        agent_rows = [r for r in self.history if r["source"] in ("agent", "agent_failed")]
        buy_hold = ((price / self.first_price - 1) * 100) if self.first_price else 0.0
        return {
            "run_id": self.run_id,
            "steps": self.step_no,
            "price": round(price, 2),
            "equity_usd": round(pf.equity(price), 2),
            "total_pnl_usd": round(pf.total_pnl(price), 2),
            "total_return_pct": round(pf.total_return_pct(price), 2),
            "buy_and_hold_return_pct": round(buy_hold, 2),
            "realized_pnl_usd": round(pf.realized_pnl, 2),
            "fees_paid_usd": round(pf.fees_paid, 2),
            "trades_executed": sum(1 for r in self.history if r["executed"]),
            "agent_cycles": len(agent_rows),
            "skipped_cycles": sum(1 for r in self.history if r["source"] == "skipped"),
            "risk_guard_actions": sum(1 for r in self.history if r["source"] == "risk_guard"),
            "agent_failures": sum(1 for r in self.history if r["source"] == "agent_failed"),
            "rejected_by_risk": sum(1 for r in self.history if r.get("risk_status") == "REJECTED"),
            "llm_calls": sum(r["llm_calls"] for r in self.history),
            "llm_model": getattr(self.llm, "model_name", "?"),
        }


# ------------------------------------------------------------------- fábrica
def create_session(settings: Settings, *, mode: str = "scenario", scenario: str | None = None,
                   llm_kind: str | None = None, wake: str | None = None, stride: int | None = None,
                   start_index: int | None = None, db: Database | None = None,
                   run_id: str | None = None, llm: LLMClient | None = None) -> TradingSession:
    """Arma una sesión lista para ejecutar. mode: scenario | backtest | live."""
    # Los escenarios usan SIEMPRE el dataset de ejemplo: sus índices pertenecen a ese archivo.
    data_file = settings.resolve(settings.sample_data_path if mode == "scenario" else settings.data_path)
    if not data_file.exists():
        raise DataUnavailable(
            f"No existe el dataset {data_file}. Genera el de ejemplo con: python scripts/make_sample_data.py"
        )
    df = load_csv(data_file)
    llm = llm or build_llm(settings, llm_kind)
    db = db or Database(settings.resolve(settings.db_path))
    sc: Scenario | None = None

    if mode == "scenario":
        scenarios = load_scenarios(settings)
        if scenario not in scenarios:
            raise ValueError(f"Escenario desconocido: {scenario!r}. Disponibles: {', '.join(scenarios)}")
        sc = scenarios[scenario]
        provider = ReplayProvider(df, sc.start_index)
        portfolio = build_portfolio(sc, provider.latest_price, settings.initial_cash)
        wake = wake or "always"
        stride = stride or sc.stride
    elif mode == "backtest":
        provider = ReplayProvider(df, start_index if start_index is not None else 100)
        portfolio = Portfolio.new(settings.initial_cash)
        wake, stride = wake or "events", stride or 1
    elif mode == "live":
        fallback = ReplayProvider(df, len(df) - 1)
        provider = LiveProvider(settings.symbol, settings.interval, settings.candles_window, fallback)
        portfolio = Portfolio.new(settings.initial_cash)
        wake, stride = wake or "always", 1
    else:
        raise ValueError(f"Modo desconocido: {mode}")

    run_id = run_id or f"{mode}-{scenario or 'run'}-{datetime.now():%Y%m%d-%H%M%S}"
    db.create_run(run_id, mode, scenario, settings.symbol, portfolio.initial_cash,
                  getattr(llm, "model_name", None),
                  {"wake": wake, "stride": stride, "data": provider.source_name,
                   "max_trade_pct": settings.max_trade_pct, "max_btc_position": settings.max_btc_position,
                   "stop_loss_pct": settings.stop_loss_pct})
    return TradingSession(settings, provider, llm, db, portfolio, mode, run_id, wake, stride, sc)
