#!/usr/bin/env python3
import gzip
import json
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "sources.json").read_text())
BASE_URL = CFG["base_url"].rstrip("/")
TAGS = CFG["tags"]
PLUTO_REGIONS = CFG.get("pluto_regions", [])
WINDOW_HOURS = int(CFG.get("window_hours", 12))
OUT = ROOT / "guide.xml"
TMP = ROOT / ".tmp_epg"
TMP.mkdir(exist_ok=True)

now = datetime.now(timezone.utc)
window_end = now + timedelta(hours=WINDOW_HOURS)

def parse_xmltv_dt(value):
    if not value:
        return None
    value = value.strip()
    main = value[:14]
    tz = value[14:].strip()
    try:
        dt = datetime.strptime(main, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    if tz and len(tz) >= 5 and tz[0] in "+-" and tz[1:5].isdigit():
        sign = 1 if tz[0] == "+" else -1
        offset = timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5])) * sign
        dt = dt.replace(tzinfo=timezone(offset)).astimezone(timezone.utc)
    else:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def iso_to_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None

def dt_to_xmltv(dt):
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S +0000")

def fetch_bytes(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "Chevosky-IPTV-EPG/1.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def fetch_epgshare(tag):
    url = f"{BASE_URL}/epg_ripper_{tag}.xml.gz"
    return fetch_bytes(url)

channels = {}
programmes = {}
ok_tags = []
failed_tags = []

for tag in TAGS:
    try:
        raw = fetch_epgshare(tag)
        root = ET.fromstring(gzip.decompress(raw))

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

def load_pluto_region(region):
    src = f"https://raw.githubusercontent.com/iptv-org/epg/master/sites/pluto.tv/pluto.tv_{region}.channels.xml"
    root = ET.fromstring(fetch_bytes(src))
    rows = []
    for ch in root.findall("channel"):
        site_id = ch.get("site_id")
        name = (ch.text or "").strip()
        xmltv_id = (ch.get("xmltv_id") or "").strip()
        lang = (ch.get("lang") or "").strip()
        if not site_id or not name:
            continue
        cid = xmltv_id or f"pluto.{region}.{site_id}"
        rows.append((site_id, cid, name, lang))
    return rows

pluto_ok = []
pluto_failed = []

for region in PLUTO_REGIONS:
    try:
        rows = load_pluto_region(region)
        region_kept = 0
        for site_id, cid, name, lang in rows:
            try:
                ch = ET.Element("channel", {"id": cid})
                dn = ET.SubElement(ch, "display-name")
                if lang:
                    dn.set("lang", lang)
                dn.text = name
                channels.setdefault(cid, ch)

                params = urllib.parse.urlencode({
                    "start": now.isoformat().replace("+00:00", "Z"),
                    "stop": window_end.isoformat().replace("+00:00", "Z")
                })
                url = f"https://api.pluto.tv/v2/channels/{site_id}?{params}"
                data = json.loads(fetch_bytes(url, timeout=60).decode("utf-8"))

                for item in data.get("timelines", []):
                    start = iso_to_dt(item.get("start"))
                    stop = iso_to_dt(item.get("stop"))
                    if start is None:
                        continue
                    if stop is not None and stop < now:
                        continue
                    if start > window_end:
                        continue

                    p = ET.Element("programme", {
                        "channel": cid,
                        "start": dt_to_xmltv(start),
                        "stop": dt_to_xmltv(stop or (start + timedelta(hours=1)))
                    })
                    title = ET.SubElement(p, "title")
                    if lang:
                        title.set("lang", lang)
                    title.text = item.get("title") or name

                    ep = item.get("episode") or {}
                    subtitle = ep.get("name") or ""
                    if subtitle:
                        st = ET.SubElement(p, "sub-title")
                        if lang:
                            st.set("lang", lang)
                        st.text = subtitle

                    desc_txt = ep.get("description") or ""
                    if desc_txt:
                        desc = ET.SubElement(p, "desc")
                        if lang:
                            desc.set("lang", lang)
                        desc.text = desc_txt

                    for cat in [ep.get("genre"), ep.get("subGenre")]:
                        if cat:
                            c = ET.SubElement(p, "category")
                            if lang:
                                c.set("lang", lang)
                            c.text = cat

                    icon_path = (((ep.get("series") or {}).get("tile") or {}).get("path") or "")
                    if icon_path:
                        ET.SubElement(p, "icon", {"src": icon_path})

                    key = (cid, p.get("start"), p.get("stop"), (p.findtext("title") or "").strip())
                    if key not in programmes:
                        programmes[key] = p
                        region_kept += 1
            except Exception as e:
                print(f"Pluto {region}/{name}: FAILED: {e}", file=sys.stderr)
        pluto_ok.append(region)
        print(f"Pluto {region}: {len(rows)} channels, {region_kept} programmes kept")
    except Exception as e:
        pluto_failed.append((region, str(e)))
        print(f"Pluto {region}: FAILED: {e}", file=sys.stderr)

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
ET.ElementTree(out_root).write(OUT, encoding="utf-8", xml_declaration=True)

size_mb = OUT.stat().st_size / (1024 * 1024)
print(f"Wrote {OUT} ({size_mb:.1f} MB)")
print(f"Successful EPGShare sources: {len(ok_tags)}/{len(TAGS)}")
print(f"Successful Pluto regions: {len(pluto_ok)}/{len(PLUTO_REGIONS)}")
if failed_tags:
    print("Failed EPGShare sources:")
    for tag, err in failed_tags:
        print(f"  - {tag}: {err}")
if pluto_failed:
    print("Failed Pluto regions:")
    for region, err in pluto_failed:
        print(f"  - {region}: {err}")

if OUT.stat().st_size > 95 * 1024 * 1024:
    raise SystemExit(f"guide.xml is {size_mb:.1f} MB, too large for safe GitHub publishing")
