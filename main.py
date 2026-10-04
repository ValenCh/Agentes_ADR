"""Punto de entrada por línea de comandos.

Ejemplos:
    python main.py check                       # verifica configuración, datos y modelo
    python main.py scenarios                   # lista escenarios de demostración
    python main.py demo --scenario dip         # demo con el LLM real
    python main.py demo --scenario dip --llm mock   # prueba sin IA (solo desarrollo)
    python main.py backtest --steps 200        # simulación vela por vela
    python main.py history                     # historial de la última ejecución
    python main.py live                        # 1 ciclo con velas reales (datos públicos)

PROYECTO EDUCATIVO: todo es simulado, con dinero virtual. No es asesoramiento financiero.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from agent.llm import LLMConfigError, build_llm
from agent.tools import TOOL_SCHEMAS
from config import Settings, load_settings
from market.data_provider import DataUnavailable, load_csv
from simulation.runner import EndOfData, TradingSession, create_session
from simulation.scenarios import load_scenarios
from storage.database import Database

DISCLAIMER = ("Simulación educativa con dinero virtual. Los resultados NO constituyen una estrategia validada "
              "ni garantía de rentabilidad.")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S", stream=sys.stdout, force=True)
    for noisy in ("httpx", "httpcore", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def print_summary(s: dict) -> None:
    print("\n" + "=" * 64)
    print(f" RESUMEN  ({s['run_id']})")
    print("=" * 64)
    print(f" Modelo               : {s['llm_model']}")
    print(f" Ciclos ejecutados    : {s['steps']}  (agente: {s['agent_cycles']}, sin consultar: {s['skipped_cycles']}, "
          f"stop-loss: {s['risk_guard_actions']})")
    print(f" Llamadas al LLM      : {s['llm_calls']}")
    print(f" Operaciones          : {s['trades_executed']}   rechazadas por riesgo: {s['rejected_by_risk']}   "
          f"fallos del agente: {s['agent_failures']}")
    print(f" Precio final         : {s['price']:,.2f} USD")
    print(f" Valor de la cartera  : {s['equity_usd']:,.2f} USD")
    print(f" Ganancia/perdida     : {s['total_pnl_usd']:+,.2f} USD  ({s['total_return_pct']:+.2f}%)")
    print(f" Comparacion 'comprar y mantener' en el mismo tramo: {s['buy_and_hold_return_pct']:+.2f}%")
    print(f" Comisiones simuladas : {s['fees_paid_usd']:,.2f} USD")
    print("-" * 64)
    print(" " + DISCLAIMER)
    print("=" * 64)


def print_history(rows: list[dict]) -> None:
    if not rows:
        print("(sin decisiones registradas)")
        return
    print(f"{'#':>3} {'vela (UTC)':<17} {'precio':>10} {'origen':<12} {'accion':<6} {'cant. BTC':>10} "
          f"{'conf':>5} {'riesgo':<9} {'ejec':<4} {'equity':>10}")
    for r in rows:
        conf = f"{r['confidence']:.2f}" if r.get("confidence") is not None else "-"
        print(f"{r['step']:>3} {str(r['candle_time'])[:16]:<17} {r['price']:>10,.2f} {r['source']:<12} "
              f"{r['action']:<6} {(r['amount'] or 0):>10.6f} {conf:>5} {str(r['risk_status'] or '-'):<9} "
              f"{'si' if r['executed'] else 'no':<4} {(r['equity_after'] or 0):>10,.2f}")
    print()
    for r in rows:
        if r["source"] != "skipped":
            print(f"  #{r['step']} [{r['action']}] {r['reason']}")


def run_and_report(session: TradingSession, n: int) -> None:
    session.run(n)
    print_summary(session.summary())


# ----------------------------------------------------------------- comandos
def cmd_check(args, settings: Settings) -> int:
    ok = True
    print("== Configuración ==")
    print(f" Proveedor LLM : {settings.llm_provider}   modelo: {settings.llm_model}")
    print(f" Base URL      : {settings.llm_base_url}")
    print(f" API key       : {'configurada' if settings.llm_api_key else 'NO configurada'}")
    print(f" Base de datos : {settings.resolve(settings.db_path)}")
    print("== Datos ==")
    try:
        df = load_csv(settings.resolve(settings.data_path))
        print(f" OK: {len(df)} velas en {settings.data_path} ({df['time'].iloc[0]:%Y-%m-%d} -> {df['time'].iloc[-1]:%Y-%m-%d})")
        print(f" Escenarios: {', '.join(load_scenarios(settings))}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f" ERROR: {exc}\n Solución: python scripts/make_sample_data.py")
    print("== Modelo de lenguaje ==")
    if settings.llm_provider == "mock":
        print(" Modo MOCK activo (sin IA). Para usar un LLM real: LLM_PROVIDER=api en .env")
        return 0 if ok else 1
    try:
        llm = build_llm(settings)
        resp = llm.chat([{"role": "user", "content": "Para probar la conexión, llama a la herramienta get_market_data."}],
                        TOOL_SCHEMAS)
        if resp.tool_calls:
            print(f" OK: el modelo responde y soporta herramientas (pidió: {resp.tool_calls[0].name}).")
        else:
            ok = False
            print(" ATENCIÓN: el modelo respondió pero NO llamó a ninguna herramienta. "
                  "Un agente necesita un modelo con soporte de function calling.")
    except LLMConfigError as exc:
        ok = False
        print(f" ERROR: {exc}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f" ERROR al llamar al modelo: {type(exc).__name__}: {exc}")
        print(" Revisa LLM_API_KEY, LLM_BASE_URL y LLM_MODEL en tu .env")
        if "404" in str(exc) or "not found" in str(exc).lower() or "no longer available" in str(exc).lower():
            print(" Parece que el nombre del modelo ya no es válido. Mira cuáles ve tu clave con:  python main.py models")
    return 0 if ok else 1


def cmd_models(args, settings: Settings) -> int:
    """Lista los modelos que ve tu clave (útil cuando el nombre del modelo quedó viejo)."""
    if not settings.llm_api_key:
        print("ERROR: falta LLM_API_KEY en .env")
        return 1
    from openai import OpenAI

    try:
        client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url, timeout=30.0)
        ids = sorted(m.id.removeprefix("models/") for m in client.models.list())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR al listar modelos: {type(exc).__name__}: {exc}")
        return 1
    shown = [i for i in ids if args.filter.lower() in i.lower()]
    print(f"{len(shown)} modelos disponibles para tu clave (filtro: '{args.filter}'):")
    for i in shown:
        print(f"  {i}")
    print("\nCopia uno en LLM_MODEL (archivo .env) y ejecuta: python main.py check")
    print("Elige un modelo de texto con soporte de function calling; evita los de imagen, audio o embeddings.")
    return 0


def cmd_scenarios(args, settings: Settings) -> int:
    for sc in load_scenarios(settings).values():
        print(f"\n[{sc.name}] {sc.title}\n  {sc.description}\n  Qué observar: {sc.watch}\n  "
              f"({sc.steps} ciclos, avanza {sc.stride} velas por ciclo)")
    return 0


def cmd_demo(args, settings: Settings) -> int:
    scenarios = load_scenarios(settings)
    names = list(scenarios) if args.scenario == "all" else [args.scenario]
    db = Database(settings.resolve(settings.db_path))
    for name in names:
        if name not in scenarios:
            print(f"Escenario desconocido: {name}. Disponibles: {', '.join(scenarios)}, all")
            return 2
        sc = scenarios[name]
        print(f"\n######## ESCENARIO: {sc.title} ########\n{sc.description}\nQué observar: {sc.watch}\n")
        session = create_session(settings, mode="scenario", scenario=name, llm_kind=args.llm, wake=args.wake, db=db)
        run_and_report(session, args.steps or sc.steps)
    return 0


def cmd_backtest(args, settings: Settings) -> int:
    session = create_session(settings, mode="backtest", llm_kind=args.llm, wake=args.wake, stride=args.stride,
                             start_index=args.start)
    run_and_report(session, args.steps)
    return 0


def cmd_live(args, settings: Settings) -> int:
    session = create_session(settings, mode="live", llm_kind=args.llm)
    if session.provider.using_fallback:
        print("AVISO: no se pudo contactar la API pública; se usan datos locales de respaldo.")
    for i in range(args.steps):
        session.step()
        if i < args.steps - 1:
            time.sleep(args.sleep)
    print_summary(session.summary())
    return 0


def cmd_history(args, settings: Settings) -> int:
    db = Database(settings.resolve(settings.db_path))
    run_id = args.run_id
    if not run_id:
        runs = db.list_runs(1)
        if not runs:
            print("Todavía no hay ejecuciones registradas.")
            return 0
        run_id = runs[0]["run_id"]
    print(f"Historial de {run_id}\n")
    rows = db.list_decisions(run_id, include_skipped=args.all)
    print_history(rows[-args.limit:])
    return 0


def cmd_runs(args, settings: Settings) -> int:
    for r in Database(settings.resolve(settings.db_path)).list_runs():
        print(f"{r['run_id']:<40} {r['mode']:<9} {r['scenario'] or '-':<9} {r['llm_model'] or '-'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Agente autónomo de trading SIMULADO (proyecto educativo).")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    llm_help = "api = LLM real (por defecto), mock = sin IA (solo desarrollo)"

    sub.add_parser("check", help="verifica configuración, datos y conexión con el modelo").set_defaults(fn=cmd_check)
    m = sub.add_parser("models", help="lista los modelos disponibles para tu API key")
    m.add_argument("--filter", default="flash", help="texto a buscar en el nombre (por defecto: flash; usa '' para todos)")
    m.set_defaults(fn=cmd_models)
    sub.add_parser("scenarios", help="lista los escenarios de demostración").set_defaults(fn=cmd_scenarios)
    sub.add_parser("runs", help="lista las ejecuciones guardadas").set_defaults(fn=cmd_runs)

    d = sub.add_parser("demo", help="ejecuta un escenario de demostración")
    d.add_argument("--scenario", default="dip", help="dip | rally | sideways | crash | all")
    d.add_argument("--steps", type=int, default=None)
    d.add_argument("--wake", choices=["always", "events"], default=None)
    d.add_argument("--llm", choices=["api", "mock"], default=None, help=llm_help)
    d.set_defaults(fn=cmd_demo)

    b = sub.add_parser("backtest", help="simulación vela por vela sobre el dataset")
    b.add_argument("--start", type=int, default=100, help="índice de la primera vela")
    b.add_argument("--steps", type=int, default=150)
    b.add_argument("--stride", type=int, default=1, help="velas que avanza por ciclo")
    b.add_argument("--wake", choices=["always", "events"], default="events")
    b.add_argument("--llm", choices=["api", "mock"], default=None, help=llm_help)
    b.set_defaults(fn=cmd_backtest)

    lv = sub.add_parser("live", help="ciclos con velas reales de datos públicos (opcional)")
    lv.add_argument("--steps", type=int, default=1)
    lv.add_argument("--sleep", type=float, default=60.0)
    lv.add_argument("--llm", choices=["api", "mock"], default=None, help=llm_help)
    lv.set_defaults(fn=cmd_live)

    h = sub.add_parser("history", help="muestra las decisiones guardadas")
    h.add_argument("--run-id", default=None)
    h.add_argument("--limit", type=int, default=30)
    h.add_argument("--all", action="store_true", help="incluir ciclos sin consulta al agente")
    h.set_defaults(fn=cmd_history)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    settings = load_settings()
    try:
        return args.fn(args, settings)
    except (LLMConfigError, DataUnavailable, ValueError) as exc:
        print(f"\nERROR: {exc}")
        return 1
    except EndOfData as exc:
        print(f"\n{exc}")
        return 0
    except KeyboardInterrupt:
        print("\nInterrumpido.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
