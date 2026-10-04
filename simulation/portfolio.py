"""Cartera virtual. Solo dinero simulado en USD y BTC ficticios."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class Portfolio:
    initial_cash: float            # capital de referencia para medir ganancia/pérdida
    cash_usd: float
    btc: float = 0.0
    cost_basis_usd: float = 0.0    # costo total (incluye comisiones) del BTC que se tiene
    realized_pnl: float = 0.0      # ganancia realizada por ventas (neta de comisiones)
    fees_paid: float = 0.0

    # -- construcción ------------------------------------------------------
    @classmethod
    def new(cls, initial_cash: float) -> "Portfolio":
        return cls(initial_cash=initial_cash, cash_usd=initial_cash)

    @classmethod
    def with_position(cls, cash: float, btc: float, entry_price: float, price_now: float) -> "Portfolio":
        """Cartera que ya empieza con BTC (para escenarios de demo).

        `initial_cash` queda como el valor total al inicio, así la ganancia/pérdida
        mide solo lo que ocurre durante la sesión.
        """
        return cls(
            initial_cash=cash + btc * price_now,
            cash_usd=cash,
            btc=btc,
            cost_basis_usd=btc * entry_price,
        )

    def copy(self) -> "Portfolio":
        return Portfolio(**asdict(self))

    # -- métricas ----------------------------------------------------------
    def equity(self, price: float) -> float:
        return self.cash_usd + self.btc * price

    @property
    def avg_entry_price(self) -> float | None:
        return self.cost_basis_usd / self.btc if self.btc > 0 else None

    def unrealized_pnl(self, price: float) -> float:
        return self.btc * price - self.cost_basis_usd if self.btc > 0 else 0.0

    def unrealized_pnl_pct(self, price: float) -> float:
        if self.btc <= 0 or self.cost_basis_usd <= 0:
            return 0.0
        return (self.btc * price / self.cost_basis_usd - 1) * 100

    def total_pnl(self, price: float) -> float:
        return self.equity(price) - self.initial_cash

    def total_return_pct(self, price: float) -> float:
        return self.total_pnl(price) / self.initial_cash * 100

    def to_dict(self, price: float | None = None) -> dict:
        d = {
            "cash_usd": round(self.cash_usd, 2),
            "btc": round(self.btc, 8),
            "avg_entry_price": round(self.avg_entry_price, 2) if self.avg_entry_price else None,
            "cost_basis_usd": round(self.cost_basis_usd, 2),
            "realized_pnl_usd": round(self.realized_pnl, 2),
            "fees_paid_usd": round(self.fees_paid, 2),
            "initial_cash_usd": round(self.initial_cash, 2),
        }
        if price is not None:
            d["price"] = round(price, 2)
            d["equity_usd"] = round(self.equity(price), 2)
            d["unrealized_pnl_usd"] = round(self.unrealized_pnl(price), 2)
            d["unrealized_pnl_pct"] = round(self.unrealized_pnl_pct(price), 2)
            d["total_pnl_usd"] = round(self.total_pnl(price), 2)
            d["total_return_pct"] = round(self.total_return_pct(price), 2)
        return d
