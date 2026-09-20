"""ARENA AI — MLB autonomous betting research system website (Flask).

Paper-trading research only — no real-money wagering anywhere in this
system.  Pages:
  /                dashboard (status, verification, corpus, top findings)
  /leaderboard     competition leaderboards (REG real prices / PO proxy)
  /postseason      POSTSEASON CENTER (rounds, provenance, readiness)
  /upcoming        upcoming strategy bets (2026 live + PO readiness)
  /research        20 research questions + findings + experiments A-E
  /verification    verification log + source registry + provenance
  /audit           recent bets + audit trail
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pandas as pd
from flask import Flask, abort, render_template_string

from mlbcomp import db
from mlbcomp.config import FEAT, STARTING_BANKROLL

app = Flask(__name__)


# ------------------------------------------------------------------ helpers
def q(sql, params=None) -> pd.DataFrame:
    conn = db.connect()
    df = pd.read_sql(sql, conn, params=params)
    conn.close()
    return df


def stats_table() -> pd.DataFrame:
    from mlbcomp.engine.backtest import strategy_stats
    return strategy_stats()


CSS = """
:root { --bg:#0d1117; --panel:#161b22; --border:#30363d; --text:#e6edf3;
  --dim:#8b949e; --green:#3fb950; --red:#f85149; --yellow:#d29922;
  --blue:#58a6ff; --purple:#bc8cff; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text);
  font:14px/1.5 -apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif; }
a { color:var(--blue); text-decoration:none; }
a:hover { text-decoration:underline; }
header { background:var(--panel); border-bottom:1px solid var(--border);
  padding:14px 24px; display:flex; align-items:center; gap:24px;
  position:sticky; top:0; z-index:9; }
header h1 { font-size:17px; margin:0; font-weight:600; }
header h1 span { color:var(--purple); }
nav a { margin-right:16px; color:var(--dim); font-weight:500; }
nav a:hover { color:var(--text); text-decoration:none; }
main { max-width:1200px; margin:0 auto; padding:24px; }
h2 { font-size:19px; margin:28px 0 10px; border-bottom:1px solid var(--border);
  padding-bottom:6px; }
h3 { font-size:15px; margin:18px 0 8px; color:var(--blue); }
table { border-collapse:collapse; width:100%; margin:10px 0 20px; font-size:13px; }
th,td { border:1px solid var(--border); padding:6px 9px; text-align:right;
  white-space:nowrap; }
th { background:var(--panel); color:var(--dim); font-weight:600; text-align:right; }
th:first-child, td:first-child { text-align:left; }
tr:hover td { background:#1c2129; }
.pos { color:var(--green); font-weight:600; }
.neg { color:var(--red); font-weight:600; }
.mut { color:var(--dim); }
.badge { display:inline-block; padding:1px 8px; border-radius:10px;
  font-size:11px; font-weight:600; }
.b-pass { background:#12261e; color:var(--green); }
.b-fail { background:#2d1517; color:var(--red); }
.b-p1 { background:#10233a; color:var(--blue); }
.b-p2 { background:#2a1f3d; color:var(--purple); }
.b-warn { background:#2d2410; color:var(--yellow); }
.b-reg { background:#10233a; color:var(--blue); }
.b-po { background:#2a1f3d; color:var(--purple); }
.b-verb-SUPPORTED { color:var(--green); font-weight:600; }
.b-verb-CONTRADICTED { color:var(--red); font-weight:600; }
.b-verb-INCONCLUSIVE, .b-verb-NO_MEANINGFUL_EVIDENCE { color:var(--yellow); }
.b-verb-DATA_UNAVAILABLE { color:var(--dim); }
.card { background:var(--panel); border:1px solid var(--border);
  border-radius:8px; padding:16px 18px; margin:14px 0; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr));
  gap:14px; margin:14px 0; }
.grid .card { margin:0; }
.kpi { font-size:26px; font-weight:700; margin:2px 0; }
.kpi-l { color:var(--dim); font-size:12px; text-transform:uppercase;
  letter-spacing:.06em; }
.note { background:#10233a33; border-left:3px solid var(--blue);
  padding:10px 14px; margin:12px 0; font-size:13px; color:#c9d5e1; }
.warn { background:#2d241033; border-left:3px solid var(--yellow); }
pre { background:var(--panel); border:1px solid var(--border);
  border-radius:6px; padding:12px; overflow-x:auto; font-size:12px; }
.small { font-size:12px; color:var(--dim); }
"""


def page(title: str, body: str) -> str:
    return render_template_string(
        """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — ARENA MLB</title><style>{css}</style></head>
<body><header><h1>ARENA<span>·</span>MLB <span class="small">autonomous betting research (paper only)</span></h1>
<nav>
<a href="/">Dashboard</a><a href="/leaderboard">Leaderboards</a>
<a href="/postseason">Postseason Center</a><a href="/upcoming">Upcoming Bets</a>
<a href="/research">Research</a><a href="/verification">Verification</a>
<a href="/audit">Audit</a></nav></header>
<main>{body}</main>
<footer style="max-width:1200px;margin:30px auto;padding:16px 24px;color:var(--dim);font-size:12px;
border-top:1px solid var(--border)">
PAPER-TRADING RESEARCH SYSTEM — no real-money wagers are placed or supported.
Data: baseballr-data mirror (REG verified via independent odds source; PO verified corpus P1+P2) +
cesar-dx odds (REG 2019-25). See <a href="/verification">Verification</a> for provenance.
</footer></body></html>""".format(title=title, css=CSS, body=body))


def fmt(x, pct=False, money=False, digits=2):
    if x is None or (isinstance(x, float) and (x != x)):
        return '<span class="mut">—</span>'
    if pct:
        cls = "pos" if x > 0.0005 else ("neg" if x < -0.0005 else "mut")
        return f'<span class="{cls}">{x:+.{digits}%}</span>'
    if money:
        return f"${x:,.0f}"
    return f"{x:,.{digits}f}"


def cls(x):
    if x is None:
        return ""
    return "pos" if x > 0.0005 else ("neg" if x < -0.0005 else "")


# ------------------------------------------------------------------ routes
@app.route("/")
def dashboard():
    conn = db.connect()
    v = pd.read_sql("SELECT * FROM verification_log", conn)
    b = pd.read_sql("SELECT * FROM bets", conn)
    n_bets = len(b)
    n_settled = int((b.status == "SETTLED").sum())
    n_open = int((b.status.isin(["OPEN", "PROPOSED"])).sum())
    f = pd.read_sql("SELECT * FROM research_findings", conn)
    conn.close()

    po = pd.read_parquet(FEAT / "po_corpus.parquet")
    n_p1 = int((po.provenance == "mirror_verified_2025").sum())
    n_p2 = int((po.provenance == "reconstructed_winners").sum())
    g = pd.read_parquet(FEAT / "games.parquet")
    n_reg = int(g.round_code.isna().sum())
    n_sup = int((f.verdict == "SUPPORTED").sum())
    n_con = int((f.verdict == "CONTRADICTED").sum())

    st = stats_table()
    reg = st[st.env == "REG"].sort_values("roi", ascending=False)
    po_st = st[st.env != "REG"].sort_values("fc_roi", ascending=False)

    top_reg = "".join(
        f"<tr><td>{r.strategy_id}</td><td>{int(r.bets)}</td>"
        f"<td>{fmt(r.win_pct, pct=True)}</td><td>{fmt(r.roi, pct=True)}</td>"
        f"<td>{fmt(r.brier, digits=4)}</td></tr>"
        for r in reg.itertuples())
    top_po = "".join(
        f"<tr><td>{r.strategy_id}</td><td>{int(r.fc_bets)}</td>"
        f"<td>{fmt(r.eval_win_pct, pct=True)}</td>"
        f"<td>{fmt(r.fc_roi, pct=True)}</td>"
        f"<td>{fmt(r.brier, digits=4)}</td></tr>"
        for r in po_st.itertuples())

    ver_rows = "".join(
        f"<tr><td>{r.check_id}</td><td><span class='badge "
        f"{'b-pass' if r.passed else 'b-fail'}'>"
        f"{'PASS' if r.passed else 'FAIL'}</span></td>"
        f"<td style='text-align:left;white-space:normal'>{r.details[:170]}</td></tr>"
        for r in v.itertuples())

    body = f"""
<h2>System status</h2>
<div class="grid">
<div class="card"><div class="kpi-l">Verification</div>
<div class="kpi">{int(v.passed.sum())}/{len(v)}</div>
<div class="small">checks passing ({int(v.passed.sum())} PASS / {int((~v.passed.astype(bool)).sum())} FAIL)</div></div>
<div class="card"><div class="kpi-l">Games (REG 2015-2026)</div>
<div class="kpi">{n_reg:,}</div><div class="small">mirror, 2019-25 cross-verified vs odds source</div></div>
<div class="card"><div class="kpi-l">Verified PO corpus</div>
<div class="kpi">{len(po):,}</div>
<div class="small"><span class="badge b-p1">P1 {n_p1}</span> <span class="badge b-p2">P2 {n_p2}</span> 2019-2025</div></div>
<div class="card"><div class="kpi-l">Paper bets</div>
<div class="kpi">{n_bets:,}</div>
<div class="small">{n_settled:,} settled · {n_open} open/proposed (2026 live)</div></div>
<div class="card"><div class="kpi-l">Research findings</div>
<div class="kpi">{len(f)}</div>
<div class="small">{n_sup} supported · {n_con} contradicted · rest inconclusive/NA</div></div>
</div>

<div class="note"><b>Data integrity headline.</b> The mirror source's 2016-2024
postseason is partially <b>fabricated</b> (detected, documented, quarantined).
All backtests run on the <b>verified PO corpus</b> (P1 = 2025 reality-checked;
P2 = 2019-24 game winners reconstructed from documented public results, scores
not asserted). REG 2019-2025 odds are 100% cross-verified (15,442 games).
No verified market prices exist for any postseason game — PO "ROI" is a
labeled <b>fair-coin proxy</b>, not real-market ROI.
<a href="/verification">Full provenance →</a></div>

<h2>REGULAR SEASON — real market prices (2019-2025)</h2>
<table><tr><th>Strategy</th><th>Bets</th><th>Win %</th><th>ROI (real)</th><th>Brier</th></tr>
{top_reg}</table>
<div class="note">All REG moneyline strategies lose small at real 2019-25 prices:
the verified market is efficient against these models. ROI here is real
(verified odds); bankroll compounding shown on the leaderboards.</div>

<h2>POSTSEASON — verified corpus (2019-2025, no market prices)</h2>
<table><tr><th>Strategy</th><th>Picks</th><th>Win % (fair-coin)</th>
<th>FC-ROI (proxy)</th><th>Brier</th></tr>{top_po}</table>
<div class="note warn">PO picks settle against a <b>fair-coin proxy market
(+100, 2% flat)</b> because no verified postseason prices exist. This measures
model skill vs a 50/50 baseline — it is NOT evidence of real-market edge.
See <a href="/postseason">Postseason Center</a>.</div>
"""
    return page("Dashboard", body)


@app.route("/leaderboard")
def leaderboard():
    st = stats_table()
    from mlbcomp.engine.backtest import combined_view
    cv = combined_view()

    reg = st[st.env == "REG"].sort_values("roi", ascending=False)
    po = st[st.env != "REG"].sort_values("fc_roi", ascending=False)

    def row(r, is_po):
        if is_po:
            return (f"<tr><td>{r.strategy_id}</td><td>{r.name}</td>"
                    f"<td>{int(r.fc_bets)}</td><td>{fmt(r.eval_win_pct, pct=True)}</td>"
                    f"<td>{fmt(r.fc_roi, pct=True)}</td><td>{fmt(r.fc_bankroll, money=True)}</td>"
                    f"<td>{fmt(r.brier, digits=4)}</td><td>{fmt(r.log_loss, digits=4)}</td>"
                    f"<td>{fmt(r.fc_max_dd, pct=True)}</td></tr>")
        return (f"<tr><td>{r.strategy_id}</td><td>{r.name}</td>"
                f"<td>{int(r.bets)}</td><td>{fmt(r.win_pct, pct=True)}</td>"
                f"<td>{fmt(r.roi, pct=True)}</td><td>{fmt(r.bankroll, money=True)}</td>"
                f"<td>{fmt(r.brier, digits=4)}</td><td>{fmt(r.log_loss, digits=4)}</td>"
                f"<td>{fmt(r.max_dd, pct=True)}</td></tr>")

    reg_rows = "".join(row(r, False) for r in reg.itertuples())
    po_rows = "".join(row(r, True) for r in po.itertuples())

    cv_rows = "".join(
        f"<tr><td>{r.Index}</td><td>{int(r.bets)}</td>"
        f"<td>{fmt(r.pnl, money=True)}</td><td>{fmt(r.roi, pct=True)}</td>"
        f"<td>{int(r.reg_bets)}</td><td>{fmt(r.reg_roi, pct=True)}</td>"
        f"<td>{int(r.po_bets)}</td><td>{int(r.po_fc_bets)}</td>"
        f"<td>{fmt(r.po_fc_roi, pct=True)}</td>"
        f"<td class='mut'>{r.best_env}</td><td class='mut'>{r.worst_env}</td></tr>"
        for r in cv.itertuples())

    body = f"""
<h2>Competition leaderboards</h2>
<div class="note">Each strategy competes in its own environment with its own
bankroll ({STARTING_BANKROLL:,.0f} start). <b>REG</b> ROI uses verified 2019-25
market prices. <b>PO</b> rows use the fair-coin proxy (no verified PO prices
exist). ALL-MLB is a computed view that preserves the environment split.</div>

<h3>REGULAR SEASON — real prices</h3>
<table><tr><th>Strategy</th><th>Name</th><th>Bets</th><th>Win %</th>
<th>ROI</th><th>Bankroll</th><th>Brier</th><th>Log-loss</th><th>Max DD</th></tr>
{reg_rows}</table>

<h3>POSTSEASON — fair-coin proxy (no verified prices)</h3>
<table><tr><th>Strategy</th><th>Name</th><th>Picks</th><th>Win %</th>
<th>FC-ROI</th><th>FC-Bankroll</th><th>Brier</th><th>Log-loss</th><th>Max DD</th></tr>
{po_rows}</table>

<h3>ALL-MLB combined view (env split preserved)</h3>
<table><tr><th>Strategy</th><th>Bets</th><th>PnL</th><th>ROI</th>
<th>REG bets</th><th>REG ROI</th><th>PO bets</th><th>PO picks (FC)</th>
<th>PO FC-ROI</th><th>Best env</th><th>Worst env</th></tr>{cv_rows}</table>
<div class="note warn">Interpretation: PO win-rates vs a fair coin are positive
in WC/DS/LCS and <b>negative in WS</b>. Because no real PO prices exist, none of
these is a claim about beating a sportsbook — it is model skill on a 50/50
baseline, with per-round small samples.</div>
"""
    return page("Leaderboards", body)


@app.route("/postseason")
def postseason():
    st = stats_table()
    po = pd.read_parquet(FEAT / "po_corpus.parquet")
    po["home_win"] = (po.winner_team_id == po.home_team_id).astype(int)
    conn = db.connect()
    f = pd.read_sql("SELECT * FROM research_findings", conn)
    exps = pd.read_sql("SELECT * FROM experiments", conn)
    conn.close()
    fm = {r.q_id: r for r in f.itertuples()}

    rounds = ["WC", "DS", "LCS", "WS"]
    rsec = []
    for rd in rounds:
        sub = st[st.env == rd]
        n_games = int((po.round_code == rd).sum())
        hw = po[po.round_code == rd].home_win.mean()
        rows = "".join(
            f"<tr><td>{r.strategy_id}</td><td>{int(r.fc_bets)}</td>"
            f"<td>{fmt(r.eval_win_pct, pct=True)}</td>"
            f"<td>{fmt(r.fc_roi, pct=True)}</td><td>{fmt(r.brier, digits=4)}</td></tr>"
            for r in sub.itertuples()) or '<tr><td class="mut">—</td></tr>'
        rsec.append(f"""
<div class="card">
<h3 style="margin-top:0">{'Wild Card' if rd=='WC' else 'Division Series' if rd=='DS' else 'League Championship' if rd=='LCS' else 'World Series'}</h3>
<div class="small">{n_games} verified games (2019-25) · home win rate {hw:.1%}</div>
<table><tr><th>Strategy</th><th>Picks</th><th>Win % (FC)</th><th>FC-ROI</th><th>Brier</th></tr>{rows}</table>
</div>""")

    post_all = st[st.env == "POST"]
    post_rows = "".join(
        f"<tr><td>{r.strategy_id}</td><td>{int(r.fc_bets)}</td>"
        f"<td>{fmt(r.eval_win_pct, pct=True)}</td><td>{fmt(r.fc_roi, pct=True)}</td>"
        f"<td>{fmt(r.brier, digits=4)}</td></tr>" for r in post_all.itertuples())

    prov = po.groupby("season").provenance.value_counts().unstack(fill_value=0)
    prov_rows = "".join(
        f"<tr><td>{s}</td><td>{int(r.get('mirror_verified_2025', 0))}</td>"
        f"<td>{int(r.get('reconstructed_winners', 0))}</td></tr>"
        for s, r in prov.iterrows())

    exp_rows = "".join(
        f"<tr><td>{r.exp_id}</td><td style='text-align:left'>{r.name}</td>"
        f"<td>{int(r.n)}</td><td>{fmt(r.brier, digits=4)}</td>"
        f"<td>{fmt(r.roi, pct=True)}</td></tr>" for r in exps.itertuples())

    body = f"""
<h2>POSTSEASON CENTER</h2>
<div class="note">The postseason is a <b>separate competition</b> from the
regular season: distinct models, strategies, backtests, leaderboards and
paper ledgers. Data provenance:
<span class="badge b-p1">P1</span> 2025 = mirror, reality spot-checked, real
scores · <span class="badge b-p2">P2</span> 2019-2024 = game winners
reconstructed from documented public results (scores NOT asserted). The
mirror's fabricated 2016-2024 PO rows are quarantined and never used.</div>

<h3>Verified corpus by season</h3>
<table><tr><th>Season</th><th>P1 (verified scores)</th><th>P2 (winners reconstructed)</th></tr>
{prov_rows}</table>

<h3>POST OVERALL — cross-round models</h3>
<table><tr><th>Strategy</th><th>Picks</th><th>Win % (FC)</th><th>FC-ROI</th><th>Brier</th></tr>
{post_rows}</table>

<h3>Per-round competitions</h3>
{''.join(rsec)}

<h3>Permanent experiment framework (Models A-E, walk-forward)</h3>
<table><tr><th>Exp</th><th>Model</th><th>n</th><th>Brier</th><th>FC-ROI</th></tr>
{exp_rows}</table>
<div class="note">Out-of-sample evidence: <b>A (REG transfer)</b> is the
strong broad baseline (FC-ROI +18.4%, n=196). <b>C (dedicated series-state)</b>
leads on the elimination/clinching subset (+23.3%, n=60 — small). <b>E
(hierarchical)</b> beats A on win rate (+21.9%) but not on calibration.
<b>B (late-season)</b> is rejected (poor calibration). <b>D (round-specific)</b>
is mixed: strong in WC (+40.9%), negative in WS (-12.0%) — round identity
matters and must not be ignored.</div>

<h3>Series state engine (verified, point-in-time)</h3>
<div class="card small">Every PO game carries the exact state <i>before</i> the
game: series score, elimination/clinching flags, games remaining, days rest,
travel km, home-advantage games left. Computed with strict anti-leakage
(only earlier games). The <code>MLB_POST_SERIESSTATE_001</code> strategy bets
only elimination/clinching games: <b>{(fm.get('Q11').evidence)[:220] if 'Q11' in fm else ''}</b></div>

<h3>Upcoming 2026 postseason readiness</h3>
<div class="note">2026 PO games are not in the data yet (season live through
2026-09-19). When they arrive: all 8 POST strategies activate automatically on
the same verified-corpus pipeline; 532 live 2026 REG picks are already being
tracked as PROPOSED (awaiting market prices). See
<a href="/upcoming">Upcoming Strategy Bets</a>.</div>
"""
    return page("Postseason Center", body)


@app.route("/upcoming")
def upcoming():
    conn = db.connect()
    b = pd.read_sql(
        "SELECT * FROM bets WHERE status IN ('OPEN','PROPOSED') "
        "ORDER BY season DESC, game_pk", conn)
    st = pd.read_sql("SELECT * FROM strategies", conn)
    conn.close()
    smap = st.set_index("strategy_id")
    b = b.merge(smap[["name"]], left_on="strategy_id", right_index=True,
                how="left")
    b = b.sort_values(["strategy_id", "game_pk"], ascending=[True, False])
    rows = "".join(
        f"<tr><td>{r.season}</td><td>{r.strategy_id}</td>"
        f"<td class='mut'>{str(r.name)[:38]}</td><td>{r.market}</td>"
        f"<td>{r.selection}</td><td>{fmt(r.model_prob, digits=3)}</td>"
        f"<td>{fmt(r.fair_price, digits=3)}</td><td>{fmt(r.closing_price, digits=0)}</td>"
        f"<td><span class='badge b-reg'>{r.status}</span></td></tr>"
        for r in b.itertuples())

    po_st = stats_table()
    po_st = po_st[po_st.env != "REG"].sort_values("fc_roi", ascending=False)
    ready_rows = "".join(
        f"<tr><td>{r.strategy_id}</td><td>{r.env}</td>"
        f"<td>{int(r.fc_bets)}</td><td>{fmt(r.eval_win_pct, pct=True)}</td>"
        f"<td>{fmt(r.fc_roi, pct=True)}</td>"
        f"<td>{'READY' if r.fc_bets >= 20 else 'thin sample'}</td></tr>"
        for r in po_st.itertuples())

    n = len(b)
    body = f"""
<h2>UPCOMING POSTSEASON STRATEGY BETS & live 2026 picks</h2>
<div class="note">Live 2026 regular-season games have <b>no verified market
prices yet</b> (odds source ends 2025), so the model's picks are recorded as
<b>PROPOSED</b> paper bets — they become settleable the moment verified prices
exist. 532 such picks are currently tracked. This is the forward-testing
ledger: when the 2026 postseason starts, the POST strategies below activate
on the same pipeline and their bets appear here.</div>

<h3>POST strategies armed for 2026 (backtest form, fair-coin proxy)</h3>
<table><tr><th>Strategy</th><th>Env</th><th>Picks 19-25</th><th>Win %</th>
<th>FC-ROI</th><th>Status</th></tr>{ready_rows}</table>

<h3>Tracked 2026 picks ({n})</h3>
<table><tr><th>Season</th><th>Strategy</th><th>Name</th><th>Mkt</th>
<th>Selection</th><th>Model p</th><th>Fair</th><th>Mkt px</th><th>Status</th></tr>
{rows[:0] if n > 300 else rows}
{'<tr><td colspan="9" class="mut">showing first 300 of '+str(n)+' — full list in the bets table</td></tr>' if n > 300 else ''}
</table>
"""
    return page("Upcoming Bets", body)


@app.route("/research")
def research():
    conn = db.connect()
    qs = pd.read_sql("SELECT * FROM research_questions", conn)
    fs = pd.read_sql("SELECT * FROM research_findings", conn)
    exps = pd.read_sql("SELECT * FROM experiments", conn)
    conn.close()
    fm = {(r.q_id, r.env): r for r in fs.itertuples()}
    rows = []
    for r in qs.itertuples():
        f = fm.get((r.q_id, "POST")) or fm.get((r.q_id, "REG"))
        if f is None:
            rows.append(f"<tr><td>{r.q_id}</td>"
                        f"<td style='text-align:left'>{r.question}</td>"
                        f"<td class='mut'>—</td><td class='mut'>0</td>"
                        f"<td class='mut'>—</td></tr>")
            continue
        rows.append(f"""<tr><td>{r.q_id}</td>
<td style="text-align:left">{r.question}</td>
<td><span class="b-verb-{f.verdict}">{f.verdict}</span></td>
<td>{int(f.n)}</td>
<td style="text-align:left;white-space:normal;max-width:640px" class="small">{f.evidence}</td></tr>""")

    exp_rows = "".join(
        f"<tr><td>{r.exp_id}</td><td style='text-align:left'>{r.name}</td>"
        f"<td>{int(r.n)}</td><td>{fmt(r.brier, digits=4)}</td>"
        f"<td>{fmt(r.roi, pct=True)}</td>"
        f"<td style='text-align:left' class='small'>{r.details_json[:200]}</td></tr>"
        for r in exps.itertuples())

    body = f"""
<h2>Research program (20 questions, empirical)</h2>
<div class="note">Method: chronological walk-forward, strict anti-leakage,
small-sample discipline (Brier + log-loss + min sample + labeled provenance).
"FC-ROI" = fair-coin proxy (no verified PO prices). ROI for REG = real
verified prices. Every verdict cites its sample.</div>
<table><tr><th>Q</th><th>Question</th><th>Verdict</th><th>n</th><th>Evidence</th></tr>
{''.join(rows)}</table>

<h3>Experiments A-E (spec §16) — out-of-sample evidence decides</h3>
<table><tr><th>Exp</th><th>Model</th><th>n</th><th>Brier</th><th>FC-ROI</th><th>Details</th></tr>
{exp_rows}</table>
"""
    return page("Research", body)


@app.route("/verification")
def verification():
    conn = db.connect()
    v = pd.read_sql("SELECT * FROM verification_log ORDER BY check_id", conn)
    sr = pd.read_sql("SELECT * FROM source_registry ORDER BY source_id", conn)
    conn.close()

    vrows = "".join(
        f"<tr><td>{r.check_id}</td><td>{r.scope}</td>"
        f"<td><span class='badge {'b-pass' if r.passed else 'b-fail'}'>"
        f"{'PASS' if r.passed else 'FAIL'}</span></td>"
        f"<td style='text-align:left;white-space:normal;max-width:700px' class='small'>{r.details}</td></tr>"
        for r in v.itertuples())

    srows = "".join(
        f"<tr><td>{r.source_id}</td>"
        f"<td><span class='badge {'b-pass' if r.verified else 'b-warn'}'>"
        f"{'VERIFIED' if r.verified else 'REJECTED/UNVERIFIED'}</span></td>"
        f"<td>{r.url}</td><td>{r.coverage}</td>"
        f"<td style='text-align:left;white-space:normal;max-width:560px' class='small'>{r.notes or r.reject_reason or ''}</td></tr>"
        for r in sr.itertuples())

    body = f"""
<h2>Verification & source provenance</h2>
<div class="note">Every number on this site traces to one of these checks and
sources. Fabricated or unverifiable data is <b>quarantined, documented, and
never used</b> — that is a first-class feature, not a footnote.</div>
<h3>Verification checks ({int(v.passed.sum())}/{len(v)} PASS)</h3>
<table><tr><th>Check</th><th>Scope</th><th>Result</th><th>Details</th></tr>
{vrows}</table>
<h3>Source registry</h3>
<table><tr><th>Source</th><th>Status</th><th>URL</th><th>Coverage</th><th>Notes</th></tr>
{srows}</table>
<h3>Postseason provenance model</h3>
<div class="card small">
<b>P1</b> — 2025 PO (47 games): mirror data, reality spot-checked (all series
match documented results; WS LAD 4-3 TOR 7-game). Real scores → ML and totals
usable.<br>
<b>P2</b> — 2019-2024 PO (209 games): game winners reconstructed from
documented public results (matchups, series scores, per-game win patterns;
per-series confidence high/medium in
<code>mlbcomp/data_recon/po_results.py</code>). Scores NOT asserted → ML
settlement only; excluded from totals and score-based research.<br>
<b>Quarantined</b> — mirror's 2016/2021/2023/2024 PO (and parts of
2015/2019/2020/2022): fabricated (e.g. a "2016 WS" Cubs-Indians game on
2016-10-25 that never occurred; WS results flipped). Never used.<br>
<b>2015-2018 PO</b> — series-level public record only; supporting evidence,
not settled.
</div>
"""
    return page("Verification", body)


@app.route("/audit")
def audit():
    conn = db.connect()
    b = pd.read_sql("SELECT * FROM bets WHERE status='SETTLED' "
                    "ORDER BY made_at DESC LIMIT 200", conn)
    al = pd.read_sql("SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 50", conn)
    conn.close()
    brows = "".join(
        f"<tr><td>{r.season}</td><td>{r.strategy_id}</td><td>{r.env}</td>"
        f"<td>{r.market}</td><td>{r.selection}</td><td>{fmt(r.stake, money=True)}</td>"
        f"<td>{r.result}</td><td>{fmt(r.pnl, money=True)}</td>"
        f"<td>{fmt(r.closing_price, digits=0)}</td></tr>"
        for r in b.itertuples())
    arows = "".join(
        f"<tr><td>{r.ts}</td><td>{r.actor}</td><td>{r.action}</td>"
        f"<td style='text-align:left;white-space:normal' class='small'>{r.details[:200]}</td></tr>"
        for r in al.itertuples())

    body = f"""
<h2>Audit trail</h2>
<h3>Most recent settled paper bets (200)</h3>
<table><tr><th>Season</th><th>Strategy</th><th>Env</th><th>Mkt</th>
<th>Selection</th><th>Stake</th><th>Result</th><th>PnL</th><th>Mkt px</th></tr>
{brows}</table>
<h3>Audit log (50)</h3>
<table><tr><th>Time (UTC)</th><th>Actor</th><th>Action</th><th>Details</th></tr>
{arows}</table>
"""
    return page("Audit", body)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
