#!/usr/bin/env python3
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

if len(sys.argv) != 3:
    raise SystemExit("Usage: merge_xmltv.py <input_dir> <output.xml>")

indir=Path(sys.argv[1])
out=Path(sys.argv[2])
channels={}
programmes={}

for path in sorted(indir.glob("*.xml")):
    if not path.is_file() or path.stat().st_size == 0:
        continue
    try:
        root=ET.parse(path).getroot()
    except Exception as e:
        print(f"Skipping {path}: {e}")
        continue
    for ch in root.findall("channel"):
        cid=ch.get("id")
        if cid and cid not in channels:
            channels[cid]=ET.fromstring(ET.tostring(ch,encoding="utf-8"))
    for p in root.findall("programme"):
        cid=p.get("channel")
        key=(cid,p.get("start"),p.get("stop"),(p.findtext("title") or "").strip())
        if cid and key not in programmes:
            programmes[key]=ET.fromstring(ET.tostring(p,encoding="utf-8"))

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
