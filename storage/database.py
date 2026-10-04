"""Historial en SQLite (archivo local, sin servidor).

Una fila por ciclo del agente en la tabla `decisions`, con la cartera antes y
después, el motivo, el veredicto de riesgo y la traza de herramientas usadas.
Así se puede reconstruir en la exposición cómo se tomó cada decisión.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    mode         TEXT NOT NULL,
    scenario     TEXT,
    symbol       TEXT NOT NULL,
    initial_cash REAL NOT NULL,
    llm_model    TEXT,
    config_json  TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT NOT NULL,
    step             INTEGER NOT NULL,
    timestamp        TEXT NOT NULL,
    candle_time      TEXT,
    price            REAL,
    source           TEXT NOT NULL,   -- agent | risk_guard | skipped | agent_failed
    wake_reason      TEXT,
    action           TEXT NOT NULL,
    amount           REAL,
    confidence       REAL,
    reason           TEXT,
    risk_status      TEXT,
    risk_notes       TEXT,
    executed         INTEGER NOT NULL DEFAULT 0,
    fill_price       REAL,
    fee              REAL,
    realized_pnl     REAL,
    equity_after     REAL,
    portfolio_before TEXT,
    portfolio_after  TEXT,
    cited_values     TEXT,
    tool_trace       TEXT,
    llm_calls        INTEGER DEFAULT 0,
    FOREIGN KEY (run_id) REFERENCES runs (run_id)
);
CREATE INDEX IF NOT EXISTS idx_decisions_run ON decisions (run_id, step);
"""

JSON_COLUMNS = ("portfolio_before", "portfolio_after", "cited_values", "tool_trace", "risk_notes")


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        # Una conexión por operación: simple y seguro con Streamlit (varios hilos).
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    # -- runs ---------------------------------------------------------------
    def create_run(self, run_id: str, mode: str, scenario: str | None, symbol: str,
                   initial_cash: float, llm_model: str | None, config: dict | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?)",
                (run_id, datetime.now().isoformat(timespec="seconds"), mode, scenario, symbol,
                 initial_cash, llm_model, json.dumps(config or {}, default=str)),
            )

    def list_runs(self, limit: int = 30) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_run(self, run_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    # -- decisions ----------------------------------------------------------
    def add_decision(self, run_id: str, record: dict) -> int:
        rec = dict(record)
        for col in JSON_COLUMNS:
            if col in rec and not isinstance(rec[col], str):
                rec[col] = json.dumps(rec[col], default=str, ensure_ascii=False)
        rec["run_id"] = run_id
        rec.setdefault("timestamp", datetime.now().isoformat(timespec="seconds"))
        rec["executed"] = int(bool(rec.get("executed", False)))
        cols = ", ".join(rec.keys())
        marks = ", ".join("?" for _ in rec)
        with self._connect() as conn:
            cur = conn.execute(f"INSERT INTO decisions ({cols}) VALUES ({marks})", tuple(rec.values()))
            return int(cur.lastrowid)

    def list_decisions(self, run_id: str, include_skipped: bool = True, limit: int | None = None,
                       newest_first: bool = False) -> list[dict]:
        sql = "SELECT * FROM decisions WHERE run_id = ?"
        params: list = [run_id]
        if not include_skipped:
            sql += " AND source != 'skipped'"
        sql += " ORDER BY step " + ("DESC" if newest_first else "ASC")
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._decode(dict(r)) for r in rows]

    @staticmethod
    def _decode(row: dict) -> dict:
        for col in JSON_COLUMNS:
            if isinstance(row.get(col), str):
                try:
                    row[col] = json.loads(row[col])
                except json.JSONDecodeError:
                    pass
        return row
