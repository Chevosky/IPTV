#!/usr/bin/env python3
import json, re, unicodedata
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PLAYLIST=ROOT/"favorites-test.m3u"
GUIDE=ROOT/"guide.xml"
COVERAGE=ROOT/"coverage-favorites.json"
OUT=ROOT/"mapping-candidates.json"

def clean_name(s):
    s=unicodedata.normalize("NFKD", s or "")
    s="".join(c for c in s if not unicodedata.combining(c))
    s=s.lower()
    s=re.sub(r'\[[^\]]*\]', ' ', s)
    s=re.sub(r'\((?:\d{3,4}p|hd|sd|not 24/7|geo-blocked)\)', ' ', s)
    s=s.replace("&"," and ")
    s=re.sub(r'[^a-z0-9]+',' ',s)
    s=re.sub(r'\b(?:tv|channel)\b',' ',s)
    return " ".join(s.split())

def playlist_names():
    out={}
    for line in PLAYLIST.read_text(encoding="utf-8",errors="ignore").splitlines():
        if not line.startswith("#EXTINF:"): continue
        m=re.search(r'tvg-id="([^"]+)"',line)
        if not m: continue
        cid=m.group(1).strip()
        name=line[line.rfind(",")+1:].strip()
        if cid: out[cid]=name
    return out

playlist=playlist_names()
coverage=json.loads(COVERAGE.read_text())
missing_ids={x["tvg_id"] for x in coverage["missing"]}

guide_names={}
for _,elem in ET.iterparse(GUIDE,events=("end",)):
    if elem.tag=="channel":
        cid=elem.get("id")
        names=[(x.text or "").strip() for x in elem.findall("display-name") if (x.text or "").strip()]
        if cid and names:
            guide_names[cid]=names
        elem.clear()
    elif elem.tag=="programme":
        elem.clear()

norm_index={}
for cid,names in guide_names.items():
    for n in names:
        norm_index.setdefault(clean_name(n),set()).add(cid)

rows=[]
for target_id in sorted(missing_ids):
    target_name=playlist.get(target_id,target_id)
    tn=clean_name(target_name)
    exact=sorted(norm_index.get(tn,[]))
    scored=[]
    if not exact:
        for source_id,names in guide_names.items():
            best=0.0
            best_name=""
            for n in names:
                sn=clean_name(n)
                if not sn: continue
                # avoid comparing obviously unrelated brands
                twords=set(tn.split()); swords=set(sn.split())
                if not twords or not swords or not (twords & swords):
                    continue
                score=SequenceMatcher(None,tn,sn).ratio()
                if score>best:
                    best=score; best_name=n
            if best>=0.60:
                scored.append((best,source_id,best_name))
        scored.sort(reverse=True)
    rows.append({
        "target_id":target_id,
        "target_name":target_name,
        "normalized":tn,
        "exact_normalized_matches":[
            {"source_id":cid,"source_names":guide_names[cid]} for cid in exact[:10]
        ],
        "top_fuzzy_matches":[
            {"score":round(score,3),"source_id":cid,"source_name":name}
            for score,cid,name in scored[:8]
        ]
    })

summary={
    "missing_targets":len(rows),
    "with_exact_normalized_match":sum(1 for r in rows if r["exact_normalized_matches"]),
    "with_fuzzy_090_plus":sum(1 for r in rows if r["top_fuzzy_matches"] and r["top_fuzzy_matches"][0]["score"]>=0.90),
}
OUT.write_text(json.dumps({"summary":summary,"candidates":rows},ensure_ascii=False,indent=2)+"\n")
print(json.dumps(summary,indent=2))
