#!/usr/bin/env python3
"""Fail closed before replacing the last known-good Zapper guide."""
import argparse
import os
import json
import xml.etree.ElementTree as ET
from pluto_identity import retained_epg_check
from channel_policy import active_channels, load_policy
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(os.environ.get("ZAPPER_DATA_DIR", str(Path(__file__).resolve().parents[1])))


def when(value):
    return datetime.strptime(value[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc) if not value[14:].strip() else datetime.strptime(value, "%Y%m%d%H%M%S %z").astimezone(timezone.utc)


def stats(path, wanted, now, require_fresh=True, excluded_ids=frozenset()):
    raw = path.read_bytes()
    if not 100_000 <= len(raw) <= 20_000_000 or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError(f"{path.name}: invalid size or XML declaration")
    root = ET.fromstring(raw)
    if root.tag != "tv":
        raise ValueError(f"{path.name}: expected <tv>")
    ids = [c.get("id") for c in root.findall("channel")]
    effective_ids = [cid for cid in ids if cid not in excluded_ids]
    if set(effective_ids) != wanted or len(effective_ids) != len(wanted) or len(ids) != len(set(ids)):
        raise ValueError(f"{path.name}: expected exactly {len(wanted)} active catalog channel IDs")
    count = Counter()
    horizon = now
    total = 0
    future_ids = set()
    for p in root.findall("programme"):
        cid = p.get("channel")
        if cid in excluded_ids:
            continue
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
    if total < len(wanted) or (require_fresh and (current < 60 or len(future_ids) < len(wanted)*0.5 or horizon < now + timedelta(hours=4))):
        raise ValueError(f"{path.name}: insufficient programmes/current coverage/horizon ({total}, {current}, {horizon.isoformat()})")
    return {"channels": len(effective_ids), "programmes": total, "currentCoverage": current, "nonStaleChannels": len(future_ids), "horizonUTC": horizon.isoformat()}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("candidate", type=Path)
    p.add_argument("--previous", type=Path)
    p.add_argument("--coverage-report", type=Path)
    a = p.parse_args()
    catalog = json.loads((ROOT / "catalog-flex.json").read_text())
    policy = load_policy(ROOT)
    active = active_channels(catalog, policy)
    wanted = {c["tvgId"] for c in active}
    excluded_ids = {c["tvgId"] for c in catalog["channels"]} - wanted
    if len(wanted) != len(active) or len(catalog["channels"]) != 237:
        raise SystemExit("catalog has duplicate/missing tvg-id")
    now = datetime.now(timezone.utc)
    candidate = stats(a.candidate, wanted, now)
    coverage = None
    if a.coverage_report:
        coverage = json.loads(a.coverage_report.read_text())
        if coverage.get("playlist_channels") != len(wanted) or coverage.get("guide_channels") != len(wanted) or coverage.get("covered_channels", 0) < 83 or coverage.get("synthetic_channels") != len(wanted) - coverage.get("covered_channels", 0):
            raise SystemExit("real EPG coverage below approved baseline floor; preserve last known-good guide")
        reference_path = ROOT / 'publication/baseline/pluto-epg-59.json'
        reference = json.loads(reference_path.read_text(encoding='utf-8'))
        reference = {**reference, "tvgIds":[cid for cid in reference["tvgIds"] if cid not in excluded_ids]}
        tracked, missing = retained_epg_check(coverage, reference)
        if missing:
            raise SystemExit(f'Pluto EPG regression: {len(missing)}/{tracked} previously verified channel IDs lost real programming; preserve last-known-good guide')
    if a.previous and a.previous.exists():
        previous = stats(a.previous, wanted, now, require_fresh=False, excluded_ids=excluded_ids)
        if (previous["currentCoverage"] >= 60 and candidate["currentCoverage"] < previous["currentCoverage"] * 0.7) or candidate["programmes"] < previous["programmes"] * 0.5:
            raise SystemExit("abrupt guide coverage/programme drop")
    print(json.dumps({"candidate": candidate, "realCoverage": coverage.get("covered_channels") if coverage else None, "syntheticCoverage": coverage.get("synthetic_channels") if coverage else None, "previous": previous if a.previous and a.previous.exists() else None}))


if __name__ == "__main__":
    main()

