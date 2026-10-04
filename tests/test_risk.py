import pytest

from config import Settings
from simulation.portfolio import Portfolio
from simulation.risk import RiskManager

PRICE = 60_000.0


@pytest.fixture
def risk():
    return RiskManager(Settings())


def test_valid_buy_is_approved(risk):
    v = risk.validate("BUY", 0.02, Portfolio.new(10_000), PRICE)
    assert v.status == "APPROVED" and v.amount == 0.02


def test_buy_without_funds_is_rejected(risk):
    v = risk.validate("BUY", 0.02, Portfolio.new(50), PRICE)
    assert v.status == "REJECTED"
    assert "Saldo insuficiente" in v.notes[0]


def test_buy_is_capped_by_max_trade_percentage(risk):
    # 25 % de 10.000 USD = 2.500 USD -> 2.500 / 60.000 = 0,04166666 BTC
    v = risk.validate("BUY", 0.08, Portfolio.new(10_000), PRICE)
    assert v.status == "ADJUSTED"
    assert v.amount == pytest.approx(0.04166666, abs=1e-8)
    assert "capital máximo por operación" in v.notes[0]


def test_buy_is_capped_by_max_btc_position():
    risk = RiskManager(Settings(max_trade_pct=1.0))   # aislamos el límite de posición
    pf = Portfolio.with_position(cash=10_000, btc=0.09, entry_price=30_000, price_now=30_000)
    v = risk.validate("BUY", 0.05, pf, 30_000)
    assert v.status == "ADJUSTED"
    assert v.amount == pytest.approx(0.01)


def test_buy_rejected_when_position_is_full():
    risk = RiskManager(Settings(max_trade_pct=1.0))
    pf = Portfolio.with_position(cash=10_000, btc=0.10, entry_price=30_000, price_now=30_000)
    v = risk.validate("BUY", 0.01, pf, 30_000)
    assert v.status == "REJECTED" and "máximo" in v.notes[0]


def test_buy_below_minimum_order_is_rejected(risk):
    v = risk.validate("BUY", 0.00005, Portfolio.new(10_000), PRICE)   # 3 USD
    assert v.status == "REJECTED" and "mínimo" in v.notes[0]


def test_sell_validations(risk):
    assert risk.validate("SELL", 0.01, Portfolio.new(10_000), PRICE).status == "REJECTED"
    pf = Portfolio.with_position(cash=5_000, btc=0.03, entry_price=PRICE, price_now=PRICE)
    assert risk.validate("SELL", 0.05, pf, PRICE).status == "REJECTED"      # más de lo que tiene
    ok = risk.validate("SELL", 0.03, pf, PRICE)
    assert ok.status == "APPROVED" and ok.amount == pytest.approx(0.03)


def test_hold_is_always_approved_with_zero_amount(risk):
    v = risk.validate("HOLD", 0, Portfolio.new(0), PRICE)
    assert v.approved and v.amount == 0


def test_stop_loss_triggers_only_below_threshold(risk):
    pf = Portfolio.with_position(cash=6_000, btc=0.06, entry_price=PRICE, price_now=PRICE)
    assert not risk.stop_loss_triggered(pf, PRICE * 0.96)
    assert risk.stop_loss_triggered(pf, PRICE * 0.94)
    assert not risk.stop_loss_triggered(Portfolio.new(10_000), 1.0)   # sin posición no hay stop


def test_buys_are_blocked_after_maximum_loss(risk):
    pf = Portfolio(initial_cash=10_000, cash_usd=7_900)               # -21 % del capital
    assert risk.buys_blocked_reason(pf, PRICE) is not None
    v = risk.validate("BUY", 0.01, pf, PRICE)
    assert v.status == "REJECTED" and "bloqueadas" in v.notes[0]
    assert risk.max_buy_btc(pf, PRICE) == 0.0


def test_snapshot_reports_limits(risk):
    pf = Portfolio.with_position(cash=6_000, btc=0.06, entry_price=PRICE, price_now=PRICE)
    snap = risk.snapshot(pf, PRICE)
    assert snap["max_sell_btc"] == pytest.approx(0.06)
    assert snap["stop_loss_price"] == pytest.approx(PRICE * 0.95)
    assert 0 < snap["max_buy_btc"] <= 0.04166666
    assert snap["buys_blocked"] is False
