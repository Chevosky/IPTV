#!/usr/bin/env python3
"""Fail closed before replacing the last known-good Zapper guide."""
import argparse
import os
import json
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(os.environ.get("ZAPPER_DATA_DIR", str(Path(__file__).resolve().parents[1])))


def when(value):
    return datetime.strptime(value[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc) if not value[14:].strip() else datetime.strptime(value, "%Y%m%d%H%M%S %z").astimezone(timezone.utc)


def stats(path, wanted, now, require_fresh=True):
    raw = path.read_bytes()
    if not 100_000 <= len(raw) <= 20_000_000 or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError(f"{path.name}: invalid size or XML declaration")
    root = ET.fromstring(raw)
    if root.tag != "tv":
        raise ValueError(f"{path.name}: expected <tv>")
    ids = [c.get("id") for c in root.findall("channel")]
    if len(ids) != 237 or set(ids) != wanted or len(set(ids)) != 237:
        raise ValueError(f"{path.name}: expected exactly 237 catalog channel IDs")
    count = Counter()
    horizon = now
    total = 0
    future_ids = set()
    for p in root.findall("programme"):
        cid = p.get("channel")
        if cid not in wanted or not p.findtext("title"):
            raise ValueError(f"{path.name}: invalid programme channel/title")
        start, stop = when(p.get("start", "")), when(p.get("stop", ""))
        if stop <= start:
            raise ValueError(f"{path.name}: invalid programme times")
        total += 1
        if stop > now: future_ids.add(cid)
        if start <= now < stop:
            count[cid] += 1
        horizon = max(horizon, stop)
    current = len(count)
    if total < 237 or (require_fresh and (current < 60 or len(future_ids) < 119 or horizon < now + timedelta(hours=4))):
        raise ValueError(f"{path.name}: insufficient programmes/current coverage/horizon ({total}, {current}, {horizon.isoformat()})")
    return {"channels": len(ids), "programmes": total, "currentCoverage": current, "nonStaleChannels": len(future_ids), "horizonUTC": horizon.isoformat()}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("candidate", type=Path)
    p.add_argument("--previous", type=Path)
    p.add_argument("--coverage-report", type=Path)
    a = p.parse_args()
    catalog = json.loads((ROOT / "catalog-flex.json").read_text())
    wanted = {c["tvgId"] for c in catalog["channels"]}
    if len(wanted) != 237:
        raise SystemExit("catalog has duplicate/missing tvg-id")
    now = datetime.now(timezone.utc)
    candidate = stats(a.candidate, wanted, now)
    coverage = None
    if a.coverage_report:
        coverage = json.loads(a.coverage_report.read_text())
        if coverage.get("playlist_channels") != 237 or coverage.get("guide_channels") != 237 or coverage.get("covered_channels", 0) < 83 or coverage.get("synthetic_channels") != 237 - coverage.get("covered_channels", 0):
            raise SystemExit("real EPG coverage below approved baseline floor; preserve last known-good guide")
    if a.previous and a.previous.exists():
        previous = stats(a.previous, wanted, now, require_fresh=False)
        if (previous["currentCoverage"] >= 60 and candidate["currentCoverage"] < previous["currentCoverage"] * 0.7) or candidate["programmes"] < previous["programmes"] * 0.5:
            raise SystemExit("abrupt guide coverage/programme drop")
    print(json.dumps({"candidate": candidate, "realCoverage": coverage.get("covered_channels") if coverage else None, "syntheticCoverage": coverage.get("synthetic_channels") if coverage else None, "previous": previous if a.previous and a.previous.exists() else None}))


if __name__ == "__main__":
    main()
