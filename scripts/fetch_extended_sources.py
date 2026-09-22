"""Fetch extended verified-tier sources via the GitHub blobs API.

Adds to data/raw/ and appends content-addressed entries (blob sha + local
sha256) to data/raw/FETCH_MANIFEST.json:

  S1  sportsdataverse/baseballr-data
      - mlb/pitching_models/parquet/*   (stuff+/command+/xera pitcher-seasons)
      - mlb/hitting_models/parquet/*    (batter projections, expected stats)
      - mlb/fielding_models/parquet/*   (framing, OAA)
      - mlb/game_state/parquet/*        (leverage/win-expectancy tables)
      - model cards (JSON) for provenance
  S3  pwu97/bettingtools
      - data/mlb_odds_2014..2019.rda    (open/close MLB Vegas lines)
      - data-raw/MLB_Datasets.R         (build script / provenance)

Every file is content-addressed: the git blob SHA-1 is recorded and the local
SHA-256 digest is computed so later runs can verify integrity.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
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
S3 = "pwu97/bettingtools"


def _token() -> str | None:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")


def _get(url: str, timeout: int = 120):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "mlbcomp-source-fetcher",
        **({"Authorization": f"Bearer {_token()}"} if _token() else {}),
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _raw(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github.raw",
        "User-Agent": "mlbcomp-source-fetcher",
        **({"Authorization": f"Bearer {_token()}"} if _token() else {}),
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _git_blob_sha1(data: bytes) -> str:
    import hashlib as _h
    return _h.sha1(b"blob %d\x00" % len(data) + data).hexdigest()


def list_dir(owner_repo: str, path: str) -> list[dict]:
    entries = _get(f"{API}/repos/{owner_repo}/contents/{path}")
    return [e for e in entries if e["type"] == "file"]


def fetch_file(owner_repo: str, path: str, dest: Path) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    meta = _get(f"{API}/repos/{owner_repo}/contents/{path}")
    raw = _raw(f"{API}/repos/{owner_repo}/contents/{path}")
    if len(raw) != meta["size"]:
        raise RuntimeError(f"size mismatch {path}: got {len(raw)} want {meta['size']}")
    local_sha1 = _git_blob_sha1(raw)
    if local_sha1 != meta["sha"]:
        raise RuntimeError(f"blob sha mismatch {path}: computed {local_sha1} api {meta['sha']}")
    dest.write_bytes(raw)
    return {
        "source_repo": owner_repo,
        "source_path": path,
        "blob_sha": meta["sha"],
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "dest": str(dest.relative_to(ROOT)),
        "fetched_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def load_manifest() -> dict:
    path = RAW / "FETCH_MANIFEST.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            pass
    return {"fetched_utc": None, "access_method": "api.github.com git contents/blobs",
            "files": []}


def save_manifest(m: dict) -> None:
    m["fetched_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (RAW / "FETCH_MANIFEST.json").write_text(json.dumps(m, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-models", action="store_true")
    ap.add_argument("--skip-odds", action="store_true")
    ap.add_argument("--only", default=None, help="substring filter on dest path")
    args = ap.parse_args()

    manifest = load_manifest()
    known = {f["dest"] for f in manifest["files"]}
    added = []

    def record(m: dict) -> None:
        if m["dest"] not in known:
            manifest["files"].append(m)
            known.add(m["dest"])
        else:
            manifest["files"] = [m if f["dest"] == m["dest"] else f
                                 for f in manifest["files"]]
        added.append(m)

    def want(dest: str) -> bool:
        return args.only is None or args.only in dest

    if not args.skip_models:
        model_dirs = [
            ("mlb/pitching_models/parquet", RAW / "models" / "pitching"),
            ("mlb/hitting_models/parquet", RAW / "models" / "hitting"),
            ("mlb/fielding_models/parquet", RAW / "models" / "fielding"),
            ("mlb/game_state/parquet", RAW / "models" / "game_state"),
        ]
        cards = [
            "mlb/pitching_models/mlb_pitching_models_card.json",
            "mlb/hitting_models/mlb_hitting_models_card.json",
            "mlb/fielding_models/mlb_fielding_models_card.json",
            "mlb/game_state/mlb_game_state_card.json",
        ]
        for path in cards:
            dest = RAW / "models" / Path(path).name
            if want(str(dest)) and not (str(dest) in known and dest.exists()):
                try:
                    m = fetch_file(S1, path, dest)
                    record(m)
                    print(f"  card {Path(path).name} {m['bytes']:,}")
                except Exception as exc:  # availability issue, keep going
                    print(f"  SKIP {path}: {exc}")
        for remote, local in model_dirs:
            try:
                entries = list_dir(S1, remote)
            except Exception as exc:
                print(f"  LIST FAIL {remote}: {exc}")
                continue
            for e in entries:
                dest = local / Path(e["path"]).name
                if not want(str(dest)):
                    continue
                if str(dest.relative_to(ROOT)) in known and dest.exists():
                    continue
                try:
                    m = fetch_file(S1, e["path"], dest)
                    record(m)
                    print(f"  {e['path']} {m['bytes']:,}")
                except Exception as exc:
                    print(f"  SKIP {e['path']}: {exc}")

    if not args.skip_odds:
        odds_files = [f"data/mlb_odds_{y}.rda" for y in range(2014, 2020)]
        odds_files.append("data-raw/MLB_Datasets.R")
        for path in odds_files:
            dest = RAW / "odds_open_close" / Path(path).name
            if not want(str(dest)):
                continue
            if str(dest.relative_to(ROOT)) in known and dest.exists():
                continue
            try:
                m = fetch_file(S3, path, dest)
                record(m)
                print(f"  bettingtools/{path} {m['bytes']:,}")
            except Exception as exc:
                print(f"  SKIP bettingtools/{path}: {exc}")

    save_manifest(manifest)
    print(f"\nfetched/verified {len(added)} files; manifest now {len(manifest['files'])} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
