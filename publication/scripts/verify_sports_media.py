#!/usr/bin/env python3
"""Bounded second-pass evidence for original linear sources; never auto-publish.
Uses original locators only. Captures one frame for identity review, not proof
that every regional feed, scheduled event or downstream client is compatible.
"""
from __future__ import annotations
import concurrent.futures, hashlib, json, re, socket, subprocess, threading, time
import urllib.error, urllib.parse
from collections import Counter
from pathlib import Path
import recover_sports as core

ROOT=Path('sports-media-review')
thread=threading.local()
original_decode=core.decode_media
original_get=core.get

def inspected_get(url,deadline):
    try: result=original_get(url,deadline)
    except urllib.error.URLError as exc:
        reason=exc.reason
        if isinstance(reason,socket.gaierror): thread.network_reason='DNS_ERROR'
        elif isinstance(reason,(TimeoutError,socket.timeout)): thread.network_reason='CONNECT_TIMEOUT'
        else: thread.network_reason=type(reason).__name__
        raise
    status,body,current=result
    if core.hls(body):
        text=body.decode('utf-8-sig','replace')
        thread.manifest_info={'keyMethods':sorted(set(re.findall(r'METHOD=([^,\s]+)',text))),
                              'hasEndList':'#EXT-X-ENDLIST' in text}
    return result

def decode_and_capture(url):
    result=original_decode(url)
    frame=ROOT/'frames'/(thread.source_id+'.jpg')
    if result.get('videoDecoded'):
        command=['ffmpeg','-hide_banner','-nostdin','-loglevel','error','-y',
                 '-rw_timeout','8000000','-protocol_whitelist','http,https,tcp,tls,crypto',
                 '-threads','1','-i',url,'-ss','1','-map','0:v:0','-frames:v','1',
                 '-an','-vf','scale=960:-2','-threads','1',str(frame)]
        try:
            p=subprocess.run(command,capture_output=True,timeout=18)
            if p.returncode==0 and frame.exists():
                result['frameForIdentityReview']=str(frame.relative_to(ROOT))
                result['frameSHA256']=hashlib.sha256(frame.read_bytes()).hexdigest()
            else:
                frame.unlink(missing_ok=True);result['frameReviewStatus']='CAPTURE_FAILED'
        except subprocess.TimeoutExpired:
            frame.unlink(missing_ok=True);result['frameReviewStatus']='CAPTURE_TIMEOUT'
    return result

core.get=inspected_get
core.decode_media=decode_and_capture

def verify(source):
    thread.source_id=source['id'];thread.network_reason=None;thread.manifest_info=None
    first=core.check(source)
    if thread.network_reason: first['networkReason']=thread.network_reason
    if thread.manifest_info: first['manifestInfo']=thread.manifest_info
    attempts=[first]
    # One retry only for transient connection/timeout failures. Never retry
    # access-denied responses or use alternative credentials/hosts/headers.
    if first['status'] in ('NETWORK_ERROR','HTTP_TIMEOUT'):
        time.sleep(2);thread.network_reason=None;thread.manifest_info=None
        second=core.check(source)
        if thread.network_reason: second['networkReason']=thread.network_reason
        if thread.manifest_info: second['manifestInfo']=thread.manifest_info
        attempts.append(second)
    return {'sourceId':source['id'],'adapter':source['adapter'],'contents':source['contents'],
            'attempts':attempts,'finalStatus':attempts[-1]['status'],
            'identityReview':'PENDING','publicExportApproved':False}

def group_check(sources):
    # Sequential within each provider host. Up to three distinct hosts at once.
    return [verify(s) for s in sources]

def main():
    seed=json.loads(Path('publication/sports/source-candidates.json').read_text())
    selected=[s for s in seed['sources'] if s['adapter'] in ('resolver.streamtp','resolver.la18')
              or (s['adapter']=='direct.generic' and 'soccerfull.net' not in s['url'])]
    assert selected
    (ROOT/'frames').mkdir(parents=True,exist_ok=True)
    groups={}
    for s in selected: groups.setdefault(urllib.parse.urlsplit(s['url']).hostname,[]).append(s)
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(group_check,s) for s in groups.values()]
        for f in concurrent.futures.as_completed(futures):
            for row in f.result():
                results.append(row);print(row['sourceId'],row['finalStatus'],flush=True)
    results.sort(key=lambda x:x['sourceId'])
    report={'schemaVersion':1,'catalogRevision':seed['catalogRevision'],
            'checkedAtUTC':core.now(),'sourceCount':len(selected),
            'counts':dict(Counter(x['finalStatus'] for x in results)),
            'consumerFilesChanged':False,'identityAutomaticallyApproved':False,'results':results}
    text=json.dumps(report,ensure_ascii=False,indent=2)
    assert 'https://' not in text and 'http://' not in text
    (ROOT/'report.json').write_text(text+'\n')
    (ROOT/'summary.md').write_text('# Sports linear-source second pass\n\n'+
       '\n'.join(f'- {k}: {v}' for k,v in report['counts'].items())+
       '\n\nFrames require identity review. No consumer playlist or channel ID changed.\n')
    print(json.dumps(report['counts']))

if __name__=='__main__':main()
