"""Prueba de humo de la interfaz: el script de Streamlit corre, ejecuta un ciclo y no lanza excepciones."""
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from config import ROOT  # noqa: E402


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "ui.db"))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_API_KEY", "")
    return AppTest.from_file(str(ROOT / "ui" / "app.py"), default_timeout=60)


def test_ui_loads_without_errors_and_shows_key_elements(app):
    app.run()
    assert not app.exception
    labels = {m.label for m in app.metric}
    assert {"Precio actual", "Saldo disponible", "BTC en cartera", "Valor total de la cartera",
            "Ganancia/pérdida simulada"} <= labels
    assert any("MOCK" in w.value for w in app.sidebar.warning)          # avisa que no es IA


def test_ui_runs_a_cycle_and_shows_decision_reason_and_history(app):
    app.run()
    next(b for b in app.button if b.key == "run1").click().run()
    assert not app.exception
    text = " ".join(m.value for m in app.markdown)
    assert "Motivo:" in text
    assert any("BUY" in s.value for s in app.success)                    # el escenario "dip" compra
    assert len(app.dataframe) >= 1                                      # historial visible


def test_ui_can_switch_to_backtest_and_run(app):
    app.run()
    app.sidebar.selectbox(key="source").select("Backtest sobre histórico")
    app.run()
    next(b for b in app.button if b.key == "start").click().run()
    next(b for b in app.button if b.key == "run5").click().run()
    assert not app.exception
