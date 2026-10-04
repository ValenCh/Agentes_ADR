"""Gestión de riesgo: código determinista, no IA.

Es la última barrera antes de la ejecución. El agente puede equivocarse
(o alucinar); el RiskManager garantiza que ninguna orden rompa los límites.

Controles:
1. Saldo suficiente antes de comprar.
2. Posición suficiente antes de vender.
3. Capital máximo por operación (se recorta la cantidad).
4. Máximo de BTC en cartera (se recorta la cantidad).
5. Orden mínima.
6. Pérdida máxima acumulada: al superarla se bloquean las compras.
7. Stop-loss automático: venta forzada si la posición pierde X % (ver runner).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from config import Settings
from simulation.portfolio import Portfolio

BTC_DECIMALS = 8


def _floor8(x: float) -> float:
    return math.floor(max(x, 0.0) * 10**BTC_DECIMALS) / 10**BTC_DECIMALS


@dataclass
class RiskVerdict:
    status: str                      # APPROVED | ADJUSTED | REJECTED
    amount: float                    # cantidad final aprobada (BTC)
    notes: list[str] = field(default_factory=list)

    @property
    def approved(self) -> bool:
        return self.status in ("APPROVED", "ADJUSTED")

    def to_dict(self) -> dict:
        return {"status": self.status, "approved_amount_btc": self.amount, "notes": self.notes}


class RiskManager:
    def __init__(self, settings: Settings):
        self.s = settings

    # -- estado de riesgo (lo consulta el agente con calculate_risk) --------
    def buys_blocked_reason(self, pf: Portfolio, price: float) -> str | None:
        loss_pct = -pf.total_return_pct(price)
        if loss_pct >= self.s.max_loss_pct * 100:
            return (f"Pérdida acumulada {loss_pct:.1f}% >= límite {self.s.max_loss_pct * 100:.0f}%: "
                    "compras bloqueadas.")
        return None

    def max_buy_btc(self, pf: Portfolio, price: float) -> float:
        if self.buys_blocked_reason(pf, price):
            return 0.0
        trade_cap = pf.equity(price) * self.s.max_trade_pct / price
        affordable = pf.cash_usd / (price * (1 + self.s.fee_rate))
        position_room = self.s.max_btc_position - pf.btc
        return _floor8(min(trade_cap, affordable, position_room))

    def stop_loss_price(self, pf: Portfolio) -> float | None:
        entry = pf.avg_entry_price
        return entry * (1 - self.s.stop_loss_pct) if entry else None

    def stop_loss_triggered(self, pf: Portfolio, price: float) -> bool:
        sl = self.stop_loss_price(pf)
        return sl is not None and pf.btc > 0 and price <= sl

    def snapshot(self, pf: Portfolio, price: float) -> dict:
        blocked = self.buys_blocked_reason(pf, price)
        sl = self.stop_loss_price(pf)
        return {
            "equity_usd": round(pf.equity(price), 2),
            "current_btc": round(pf.btc, 8),
            "max_buy_btc": self.max_buy_btc(pf, price),
            "max_sell_btc": round(pf.btc, 8),
            "max_trade_usd": round(pf.equity(price) * self.s.max_trade_pct, 2),
            "max_trade_pct_of_equity": self.s.max_trade_pct * 100,
            "max_btc_position": self.s.max_btc_position,
            "min_order_usd": self.s.min_order_usd,
            "fee_rate_pct": self.s.fee_rate * 100,
            "stop_loss_pct": self.s.stop_loss_pct * 100,
            "stop_loss_price": round(sl, 2) if sl else None,
            "unrealized_pnl_pct": round(pf.unrealized_pnl_pct(price), 2),
            "total_return_pct": round(pf.total_return_pct(price), 2),
            "max_loss_pct": self.s.max_loss_pct * 100,
            "buys_blocked": blocked is not None,
            "buys_blocked_reason": blocked,
        }

    # -- validación de una decisión ---------------------------------------
    def validate(self, action: str, amount: float, pf: Portfolio, price: float) -> RiskVerdict:
        action = action.upper()
        if action == "HOLD":
            return RiskVerdict("APPROVED", 0.0, ["HOLD: sin operación."])
        if action == "BUY":
            return self._validate_buy(amount, pf, price)
        if action == "SELL":
            return self._validate_sell(amount, pf)
        return RiskVerdict("REJECTED", 0.0, [f"Acción desconocida: {action}"])

    def _validate_buy(self, amount: float, pf: Portfolio, price: float) -> RiskVerdict:
        notes: list[str] = []
        reason = self.buys_blocked_reason(pf, price)
        if reason:
            return RiskVerdict("REJECTED", 0.0, [reason])
        cost = amount * price * (1 + self.s.fee_rate)
        if cost > pf.cash_usd + 1e-9:
            return RiskVerdict(
                "REJECTED", 0.0,
                [f"Saldo insuficiente: la compra cuesta {cost:,.2f} USD (con comisión) y hay "
                 f"{pf.cash_usd:,.2f} USD. Máximo comprable ahora: {self.max_buy_btc(pf, price):.8f} BTC."],
            )
        final = amount
        trade_cap = _floor8(pf.equity(price) * self.s.max_trade_pct / price)
        if final > trade_cap:
            notes.append(f"Recortado por capital máximo por operación ({self.s.max_trade_pct:.0%} del capital): "
                         f"{final:.8f} -> {trade_cap:.8f} BTC.")
            final = trade_cap
        room = _floor8(self.s.max_btc_position - pf.btc)
        if final > room:
            if room <= 0:
                return RiskVerdict("REJECTED", 0.0,
                                   [f"Ya se alcanzó el máximo de {self.s.max_btc_position} BTC en cartera."])
            notes.append(f"Recortado por máximo de BTC en cartera ({self.s.max_btc_position}): "
                         f"{final:.8f} -> {room:.8f} BTC.")
            final = room
        if final * price < self.s.min_order_usd:
            return RiskVerdict("REJECTED", 0.0,
                               [f"Orden por debajo del mínimo de {self.s.min_order_usd:.0f} USD."])
        return RiskVerdict("ADJUSTED" if notes else "APPROVED", final, notes or ["Dentro de los límites."])

    def _validate_sell(self, amount: float, pf: Portfolio) -> RiskVerdict:
        if pf.btc <= 1e-9:
            return RiskVerdict("REJECTED", 0.0, ["No hay posición de BTC para vender."])
        if amount > pf.btc + 1e-9:
            return RiskVerdict("REJECTED", 0.0,
                               [f"No se puede vender {amount:.8f} BTC: la posición es {pf.btc:.8f} BTC."])
        return RiskVerdict("APPROVED", min(amount, pf.btc), ["Dentro de los límites."])
