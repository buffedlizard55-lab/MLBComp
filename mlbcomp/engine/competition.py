"""Simulated competition engine for the leaderboard.

Competitions are deterministic re-aggregations of the verified backtest
records — never invented games, prices, fills, results or PnL:

* Competition windows use *randomized start dates* drawn from a seeded RNG.
  The seed is recorded in the output, so identical inputs reproduce identical
  windows byte-for-byte.  A window that does not contain enough scored picks
  is redrawn (bounded attempts) and the draw diagnostics are published.
* Entrants are competition personas (usernames) bound 1:1 to a transparent
  *strategy slice*: a declarative filter on the parent strategy's exported
  records (selection side, season, model-probability band, rounds).  Every
  entrant metric is a deterministic function of the same rows a visitor can
  open in the Ledger tab — the slice spec is published so anyone can verify.
* Scoring uses only real outcomes.  Win rate / accuracy comes from W/L
  decisions, Brier score and log loss pair each row's own selected-side
  probability with its observed result.  PnL is published **only** when
  ``VERIFIED_PRICE`` rows exist inside the window; otherwise it is ``null``
  with an explicit ``pnl_status`` reason.  No closing line, price, fill or
  PnL is ever synthesized.

Nothing here places real-money wagers; it is a paper-research competition.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable

ENGINE_VERSION = "competition-engine/v1"

RESULTS_SCORED = ("W", "L")
RESULTS_ALL = ("W", "L", "P")


# ---------------------------------------------------------------- primitives


def _seed_int(seed: str) -> int:
    return int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:8], "big")


def _is_date(value: Any) -> str | None:
    """Normalize an exported game_date / ISO timestamp to YYYY-MM-DD."""
    if value is None:
        return None
    text = str(value)
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return None


def _ts(date_str: str, end: bool = False) -> str:
    """Window boundary timestamp, precise to the second (UTC)."""
    d = date.fromisoformat(date_str)
    t = time(23, 59, 59) if end else time(0, 0, 0)
    return datetime.combine(d, t, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- slices


@dataclass(frozen=True)
class Slice:
    """A transparent, declarative filter over a parent strategy's records.

    Every field corresponds to data already published on each ledger row, so
    the entrant's performance can be re-derived by anyone with the export.
    """

    strategy_id: str
    side: str | None = None            # HOME | AWAY | OVER | UNDER
    seasons: tuple[int, ...] = ()
    min_prob: float | None = None      # inclusive lower bound on model_prob
    max_prob: float | None = None      # exclusive upper bound on model_prob
    rounds: tuple[str, ...] = ()       # restrict to WC/DS/LCS/WS rows
    label: str = "all picks"

    def matches(self, row: dict) -> bool:
        if str(row.get("strategy_id")) != self.strategy_id:
            return False
        if self.side:
            selection = str(row.get("selection") or "")
            if not selection.upper().startswith(self.side):
                return False
        if self.seasons and int(row.get("season") or 0) not in self.seasons:
            return False
        if self.min_prob is not None:
            prob = row.get("model_prob")
            if not _finite(prob) or float(prob) < self.min_prob:
                return False
        if self.max_prob is not None:
            prob = row.get("model_prob")
            if _finite(prob) and float(prob) >= self.max_prob:
                return False
        if self.rounds:
            round_code = row.get("round_code") or row.get("env")
            if str(round_code) not in self.rounds:
                return False
        return True

    def to_spec(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "side": self.side,
            "seasons": list(self.seasons) or None,
            "min_prob": self.min_prob,
            "max_prob": self.max_prob,
            "rounds": list(self.rounds) or None,
            "label": self.label,
        }


# ---------------------------------------------------------------- entrants


@dataclass
class Entrant:
    username: str
    slice: Slice
    style: str
    parent_name: str
    parent_env: str
    parent_model: str
    parent_market: str

    @property
    def cohort(self) -> str:
        return "REG" if self.parent_env == "REG" else "POST"

    def to_record(self, career: dict | None) -> dict[str, Any]:
        import re
        initials = re.findall(r"[A-Z0-9][a-z0-9]*", self.username) or [self.username]
        return {
            "username": self.username,
            "badge": "".join(part[0] for part in initials)[:3].upper(),
            "style": self.style,
            "strategy_id": self.slice.strategy_id,
            "parent_name": self.parent_name,
            "parent_env": self.parent_env,
            "parent_model": self.parent_model,
            "parent_market": self.parent_market,
            "cohort": self.cohort,
            "slice": self.slice.to_spec(),
            "slice_label": self.slice.label,
            "career": career,
        }


# Persona handles are display identities for a *simulated* competition; each
# is bound to exactly one published strategy slice and owns no other data.
# Pools are per cohort so every parent strategy with exported rows receives at
# least one entrant before any receives a second (round-robin allocation).
_PERSONAS_REG = [
    "EloWhisperer", "HomeFieldAudit", "RoadDogResearch", "ConfidenceCurve", "SeptemberScout",
    "BullpenBookkeeper", "HomeDugoutData", "BullpenTraveler", "LeverageLens", "FatigueAudit",
    "RatingsRidge", "BaselineBeacon", "HomeCookingLab", "AwaySignalDesk", "DayOffDelta",
    "FormFollower", "RunDiffRanger", "HomeStretchMetrics", "VisitingValueDesk", "HighConvictionQC",
    "LateInningLedger", "SeasonArcAnalytics", "GrindLineGroup", "TailgateQuant", "RoadCurveReview",
    "PillowRestLabs", "HomeRestHarbor", "AwayRestReview", "SeptemberSignal", "TravelDayDesk",
    "PoissonPadre", "OverDesk", "UnderUnion", "TotalRecallQuant", "NumberNarrower",
    "TotalZoneReview", "LineValueLab", "OverUnderOffice", "RunLineReader", "TotalsTower",
]

_PERSONAS_POST = [
    "ModelACourier", "ModelBSwitchboard", "ModelCSilo", "ModelDRoundhouse", "ModelEStack",
    "TransferDesk", "AdjustmentAlley", "PriorPooler", "OctoberBullpen", "SeriesStateScout",
    "WestCoastDS", "DSDataDepot", "LCSNotebook", "PennantPressure", "FallClassicDesk",
    "OctoberOracle", "WildCardWatch", "BO3ReviewBoard", "EliminationEdge", "ClinchClock",
    "LeashLengthLab", "PostseasonPrior",
]

_STYLES = {
    "full": "full-spectrum",
    "home": "home-side specialist",
    "away": "road-side specialist",
    "over": "over specialist",
    "under": "under specialist",
    "sharp": "high-probability only",
    "surgical": "very high probability only",
    "late": "season-slice specialist",
    "round": "round specialist",
}


def _build_slices(strategy_id: str, env: str, market: str) -> list[tuple[Slice, str]]:
    """Candidate slices for a parent strategy (deduped downstream by count)."""
    mlb = strategy_id
    if env == "REG" and market == "TOTAL":
        return [
            (Slice(mlb, label="all totals picks"), "full"),
            (Slice(mlb, side="OVER", label="over selections only"), "over"),
            (Slice(mlb, side="UNDER", label="under selections only"), "under"),
            (Slice(mlb, min_prob=0.55, label="model probability ≥ 55%"), "sharp"),
            (Slice(mlb, min_prob=0.60, label="model probability ≥ 60%"), "surgical"),
        ]
    if env == "REG":
        return [
            (Slice(mlb, label="all picks"), "full"),
            (Slice(mlb, side="HOME", label="home selections only"), "home"),
            (Slice(mlb, side="AWAY", label="away selections only"), "away"),
            (Slice(mlb, min_prob=0.55, label="model probability ≥ 55%"), "sharp"),
            (Slice(mlb, seasons=(2026,), label="2026 season slice"), "late"),
        ]
    return [(Slice(mlb, label="all picks"), "full" if env == "POST" else "round")]


def build_entrants(strategies: list[dict], leaderboard: list[dict],
                   ledger_rows: list[dict]) -> list[Entrant]:
    """Bind persona usernames to strategy slices that have exported records.

    Slices without a single exported row are dropped (a slice with no rows
    would manufacture an empty entrant, not evidence).  The entrant roster is
    deterministic for identical inputs.
    """
    by_id = {str(s.get("sid") or s.get("id")): s for s in strategies}
    board = {str(r.get("id")): r for r in leaderboard}
    counts: dict[str, int] = {}
    for row in ledger_rows:
        sid = str(row.get("strategy_id"))
        counts[sid] = counts.get(sid, 0) + 1

    per_strategy: list[tuple[str, str, str, str, str, list[tuple[Slice, str, int]]]] = []
    for sid in sorted(counts):
        meta = by_id.get(sid) or board.get(sid)
        if not meta:
            continue
        env = str(meta.get("env", "REG"))
        market = str(meta.get("market", "ML"))
        name = str(meta.get("name", sid))
        model = str(meta.get("model", "unknown"))
        usable = []
        for slc, style_key in _build_slices(sid, env, market):
            n_rows = sum(1 for row in ledger_rows if slc.matches(row))
            if n_rows > 0:
                usable.append((slc, style_key, n_rows))
        if usable:
            per_strategy.append((sid, env, market, name, model, usable))

    # Round-robin across parent strategies so every parent with exported rows
    # gets an entrant before any parent gets its second.
    pools = {"REG": [*_PERSONAS_REG], "POST": [*_PERSONAS_POST]}
    fallback_n = {"REG": 0, "POST": 0}
    entrants: list[Entrant] = []
    depth = 0
    while True:
        assigned_this_pass = 0
        for sid, env, market, name, model, usable in per_strategy:
            if depth >= len(usable):
                continue
            cohort = "REG" if env == "REG" else "POST"
            if pools[cohort]:
                username = pools[cohort].pop(0)
            else:
                # Deterministic fallback for larger future exports; still a
                # display persona bound to a published strategy slice.
                fallback_n[cohort] += 1
                username = f"{cohort}Entrant{fallback_n[cohort]:02d}"
            slc, style_key, _n = usable[depth]
            entrants.append(Entrant(
                username=username, slice=slc, style=_STYLES[style_key],
                parent_name=name, parent_env=env, parent_model=model,
                parent_market=market,
            ))
            assigned_this_pass += 1
        if not assigned_this_pass:
            break
        depth += 1
    return entrants


def entrant_career(entrant: Entrant, leaderboard: list[dict]) -> dict[str, Any]:
    """All-history snapshot aggregates for the parent strategy (not windowed)."""
    row = next((r for r in leaderboard if str(r.get("id")) == entrant.slice.strategy_id), None)
    if not row:
        return None
    return {
        "scope": "ALL_HISTORY_SNAPSHOT",
        "strategy_status": row.get("metric_status"),
        "verified_bets": row.get("verified_bets"),
        "verified_pnl": row.get("verified_pnl"),
        "verified_roi": row.get("verified_roi"),
        "brier": row.get("brier"),
        "log_loss": row.get("log_loss"),
        "win_rate": row.get("win_rate"),
        "note": "Parent strategy aggregates from the published snapshot; "
                "not computed inside a competition window.",
    }


# ---------------------------------------------------------------- scoring


def score_rows(rows: Iterable[dict]) -> dict[str, Any]:
    """Deterministic metrics over scored rows.  PnL only from verified rows."""
    rows = list(rows)
    scored = [r for r in rows if r.get("result") in RESULTS_ALL]
    decided = [r for r in scored if r.get("result") in RESULTS_SCORED]
    pushes = len(scored) - len(decided)
    brier_sum = 0.0
    brier_n = 0
    ll_sum = 0.0
    ll_n = 0
    for r in scored:
        prob = r.get("model_prob")
        if not _finite(prob):
            continue
        p = min(max(float(prob), 1e-15), 1 - 1e-15)
        outcome = {"W": 1.0, "L": 0.0, "P": 0.5}[r["result"]]
        brier_sum += (p - outcome) ** 2
        brier_n += 1
        if r["result"] != "P":
            ll_sum += -math.log(p if r["result"] == "W" else 1 - p)
            ll_n += 1
    verified = [r for r in scored if r.get("verification_status") == "VERIFIED_PRICE"]
    pnl = None
    roi = None
    if verified:
        pnl = round(sum(float(r.get("pnl") or 0.0) for r in verified), 2)
        stake = sum(float(r.get("stake") or 0.0) for r in verified)
        roi = round(pnl / stake * 100, 4) if stake else None
    return {
        "picks": len(scored),
        "wins": sum(1 for r in decided if r["result"] == "W"),
        "losses": sum(1 for r in decided if r["result"] == "L"),
        "pushes": pushes,
        "accuracy": (sum(1 for r in decided if r["result"] == "W") / len(decided)
                     if decided else None),
        "brier": (brier_sum / brier_n) if brier_n else None,
        "log_loss": (ll_sum / ll_n) if ll_n else None,
        "verified_price_rows": len(verified),
        "pnl": pnl,
        "roi": roi,
        "pnl_status": ("VERIFIED_PRICE_ROWS" if verified else
                       "UNAVAILABLE_NO_VERIFIED_PRICE_ROWS_IN_WINDOW"),
    }


# ---------------------------------------------------------------- competitions


@dataclass(frozen=True)
class CompetitionSpec:
    comp_id: str
    name: str
    env_scope: str                     # REG | POST | WC | DS | LCS | WS
    window_days: int
    randomize_start: tuple[str, str]   # eligible start range (YYYY-MM-DD, inclusive bounds for the START date)
    min_picks: int
    description: str
    cohort: str | None = None          # default: REG for REG scope else POST


def competition_scopes(row: dict) -> set[str]:
    """Which competition scopes a ledger row belongs to."""
    scopes = set()
    env = str(row.get("env") or "")
    round_code = row.get("round_code")
    if round_code:
        scopes.update({str(round_code), "POST"})
    if env:
        scopes.add(env)
        if env in {"WC", "DS", "LCS", "WS"}:
            scopes.add("POST")
    return scopes


def _row_in_scope(row: dict, scope: str) -> bool:
    if scope == "REG":
        return str(row.get("env")) == "REG" and not row.get("round_code")
    return scope in competition_scopes(row)


def draw_window(rng: random.Random, start_lo: str, start_hi: str,
                days: int, valid_dates: set[str], tries: int = 60) -> dict[str, Any]:
    """Draw a randomized start date; require at least one scored date inside."""
    lo = date.fromisoformat(start_lo)
    hi = date.fromisoformat(start_hi)
    if hi < lo:
        raise ValueError(f"empty start range {start_lo}..{start_hi}")
    span = (hi - lo).days
    for attempt in range(1, tries + 1):
        offset = rng.randint(0, span) if span else 0
        start = lo + timedelta(days=offset)
        end = start + timedelta(days=days - 1)
        covered = sum(1 for i in range(days) if (start + timedelta(days=i)).isoformat() in valid_dates)
        if covered:
            return {"start": start.isoformat(), "end": end.isoformat(),
                    "draw_attempts": attempt, "scored_dates_in_window": covered}
    raise ValueError(f"could not draw a qualifying {days}-day window in {start_lo}..{start_hi}")


def build_competitions(ledger_rows: list[dict], entrants: list[Entrant],
                       specs: list[CompetitionSpec], seed: str) -> list[dict[str, Any]]:
    """Draw windows (seeded) and derive standings from real records."""
    rng = random.Random(_seed_int(seed))
    scored_dates = {d for d in (_is_date(r.get("game_date")) for r in ledger_rows
                                if r.get("result") in RESULTS_ALL) if d}
    by_scope: dict[str, list[dict]] = {}
    for spec in specs:
        scope_rows = [r for r in ledger_rows if _row_in_scope(r, spec.env_scope)]
        by_scope[spec.comp_id] = scope_rows

    competitions = []
    for spec in specs:
        scope_rows = by_scope[spec.comp_id]
        cohort = spec.cohort or ("REG" if spec.env_scope == "REG" else "POST")
        try:
            window = draw_window(rng, spec.randomize_start[0], spec.randomize_start[1],
                                 spec.window_days, scored_dates | {
                                     d for d in (_is_date(r.get("game_date")) for r in scope_rows) if d})
        except ValueError as exc:
            # Explicit, inspectable failure — a spec that cannot draw on the
            # available records is flagged, never silently widened.
            competitions.append({
                "id": spec.comp_id, "name": spec.name, "description": spec.description,
                "env_scope": spec.env_scope, "cohort": cohort,
                "window_start": None, "window_end": None,
                "window_days": spec.window_days, "randomized_start": True,
                "status": "NOT_DRAWN", "not_drawn_reason": str(exc),
                "rng_draw_attempts": None, "scored_dates_in_window": 0,
                "min_picks": spec.min_picks,
                "entrants_in_cohort": sum(1 for e in entrants if e.cohort == cohort),
                "qualified_entrants": 0, "scope_rows": len(scope_rows),
                "standings": [],
            })
            continue
        standings = []
        for entrant in entrants:
            if entrant.cohort != cohort:
                continue
            entrant_rows = [r for r in scope_rows
                            if entrant.slice.matches(r)
                            and window["start"] <= (_is_date(r.get("game_date")) or "") <= window["end"]]
            scored = score_rows([r for r in entrant_rows if r.get("result") in RESULTS_ALL])
            qualified = scored["picks"] >= spec.min_picks
            standings.append({
                "username": entrant.username,
                "strategy_id": entrant.slice.strategy_id,
                "slice_label": entrant.slice.label,
                "qualified": qualified,
                "qualification_status": "QUALIFIED" if qualified else "BELOW_MIN_PICKS",
                **scored,
            })
        qualified_rows = [s for s in standings if s["qualified"]]
        ranked = sorted(qualified_rows,
                        key=lambda s: (-(s["accuracy"] if s["accuracy"] is not None else -1.0),
                                       (s["brier"] if s["brier"] is not None else 1.0),
                                       -s["picks"], s["username"]))
        for rank, row in enumerate(ranked, 1):
            row["rank"] = rank
        for row in standings:
            if "rank" not in row:
                row["rank"] = None
        standings_sorted = sorted(standings, key=lambda s: (s["rank"] is None, s["rank"] or 0, s["username"]))
        competitions.append({
            "id": spec.comp_id,
            "name": spec.name,
            "description": spec.description,
            "env_scope": spec.env_scope,
            "cohort": cohort,
            "status": "DRAWN",
            "window_start": _ts(window["start"]),
            "window_end": _ts(window["end"], end=True),
            "window_days": spec.window_days,
            "randomized_start": True,
            "rng_draw_attempts": window["draw_attempts"],
            "scored_dates_in_window": window["scored_dates_in_window"],
            "min_picks": spec.min_picks,
            "entrants_in_cohort": len(standings),
            "qualified_entrants": len(ranked),
            "scope_rows": len(scope_rows),
            "standings": standings_sorted,
        })
    return competitions


COMPETITION_SPECS: list[CompetitionSpec] = [
    CompetitionSpec(
        "COMP-2025-PENNANT-SPRINT", "Pennant Sprint '25", "REG", 21,
        ("2025-08-29", "2025-09-08"), 10,
        "A 21-day simulated regular-season race over the tail of the 2025 "
        "schedule, with the start date randomized inside 2025-08-29…09-08."),
    CompetitionSpec(
        "COMP-2026-OPENING-DASH", "Opening Dash '26", "REG", 14,
        ("2026-03-25", "2026-04-16"), 8,
        "A 14-day simulated early-season dash across the 2026 open."),
    CompetitionSpec(
        "COMP-2026-SUMMER-GRIND", "Summer Grind '26", "REG", 35,
        ("2026-05-01", "2026-07-28"), 20,
        "A 35-day simulated mid-season marathon; the longest window tests streak robustness."),
    CompetitionSpec(
        "COMP-2026-MIDSEASON-BLITZ", "Midseason Blitz '26", "REG", 7,
        ("2026-05-15", "2026-08-24"), 5,
        "A one-week simulated sprint — high variance by design; treated accordingly."),
    CompetitionSpec(
        "COMP-2026-STRETCH-RUN", "Stretch Run '26", "REG", 21,
        ("2026-08-15", "2026-08-31"), 12,
        "A 21-day simulated race over the 2026 stretch run before the export cutoff."),
    CompetitionSpec(
        "COMP-2025-WC-WEEK", "Wild Card Week '25", "WC", 4,
        ("2025-09-29", "2025-09-30"), 3,
        "Simulated Wild Card competition over the best-of-three window (2025-09-30…10-02)."),
    CompetitionSpec(
        "COMP-2025-DS-CLASH", "Division Series Clash '25", "DS", 8,
        ("2025-10-03", "2025-10-04"), 3,
        "Simulated Division Series competition across the 2025 best-of-five window."),
    CompetitionSpec(
        "COMP-2025-LCS-GAUNTLET", "LCS Gauntlet '25", "LCS", 9,
        ("2025-10-11", "2025-10-12"), 3,
        "Simulated League Championship competition across the 2025 LCS window."),
    CompetitionSpec(
        "COMP-2025-WS-FINALE", "Fall Classic Finale '25", "WS", 9,
        ("2025-10-23", "2025-10-24"), 2,
        "Simulated World Series competition across the 2025 finale window."),
    CompetitionSpec(
        "COMP-2025-POST-MARATHON", "Postseason Marathon '25", "POST", 34,
        ("2025-09-29", "2025-09-30"), 5,
        "Simulated all-rounds postseason competition spanning WC through the World Series."),
]

DEFAULT_SEED = "mlbcomp-competitions|2026-09-22|v1"


def build_payloads(strategies: list[dict], leaderboard: list[dict],
                   ledger_rows: list[dict], seed: str = DEFAULT_SEED,
                   generated_at: str | None = None) -> tuple[dict, dict]:
    """Return (competitors_payload, competitions_payload) ready for JSON export."""
    generated_at = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    has_scored_rows = any(r.get("result") in RESULTS_ALL for r in ledger_rows)
    entrants = build_entrants(strategies, leaderboard, ledger_rows) if has_scored_rows else []
    board = {str(r.get("id")): r for r in leaderboard}
    competitors_payload = {
        "generated_at": generated_at,
        "generator": ENGINE_VERSION,
        "cohort_note": (
            "SIMULATED competition entrants. Each username is a competition persona bound to exactly "
            "one published strategy slice (a transparent filter over the parent strategy's exported "
            "backtest records). Entrant performance is re-derived deterministically from the same "
            "rows visible in the Ledger tab — no games, prices, fills, results or PnL are invented "
            "for any entrant. Career aggregates are the parent strategy's all-history snapshot values."
        ),
        "entrants": [
            entrant.to_record(entrant_career(entrant, leaderboard))
            for entrant in entrants
        ],
    }
    dates = sorted(d for d in (_is_date(r.get("game_date")) for r in ledger_rows) if d)
    competitions = (build_competitions(ledger_rows, entrants, COMPETITION_SPECS, seed)
                    if has_scored_rows and entrants else [])
    competitions_payload = {
        "generated_at": generated_at,
        "generator": ENGINE_VERSION,
        "rng_seed": seed,
        "rng_note": (
            "Window start dates are drawn with random.Random seeded by sha256(rng_seed). "
            "Identical inputs reproduce identical windows; the seed and draw attempts are "
            "published per competition so the randomization is auditable."
        ),
        "source": {
            "ledger_rows": len(ledger_rows),
            "earliest_game_date": dates[0] if dates else None,
            "latest_game_date": dates[-1] if dates else None,
            "unique_parent_strategies": len({str(r.get('strategy_id')) for r in ledger_rows}),
        },
        "scoring_policy": {
            "primary": "accuracy (win rate on decided W/L selections), requires >= min_picks scored picks",
            "tiebreak": "lower Brier score, then more picks, then username",
            "brier": "mean((model_probability_of_selected_side - outcome)^2) over scored rows (push = 0.5)",
            "log_loss": "pairwise on decided rows using each row's own selected-side probability",
            "pnl": "summed only over VERIFIED_PRICE rows; null with an explicit pnl_status otherwise",
            "small_sample": "postseason windows are tiny by construction; INSUFFICIENT_SAMPLE applies "
                            "— a standing is not an edge claim",
        },
        "disclaimer": (
            "SIMULATED paper competition. Standings are deterministic re-aggregations of the "
            "published backtest export inside randomized windows. No real-money wagers exist; "
            "a competition result is not a claim of a market edge."
        ),
        "competitions": competitions,
    }
    return competitors_payload, competitions_payload


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=str(Path(__file__).resolve().parents[2] / "data"))
    parser.add_argument("--seed", default=DEFAULT_SEED)
    args = parser.parse_args()
    root = Path(args.data)
    strategies = json.loads((root / "strategies.json").read_text())
    board = json.loads((root / "leaderboard.json").read_text())
    ledger = json.loads((root / "bets_ledger.json").read_text())
    competitors, competitions = build_payloads(strategies, board, ledger, seed=args.seed)
    (root / "competitors.json").write_text(json.dumps(competitors, indent=2) + "\n")
    (root / "competitions.json").write_text(json.dumps(competitions, indent=2) + "\n")
    print(json.dumps({"entrants": len(competitors["entrants"]),
                      "competitions": len(competitions["competitions"])}, indent=2))
