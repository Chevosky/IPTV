#!/usr/bin/env python3
"""Recheck original Sports sources independently of exportEligible.
No credentials, DRM bypass, new source discovery, or consumer replacement.
HTTP, media decoding, identity and public endpoints are separate evidence.
"""
from __future__ import annotations
import array, concurrent.futures, hashlib, ipaddress, json, re, shutil, socket
import subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path
from datetime import datetime, timezone
LIMIT=1024*1024
QUERY_KEYS={'stream','id','w','h','ads.xumo_channelId'}
def now(): return datetime.now(timezone.utc).isoformat()
class CheckError(Exception): pass
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None

def public_url(url,resolve=True):
    p=urllib.parse.urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:
        raise CheckError('UNSAFE_URL')
    if p.port not in (None,80,443) or any(ord(c)<32 for c in url): raise CheckError('UNSAFE_URL')
    if p.hostname.lower() in ('localhost','metadata.google.internal'): raise CheckError('UNSAFE_ADDRESS')
    if resolve:
        for addr in socket.getaddrinfo(p.hostname,p.port or 443,type=socket.SOCK_STREAM):
            if not ipaddress.ip_address(addr[4][0]).is_global: raise CheckError('UNSAFE_ADDRESS')
    return url

def get(url,deadline):
    opener=urllib.request.build_opener(NoRedirect)
    for _ in range(4):
        public_url(url)
        remaining=deadline-time.monotonic()
        if remaining<=0: raise CheckError('HTTP_TIMEOUT')
        req=urllib.request.Request(url,headers={'User-Agent':'Zapper-Source-Check/1.0'})
        try: resp=opener.open(req,timeout=min(8,remaining))
        except urllib.error.HTTPError as exc:
            if exc.code in (301,302,303,307,308) and exc.headers.get('Location'):
                url=urllib.parse.urljoin(url,exc.headers['Location']);exc.close();continue
            status=exc.code;exc.close();return status,b'',url
        with resp:
            data=resp.read(LIMIT+1)
            if len(data)>LIMIT: raise CheckError('BODY_TOO_LARGE')
            return resp.status,data,url
    raise CheckError('TOO_MANY_REDIRECTS')

def hls(body):
    text=body.decode('utf-8-sig',errors='replace').lstrip()
    return text.startswith('#EXTM3U') and ('#EXT-X-STREAM-INF:' in text or '#EXTINF:' in text)

def explicit_media(body):
    text=body.decode('utf-8',errors='replace')
    matches=re.findall(r'''["']?playbackURL["']?\s*[:=]\s*("(?:\\.|[^"\\])*")''',text)
    values=set()
    for match in matches:
        try:
            value=json.loads(match);public_url(value,resolve=False);values.add(value)
        except (ValueError,CheckError): pass
    return sorted(values)

def decode_media(url):
    result={'videoDecoded':False,'audioDecoded':False,'nonSilentPCM':False,'advanceSeconds':0.0,
            'physicalAudio':'NOT_TESTED','avPlayerCompatibility':'NOT_TESTED','flexCompatibility':'NOT_TESTED'}
    with tempfile.TemporaryDirectory(prefix='zapper-media-') as td:
        tmp=Path(td)
        command=['ffmpeg','-hide_banner','-nostdin','-loglevel','error','-y','-rw_timeout','8000000',
            '-protocol_whitelist','http,https,tcp,tls,crypto','-threads','1','-i',url,
            '-map','0:v:0','-an','-t','4','-vf','scale=320:-2','-threads','1','-f','framehash',str(tmp/'frames.txt'),
            '-map','0:a:0?','-vn','-t','4','-ar','8000','-ac','1','-f','s16le',str(tmp/'audio.pcm')]
        start=time.monotonic()
        try:
            proc=subprocess.run(command,capture_output=True,timeout=38);result['decoderExit']=proc.returncode
            err=proc.stderr.decode('utf-8','replace').lower()
            if proc.returncode: result['decodeFailure']=('SEGMENT_EXTENSION_UNSUPPORTED' if 'extension' in err else 'ACCESS_DENIED' if '403' in err or '401' in err else 'DECODER_FAILED')
        except subprocess.TimeoutExpired:
            result['decoderExit']=None;result['decodeFailure']='DECODE_TIMEOUT'
        result['sampleWallSeconds']=round(time.monotonic()-start,3)
        times=[];frames=tmp/'frames.txt'
        if frames.exists():
            tb=1.0
            for line in frames.read_text().splitlines():
                if line.startswith('#tb 0:'):
                    a,b=line.split(':',1)[1].strip().split('/');tb=int(a)/int(b)
                elif line and not line.startswith('#'):
                    fields=[v.strip() for v in line.split(',')]
                    if len(fields)>=6: times.append(int(fields[2])*tb)
            result['decodedFrames']=len(times);result['videoDecoded']=bool(times)
            if len(times)>1: result['advanceSeconds']=round(max(times)-min(times),3)
        audio=tmp/'audio.pcm'
        if audio.exists():
            raw=audio.read_bytes();samples=array.array('h',raw[:len(raw)//2*2])
            if sys.byteorder!='little': samples.byteswap()
            result['audioDecoded']=bool(samples);result['nonSilentPCM']=any(abs(x)>8 for x in samples)
    return result

def check(source):
    begin=time.monotonic()
    result={'sourceId':source['id'],'adapter':source['adapter'],'contents':source['contents'],
        'checkedAtUTC':now(),'previouslyIdentityVerified':source.get('previouslyIdentityVerified',False),
        'identityVerifiedThisRun':False,'epgVerifiedThisRun':False,'playbackTested':False,'status':'NOT_TESTED'}
    try:
        p=urllib.parse.urlsplit(source['url'])
        if set(urllib.parse.parse_qs(p.query))-QUERY_KEYS: raise CheckError('UNAPPROVED_LOCATOR_PARAMETERS')
        status,body,current=get(source['url'],begin+16);result['httpStatus']=status
        if status!=200: result['status']='HTTP_'+str(status);return result
        result['responseBytes']=len(body);result['responseSHA256']=hashlib.sha256(body).hexdigest()
        if not hls(body):
            if source['adapter']=='direct.generic': result['status']='INVALID_MEDIA_RESPONSE';return result
            found=explicit_media(body);result['explicitMediaCandidates']=len(found)
            if len(found)!=1:
                result['status']='BROWSER_OR_ADAPTER_REQUIRED' if not found else 'AMBIGUOUS_MEDIA';return result
            status,body,current=get(found[0],begin+25);result['mediaHTTPStatus']=status
            if status!=200 or not hls(body): result['status']='INVALID_MEDIA_RESPONSE';return result
        text=body.decode('utf-8-sig','replace')
        if '#EXT-X-KEY:' in text and ('SAMPLE-AES' in text or 'KEYFORMAT=' in text):
            result['status']='PROTECTED_MEDIA_NO_BYPASS';return result
        result['manifestValid']=True;result['playbackTested']=True;result.update(decode_media(current))
        result['status']=('MEDIA_SAMPLE_PASS_IDENTITY_PENDING' if result['videoDecoded'] and result['advanceSeconds']>=3 and result['nonSilentPCM'] else 'MEDIA_SAMPLE_INCOMPLETE')
        result['publicResolverRequired']=source['adapter']!='direct.generic'
    except CheckError as exc: result['status']=str(exc)
    except (TimeoutError,socket.timeout): result['status']='HTTP_TIMEOUT'
    except (urllib.error.URLError,socket.gaierror): result['status']='NETWORK_ERROR'
    except Exception as exc: result['status']='CHECK_ERROR';result['errorType']=type(exc).__name__
    finally: result['wallSeconds']=round(time.monotonic()-begin,3)
    return result

def selftest():
    assert hls(b'#EXTM3U\n#EXTINF:4,\nsegment.ts\n')
    assert not hls(b'#EXTM3U\n') and not hls(b'Not found!')
    assert explicit_media(b'let playbackURL = "https://example.com/a.m3u8?token=abc";')==['https://example.com/a.m3u8?token=abc']
    assert explicit_media(b'{"playbackURL":"https:\\/\\/example.com\\/live.m3u8"}')==['https://example.com/live.m3u8']
    for value in ['file:///etc/passwd','https://user:password@example.com/a','http://localhost/a']:
        try: public_url(value,resolve=False);raise AssertionError('unsafe URL accepted')
        except CheckError: pass
    print('SELFTEST PASS')

def main():
    import argparse
    from collections import Counter
    ap=argparse.ArgumentParser();ap.add_argument('--selftest',action='store_true')
    ap.add_argument('--input',default='publication/sports/source-candidates.json')
    ap.add_argument('--output',default='sports-recovery-results');args=ap.parse_args()
    if args.selftest: selftest();return
    if not shutil.which('ffmpeg'): raise SystemExit('ffmpeg missing; no false playback results generated')
    seed=json.loads(Path(args.input).read_text());sources=seed['sources']
    assert len(sources)==len({s['id'] for s in sources})
    folder=Path(args.output);folder.mkdir(parents=True,exist_ok=True);results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(check,s):s['id'] for s in sources}
        for future in concurrent.futures.as_completed(futures):
            result=future.result();results.append(result);print(result['sourceId'],result['status'],flush=True)
    results.sort(key=lambda r:r['sourceId'])
    report={'schemaVersion':1,'catalogRevision':seed['catalogRevision'],'checkedAtUTC':now(),
        'sourceCount':len(sources),'counts':dict(Counter(r['status'] for r in results)),
        'publicationChanged':False,'identityAutoPromotion':False,'results':results}
    output=json.dumps(report,ensure_ascii=False,indent=2)
    assert 'http://' not in output and 'https://' not in output
    (folder/'report.json').write_text(output+'\n')
    (folder/'summary.md').write_text('# Original Sports source recovery\n\n'+'\n'.join(f'- {k}: {v}' for k,v in report['counts'].items())+'\n\nDecoded media does not prove channel/event identity or Flex/AVPlayer compatibility.\n')
    print(json.dumps({k:v for k,v in report.items() if k!='results'},ensure_ascii=False))
    if all(r['status'] in ('NETWORK_ERROR','HTTP_TIMEOUT','CHECK_ERROR') for r in results):
        raise SystemExit('Runner could not reach sources; consumers unchanged')
if __name__=='__main__': main()
