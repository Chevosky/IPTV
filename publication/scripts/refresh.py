"""Scheduled preparation: observations never promote unknown content to playable."""
import json,os,subprocess,urllib.request,urllib.error
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path.cwd();mode=os.environ['REFRESH_MODE'];report={'mode':mode,'checkedAtUTC':datetime.now(timezone.utc).isoformat(),'published':False,'results':[]}
def probe(url):
 # Standard curl does not follow redirects or retain headers/signed locations.
 try:
  p=subprocess.run(['curl','--silent','--max-time','20','--output','/dev/null','--write-out','%{http_code}',url],capture_output=True,text=True,timeout=22)
  return int(p.stdout) if p.returncode==0 and p.stdout.isdigit() else 'NETWORK_ERROR'
 except Exception:return 'NETWORK_ERROR'
try:
 flex=json.loads((ROOT/'catalog-flex.json').read_text())
 assert len(flex['channels'])==237
 from validate_flex import validate
 report['canonicalValidation']=validate(ROOT,ROOT/'publication/baseline')
 if mode=='sports':
  sports=json.loads((ROOT/'catalog-sports-news.json').read_text());assert len(sports['channels'])==1120
 if mode=='pluto':
  if os.environ.get('GITHUB_EVENT_NAME')=='schedule' and int(datetime.now(timezone.utc).timestamp()//43200)%3:
   report['skipped']='outside approximate 36h slot'
  else:
   ids=[s['channelId'] for c in flex['channels'] for s in c['sources'] if s['type']=='pluto'];assert len(set(ids))==136
   origin=os.environ['PUBLIC_RESOLVER_ORIGIN'];assert origin=='https://zapper-pluto-flex.chevosky.workers.dev'
   for uid in ids:report['results'].append({'uuid':uid,'status':probe(origin+'/pluto/'+uid+'.m3u8')})
   assert all(x['status']==302 for x in report['results']),'Pluto public resolution failed'
 elif mode=='catalog':
  for c in flex['channels']:
   for s in c['sources']:
    if s['type']=='direct':report['results'].append({'channelId':c['id'],'status':probe(s['url'])})
  report['identityChangePolicy']='review only; no substitute or automatic identity reassignment'
 elif mode=='sports':
  seen=set()
  for c in sports['channels']:
   for s in c['sources']:
    if not s.get('exportEligible') or s['id'] in seen:continue
    seen.add(s['id']);status=probe(os.environ['PUBLIC_RESOLVER_ORIGIN']+'/sports/'+s['id']+'.m3u8')
    report['results'].append({'sourceId':s['id'],'availabilityHTTP':status,'playbackVerified':False})
  report['scheduleUpdates']='Lens verified schedule bindings required; no identity inferred from HTTP'
  assert seen,'No eligible Sports sources supplied; cannot certify maintenance'
 elif mode=='epg':
  subprocess.run(['python','scripts/build_epg.py'],check=True)
  env={**os.environ,'ZAPPER_PLAYLIST':'zapper-flex.m3u'};subprocess.run(['python','publication/scripts/build_flex_guide.py'],env=env,check=True)
  subprocess.run(['python','publication/scripts/validate_zapper_guide.py','guide-flex-candidate.xml','--previous','guide-flex.xml','--coverage-report','coverage-flex-candidate.json'],env={**os.environ,'ZAPPER_DATA_DIR':str(ROOT)},check=True)
  # An explicit verified identity map is necessary to refresh Sports EPG.
  report['scope']='Flex only; Sports guide preserved independently'
  import shutil
  shutil.copy2('guide-flex-candidate.xml',os.environ['RUNNER_TEMP']+'/guide-flex.xml')
  subprocess.run(['git','fetch','origin','main'],check=True)
  assert subprocess.check_output(['git','rev-parse','HEAD'])==subprocess.check_output(['git','rev-parse','origin/main']),'remote advanced; retry regeneration'
  shutil.copy2(os.environ['RUNNER_TEMP']+'/guide-flex.xml','guide-flex.xml')
  subprocess.run(['git','add','--','guide-flex.xml'],check=True)
  subprocess.run(['git','diff','--cached','--check'],check=True)
  if subprocess.run(['git','diff','--cached','--quiet']).returncode:
   subprocess.run(['git','-c','user.name=github-actions[bot]','-c','user.email=41898282+github-actions[bot]@users.noreply.github.com','commit','-m','Zapper Publication: validated EPG refresh'],check=True)
   subprocess.run(['git','push','origin','HEAD:main'],check=True);report['published']=True
 else:raise ValueError('unsupported refresh mode')
 report['status']='PASS'
except Exception as e:
 report['status']='FAIL';report['errorType']=type(e).__name__
 # No raw URL/exception text; report contains only IDs and status.
 raise
finally:Path('publication-review.json').write_text(json.dumps(report,indent=2))
