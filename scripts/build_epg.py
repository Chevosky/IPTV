#!/usr/bin/env python3
import gzip
import json
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "sources.json").read_text())
BASE_URL = CFG["base_url"].rstrip("/")
TAGS = CFG["tags"]
WINDOW_HOURS = int(CFG.get("window_hours", 48))
OUT = ROOT / "guide.xml"
TMP = ROOT / ".tmp_epg"
TMP.mkdir(exist_ok=True)

now = datetime.now(timezone.utc)
window_end = now + timedelta(hours=WINDOW_HOURS)

def parse_xmltv_dt(value):
    if not value:
        return None
    value = value.strip()
    # XMLTV: YYYYMMDDHHMMSS +ZZZZ (seconds/timezone may be omitted)
    main = value[:14]
    tz = value[14:].strip()
    fmt = "%Y%m%d%H%M%S"
    try:
        dt = datetime.strptime(main, fmt)
    except ValueError:
        return None
    if tz and len(tz) >= 5 and (tz[0] in "+-") and tz[1:5].isdigit():
        sign = 1 if tz[0] == "+" else -1
        offset = timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5])) * sign
        dt = dt.replace(tzinfo=timezone(offset)).astimezone(timezone.utc)
    else:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def fetch(tag):
    url = f"{BASE_URL}/epg_ripper_{tag}.xml.gz"
    dst = TMP / f"{tag}.xml.gz"
    req = urllib.request.Request(url, headers={"User-Agent": "Chevosky-IPTV-EPG/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r, dst.open("wb") as f:
        f.write(r.read())
    return dst

channels = {}
programmes = {}
ok_tags = []
failed_tags = []

for tag in TAGS:
    try:
        gz_path = fetch(tag)
        with gzip.open(gz_path, "rb") as f:
            tree = ET.parse(f)
        root = tree.getroot()

        local_channels = {}
        for ch in root.findall("channel"):
            cid = ch.get("id")
            if not cid:
                continue
            local_channels[cid] = ch
            channels.setdefault(cid, ch)

        kept = 0
        for p in root.findall("programme"):
            cid = p.get("channel")
            if not cid:
                continue
            start = parse_xmltv_dt(p.get("start"))
            stop = parse_xmltv_dt(p.get("stop"))
            if start is None:
                continue
            # Keep anything that overlaps the rolling window.
            if stop is not None and stop < now:
                continue
            if start > window_end:
                continue
            key = (cid, p.get("start"), p.get("stop"), (p.findtext("title") or "").strip())
            if key not in programmes:
                programmes[key] = p
                kept += 1
        ok_tags.append(tag)
        print(f"{tag}: {len(local_channels)} channels, {kept} programmes kept")
    except Exception as e:
        failed_tags.append((tag, str(e)))
        print(f"{tag}: FAILED: {e}", file=sys.stderr)

if not programmes:
    raise SystemExit("No programme data was produced; refusing to overwrite guide.xml")

used_channel_ids = {p.get("channel") for p in programmes.values()}
out_root = ET.Element("tv", {
    "generator-info-name": "Chevosky/IPTV",
    "generator-info-url": "https://github.com/Chevosky/IPTV"
})

for cid in sorted(used_channel_ids):
    ch = channels.get(cid)
    if ch is not None:
        out_root.append(ch)

def prog_sort_key(p):
    return (p.get("channel") or "", p.get("start") or "")

for p in sorted(programmes.values(), key=prog_sort_key):
    out_root.append(p)

ET.indent(out_root, space="  ")
tree = ET.ElementTree(out_root)
tree.write(OUT, encoding="utf-8", xml_declaration=True)

size_mb = OUT.stat().st_size / (1024 * 1024)
print(f"Wrote {OUT} ({size_mb:.1f} MB)")
print(f"Successful sources: {len(ok_tags)}/{len(TAGS)}")
if failed_tags:
    print("Failed sources:")
    for tag, err in failed_tags:
        print(f"  - {tag}: {err}")

# Hard guard for GitHub's regular file limit.
if OUT.stat().st_size > 95 * 1024 * 1024:
    raise SystemExit(f"guide.xml is {size_mb:.1f} MB, too large for safe GitHub publishing")
