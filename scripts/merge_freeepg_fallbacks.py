#!/usr/bin/env python3
import gzip
import io
import json
import re
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
GUIDE=ROOT/"guide-iptvorg-exact.xml"
CFG=ROOT/"freeepg-fallbacks.json"
BASE="https://free-epg.de/api/epg"

def norm(s):
    s=unicodedata.normalize("NFKD",s or "")
    s="".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+"," ",s).strip()

def parse_dt(value):
    if not value:
        return None
    value=value.strip()
    try:
        dt=datetime.strptime(value[:14],"%Y%m%d%H%M%S")
    except Exception:
        return None
    tz=value[14:].strip()
    if len(tz)>=5 and tz[0] in "+-" and tz[1:5].isdigit():
        sign=1 if tz[0]=="+" else -1
        off=timedelta(hours=int(tz[1:3]),minutes=int(tz[3:5]))*sign
        return dt.replace(tzinfo=timezone(off)).astimezone(timezone.utc)
    return dt.replace(tzinfo=timezone.utc)

def fetch_country(code):
    urls=[f"{BASE}/{code}.xml.gz",f"{BASE}/{code}.xml"]
    last=None
    for url in urls:
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"Chevosky-IPTV-EPG/3.0"})
            with urllib.request.urlopen(req,timeout=90) as r:
                raw=r.read()
                enc=(r.headers.get("Content-Encoding") or "").lower()
            if url.endswith(".gz") or enc=="gzip" or raw[:2]==b"\x1f\x8b":
                raw=gzip.decompress(raw)
            return ET.fromstring(raw)
        except Exception as e:
            last=e
    raise last or RuntimeError("fetch failed")

cfg=json.loads(CFG.read_text(encoding="utf-8"))
root=ET.parse(GUIDE).getroot() if GUIDE.exists() else ET.Element("tv")
existing_prog=defaultdict(list)
for p in root.findall("programme"):
    existing_prog[p.get("channel")].append(p)

targets_by_country=defaultdict(dict)
for target,spec in cfg.items():
    if existing_prog.get(target):
        continue
    targets_by_country[spec["country"].lower()][target]=spec

now=datetime.now(timezone.utc)
start_floor=now-timedelta(hours=2)
end_ceil=now+timedelta(hours=36)
added_channels=0
added_programmes=0
resolved=[]

existing_channel_ids={c.get("id") for c in root.findall("channel")}
existing_keys={(p.get("channel"),p.get("start"),p.get("stop"),(p.findtext("title") or "").strip()) for p in root.findall("programme")}

for country,targets in sorted(targets_by_country.items()):
    try:
        feed=fetch_country(country)
    except Exception as e:
        print(f"FreeEPG {country}: fetch failed: {e}")
        continue

    name_to_ids=defaultdict(list)
    channel_nodes={}
    for ch in feed.findall("channel"):
        cid=ch.get("id")
        if not cid:
            continue
        channel_nodes[cid]=ch
        for dn in ch.findall("display-name"):
            if dn.text:
                name_to_ids[norm(dn.text)].append(cid)

    progs_by_id=defaultdict(list)
    for p in feed.findall("programme"):
        cid=p.get("channel")
        if not cid:
            continue
        st=parse_dt(p.get("start"))
        sp=parse_dt(p.get("stop"))
        if st is None or st>end_ceil or (sp is not None and sp<start_floor):
            continue
        progs_by_id[cid].append(p)

    for target,spec in targets.items():
        matched=[]
        for name in spec["names"]:
            matched.extend(name_to_ids.get(norm(name),[]))
        # stable unique
        matched=list(dict.fromkeys(matched))
        source=None
        for cid in matched:
            if progs_by_id.get(cid):
                source=cid
                break
        if not source:
            print(f"FreeEPG {country}: no current programmes for {target} ({', '.join(spec['names'])})")
            continue

        if target not in existing_channel_ids:
            src=channel_nodes.get(source)
            ch=ET.fromstring(ET.tostring(src,encoding="utf-8")) if src is not None else ET.Element("channel")
            ch.set("id",target)
            dns=ch.findall("display-name")
            if dns:
                dns[0].text=spec["names"][0]
            else:
                ET.SubElement(ch,"display-name").text=spec["names"][0]
            root.append(ch)
            existing_channel_ids.add(target)
            added_channels+=1

        count=0
        for srcp in progs_by_id[source]:
            p=ET.fromstring(ET.tostring(srcp,encoding="utf-8"))
            p.set("channel",target)
            key=(target,p.get("start"),p.get("stop"),(p.findtext("title") or "").strip())
            if key in existing_keys:
                continue
            root.append(p)
            existing_keys.add(key)
            count+=1
            added_programmes+=1
        if count:
            resolved.append(target)
            print(f"FreeEPG {country}: {target} <- {source}: {count} programmes")

# Reorder channels then programmes for clean XMLTV
channels=[x for x in root if x.tag=="channel"]
programmes=[x for x in root if x.tag=="programme"]
others=[x for x in root if x.tag not in ("channel","programme")]
newroot=ET.Element("tv",root.attrib)
for x in others:
    newroot.append(x)
for x in sorted(channels,key=lambda e:e.get("id") or ""):
    newroot.append(x)
for x in sorted(programmes,key=lambda e:(e.get("channel") or "",e.get("start") or "")):
    newroot.append(x)
ET.indent(newroot,space="  ")
ET.ElementTree(newroot).write(GUIDE,encoding="utf-8",xml_declaration=True)
print(f"FreeEPG fallback: resolved {len(resolved)} targets; +{added_channels} channels / +{added_programmes} programmes")
