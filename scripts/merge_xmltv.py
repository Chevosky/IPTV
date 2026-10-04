#!/usr/bin/env python3
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

if len(sys.argv) != 3:
    raise SystemExit("Usage: merge_xmltv.py <input_dir> <output.xml>")

indir=Path(sys.argv[1])
out=Path(sys.argv[2])
channels={}
programmes={}
now=datetime.now(timezone.utc)
window_end=now+timedelta(hours=36)
grace_start=now-timedelta(hours=2)

def parse_xmltv_dt(value):
    if not value:
        return None
    value=value.strip()
    main=value[:14]
    tz=value[14:].strip()
    try:
        dt=datetime.strptime(main,"%Y%m%d%H%M%S")
    except ValueError:
        return None
    if tz and len(tz)>=5 and tz[0] in "+-" and tz[1:5].isdigit():
        sign=1 if tz[0]=="+" else -1
        off=timedelta(hours=int(tz[1:3]),minutes=int(tz[3:5]))*sign
        return dt.replace(tzinfo=timezone(off)).astimezone(timezone.utc)
    return dt.replace(tzinfo=timezone.utc)

def load_file(path, allow_programmes_for=None):
    if not path.is_file() or path.stat().st_size == 0:
        return set()
    try:
        root=ET.parse(path).getroot()
    except Exception as e:
        print(f"Skipping {path}: {e}")
        return set()

    loaded=set()
    for ch in root.findall("channel"):
        cid=ch.get("id")
        if cid and cid not in channels:
            channels[cid]=ET.fromstring(ET.tostring(ch,encoding="utf-8"))

    for p in root.findall("programme"):
        cid=p.get("channel")
        start=parse_xmltv_dt(p.get("start"))
        stop=parse_xmltv_dt(p.get("stop"))
        if not cid or start is None:
            continue
        if allow_programmes_for is not None and cid not in allow_programmes_for:
            continue
        if stop is not None and stop < grace_start:
            continue
        if start > window_end:
            continue
        key=(cid,p.get("start"),p.get("stop"),(p.findtext("title") or "").strip())
        if key not in programmes:
            programmes[key]=ET.fromstring(ET.tostring(p,encoding="utf-8"))
            loaded.add(cid)
    return loaded

# Fresh batches are authoritative. The previous merged guide is only a
# per-channel fallback when today's selected source produced no programmes.
fresh_paths=[
    p for p in sorted(indir.glob("*.xml"))
    if p.name != "_previous.xml"
]
fresh_channels=set()
for path in fresh_paths:
    fresh_channels |= load_file(path)

previous=indir/"_previous.xml"
if previous.exists():
    previous_root=ET.parse(previous).getroot()
    fallback_ids={
        p.get("channel")
        for p in previous_root.findall("programme")
        if p.get("channel") and p.get("channel") not in fresh_channels
    }
    load_file(previous, allow_programmes_for=fallback_ids)

root=ET.Element("tv",{
    "generator-info-name":"Chevosky/IPTV exact iptv-org merge",
    "generator-info-url":"https://github.com/Chevosky/IPTV"
})
used={p.get("channel") for p in programmes.values()}
for cid in sorted(used):
    ch=channels.get(cid)
    if ch is None:
        ch=ET.Element("channel",{"id":cid})
        ET.SubElement(ch,"display-name").text=cid
    root.append(ch)

for p in sorted(programmes.values(),key=lambda x:(x.get("channel") or "",x.get("start") or "")):
    root.append(p)

if hasattr(ET,"indent"):
    ET.indent(root,space="  ")
ET.ElementTree(root).write(out,encoding="utf-8",xml_declaration=True)
print(f"Merged {len(used)} channels / {len(programmes)} programmes -> {out} ({out.stat().st_size/1024/1024:.2f} MB)")
if not programmes:
    raise SystemExit("No programme data found in batch guides")
