#!/usr/bin/env python3
import json, re, sys
import xml.etree.ElementTree as ET
from collections import defaultdict, Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PLAYLIST=ROOT/"favorites-test.m3u"
EPG_ROOT=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/"vendor/epg"
OUT_XML=ROOT/"iptvorg-selected.channels.xml"
OUT_MISSING_XML=ROOT/"iptvorg-missing-selected.channels.xml"
BATCH_DIR=ROOT/"iptvorg-batches"
OUT_JSON=ROOT/"iptvorg-epg-coverage.json"
COVERAGE=ROOT/"coverage-favorites.json"
MANUAL=ROOT/"manual-epg-sources.json"
EXACT_GUIDE=ROOT/"guide-iptvorg-exact.xml"

SITE_PRIORITY=[
    "mi.tv",
    "meuguia.tv",
    "guiadetv.com",
    "gatotv.com",
    "programacion.tcc.com.uy",
    "xumo.tv",
    "plex.tv",
    "pluto.tv",
    "i.mjh.nz",
]

def parse_playlist(path):
    ids={}
    for line in path.read_text(encoding="utf-8",errors="ignore").splitlines():
        if not line.startswith("#EXTINF:"): continue
        m=re.search(r'tvg-id="([^"]+)"',line)
        if not m: continue
        cid=m.group(1).strip()
        name=line[line.rfind(",")+1:].strip()
        if cid: ids[cid]=name
    return ids

def rank(site,path):
    try:
        return SITE_PRIORITY.index(site)
    except ValueError:
        pass
    # stable fallback after preferred sources
    return len(SITE_PRIORITY)+1

wanted=parse_playlist(PLAYLIST)
matches=defaultdict(list)

for path in EPG_ROOT.glob("sites/**/*.channels.xml"):
    try:
        root=ET.parse(path).getroot()
    except Exception:
        continue
    for ch in root.findall("channel"):
        cid=(ch.get("xmltv_id") or "").strip()
        if cid not in wanted:
            continue
        site=(ch.get("site") or path.parent.name).strip()
        matches[cid].append({
            "site":site,
            "site_id":ch.get("site_id") or "",
            "lang":ch.get("lang") or "",
            "xmltv_id":cid,
            "name":(ch.text or "").strip(),
            "path":str(path.relative_to(EPG_ROOT))
        })

if MANUAL.exists():
    try:
        manual=json.loads(MANUAL.read_text(encoding="utf-8"))
    except Exception:
        manual={}
    for cid,rows in manual.items():
        if cid not in wanted:
            continue
        for r in rows:
            matches[cid].append({
                "site":r["site"],
                "site_id":r["site_id"],
                "lang":r.get("lang",""),
                "xmltv_id":cid,
                "name":r.get("name") or wanted[cid],
                "path":"manual-epg-sources.json"
            })

selected={}
for cid,rows in matches.items():
    rows=sorted(rows,key=lambda r:(rank(r["site"],r["path"]),r["site"],r["path"],r["site_id"]))
    selected[cid]=rows[0]

root=ET.Element("channels")
for cid in sorted(selected):
    r=selected[cid]
    ch=ET.SubElement(root,"channel",{
        "site":r["site"],
        "site_id":r["site_id"],
        "lang":r["lang"],
        "xmltv_id":cid,
    })
    ch.text=r["name"] or wanted[cid]

ET.indent(root,space="  ")
ET.ElementTree(root).write(OUT_XML,encoding="utf-8",xml_declaration=True)

current_missing=set()
if COVERAGE.exists():
    try:
        cov=json.loads(COVERAGE.read_text(encoding="utf-8"))
        current_missing={x["tvg_id"] for x in cov.get("missing",[])}
    except Exception:
        current_missing=set()

missing_root=ET.Element("channels")
missing_selected={}
for cid in sorted(selected):
    if current_missing and cid not in current_missing:
        continue
    r=selected[cid]
    ch=ET.SubElement(missing_root,"channel",{
        "site":r["site"],
        "site_id":r["site_id"],
        "lang":r["lang"],
        "xmltv_id":cid,
    })
    ch.text=r["name"] or wanted[cid]
    missing_selected[cid]=r
ET.indent(missing_root,space="  ")
ET.ElementTree(missing_root).write(OUT_MISSING_XML,encoding="utf-8",xml_declaration=True)

# Create one authoritative per-site batch entry per target.
# Alternate sources must never be merged under the same xmltv_id because their
# schedules can differ; the previous exact guide is the fallback if today's
# selected source fails.
if BATCH_DIR.exists():
    for p in BATCH_DIR.glob("*.channels.xml"):
        p.unlink()
else:
    BATCH_DIR.mkdir(parents=True)

refresh_ids=set()
if EXACT_GUIDE.exists():
    try:
        eroot=ET.parse(EXACT_GUIDE).getroot()
        refresh_ids={ch.get("id") for ch in eroot.findall("channel") if ch.get("id")}
    except Exception:
        refresh_ids=set()

batch_targets=current_missing | refresh_ids

site_rows=defaultdict(list)
for cid in sorted(batch_targets):
    r=selected.get(cid)
    if not r:
        continue
    site_rows[r["site"]].append(r)

def safe_name(site):
    return re.sub(r"[^A-Za-z0-9._-]+","_",site)

for site,rows in sorted(site_rows.items()):
    rroot=ET.Element("channels")
    for r in rows:
        ch=ET.SubElement(rroot,"channel",{
            "site":r["site"],
            "site_id":r["site_id"],
            "lang":r["lang"],
            "xmltv_id":r["xmltv_id"],
        })
        ch.text=r["name"] or wanted[r["xmltv_id"]]
    ET.indent(rroot,space="  ")
    ET.ElementTree(rroot).write(BATCH_DIR/f"{safe_name(site)}.channels.xml",encoding="utf-8",xml_declaration=True)

site_counts=Counter(r["site"] for r in selected.values())
missing=sorted(set(wanted)-set(selected))
report={
    "playlist_channels":len(wanted),
    "exactly_supported_channels":len(selected),
    "support_percent":round(len(selected)/len(wanted)*100,1) if wanted else 0,
    "selected_sites":dict(site_counts.most_common()),
    "selected":[
        {"tvg_id":cid,"playlist_name":wanted[cid],**selected[cid]}
        for cid in sorted(selected)
    ],
    "missing":[{"tvg_id":cid,"name":wanted[cid]} for cid in missing],
    "all_matches":{cid:rows for cid,rows in sorted(matches.items())}
}
OUT_JSON.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps({
    "playlist_channels":report["playlist_channels"],
    "exactly_supported_channels":report["exactly_supported_channels"],
    "support_percent":report["support_percent"],
    "selected_sites":report["selected_sites"],
    "missing":len(missing),
    "currently_missing_but_exact_supported":len(missing_selected),
    "batch_sites":len(site_rows),
    "batch_entries":sum(len(v) for v in site_rows.values()),
    "refresh_ids":len(refresh_ids),
    "batch_targets":len(batch_targets)
},ensure_ascii=False,indent=2))
