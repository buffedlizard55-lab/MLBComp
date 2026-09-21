"""Source discovery, registry and verification helpers.

A catalog entry is a research lead, not evidence.  It becomes VERIFIED only
when the fetcher records a response/checksum and the validation checks pass.
This distinction prevents an attractive URL or a free trial from silently
becoming a production dependency.
"""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import db
from .config import DATA


@dataclass(frozen=True)
class Source:
    source_id: str
    name: str
    url: str
    data_type: str
    historical_depth: str
    current_availability: str
    access_method: str
    cost: str
    restrictions: str
    licensing: str
    reliability: str
    granularity: str
    automation_capability: str
    verification_status: str
    limitations: str


# URLs are intentionally limited to public, discoverable endpoints.  No source
# is marked verified by this list alone.  A pipeline run updates the status.
SOURCE_CATALOG: tuple[Source, ...] = (
    Source("mlb_stats_api", "MLB Stats API", "https://statsapi.mlb.com/api/v1/",
           "schedules, games, rosters, people, lineups", "official API history varies by endpoint",
           "NOT_CHECKED", "HTTPS JSON", "free public endpoint", "rate limits and endpoint changes",
           "MLB terms apply", "official-primary candidate", "game/player/event", "high",
           "NOT_VERIFIED", "Must record retrieval and availability timestamps; live status is not historical closing data."),
    Source("baseballsavant_statcast", "Baseball Savant Statcast", "https://baseballsavant.mlb.com/",
           "pitch-level Statcast", "Statcast era; endpoint dependent", "NOT_CHECKED", "web/CSV endpoint",
           "free public access", "robots/rate limits and terms; endpoint stability", "MLB terms apply",
           "primary-derived candidate", "pitch/event", "medium", "NOT_VERIFIED",
           "Do not scrape unless the endpoint and terms are verified."),
    Source("sportsdataverse_baseballr", "sportsdataverse baseballr-data", "https://github.com/sportsdataverse/baseballr-data",
           "schedules, scores, play-by-play parquet", "repository history; file dependent", "NOT_CHECKED",
           "GitHub API/git blobs", "free public repository", "GitHub/API rate limits; repository license must be checked",
           "repository license applies", "secondary mirror candidate", "game/play", "high", "NOT_VERIFIED",
           "Content must be checksum-pinned and cross-checked before settlement."),
    Source("retrosheet", "Retrosheet", "https://www.retrosheet.org/",
           "historical game logs and event files", "deep historical coverage", "NOT_CHECKED", "downloadable files",
           "free public/non-commercial conditions may apply", "terms and file formats", "Retrosheet terms",
           "historical primary/secondary", "game/event", "high", "NOT_VERIFIED",
           "Excellent history; current pre-game availability is limited."),
    Source("pybaseball", "pybaseball", "https://github.com/jldbc/pybaseball",
           "Python access layer for public baseball data", "wrapper-dependent", "NOT_CHECKED", "Python package/HTTP",
           "open-source package; upstream sources may have restrictions", "upstream terms/rate limits", "package license plus upstream terms",
           "aggregation layer", "player/game/stat", "medium", "NOT_VERIFIED",
           "Never treat a wrapper as independent corroboration of its upstream source."),
    Source("fangraphs", "FanGraphs", "https://www.fangraphs.com/",
           "advanced player/team statistics", "historical and current; access dependent", "NOT_CHECKED", "web/export",
           "some data public; commercial products exist", "terms/rate limits", "FanGraphs terms",
           "secondary analytics", "player/team/day", "low", "NOT_VERIFIED",
           "Access and licensing must be checked for each dataset."),
    Source("baseball_reference", "Baseball-Reference", "https://www.baseball-reference.com/",
           "player/team/game reference", "deep historical coverage", "NOT_CHECKED", "web pages",
           "public browsing; automation restrictions may apply", "terms/robots/rate limits", "Sports Reference terms",
           "secondary reference", "game/player", "low", "NOT_VERIFIED",
           "Not a default automated dependency."),
    Source("noaa_ncei", "NOAA NCEI", "https://www.ncei.noaa.gov/",
           "historical weather observations", "station history dependent", "NOT_CHECKED", "public API/download",
           "free public data", "API keys/rate limits for some services", "NOAA public data terms",
           "official weather candidate", "station/time", "high", "NOT_VERIFIED",
           "Join must use venue coordinates and observation time before first pitch."),
    Source("weather_gov", "National Weather Service", "https://api.weather.gov/",
           "forecast and weather observations", "current/history endpoint dependent", "NOT_CHECKED", "HTTPS JSON",
           "free public endpoint", "User-Agent/rate limits; historical forecast archive required", "NWS terms",
           "official forecast candidate", "station/time", "medium", "NOT_VERIFIED",
           "A current observation is not a historical forecast available at decision time."),
    Source("umpire_scorecards", "Umpire Scorecards", "https://umpscorecards.com/",
           "umpire assignments and called-strike summaries", "site-dependent", "NOT_CHECKED", "web pages",
           "public website", "terms/robots and historical availability", "site terms",
           "secondary candidate", "game/umpire", "low", "NOT_VERIFIED",
           "Use only if assignment timestamp and historical page are preserved."),
    Source("mlb_schedule_official", "MLB official schedule", "https://www.mlb.com/schedule",
           "schedule, probable starters, lineups", "current and recent", "NOT_CHECKED", "web/API candidate",
           "free public browsing", "terms, endpoint and rate limits", "MLB terms",
           "official-primary candidate", "game", "medium", "NOT_VERIFIED",
           "Probable is not confirmed; retain both timestamps."),
    Source("cesar_dx_mlb_odds", "cesar-dx MLB betting ML", "https://github.com/cesar-dx/mlb-betting-ml",
           "historical moneyline records", "repository-dependent", "NOT_CHECKED", "GitHub CSV",
           "free public repository", "repository license and coverage must be verified",
           "repository license applies", "secondary market candidate", "game/quote", "high", "NOT_VERIFIED",
           "A row must include quote time and coverage; do not infer missing postseason prices."),
    Source("kalshi_api", "Kalshi API", "https://trading-api.kalshi.com/trade-api/v2/",
           "prediction-market contracts, quotes, trades", "API history/account dependent", "NOT_CHECKED", "HTTPS API",
           "public endpoints may require account/auth; not assumed free", "terms, authentication, rate limits, market rules",
           "Kalshi terms", "market-primary candidate", "contract/quote/trade", "high", "NOT_VERIFIED",
           "Optional only. No order, fill, liquidity or price is created if the API is unavailable."),
    Source("github_search", "GitHub public analytics repositories", "https://github.com/search?q=mlb+statcast&type=repositories",
           "research discovery", "repository dependent", "NOT_CHECKED", "web/API search", "free public search",
           "repository licenses vary", "each repository license", "discovery only", "repository", "medium", "NOT_VERIFIED",
           "Discovery is not validation; pin commit and reproduce before use."),
    Source("academic_search", "Academic baseball research discovery", "https://scholar.google.com/",
           "papers and methods", "paper dependent", "NOT_CHECKED", "web search", "public search; paper access varies",
           "publisher access and license", "publisher/license terms", "research discovery", "paper", "low", "NOT_VERIFIED",
           "Use for hypotheses and methods, not as an unverified data feed."),
)


def register_catalog() -> None:
    """Write the catalog without upgrading an unverified source to verified."""
    db.init_db()
    with db.db() as conn:
        for source in SOURCE_CATALOG:
            conn.execute(
                "INSERT OR IGNORE INTO source_registry "
                "(source_id,url,description,coverage,verified,reject_reason,fetched_at,notes) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (source.source_id, source.url, source.data_type, source.historical_depth,
                 0, None, None, source.limitations),
            )
            conn.execute(
                "INSERT OR IGNORE INTO source_metadata "
                "(source_id,name,url,data_type,historical_depth,current_availability,access_method,cost,"
                "restrictions,licensing,reliability,granularity,automation_capability,verification_date,"
                "verification_status,limitations) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (source.source_id, source.name, source.url, source.data_type,
                 source.historical_depth, source.current_availability, source.access_method,
                 source.cost, source.restrictions, source.licensing, source.reliability,
                 source.granularity, source.automation_capability, None,
                 source.verification_status, source.limitations),
            )


def _status_from_http(status: int | None, error: str | None) -> str:
    if status is None:
        return "UNAVAILABLE"
    if 200 <= status < 300:
        return "PARTIALLY_VERIFIED"
    if status in (401, 403, 429):
        return "RESTRICTED"
    return "UNAVAILABLE" if error else "NOT_VERIFIED"


def verify_source(source_id: str, url: str | None = None, timeout: int = 15) -> dict[str, Any]:
    """Perform an explicit reachability check and record it.

    This checks availability only.  It does not mark data accurate or licensed;
    dataset-specific validators must create the VERIFIED observation.
    """
    source = next((x for x in SOURCE_CATALOG if x.source_id == source_id), None)
    if source is None and not url:
        raise KeyError(source_id)
    target = url or source.url
    status = None
    error = None
    digest = None
    try:
        request = urllib.request.Request(target, headers={"User-Agent": "MLBComp-source-verifier/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            body = response.read(4096)
            digest = hashlib.sha256(body).hexdigest()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        error = str(exc)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    verification = _status_from_http(status, error)
    db.init_db()
    with db.db() as conn:
        conn.execute(
            "UPDATE source_metadata SET current_availability=?,verification_date=?,verification_status=?,"
            "last_http_status=?,last_error=? WHERE source_id=?",
            ("AVAILABLE" if status and 200 <= status < 300 else "UNAVAILABLE",
             now, verification, status, error, source_id),
        )
        conn.execute(
            "INSERT OR REPLACE INTO source_observations "
            "(observation_id,source_id,source_locator,retrieval_time,checksum,verification_status,notes) "
            "VALUES (?,?,?,?,?,?,?)",
            (f"reachability:{source_id}:{now}", source_id, target, now, digest,
             verification, error or "HTTP reachability only; content not validated"),
        )
    db.audit("source:reachability", {"source_id": source_id, "status": verification})
    return {"source_id": source_id, "url": target, "status": verification,
            "http_status": status, "error": error, "retrieved_at": now,
            "body_sha256_prefix": digest[:16] if digest else None}


def registry_snapshot() -> list[dict[str, Any]]:
    register_catalog()
    rows = db.query_df("SELECT * FROM source_metadata ORDER BY source_id")
    return rows.where(rows.notna(), None).to_dict(orient="records")


def load_fetch_manifest() -> dict[str, Any] | None:
    path = DATA / "raw" / "FETCH_MANIFEST.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


if __name__ == "__main__":
    register_catalog()
    print(json.dumps(registry_snapshot(), indent=2, default=str))
