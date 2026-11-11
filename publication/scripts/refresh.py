"""Scheduled preparation: observations never promote unknown content to playable."""
import base64,hashlib,json,os,subprocess,sys,urllib.request,urllib.error
from tempfile import TemporaryDirectory
from channel_policy import filter_playlist, load_policy
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path.cwd();mode=os.environ['REFRESH_MODE'];report={'mode':mode,'checkedAtUTC':datetime.now(timezone.utc).isoformat(),'published':False,'results':[]}
class PublicationMainAdvanced(RuntimeError):
 pass

def revision(ref):
 return subprocess.check_output(['git','rev-parse',ref],text=True).strip()

def latest_main():
 subprocess.run(['git','fetch','--no-tags','origin','refs/heads/main:refs/remotes/origin/main'],check=True)
 return revision('origin/main')

def validate_coordinated_outputs(output):
 envelope=json.loads((output/'coordinated-generation.json').read_bytes())
 data=base64.b64decode(envelope['payload'],validate=True)
 if hashlib.sha256(data).hexdigest()!=envelope['sha256']:
  raise ValueError('Invalid coordinated generation digest')
 payload=json.loads(data)
 if 'flexSports' in payload:
  from filter_expansion import playlist_rows
  from flex_sports import project
  playlist,guide=project(payload,playlist_rows)
 else:
  playlist,guide=payload['playlist'],payload['flexXML'].encode()
 for file,expected in (('zapper-flex.m3u',playlist.encode()),('guide-flex.xml',guide),('guide-flex-20261003.xml',guide)):
  if (output/file).read_bytes()!=expected:
   raise ValueError(f'{file} differs from the coordinated generation')
 if (ROOT/'guide-sports-news.xml').read_bytes()!=payload['sportsXML'].encode():
  raise ValueError('Sports input differs from the coordinated generation')

def probe(url):
 # Standard curl does not follow redirects or retain headers/signed locations.
 try:
  p=subprocess.run(['curl','--silent','--max-time','20','--output','/dev/null','--write-out','%{http_code}',url],capture_output=True,text=True,timeout=22)
  return int(p.stdout) if p.returncode==0 and p.stdout.isdigit() else 'NETWORK_ERROR'
 except Exception:return 'NETWORK_ERROR'
try:
 flex=json.loads((ROOT/'catalog-flex.json').read_text())
 assert len(flex['channels'])==237
 expansion_seed=ROOT/'publication/epg/expansion-seed.json'
 playlist=ROOT/'zapper-flex.m3u'
 canonical_playlist=playlist
 if expansion_seed.exists():
  canonical_playlist=ROOT/'publication/epg/base.m3u'
  if not canonical_playlist.is_file():
   raise ValueError('The canonical Flex playlist is required for expansion refresh')
  if mode=='epg':
   canonical_playlist=ROOT/'zapper-flex-base-candidate.m3u'
   canonical_playlist.write_text((ROOT/'publication/epg/base.m3u').read_text())
 if mode=='epg' or not expansion_seed.exists():
  canonical_playlist.write_text(filter_playlist(canonical_playlist.read_text(),load_policy(ROOT)))
 from validate_flex import validate
 report['canonicalValidation']=validate(ROOT,ROOT/'publication/baseline',canonical_playlist)
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
  subprocess.run([sys.executable,'scripts/build_epg.py'],check=True)
  env={**os.environ,'ZAPPER_PLAYLIST':canonical_playlist.name}
  subprocess.run([sys.executable,'publication/scripts/build_flex_guide.py'],env=env,check=True)
  validator=[sys.executable,'publication/scripts/validate_zapper_guide.py','guide-flex-candidate.xml',
             '--previous','guide-flex.xml','--coverage-report','coverage-flex-candidate.json']
  if expansion_seed.exists():
   validator.extend(['--previous-expansion',str(expansion_seed)])
  subprocess.run(validator,env={**os.environ,'ZAPPER_DATA_DIR':str(ROOT)},check=True)
  from shutil import copyfile
  base=revision('HEAD')
  if base!=latest_main():
   raise PublicationMainAdvanced('main advanced before generation')
  guides = ('guide-flex.xml', 'guide-flex-20261003.xml')
  files = [*guides, 'zapper-flex.m3u']
  if expansion_seed.exists():
   from refresh_expansion import build
   with TemporaryDirectory() as temporary:
    output=Path(temporary)/'release'
    result=build(ROOT,canonical_playlist,ROOT/'guide-flex-candidate.xml',
                 Path(os.environ['ZAPPER_EXPANSION_BATCHES']),output)
    validate_coordinated_outputs(output)
    report['expansion']={k:result[k] for k in ('expansionCount','includedCount','freshProviderChannels')}
    report['expansion']['officialOnlyChannels']=result.get('officialOnlyChannels',0)
    report['youtubeRefresh']=result['youtubeRefresh']
    report['youtubeFallbackRefresh']=result.get('youtubeFallbackRefresh',{})
    report['nativeYouTubeFallbackRefresh']=result.get('nativeYouTubeFallbackRefresh',{})
    report['publicFallbackRefresh']=result['publicFallbackRefresh']
    files.append('coordinated-generation.json')
    for file in files:
     copyfile(output/file,file)
   report['scope']='Flex and Zapper from one generation; Sports guide preserved independently'
  else:
   for guide in guides:
    copyfile('guide-flex-candidate.xml', guide)
   report['scope']='Flex only; Sports guide preserved independently'
  subprocess.run(['git','add','--',*files],check=True)
  subprocess.run(['git','diff','--cached','--check'],check=True)
  if subprocess.run(['git','diff','--cached','--quiet']).returncode:
   subprocess.run(['git','-c','user.name=github-actions[bot]','-c','user.email=41898282+github-actions[bot]@users.noreply.github.com','commit','-m','Zapper Publication: validated EPG refresh'],check=True)
   push=subprocess.run(['git','push','origin','HEAD:main'])
   if push.returncode:
    upstream=latest_main()
    if upstream!=revision('HEAD'):
     if upstream!=base:
      raise PublicationMainAdvanced('main advanced during generation or push')
     push.check_returncode()
   report['published']=True
 else:raise ValueError('unsupported refresh mode')
 report['status']='PASS'
except PublicationMainAdvanced as e:
 report['status']='RETRY';report['errorType']=type(e).__name__
 raise SystemExit(75)
except Exception as e:
 report['status']='FAIL';report['errorType']=type(e).__name__
 # No raw URL/exception text; report contains only IDs and status.
 raise
finally:Path('publication-review.json').write_text(json.dumps(report,indent=2))

