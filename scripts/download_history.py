"""Descarga velas REALES públicas de Binance y las guarda en un CSV.

Solo lee datos públicos de mercado (sin claves, sin cuenta). Se usa el mismo
módulo de datos del proyecto, que solo permite el endpoint público de velas.

Uso:
    python scripts/download_history.py --days 45 --out data/btcusdt_1h_real.csv

Después, para usarlo en un backtest, en tu .env:
    DATA_PATH=data/btcusdt_1h_real.csv
    python main.py backtest --steps 200

Nota: los escenarios de `demo` siguen usando el dataset de ejemplo (sus índices
pertenecen a ese archivo).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from market.data_provider import DataUnavailable, fetch_klines  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Descarga velas públicas de Binance a CSV.")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="1h", help="solo 1h está probado con el resto del proyecto")
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--out", default="data/btcusdt_1h_real.csv")
    args = ap.parse_args()

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - args.days * 24 * 3600 * 1000
    frames: list[pd.DataFrame] = []
    try:
        while start_ms < now_ms:
            df = fetch_klines(args.symbol, args.interval, limit=1000, start_ms=start_ms)
            frames.append(df)
            print(f"  descargadas {len(df)} velas hasta {df['time'].iloc[-1]:%Y-%m-%d %H:%M}")
            if len(df) < 1000:
                break
            start_ms = int(df["time"].iloc[-1].timestamp() * 1000) + 1
    except DataUnavailable as exc:
        print(f"ERROR: {exc}\nRevisa tu conexión (o si tu red bloquea Binance). Puedes seguir con el CSV de ejemplo.")
        return 1

    out = pd.concat(frames).drop_duplicates("time").sort_values("time").reset_index(drop=True)
    path = ROOT / args.out
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)
    print(f"{len(out)} velas guardadas en {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
