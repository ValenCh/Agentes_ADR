"""Interfaz Streamlit del agente de trading SIMULADO.

Ejecutar:  streamlit run ui/app.py

Muestra el ciclo completo: mercado -> herramientas -> agente -> decisión ->
validación de riesgo -> orden simulada -> cartera -> historial.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Streamlit agrega la carpeta ui/ al path, no la raíz del proyecto: lo hacemos nosotros.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from agent.llm import LLMConfigError  # noqa: E402
from config import load_settings  # noqa: E402
from market.data_provider import DataUnavailable  # noqa: E402
from market.indicators import compute_indicators  # noqa: E402
from simulation.runner import EndOfData, create_session  # noqa: E402
from simulation.scenarios import load_scenarios  # noqa: E402
from storage.database import Database  # noqa: E402

st.set_page_config(page_title="Agente de trading simulado", layout="wide")
settings = load_settings()
db = Database(settings.resolve(settings.db_path))

SOURCE_LABEL = {"agent": "Agente (LLM)", "risk_guard": "Stop-loss automático (código)",
                "skipped": "Sin consulta al agente", "agent_failed": "Fallo del agente -> HOLD de seguridad"}
DISCLAIMER = ("Proyecto educativo. Todo es una simulación con dinero virtual: los resultados no constituyen una "
              "estrategia financiera validada ni garantía de rentabilidad.")


def make_session(mode: str, scenario: str | None, llm_kind: str, wake: str | None, start_index: int | None):
    try:
        st.session_state["session"] = create_session(
            settings, mode=mode, scenario=scenario, llm_kind=llm_kind, wake=wake, start_index=start_index, db=db)
        st.session_state["last"] = None
        st.session_state.pop("error", None)
    except (LLMConfigError, DataUnavailable, ValueError) as exc:
        st.session_state["error"] = str(exc)


def history_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{
        "#": r["step"], "vela (UTC)": r["candle_time"], "precio": r["price"],
        "origen": SOURCE_LABEL.get(r["source"], r["source"]), "acción": r["action"],
        "BTC": r["amount"], "confianza": r["confidence"], "riesgo": r["risk_status"],
        "ejecutada": bool(r["executed"]), "valor cartera": r["equity_after"], "motivo": r["reason"],
    } for r in rows])


# ---------------------------------------------------------------- barra lateral
scenarios = load_scenarios(settings) if settings.resolve(settings.scenarios_path).exists() else {}
has_key = bool(settings.llm_api_key) and settings.llm_provider != "mock"

with st.sidebar:
    st.header("Configuración")
    brain = st.radio("Cerebro del agente", ["LLM real (API)", "Mock (sin IA, solo pruebas)"],
                     index=0 if has_key else 1, key="brain")
    if brain.startswith("Mock"):
        st.warning("Modo MOCK: decide con una regla fija, NO es IA. Úsalo solo para probar la interfaz.")
    elif not has_key:
        st.error("Falta LLM_API_KEY en el archivo .env.")
    source = st.selectbox("Datos", ["Escenario de demostración", "Backtest sobre histórico",
                                    "En vivo (Binance público)"], key="source")
    scenario = wake = start_index = None
    if source.startswith("Escenario"):
        scenario = st.selectbox("Escenario", list(scenarios), format_func=lambda k: scenarios[k].title, key="scenario")
        if scenario:
            st.caption(scenarios[scenario].description)
        mode = "scenario"
    elif source.startswith("Backtest"):
        start_index = int(st.number_input("Vela inicial", min_value=59, max_value=1000, value=100, step=10))
        wake = st.radio("¿Cuándo consultar al agente?", ["events", "always"], horizontal=True,
                        format_func=lambda w: "Solo ante eventos" if w == "events" else "En cada vela")
        mode = "backtest"
    else:
        mode = "live"
        st.caption("Usa velas reales públicas. Si no hay conexión, cae a datos locales de respaldo.")
    if st.button("Iniciar sesión nueva", type="primary", key="start"):
        make_session(mode, scenario, "mock" if brain.startswith("Mock") else "api", wake, start_index)
    st.divider()
    st.caption(DISCLAIMER)

# Primera carga: sesión lista para usar (escenario "dip")
if "session" not in st.session_state and "error" not in st.session_state and scenarios:
    make_session("scenario", "dip", "api" if has_key else "mock", None, None)

st.title("Agente autónomo de trading simulado")
st.caption(DISCLAIMER)

if "error" in st.session_state:
    st.error(st.session_state["error"])
session = st.session_state.get("session")

tab_live, tab_hist = st.tabs(["Sesión actual", "Historial guardado (SQLite)"])

# ---------------------------------------------------------------- sesión actual
with tab_live:
    if session is None:
        st.info("Elige la fuente de datos y pulsa «Iniciar sesión nueva».")
    else:
        remaining = None
        if session.scenario is not None:
            remaining = max(session.scenario.steps - session.step_no, 0)
        c1, c2, c3, c4 = st.columns([1, 1, 1.4, 3])
        n_to_run = 0
        if c1.button("Ejecutar 1 ciclo", key="run1"):
            n_to_run = 1
        if c2.button("Ejecutar 5 ciclos", key="run5"):
            n_to_run = 5
        if remaining and c3.button(f"Completar escenario ({remaining})", key="runrest"):
            n_to_run = remaining
        c4.caption(f"Sesión {session.run_id} · modelo: {getattr(session.llm, 'model_name', '?')} · "
                   f"datos: {session.provider.source_name}")
        if session.mode == "live" and session.provider.using_fallback:
            st.warning("No se pudo contactar la API pública: se usan datos locales de respaldo.")

        if n_to_run:
            with st.spinner("El agente está analizando el mercado y razonando..."):
                for _ in range(n_to_run):
                    try:
                        st.session_state["last"] = session.step()
                    except EndOfData:
                        st.info("Se acabaron las velas del dataset.")
                        break

        price = session.provider.latest_price
        pf = session.portfolio
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Precio actual", f"{price:,.2f} USD")
        m2.metric("Saldo disponible", f"{pf.cash_usd:,.2f} USD")
        m3.metric("BTC en cartera", f"{pf.btc:.6f}")
        m4.metric("Valor total de la cartera", f"{pf.equity(price):,.2f} USD")
        m5.metric("Ganancia/pérdida simulada", f"{pf.total_pnl(price):+,.2f} USD",
                  f"{pf.total_return_pct(price):+.2f}%")

        left, right = st.columns([2, 1])
        candles = compute_indicators(session.provider.get_candles(settings.candles_window)).tail(120)
        with left:
            st.subheader("Mercado")
            st.line_chart(candles.set_index("time")[["close", "ema20", "ema50", "bb_upper", "bb_lower"]]
                          .rename(columns={"close": "precio", "bb_upper": "Bollinger sup.", "bb_lower": "Bollinger inf."}))
        with right:
            st.subheader("Indicadores")
            last_row = candles.iloc[-1]
            st.dataframe(pd.DataFrame({
                "Indicador": ["RSI (14)", "EMA 20", "EMA 50", "SMA 20", "Bollinger sup.", "Bollinger inf.", "%B"],
                "Valor": [f"{last_row['rsi14']:.1f}", f"{last_row['ema20']:,.2f}", f"{last_row['ema50']:,.2f}",
                          f"{last_row['sma20']:,.2f}", f"{last_row['bb_upper']:,.2f}", f"{last_row['bb_lower']:,.2f}",
                          f"{last_row['percent_b']:.2f}"],
            }), hide_index=True)
            st.caption("Son evidencia para el agente, no reglas: él decide cómo interpretarlos.")

        st.subheader("Última decisión del agente")
        last = st.session_state.get("last")
        if not last:
            st.info("Aún no hay decisiones. Pulsa «Ejecutar 1 ciclo».")
        else:
            box = {"BUY": st.success, "SELL": st.error, "HOLD": st.info}[last["action"]]
            amount = f" {last['amount']:.6f} BTC" if last["action"] != "HOLD" else ""
            box(f"**{last['action']}**{amount} · vela {last['candle_time']} · precio {last['price']:,.2f} USD · "
                f"origen: {SOURCE_LABEL.get(last['source'], last['source'])}")
            st.markdown(f"**Motivo:** {last['reason']}")
            cols = st.columns(3)
            if last.get("confidence") is not None:
                cols[0].progress(float(last["confidence"]), text=f"Confianza del agente: {last['confidence']:.0%}")
            cols[1].write(f"**Validación de riesgo:** {last.get('risk_status') or '-'}")
            cols[2].write(f"**Orden simulada ejecutada:** {'sí' if last['executed'] else 'no'}")
            for note in last.get("risk_notes") or []:
                st.caption(f"Riesgo: {note}")
            if last.get("cited_values"):
                st.caption(f"Cifras citadas por el agente (verificadas contra las herramientas): {last['cited_values']}")
            with st.expander(f"Traza del ciclo: {len(last['tool_trace'])} herramientas · {last['llm_calls']} llamadas al LLM"):
                if last.get("wake_reason"):
                    st.write(f"Motivo por el que se consultó al agente: {last['wake_reason']}")
                for i, t in enumerate(last["tool_trace"], 1):
                    st.markdown(f"**{i}. `{t['tool']}`**" + (f" con argumentos `{t['args']}`" if t["args"] else ""))
                    st.json(t["result"], expanded=False)
                b, a = last["portfolio_before"], last["portfolio_after"]
                st.write(f"Cartera antes: {b['cash_usd']:,.2f} USD + {b['btc']} BTC → después: "
                         f"{a['cash_usd']:,.2f} USD + {a['btc']} BTC")

        if session.history:
            st.subheader("Evolución del valor de la cartera")
            st.line_chart(pd.DataFrame(session.equity_curve).set_index("step")["equity"])
            s = session.summary()
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Operaciones ejecutadas", s["trades_executed"])
            k2.metric("Llamadas al LLM", s["llm_calls"], help="Los ciclos sin evento relevante no consumen llamadas.")
            k3.metric("Ciclos sin consultar al agente", s["skipped_cycles"])
            k4.metric("Comprar y mantener (mismo tramo)", f"{s['buy_and_hold_return_pct']:+.2f}%")

        st.subheader("Historial de decisiones")
        show_skipped = st.checkbox("Incluir ciclos sin consulta al agente", value=False, key="show_skipped")
        rows = db.list_decisions(session.run_id, include_skipped=show_skipped)
        if rows:
            st.dataframe(history_frame(rows), hide_index=True)
        else:
            st.caption("Sin decisiones registradas todavía.")

# --------------------------------------------------------------------- historial
with tab_hist:
    runs = db.list_runs()
    if not runs:
        st.info("Todavía no hay ejecuciones guardadas.")
    else:
        chosen = st.selectbox("Ejecución", [r["run_id"] for r in runs], key="hist_run")
        run = db.get_run(chosen)
        rows = db.list_decisions(chosen, include_skipped=False)
        st.caption(f"Modo: {run['mode']} · escenario: {run['scenario'] or '-'} · modelo: {run['llm_model']} · "
                   f"capital inicial: {run['initial_cash']:,.2f} USD")
        if rows:
            st.dataframe(history_frame(rows), hide_index=True)
            pick = st.selectbox("Ver el detalle de una decisión", [r["step"] for r in rows], key="hist_step")
            r = next(x for x in rows if x["step"] == pick)
            st.markdown(f"**{r['action']}** · {r['candle_time']} · {r['price']:,.2f} USD — {r['reason']}")
            st.json({"portfolio_before": r["portfolio_before"], "portfolio_after": r["portfolio_after"],
                     "risk": {"status": r["risk_status"], "notes": r["risk_notes"]},
                     "cited_values": r["cited_values"], "tools_used": [t["tool"] for t in (r["tool_trace"] or [])]},
                    expanded=False)
        else:
            st.caption("Esta ejecución no tiene decisiones.")
