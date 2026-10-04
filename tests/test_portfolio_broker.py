import pytest

from simulation.broker import SimulatedBroker
from simulation.portfolio import Portfolio

FEE = 0.001


@pytest.fixture
def broker():
    return SimulatedBroker(fee_rate=FEE)


def test_valid_buy_updates_portfolio(broker):
    pf = Portfolio.new(10_000)
    fill = broker.execute(pf, "BUY", 0.05, 60_000)
    assert fill.executed
    assert fill.notional == pytest.approx(3_000)
    assert fill.fee == pytest.approx(3.0)
    assert pf.cash_usd == pytest.approx(10_000 - 3_003)
    assert pf.btc == pytest.approx(0.05)
    assert pf.cost_basis_usd == pytest.approx(3_003)     # el costo incluye la comisión
    assert pf.fees_paid == pytest.approx(3.0)
    assert pf.avg_entry_price == pytest.approx(3_003 / 0.05)


def test_buy_without_funds_is_rejected_and_portfolio_untouched(broker):
    pf = Portfolio.new(100)
    fill = broker.execute(pf, "BUY", 0.05, 60_000)
    assert not fill.executed
    assert "Saldo insuficiente" in fill.message
    assert (pf.cash_usd, pf.btc, pf.fees_paid) == (100, 0.0, 0.0)


def test_valid_sell_realizes_pnl_and_clears_position(broker):
    pf = Portfolio.new(10_000)
    broker.execute(pf, "BUY", 0.05, 60_000)
    fill = broker.execute(pf, "SELL", 0.05, 66_000)
    assert fill.executed
    # ingresos netos 3300 - 3.3 = 3296.7 ; costo 3003 ; ganancia realizada 293.7
    assert fill.realized_pnl == pytest.approx(293.7)
    assert pf.realized_pnl == pytest.approx(293.7)
    assert pf.btc == 0 and pf.cost_basis_usd == 0
    assert pf.cash_usd == pytest.approx(10_000 - 3_003 + 3_296.7)
    assert pf.total_pnl(66_000) == pytest.approx(293.7)  # ya no hay posición abierta


def test_sell_without_position_is_rejected(broker):
    pf = Portfolio.new(10_000)
    fill = broker.execute(pf, "SELL", 0.01, 60_000)
    assert not fill.executed
    assert "No hay posición" in fill.message
    assert pf.cash_usd == 10_000


def test_sell_more_than_owned_is_rejected(broker):
    pf = Portfolio.new(10_000)
    broker.execute(pf, "BUY", 0.02, 60_000)
    fill = broker.execute(pf, "SELL", 0.05, 60_000)
    assert not fill.executed
    assert pf.btc == pytest.approx(0.02)


def test_partial_sell_keeps_proportional_cost_basis(broker):
    pf = Portfolio.new(10_000)
    broker.execute(pf, "BUY", 0.04, 60_000)
    cost = pf.cost_basis_usd
    broker.execute(pf, "SELL", 0.01, 60_000)
    assert pf.btc == pytest.approx(0.03)
    assert pf.cost_basis_usd == pytest.approx(cost * 0.75)
    assert pf.avg_entry_price == pytest.approx(cost / 0.04)   # el precio medio no cambia


def test_equity_and_unrealized_pnl(broker):
    pf = Portfolio.new(10_000)
    broker.execute(pf, "BUY", 0.05, 60_000)
    assert pf.equity(60_000) == pytest.approx(10_000 - 3.0)   # solo se pierde la comisión
    assert pf.unrealized_pnl(63_000) == pytest.approx(0.05 * 63_000 - 3_003)
    assert pf.total_return_pct(63_000) == pytest.approx(pf.total_pnl(63_000) / 10_000 * 100)


def test_hold_and_unknown_actions_do_nothing(broker):
    pf = Portfolio.new(10_000)
    assert not broker.execute(pf, "HOLD", 0, 60_000).executed
    assert not broker.execute(pf, "SHORT", 1, 60_000).executed
    assert not broker.execute(pf, "BUY", -1, 60_000).executed
    assert pf.cash_usd == 10_000 and pf.btc == 0


def test_with_position_uses_starting_equity_as_baseline():
    pf = Portfolio.with_position(cash=6_000, btc=0.06, entry_price=60_000, price_now=66_000)
    assert pf.initial_cash == pytest.approx(6_000 + 0.06 * 66_000)
    assert pf.total_pnl(66_000) == pytest.approx(0.0)
    assert pf.unrealized_pnl_pct(66_000) == pytest.approx(10.0)
