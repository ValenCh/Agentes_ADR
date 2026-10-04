"""Broker SIMULADO: la única pieza que modifica la cartera.

No hace ninguna llamada de red, no conoce ningún exchange ni ninguna clave.
Ejecuta al precio que le pasa el sistema (cierre de la vela actual) más una
comisión fija. Repite por su cuenta las comprobaciones básicas de saldo y
posición (defensa en profundidad, aunque el RiskManager ya las haya hecho).
"""
from __future__ import annotations

from dataclasses import dataclass

from simulation.portfolio import Portfolio

BTC_DECIMALS = 8
EPS = 1e-9


@dataclass
class Fill:
    executed: bool
    action: str
    amount: float = 0.0
    price: float = 0.0
    notional: float = 0.0
    fee: float = 0.0
    realized_pnl: float = 0.0
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "executed": self.executed,
            "action": self.action,
            "amount_btc": self.amount,
            "price": round(self.price, 2),
            "notional_usd": round(self.notional, 2),
            "fee_usd": round(self.fee, 4),
            "realized_pnl_usd": round(self.realized_pnl, 2),
            "message": self.message,
        }


class SimulatedBroker:
    def __init__(self, fee_rate: float = 0.001):
        self.fee_rate = fee_rate

    def execute(self, portfolio: Portfolio, action: str, amount: float, price: float) -> Fill:
        action = action.upper()
        if action == "HOLD":
            return Fill(False, "HOLD", message="HOLD: no se envió ninguna orden.")
        if action not in ("BUY", "SELL"):
            return Fill(False, action, message=f"Acción desconocida: {action}")
        amount = round(float(amount), BTC_DECIMALS)
        if amount <= 0 or price <= 0:
            return Fill(False, action, message="Cantidad o precio inválidos.")
        return self._buy(portfolio, amount, price) if action == "BUY" else self._sell(portfolio, amount, price)

    def _buy(self, pf: Portfolio, amount: float, price: float) -> Fill:
        notional = amount * price
        fee = notional * self.fee_rate
        total = notional + fee
        if total > pf.cash_usd + EPS:
            return Fill(False, "BUY", amount, price, notional, fee,
                        message=f"Saldo insuficiente: se necesitan {total:,.2f} USD y hay {pf.cash_usd:,.2f}.")
        pf.cash_usd -= total
        pf.btc = round(pf.btc + amount, BTC_DECIMALS)
        pf.cost_basis_usd += total  # el costo incluye la comisión
        pf.fees_paid += fee
        return Fill(True, "BUY", amount, price, notional, fee,
                    message=f"Compra simulada de {amount:.8f} BTC a {price:,.2f}.")

    def _sell(self, pf: Portfolio, amount: float, price: float) -> Fill:
        if pf.btc <= EPS:
            return Fill(False, "SELL", amount, price, message="No hay posición de BTC para vender.")
        if amount > pf.btc + EPS:
            return Fill(False, "SELL", amount, price,
                        message=f"No se puede vender {amount:.8f} BTC: solo hay {pf.btc:.8f}.")
        amount = min(amount, pf.btc)
        notional = amount * price
        fee = notional * self.fee_rate
        proceeds = notional - fee
        fraction = amount / pf.btc
        cost_removed = pf.cost_basis_usd * fraction
        realized = proceeds - cost_removed
        pf.cash_usd += proceeds
        pf.btc = round(pf.btc - amount, BTC_DECIMALS)
        pf.cost_basis_usd -= cost_removed
        if pf.btc <= EPS:
            pf.btc, pf.cost_basis_usd = 0.0, 0.0
        pf.realized_pnl += realized
        pf.fees_paid += fee
        return Fill(True, "SELL", amount, price, notional, fee, realized,
                    message=f"Venta simulada de {amount:.8f} BTC a {price:,.2f}.")
