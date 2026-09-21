"""Fetch the two verified MLB data sources via the GitHub REST API.

Why this exists (verified 2026-09-21 in this environment):
    raw.githubusercontent.com  -> connection refused (egress blocked)
    statsapi.mlb.com           -> connection refused (egress blocked)
    api.github.com             -> 200 OK   (works)
    codeload.github.com        -> 200 OK   (whole-repo tarball only)

The Git *blobs* endpoint returns file content base64-encoded straight from
api.github.com, so it works where `raw` does not.  Every byte downloaded here
is therefore a real, content-addressed object from the upstream repository
(the blob SHA is the git object id and is recorded in the fetch manifest).

Sources (both public, both free, both license-checked in the registry):
    S1  sportsdataverse/baseballr-data   mlb/schedule/{Y}.parquet
                                         mlb/pbp/mlb_pbp_{Y}.parquet
    S2  cesar-dx/mlb-betting-ml          data/{Y}/final_format.csv

Usage:
    python3 scripts/fetch_sources_github_api.py [--seasons 2015-2026] [--no-pbp]
Writes into data/raw/ (git-ignored) and data/raw/FETCH_MANIFEST.json.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"

API = "https://api.github.com"
S1 = "sportsdataverse/baseballr-data"
S2 = "cesar-dx/mlb-betting-ml"


def _token() -> str | None:
    t = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    return t


def _get(url: str, timeout: int = 120):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "mlbcomp-source-fetcher",
        **({"Authorization": f"Bearer {_token()}"} if _token() else {}),
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def blob_bytes(owner_repo: str, sha: str) -> bytes:
    """Download a git blob's raw bytes via the API (works without `raw`)."""
    d = _get(f"{API}/repos/{owner_repo}/git/blobs/{sha}")
    if d.get("encoding") != "base64":
        raise RuntimeError(f"unexpected blob encoding for {sha}: {d.get('encoding')}")
    return base64.b64decode(d["content"])


def list_dir(owner_repo: str, path: str) -> list[dict]:
    return [e for e in _get(f"{API}/repos/{owner_repo}/contents/{path}")
            if e["type"] == "file"]


def fetch_file(owner_repo: str, path: str, dest: Path) -> dict:
    meta = _get(f"{API}/repos/{owner_repo}/contents/{path}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    raw = blob_bytes(owner_repo, meta["sha"])
    if len(raw) != meta["size"]:
        raise RuntimeError(f"size mismatch {path}: got {len(raw)} want {meta['size']}")
    dest.write_bytes(raw)
    return {"source_repo": owner_repo, "source_path": path,
            "blob_sha": meta["sha"], "bytes": len(raw),
            "dest": str(dest.relative_to(ROOT)),
            "fetched_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="2015-2026")
    ap.add_argument("--no-pbp", action="store_true")
    a = ap.parse_args()
    lo, hi = (int(x) for x in a.seasons.split("-"))
    years = list(range(lo, hi + 1))

    manifest = {"fetched_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "access_method": "api.github.com git-blobs (raw.githubusercontent blocked)",
                "files": []}

    print(f"[S1] schedule {years[0]}-{years[-1]}")
    for y in years:
        m = fetch_file(S1, f"mlb/schedule/{y}.parquet",
                       RAW / "baseballr" / "schedule" / f"{y}.parquet")
        manifest["files"].append(m)
        print(f"  {y}.parquet  {m['bytes']:>9,}  sha={m['blob_sha'][:10]}")

    if not a.no_pbp:
        print(f"[S1] play-by-play {years[0]}-{years[-1]}")
        for y in years:
            m = fetch_file(S1, f"mlb/pbp/mlb_pbp_{y}.parquet",
                           RAW / "baseballr" / "pbp" / f"mlb_pbp_{y}.parquet")
            manifest["files"].append(m)
            print(f"  mlb_pbp_{y}.parquet  {m['bytes']:>9,}  sha={m['blob_sha'][:10]}")

    print(f"[S2] odds 2019-{years[-1]}")
    for y in years:
        if y < 2019 or y > 2025:
            continue
        m = fetch_file(S2, f"data/{y}/final_format.csv",
                       RAW / "odds" / f"{y}" / "final_format.csv")
        manifest["files"].append(m)
        print(f"  {y}/final_format.csv  {m['bytes']:>9,}  sha={m['blob_sha'][:10]}")

    (RAW / "FETCH_MANIFEST.json").write_text(json.dumps(manifest, indent=2))
    print(f"\nwrote {len(manifest['files'])} files + data/raw/FETCH_MANIFEST.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
