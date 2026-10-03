#!/usr/bin/env python3
import gzip
import io
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAYLIST = ROOT / "favorites-test.m3u"
OUT = ROOT / "guide-favorites.xml"
GUIDE_URL = "https://worker-9dd4.onrender.com/guide.xml.gz"
WINDOW_HOURS = 24

def parse_playlist_ids(path: Path):
    ids = set()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("#EXTINF:"):
            marker = 'tvg-id="'
            i = line.find(marker)
            if i >= 0:
                j = line.find('"', i + len(marker))
                if j > i:
                    tvg_id = line[i+len(marker):j].strip()
                    if tvg_id:
                        ids.add(tvg_id)
    return ids

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
        off = timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5])) * sign
        dt = dt.replace(tzinfo=timezone(off)).astimezone(timezone.utc)
    else:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

wanted = parse_playlist_ids(PLAYLIST)
if not wanted:
    raise SystemExit("No tvg-id values found in favorites-test.m3u")

now = datetime.now(timezone.utc)
end = now + timedelta(hours=WINDOW_HOURS)

print(f"Wanted tvg-id values: {len(wanted)}")
print(f"Fetching: {GUIDE_URL}")

req = urllib.request.Request(GUIDE_URL, headers={"User-Agent":"Chevosky-IPTV-EPG/2.0"})
with urllib.request.urlopen(req, timeout=180) as resp:
    gz = gzip.GzipFile(fileobj=resp)
    context = ET.iterparse(gz, events=("end",))

    channels = {}
    programmes = []

    for event, elem in context:
        if elem.tag == "channel":
            cid = elem.get("id")
            if cid in wanted:
                channels[cid] = ET.fromstring(ET.tostring(elem, encoding="utf-8"))
            elem.clear()
        elif elem.tag == "programme":
            cid = elem.get("channel")
            if cid in wanted:
                start = parse_xmltv_dt(elem.get("start"))
                stop = parse_xmltv_dt(elem.get("stop"))
                if start is not None and start <= end and (stop is None or stop >= now):
                    programmes.append(ET.fromstring(ET.tostring(elem, encoding="utf-8")))
            elem.clear()

covered = {p.get("channel") for p in programmes}
missing = sorted(wanted - covered)

root = ET.Element("tv", {
    "generator-info-name":"Chevosky/IPTV filtered from iptv-org guide",
    "generator-info-url":"https://github.com/Chevosky/IPTV"
})

for cid in sorted(covered):
    ch = channels.get(cid)
    if ch is None:
        ch = ET.Element("channel", {"id":cid})
        dn = ET.SubElement(ch, "display-name")
        dn.text = cid
    root.append(ch)

programmes.sort(key=lambda p: (p.get("channel") or "", p.get("start") or ""))
for p in programmes:
    root.append(p)

ET.indent(root, space="  ")
ET.ElementTree(root).write(OUT, encoding="utf-8", xml_declaration=True)

size_mb = OUT.stat().st_size / (1024*1024)
coverage = (len(covered)/len(wanted)*100) if wanted else 0
print(f"Covered: {len(covered)}/{len(wanted)} channels ({coverage:.1f}%)")
print(f"Programmes kept: {len(programmes)}")
print(f"Output: {OUT.name} = {size_mb:.2f} MB")
if missing:
    print("Missing tvg-id values:")
    for cid in missing:
        print(f"  - {cid}")

if not programmes:
    raise SystemExit("No programme data matched the curated playlist")
