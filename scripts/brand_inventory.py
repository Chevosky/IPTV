#!/usr/bin/env python3
import json,re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
GUIDE=ROOT/"guide.xml"
OUT=ROOT/"brand-inventory.json"

brands={
  "A&E":r"\ba\s*&\s*e\b|\ba&e\b",
  "AMC":r"\bamc\b",
  "AXN":r"\baxn\b",
  "Adult Swim":r"adult\s+swim",
  "America":r"\bamerica\b|américa",
  "BBC":r"\bbbc\b",
  "History":r"\bhistory\b",
  "Lifetime":r"\blifetime\b",
  "Sony":r"\bsony\b",
  "Studio Universal":r"studio\s+universal",
  "TNT Novelas":r"tnt\s+novelas",
  "Telefe":r"\btelefe\b",
  "TVE Internacional":r"tve\s+internacional",
  "Nickelodeon":r"nickelodeon",
  "Fashion":r"fashion",
  "DW":r"\bdw\b",
  "Rai":r"\brai\b",
  "Paraguay":r"paraguay",
  "E Entertainment":r"(^|[^a-z])e!([^a-z]|$)|e! entertainment|e entertainment",
  "DreamWorks":r"dreamworks",
  "Play TV":r"(^|[^a-z])play[ ._-]*tv([^a-z]|$)",
  "TNT Novelas":r"tnt[ ._-]*novelas",
  "Atacama":r"atacama",
  "Canal Plus":r"canal\+|canal plus",
  "De Pelicula":r"de[ ._-]*pel[ií]cula",
  "Mar del Plata":r"mar[ ._-]*del[ ._-]*plata",
  "Telefe Local":r"telefe",
  "America Paraguay":r"america[ ._-]*paraguay|américa[ ._-]*paraguay",
}
compiled={k:re.compile(v,re.I) for k,v in brands.items()}
channels={}
counts={}

for _,e in ET.iterparse(GUIDE,events=("end",)):
    if e.tag=="channel":
        cid=e.get("id")
        names=[(x.text or "").strip() for x in e.findall("display-name") if (x.text or "").strip()]
        if cid and names:
            channels[cid]=names
        e.clear()
    elif e.tag=="programme":
        cid=e.get("channel")
        if cid:
            counts[cid]=counts.get(cid,0)+1
        e.clear()

out={}
for brand,rx in compiled.items():
    rows=[]
    for cid,names in channels.items():
        if any(rx.search(n) for n in names):
            rows.append({"id":cid,"names":names,"programmes":counts.get(cid,0)})
    rows.sort(key=lambda x:(-x["programmes"],x["id"]))
    out[brand]=rows

OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
for k,v in out.items():
    print(k,len(v))
