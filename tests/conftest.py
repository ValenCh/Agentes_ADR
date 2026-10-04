import numpy as np
import pandas as pd
import pytest

from agent.tools import ToolContext
from config import Settings
from market.data_provider import ReplayProvider
from simulation.broker import SimulatedBroker
from simulation.portfolio import Portfolio
from simulation.risk import RiskManager
from storage.database import Database


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(db_path=str(tmp_path / "test.db"))


@pytest.fixture
def candles() -> pd.DataFrame:
    """400 velas horarias sintéticas y deterministas (semilla fija)."""
    rng = np.random.default_rng(0)
    close = 60_000 * np.cumprod(1 + rng.normal(0, 0.004, 400))
    open_ = np.concatenate([[60_000], close[:-1]])
    return pd.DataFrame({
        "time": pd.date_range("2025-01-01", periods=400, freq="h", tz="UTC"),
        "open": open_,
        "high": np.maximum(open_, close) * 1.001,
        "low": np.minimum(open_, close) * 0.999,
        "close": close,
        "volume": rng.uniform(1, 10, 400),
    })


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(tmp_path / "hist.db")


@pytest.fixture
def make_ctx(candles, settings, db):
    """Fábrica de contextos de herramientas listos para usar."""

    def _make(portfolio=None, cursor=250, cfg=None, with_db=True):
        cfg = cfg or settings
        provider = ReplayProvider(candles, cursor)
        pf = portfolio or Portfolio.new(cfg.initial_cash)
        if with_db:
            db.create_run("t", "test", None, cfg.symbol, pf.initial_cash, "test")
        return ToolContext(cfg, provider, pf, RiskManager(cfg), SimulatedBroker(cfg.fee_rate),
                           db if with_db else None, "t")

    return _make
