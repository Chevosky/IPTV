#!/usr/bin/env python3
import json
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAYLIST = ROOT / "favorites-test.m3u"
SOURCE = ROOT / "guide.xml"
OUT = ROOT / "guide-favorites.xml"
REPORT = ROOT / "coverage-favorites.json"

def parse_playlist(path: Path):
    ids = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.startswith("#EXTINF:"):
            continue
        marker = 'tvg-id="'
        i = line.find(marker)
        if i < 0:
            continue
        j = line.find('"', i + len(marker))
        if j <= i:
            continue
        tvg_id = line[i+len(marker):j].strip()
        name = line[line.rfind(",")+1:].strip()
        if tvg_id:
            ids[tvg_id] = name
    return ids

wanted = parse_playlist(PLAYLIST)
if not wanted:
    raise SystemExit("No tvg-id values found in favorites-test.m3u")
if not SOURCE.exists():
    raise SystemExit("guide.xml is missing")

channels = {}
programmes = []
covered = set()

context = ET.iterparse(SOURCE, events=("end",))
for _, elem in context:
    if elem.tag == "channel":
        cid = elem.get("id")
        if cid in wanted:
            channels[cid] = ET.fromstring(ET.tostring(elem, encoding="utf-8"))
        elem.clear()
    elif elem.tag == "programme":
        cid = elem.get("channel")
        if cid in wanted:
            programmes.append(ET.fromstring(ET.tostring(elem, encoding="utf-8")))
            covered.add(cid)
        elem.clear()

root = ET.Element("tv", {
    "generator-info-name":"Chevosky/IPTV curated guide",
    "generator-info-url":"https://github.com/Chevosky/IPTV"
})
for cid in sorted(covered):
    ch = channels.get(cid)
    if ch is None:
        ch = ET.Element("channel", {"id":cid})
        dn = ET.SubElement(ch, "display-name")
        dn.text = wanted.get(cid, cid)
    root.append(ch)

programmes.sort(key=lambda p: (p.get("channel") or "", p.get("start") or ""))
for p in programmes:
    root.append(p)

ET.indent(root, space="  ")
ET.ElementTree(root).write(OUT, encoding="utf-8", xml_declaration=True)

missing = sorted(set(wanted)-covered)
report = {
    "playlist_channels": len(wanted),
    "covered_channels": len(covered),
    "coverage_percent": round((len(covered)/len(wanted)*100) if wanted else 0, 1),
    "programmes": len(programmes),
    "output_bytes": OUT.stat().st_size,
    "covered": [{"tvg_id":cid,"name":wanted.get(cid,"")} for cid in sorted(covered)],
    "missing": [{"tvg_id":cid,"name":wanted.get(cid,"")} for cid in missing]
}
REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")

print(f"Playlist channels: {len(wanted)}")
print(f"Covered channels: {len(covered)} ({report['coverage_percent']}%)")
print(f"Programmes: {len(programmes)}")
print(f"Output: {OUT.stat().st_size/1024/1024:.2f} MB")
print(f"Missing: {len(missing)}")
