"""Validate canonical Flex identities and export before any publication."""
import json,re,sys,xml.etree.ElementTree as ET
from pathlib import Path

def validate(root,baseline):
 c=json.loads((root/'catalog-flex.json').read_text());old=json.loads((baseline/'catalog-flex.json').read_text())
 assert len(c['channels'])==len(old['channels'])==237
 locks=['id','number','order','name','shortName','category','country','language','tvgId','logo','sources']
 for a,b in zip(c['channels'],old['channels']):
  for key in locks:assert a.get(key)==b.get(key),key
 counts={k:sum(s['type']==k for ch in c['channels'] for s in ch['sources']) for k in ['direct','pluto']};assert counts=={'direct':101,'pluto':136}
 text=(root/'zapper-flex.m3u').read_text();lines=[x for x in text.splitlines() if x.strip()];assert lines[0]=='#EXTM3U x-tvg-url="https://raw.githubusercontent.com/Chevosky/IPTV/main/guide-flex.xml"'
 pairs=[];entry=None
 for line in lines[1:]:
  if line.startswith('#EXTINF:'):assert entry is None;entry=line
  elif not line.startswith('#'):assert entry is not None;pairs.append((entry,line));entry=None
 assert entry is None and len(pairs)==237
 for ch,(entry,url) in zip(c['channels'],pairs):
  attrs=dict(re.findall(r'([\w-]+)="([^"]*)"',entry))
  assert attrs.get('tvg-id')==ch['tvgId'] and attrs.get('group-title')==ch['category'] and attrs.get('tvg-logo')==ch['logo']
  assert re.split(r',(?=(?:[^"]*"[^"]*")*[^"]*$)',entry,maxsplit=1)[1]==ch['name']
  s=ch['sources'][0];expected=s['url'] if s['type']=='direct' else 'https://zapper-pluto-flex.chevosky.workers.dev/pluto/'+s['channelId']+'.m3u8'
  assert url==expected
  assert not re.search(r'[?&](jwt|token|session|expires|signature)=',url,re.I)
 return {'channels':237,**counts,'identityOrderAndSourcePreserved':True,'M3UEntries':237}
if __name__=='__main__':print(json.dumps(validate(Path(sys.argv[1]),Path(sys.argv[2]))))
