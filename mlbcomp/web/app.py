"""Small read-only Flask view for local/API preview.

GitHub Pages uses the static files directly.  This server only serves the
already-exported projection and read-only status; it has no order endpoint.
"""
from __future__ import annotations

import json
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

from ..config import DATA, ROOT
from .. import db

app = Flask(__name__, static_folder=str(ROOT), static_url_path="")
ALLOWED_JSON = {
    "summary.json", "leaderboard.json", "strategies.json", "upcoming_bets.json",
    "open_positions.json", "bets_ledger.json", "research_experiments.json",
    "registry.json", "audit_checks.json", "irregularities.json", "kalshi_trades.json",
    "players.json",
}


@app.get("/")
def index():
    return send_from_directory(ROOT, "index.html")


@app.get("/assets/<path:name>")
def assets(name: str):
    # Kept explicit so API paths cannot read arbitrary workspace files.
    if name not in {"app.js", "styles.css"}:
        abort(404)
    return send_from_directory(ROOT, name)


@app.get("/data/<path:name>")
def data_file(name: str):
    if name not in ALLOWED_JSON:
        abort(404)
    path = DATA / name
    if not path.exists():
        abort(404)
    return send_from_directory(DATA, name, mimetype="application/json")


@app.get("/api/health")
def health():
    db.init_db()
    with db.connect() as conn:
        ledger_rows = int(conn.execute("SELECT COUNT(*) FROM immutable_ledger").fetchone()[0])
        issues = int(conn.execute("SELECT COUNT(*) FROM data_issues WHERE status='OPEN'").fetchone()[0])
        strategies = int(conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0])
    return jsonify({"service": "mlbcomp-read-only", "real_order_connector": False,
                    "ledger_rows": ledger_rows, "open_issues": issues,
                    "strategies": strategies})


@app.get("/api/ledger")
def ledger():
    limit = min(max(int(request.args.get("limit", 100)), 1), 5000)
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM immutable_ledger ORDER BY ledger_id DESC LIMIT ?", (limit,)).fetchall()]
    return jsonify(rows)


@app.get("/api/verification")
def verification():
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT check_id,scope,passed,details,created_at FROM verification_log ORDER BY check_id").fetchall()]
    return jsonify(rows)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
