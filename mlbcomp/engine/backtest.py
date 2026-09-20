"""Walk-forward backtest + paper-trading engine (spec §17, §19, §21).

DATA GATE (verified corpus):
  * REG games: mirror schedule 2015-2026 (2019-2025 cross-verified against
    an independent odds source; 2026 single-source, live through 2026-09-19).
  * POSTSEASON games: ONLY the verified corpus (po_corpus.parquet):
      P1 = 2025 mirror (reality spot-checked, full scores)
      P2 = 2019-2024 reconstructed game winners (no scores)
    The mirror's own 2019-2024 PO rows are FABRICATED and never used.

MARKET GATE (verified prices only, spec §13):
  * Real moneylines exist ONLY for REG 2019-2025 (cesar-dx source, 15,442
    games, 100% join + outcome match).  NO verified market prices exist for
    any 2019-2025 postseason game (searched; documented in source_registry).
  * Consequences:
      - REG 2019-2025 ML bets settle at REAL prices -> real paper ROI.
      - Games without verified prices (REG 2015-18, ALL PO 2019-25, REG
        2026) are scored as EVAL picks: no real bankroll impact, but
        win-rate / Brier / log-loss / fair-coin proxy ROI are tracked.
      - The fair-coin proxy bankroll stakes 2% flat at +100 on every pick:
        a clearly-labeled market-free skill proxy (2 x (win_pct - 0.5) ROI),
        NOT a claim about real market efficiency.
      - TOTALS settle against a synthetic 8.5 line at +95 (no verified
        total odds exist anywhere in the data) - labeled synthetic.
  * Uncompleted 2026 games: PROPOSED (no price) or OPEN (real price).

Chronicity & anti-leakage:
  * games processed strictly in start-time order;
  * team state (Elo, form, season stats) updates only after a game completes;
  * round intercepts learned only from PRIOR-season PO games;
  * series state uses only earlier games of the same series;
  * uncompleted games never settle.

Bankroll: each strategy has its own bankroll per competition environment
(REG / WC / DS / LCS / WS / POST).  All-MLB is a computed view (spec §4).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .. import db
from ..config import (KELLY_FRACTION, MAX_STAKE_PCT, STARTING_BANKROLL)
from ..features.engine import compute_features, load_events, load_games
from .strategies import MODELS, Strategy, build_catalog, special_rules

FAIRCOIN_STAKE = 0.02   # flat stake fraction for the fair-coin proxy
FAIRCOIN_DECIMAL = 2.0  # +100 proxy price


def _need(yr: int, rnd: str) -> int:
    """Wins required to take the series (drives clinch/elimination flags)."""
    if rnd == "WC":
        return 1 if yr == 2019 else 2
    if rnd == "DS":
        return {2019: 3, 2020: 2, 2021: 3, 2022: 3, 2023: 3,
                2024: 3, 2025: 3}.get(yr, 3)
    return 4


def _american_to_decimal(o: float) -> float:
    if o < 0:
        return 100.0 / (-o) + 1.0
    return o / 100.0 + 1.0


# ------------------------------------------------------------------ corpus
def build_backtest_games() -> pd.DataFrame:
    """REG (mirror) + verified PO corpus in one chronological frame."""
    from ..config import FEAT
    g = load_games()
    reg = g[g.round_code.isna()].copy()
    reg["score_available"] = True

    po = pd.read_parquet(FEAT / "po_corpus.parquet")
    for c in reg.columns:
        if c not in po.columns:
            po[c] = np.nan
    po["game_type"] = "PO"
    po["status"] = "F"
    po["completed"] = True
    po["home_win"] = (po.winner_team_id == po.home_team_id).astype(int)

    # reconstructed games carry a synthetic 3-2 score ONLY so the feature
    # engine can propagate state into the following season; settlement uses
    # the known winner, never this score (score_available=False).
    recon = po[po.provenance == "reconstructed_winners"]
    po.loc[recon.index, "home_score"] = np.where(recon.home_win == 1, 3, 2)
    po.loc[recon.index, "away_score"] = np.where(recon.home_win == 1, 2, 3)

    # series ids: real mirror key for 2025 P1; synthetic for P2 (the mirror
    # series table for 2019-2024 is fabricated and must not be used)
    po["series_key"] = [
        f"R{int(s)}:{r}:{l}:{min(int(a), int(b))}-{max(int(a), int(b))}"
        for s, r, l, a, b in zip(po.season, po.round_code, po.league,
                                 po.home_team_id, po.away_team_id)]
    mirror_po = g[g.round_code.notna() & (g.season == 2025)].set_index("game_pk")
    real_key = {pk: r.series_key for pk, r in mirror_po.iterrows()
                if isinstance(r.series_key, str)}
    po["series_key"] = [real_key.get(pk, sk) for pk, sk in
                        zip(po.game_pk, po["series_key"])]

    out = pd.concat([reg, po], ignore_index=True)
    out["start_ts"] = pd.to_datetime(out.start_utc, utc=True, errors="coerce")
    out["date"] = pd.to_datetime(out.game_date, errors="coerce")
    bad = out.start_ts.isna()
    if bad.any():
        out.loc[bad, "start_ts"] = (pd.to_datetime(out.loc[bad, "game_date"],
                                                   utc=True)
                                    + pd.Timedelta(hours=12))
    out = out.sort_values(["start_ts", "game_pk"]).reset_index(drop=True)
    return out[reg.columns]


# ------------------------------------------------------------------ main
def run_backtest(po_valid_pks: set[int] | None = None,
                 min_season: int = 2015) -> dict:
    """Run the full competition backtest (verified-corpus gate)."""
    db.init_db()
    from ..config import FEAT
    games = build_backtest_games()
    events = load_events()
    odds = pd.read_parquet(FEAT / "odds.parquet")

    conn0 = db.connect()
    series_state = pd.read_sql("SELECT * FROM series_state", conn0)
    conn0.close()

    feats = compute_features(games, events, series_state, odds)
    strategies = build_catalog()
    ctx = {
        "round_diffs": {rnd: [] for rnd in ("WC", "DS", "LCS", "WS")},
        "round_intercepts": {rnd: 0.0 for rnd in ("WC", "DS", "LCS", "WS")},
        "series": {},
        "series_team_a": None,
        "fav_max": -150.0,
    }

    bankrolls: dict[tuple, float] = {}      # real-price bankroll
    fcbankrolls: dict[tuple, float] = {}    # fair-coin proxy bankroll
    balances: dict[tuple, list] = {}
    fcbalances: dict[tuple, list] = {}
    brier: dict[tuple, list] = {}
    logloss: dict[tuple, list] = {}

    def key(s: Strategy):
        return (s.sid, s.env)

    for s in strategies:
        bankrolls[key(s)] = STARTING_BANKROLL
        fcbankrolls[key(s)] = STARTING_BANKROLL
        balances[key(s)] = [(None, STARTING_BANKROLL)]
        fcbalances[key(s)] = [(None, STARTING_BANKROLL)]
        brier[key(s)] = []
        logloss[key(s)] = []

    bets = []
    n_settled = n_eval = n_proposed = n_open = 0
    totals_skipped_no_score = 0
    last_po_season = None

    for g in games.itertuples():
        if g.season < min_season:
            continue
        is_po = bool(pd.notna(g.round_code))

        # PO season transition: promote last PO season's diffs to intercepts
        if is_po and g.season != last_po_season:
            if last_po_season is not None:
                for rnd, diffs in ctx["round_diffs"].items():
                    if len(diffs) >= 15:
                        ctx["round_intercepts"][rnd] = float(
                            np.clip(np.mean(diffs), -0.25, 0.25))
                    ctx["round_diffs"][rnd] = []
            last_po_season = g.season

        f = feats.loc[g.game_pk].to_dict()
        f["game_pk"] = g.game_pk

        # ---- series bookkeeping (ctx-derived, anti-leakage)
        si = None
        if is_po and isinstance(g.series_key, str):
            sk = g.series_key
            if sk not in ctx["series"]:
                ctx["series"][sk] = {"n": 0, "a": int(g.home_team_id),
                                     "b": int(g.away_team_id), "wa": 0,
                                     "wb": 0, "yr": int(g.season),
                                     "rnd": g.round_code}
            si = ctx["series"][sk]
            f["n_series_games_before"] = si["n"]
            f["wins_a_before"] = si["wa"]
            f["wins_b_before"] = si["wb"]
            ctx["series_team_a"] = si["a"]
            need = _need(si["yr"], si["rnd"])
            f["clinch_a"] = 1 if si["wa"] == need - 1 else 0
            f["clinch_b"] = 1 if si["wb"] == need - 1 else 0
            f["elimination_a"] = 1 if si["wb"] == need - 1 else 0
            f["elimination_b"] = 1 if si["wa"] == need - 1 else 0
        else:
            f["n_series_games_before"] = 0

        for s in strategies:
            if not s.applies_to(g):
                continue
            scored = False
            try:
                decision = s.decide(g, f, ctx)
            except Exception:
                decision = None
            if decision is not None:
                decision = special_rules(s.sid, g, f, ctx, decision)
                if decision is not None and s.market == "TOTAL" \
                        and is_po and not g.score_available:
                    totals_skipped_no_score += 1
                    decision = None
            if decision is None:
                # calibration on model probability even without a bet
                try:
                    p = MODELS[s.model](g, f, ctx)
                except Exception:
                    p = np.nan
                if s.market == "ML" and g.completed and isinstance(p, float) \
                        and not math.isnan(p) and not pd.isna(g.home_win):
                    y = 1.0 if g.home_win == 1 else 0.0
                    brier[key(s)].append((p - y) ** 2)
                    p2 = min(max(p, 1e-6), 1 - 1e-6)
                    logloss[key(s)].append(-(y * math.log(p2)
                                             + (1 - y) * math.log(1 - p2)))
                    scored = True
                continue
            if g.completed and s.market == "ML" and not scored \
                    and not pd.isna(g.home_win):
                p_home = (decision["p_model"]
                          if decision["selection"] == "HOME"
                          else 1 - decision["p_model"])
                y = 1.0 if g.home_win == 1 else 0.0
                brier[key(s)].append((p_home - y) ** 2)
                p2 = min(max(p_home, 1e-6), 1 - 1e-6)
                logloss[key(s)].append(-(y * math.log(p2)
                                         + (1 - y) * math.log(1 - p2)))

            if s.market == "ML":
                sel = decision["selection"]
                p_home = (decision["p_model"] if sel == "HOME"
                          else 1 - decision["p_model"])
                mkt = f.get("home_odds" if sel == "HOME" else "away_odds",
                            np.nan)
                has_mkt = pd.notna(mkt)

                if not g.completed:
                    # live / upcoming: paper bet if priced, else proposal
                    if has_mkt:
                        decimal = _american_to_decimal(mkt)
                        kelly = decision.get("edge", p_home - 0.5) / \
                            (decimal - 1.0)
                        stake = bankrolls[key(s)] * min(
                            KELLY_FRACTION * max(kelly, 0), MAX_STAKE_PCT)
                        bets.append(_bet_row(g, s, decision, stake, "OPEN",
                                             None, 0.0, closing=mkt))
                        n_open += 1
                    else:
                        bets.append(_bet_row(g, s, decision, 0.0,
                                             "PROPOSED", None, 0.0))
                        n_proposed += 1
                    continue

                if pd.isna(g.home_win):
                    # no-decision game (tie) -> push
                    bets.append(_bet_row(g, s, decision, 0.0, "SETTLED",
                                         "P", 0.0,
                                         closing=mkt if has_mkt else None))
                    n_settled += 1
                    continue

                won = (g.home_win == 1) if sel == "HOME" else (g.home_win == 0)
                result = "W" if won else "L"

                # fair-coin proxy bankroll (every pick, +100, 2% flat)
                fc_stake = fcbankrolls[key(s)] * FAIRCOIN_STAKE
                fc_pnl = fc_stake if won else -fc_stake
                fcbankrolls[key(s)] += fc_pnl
                fcbalances[key(s)].append((str(g.game_date),
                                           fcbankrolls[key(s)]))

                if has_mkt:
                    decimal = _american_to_decimal(mkt)
                    kelly = decision.get("edge", p_home - 0.5) / (decimal - 1.0)
                    stake = bankrolls[key(s)] * min(
                        KELLY_FRACTION * max(kelly, 0), MAX_STAKE_PCT)
                    stake = max(stake, 0.0)
                    pnl = stake * (decimal - 1.0) if won else -stake
                    bankrolls[key(s)] += pnl
                    balances[key(s)].append((str(g.game_date),
                                             bankrolls[key(s)]))
                    bets.append(_bet_row(g, s, decision, stake, "SETTLED",
                                         result, pnl, closing=mkt))
                    n_settled += 1
                else:
                    # no verified price -> EVAL pick (no real bankroll)
                    bets.append(_bet_row(g, s, decision, 0.0, "EVAL", result,
                                         0.0, faircoin_pnl=fc_pnl))
                    n_eval += 1
                continue

            # ---- TOTAL (synthetic 8.5 line at +95; real score required)
            if not g.completed:
                # live / upcoming: no score yet (and no real total line
                # exists in the data) -> proposal only
                bets.append(_bet_row(g, s, decision, 0.0, "PROPOSED", None,
                                     0.0, synthetic=True))
                n_proposed += 1
                continue
            line = float(decision.get("line", 8.5))
            total = int(g.home_score) + int(g.away_score)
            over = total > line
            sel_over = decision["selection"] == "OVER"
            base = bankrolls[key(s)] * FAIRCOIN_STAKE
            if total == line:
                result, pnl = "P", 0.0
            else:
                won = over == sel_over
                result = "W" if won else "L"
                pnl = 0.95 * base if won else -base
            bankrolls[key(s)] += pnl
            balances[key(s)].append((str(g.game_date), bankrolls[key(s)]))
            bets.append(_bet_row(g, s, decision, base, "SETTLED", result,
                                 pnl, synthetic=True))
            n_settled += 1

        # ---- post-completion updates (anti-leakage: after all strategies)
        if is_po and g.completed:
            if si is not None:
                si["n"] += 1
                hw = int(g.home_win)
                if int(g.home_team_id) == si["a"]:
                    si["wa"] += hw
                    si["wb"] += 1 - hw
                else:
                    si["wb"] += 1 - hw
                    si["wa"] += hw
            pe = feats.loc[g.game_pk, "p_elo"]
            if isinstance(pe, float) and not math.isnan(pe):
                ctx["round_diffs"][g.round_code].append(
                    (1 if g.home_win == 1 else 0) - pe)

    _write_to_db(strategies, bets, balances, brier, logloss, bankrolls,
                 fcbankrolls, fcbalances, n_settled, n_eval, n_proposed,
                 n_open, totals_skipped_no_score)
    return {"settled_bets": n_settled, "eval_picks": n_eval,
            "open_bets": n_open, "proposed_bets": n_proposed,
            "totals_skipped_no_score": totals_skipped_no_score,
            "round_intercepts": {k: round(v, 4)
                                 for k, v in ctx["round_intercepts"].items()}}


def _bet_row(g, s: Strategy, decision, stake, status, result, pnl,
             closing=None, faircoin_pnl=None, synthetic=False):
    return {
        "game_pk": int(g.game_pk), "strategy_id": s.sid, "env": s.env,
        "season": int(g.season),
        "round_code": None if pd.isna(g.round_code) else g.round_code,
        "market": s.market,
        "selection": (decision["selection"] + f" {decision.get('line', '')}"
                      if s.market == "TOTAL" else decision["selection"]),
        "model_prob": decision["p_model"],
        "fair_price": decision["fair_price"],
        "market_price": decision.get("p_market"),
        "edge": decision.get("edge", abs(decision["p_model"] - 0.5)),
        "stake": stake, "made_at": db.utcnow(), "status": status,
        "result": result, "pnl": pnl, "closing_price": closing, "clv": None,
        "series_state": g.series_key if isinstance(g.series_key, str) else None,
        "faircoin_pnl": faircoin_pnl, "synthetic": 1 if synthetic else 0,
    }


def _write_to_db(strategies, bets, balances, brier, logloss, bankrolls,
                 fcbankrolls, fcbalances, n_settled, n_eval, n_proposed,
                 n_open, totals_skipped_no_score):
    conn = db.connect()
    db.audit("backtest:start", f"strategies={len(strategies)}")
    for s in strategies:
        conn.execute(
            "INSERT OR REPLACE INTO strategies VALUES (?,?,?,?,?,?,?,?,?)",
            (s.sid, s.name, s.env, s.model, s.market, s.hypothesis,
             "BACKTESTED", db.utcnow(), s.notes))
    conn.execute("DELETE FROM bets")
    for b in bets:
        conn.execute(
            "INSERT INTO bets (game_pk, strategy_id, env, season, round_code,"
            " market, selection, model_prob, fair_price, market_price, edge,"
            " stake, made_at, status, result, pnl, closing_price, clv,"
            " series_state, faircoin_pnl, synthetic)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (b["game_pk"], b["strategy_id"], b["env"], b["season"],
             b["round_code"], b["market"], b["selection"], b["model_prob"],
             b["fair_price"], b["market_price"], b["edge"], b["stake"],
             b["made_at"], b["status"], b["result"], b["pnl"],
             b["closing_price"], b["clv"], b["series_state"],
             b["faircoin_pnl"], b["synthetic"]))
    conn.execute("DELETE FROM bankroll")
    for (sid, env), bal in balances.items():
        peak = STARTING_BANKROLL
        mdd = 0.0
        for i, (d, b) in enumerate(bal):
            peak = max(peak, b)
            mdd = min(mdd, (b - peak) / peak if peak else 0.0)
            conn.execute("INSERT INTO bankroll VALUES (?,?,?,?,?,?,?)",
                         (sid, env, i, d, b,
                          (b - STARTING_BANKROLL) / STARTING_BANKROLL, mdd))
    conn.execute("DELETE FROM calibration")
    for s in strategies:
        k = (s.sid, s.env)
        if brier[k]:
            conn.execute(
                "INSERT OR REPLACE INTO calibration "
                "(strategy_id, env, n_games, brier, log_loss, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (s.sid, s.env, len(brier[k]), float(np.mean(brier[k])),
                 float(np.mean(logloss[k])), db.utcnow()))
    for s in strategies:
        conn.execute(
            "INSERT OR REPLACE INTO model_versions VALUES (?,?,?,?,?,?)",
            (f"{s.sid}_v1", s.model, s.env, db.utcnow(),
             db.jdump({"strategy": s.sid, "model": s.model,
                       "min_edge": s.min_edge, "market": s.market,
                       "bankroll": STARTING_BANKROLL}),
             "walk-forward; verified-corpus gate; real-price + faircoin; "
             "see audit_log"))
    conn.commit()
    conn.close()
    db.audit("backtest:done",
             f"settled={n_settled}, eval={n_eval}, proposed={n_proposed}, "
             f"open={n_open}, totals_skipped_no_score={totals_skipped_no_score}")


# ------------------------------------------------------------------ stats
def _last_bankroll(sid: str, env: str) -> tuple[float, float]:
    conn = db.connect()
    last = pd.read_sql(
        "SELECT balance, max_dd FROM bankroll WHERE strategy_id=? AND env=? "
        "ORDER BY seq DESC LIMIT 1", conn, params=(sid, env))
    conn.close()
    if len(last):
        return float(last.iloc[0].balance), float(last.iloc[0].max_dd)
    return STARTING_BANKROLL, 0.0


def _faircoin_stats(b: pd.DataFrame) -> pd.DataFrame:
    """Fair-coin proxy bankroll per strategy x env (2% flat at +100)."""
    fc = b[b.market == "ML"].copy()
    fc["pnl_fc"] = fc.faircoin_pnl.fillna(0.0)
    rows = []
    for (sid, env), sub in fc.groupby(["strategy_id", "env"]):
        bal = STARTING_BANKROLL
        seq_bal = [bal]
        for p in sub.pnl_fc:
            bal += p
            seq_bal.append(bal)
        peak = max(seq_bal)
        mdd = min((x - peak) / peak if peak else 0.0 for x in seq_bal)
        rows.append({"strategy_id": sid, "env": env,
                     "fc_bankroll": bal,
                     "fc_roi": (bal - STARTING_BANKROLL) / STARTING_BANKROLL,
                     "fc_max_dd": mdd,
                     "fc_bets": len(sub)})
    return pd.DataFrame(rows).set_index(["strategy_id", "env"])


def strategy_stats() -> pd.DataFrame:
    """Per-strategy performance by environment (spec §19, §22)."""
    conn = db.connect()
    b = pd.read_sql("SELECT * FROM bets", conn)
    st = pd.read_sql("SELECT * FROM strategies", conn)
    tables = {r[0] for r in
              conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    cal = pd.read_sql("SELECT * FROM calibration", conn) \
        if "calibration" in tables else None
    conn.close()
    stmap = st.set_index("strategy_id")
    calmap = (cal.set_index(["strategy_id", "env"])
              if cal is not None and len(cal) else None)
    fcmap = _faircoin_stats(b)

    rows = []
    for (sid, env), sub in b.groupby(["strategy_id", "env"]):
        setl = sub[sub.status == "SETTLED"]
        real = setl[setl.stake > 0]
        evalr = sub[sub.status == "EVAL"]
        pnl = float(real.pnl.sum())
        stakes = float(real.stake.sum())
        w = int((setl.result == "W").sum())
        l = int((setl.result == "L").sum())
        p = int((setl.result == "P").sum())
        bank, mdd = _last_bankroll(sid, env)
        try:
            fc = fcmap.loc[(sid, env)]
        except KeyError:
            fc = None
        cal_row = None
        if calmap is not None:
            try:
                cal_row = calmap.loc[(sid, env)]
            except KeyError:
                pass
        ev_w = int((evalr.result == "W").sum())
        ev_l = int((evalr.result == "L").sum())
        ev_pct = ev_w / (ev_w + ev_l) if (ev_w + ev_l) else np.nan
        rows.append({
            "strategy_id": sid, "env": env, "name": stmap.loc[sid, "name"],
            "bets": len(setl), "open": int((sub.status == "OPEN").sum()),
            "proposed": int((sub.status == "PROPOSED").sum()),
            "eval_picks": len(evalr),
            "wins": w, "losses": l, "pushes": p,
            "win_pct": w / (w + l) if (w + l) else np.nan,
            "eval_win_pct": ev_pct,
            "fair_coin_roi": 2 * (ev_pct - 0.5) if not pd.isna(ev_pct) else np.nan,
            "stake_sum": stakes,
            "bankroll": bank, "pnl": pnl,
            "roi": pnl / stakes if stakes else 0.0,
            "avg_stake": float(real.stake.mean()) if len(real) else 0.0,
            "avg_edge": float(setl.edge.mean()) if len(setl) else 0.0,
            "max_dd": mdd,
            "fc_bankroll": (float(fc.fc_bankroll) if fc is not None else STARTING_BANKROLL),
            "fc_roi": (float(fc.fc_roi) if fc is not None else 0.0),
            "fc_max_dd": (float(fc.fc_max_dd) if fc is not None else 0.0),
            "fc_bets": (int(fc.fc_bets) if fc is not None else 0),
            "brier": (float(cal_row.brier) if cal_row is not None else np.nan),
            "log_loss": (float(cal_row.log_loss)
                         if cal_row is not None else np.nan),
        })
    return pd.DataFrame(rows)


def combined_view() -> pd.DataFrame:
    """All-MLB combined view that PRESERVES the env breakdown (spec §4, §22)."""
    df = strategy_stats()
    po_envs = {"POST", "WC", "DS", "LCS", "WS"}
    rows = []
    for sid, d in df.groupby("strategy_id"):
        reg = d[~d.env.isin(po_envs)]
        po = d[d.env.isin(po_envs)]
        tot_stake = float(d.stake_sum.sum())
        tot_pnl = float(d.pnl.sum())
        rows.append({
            "strategy_id": sid,
            "bets": int(d.bets.sum()),
            "open": int(d.open.sum()) + int(d.proposed.sum()),
            "pnl": tot_pnl,
            "roi": tot_pnl / tot_stake if tot_stake else 0.0,
            "bankroll": float(d.bankroll.sum()),
            "reg_bets": int(reg.bets.sum()),
            "reg_pnl": float(reg.pnl.sum()),
            "reg_roi": (float(reg.pnl.sum() / reg.stake_sum.sum())
                        if reg.stake_sum.sum() else 0.0),
            "po_bets": int(po.bets.sum()),
            "po_pnl": float(po.pnl.sum()),
            "po_roi": (float(po.pnl.sum() / po.stake_sum.sum())
                       if po.stake_sum.sum() else 0.0),
            "po_fc_bets": int(po.fc_bets.sum()),
            "po_fc_roi": (float(po.fc_bankroll.sum() -
                                len(po) * STARTING_BANKROLL)
                          / (len(po) * STARTING_BANKROLL) if len(po) else 0.0),
            "best_env": d.loc[d.roi.idxmax(), "env"] if len(d) else None,
            "worst_env": d.loc[d.roi.idxmin(), "env"] if len(d) else None,
        })
    return pd.DataFrame(rows).set_index("strategy_id")
