"""Compare paired HLS windows while keeping all external requests public and credential-free."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import local
from time import monotonic, sleep
from urllib.parse import urljoin, urlsplit
from array import array
import hashlib
import ipaddress
import json
import math
import re
import socket
import shutil
import subprocess
import sys

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
ATTRIBUTES = re.compile(r'([A-Z0-9-]+)=(?:"([^"]*)"|([^,]*))')
SEGMENTS = 5
REVIEWED_ROLLING_PAIRS = {
    # FashionTV Paris L’Original
    ("7546ae03ad3d8b6b1c3072d076e089c813760977247a3f60cd223fdfa6d3b800",
     "cade114a921591474790c641bb0f93e63dfbfa6838b0e42813903c98b0f04d0a"),
}
# Exact reviewed Cricket Gold pair. The first locator's HTTPS media manifest
# explicitly delegates MPEG-TS segments to SOFAST's YuppTV CDN.
REVIEWED_SEGMENT_ORIGINS = {
    ("7dec9f03d6c17f34b8f29ca2b5f4eb56411b969ad566052692a95365a04f6cb6",
     "ca07d027725394bf4953bc33871c87778da2161ea8e1cb9e0730eb7d554e60d6"): (
        ("https", "streams2.sofast.tv", 443), ("https", "sofast-yupptv.vgcdn.net", 443)),
}
SUBPROCESS_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C"}


class ProbeError(Exception):
    pass


def is_public(address):
    ip = ipaddress.ip_address(address)
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


def public_route(url, *, same_origin=None, pluto_id=None):
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        official_pluto = bool(host and (host=="pluto.tv" or host.endswith(".pluto.tv")))
        checked_pluto = (pluto_id and parsed.scheme=="https" and official_pluto
                         and pluto_id in parsed.path.split("/") and parsed.port in (None,443))
        if pluto_id is not None and not checked_pluto:
            raise ProbeError("pluto-exact-official-redirect-required")
        if "?" in url and same_origin is None and not checked_pluto:
            raise ProbeError("locator-query-present")
        if parsed.username is not None or parsed.password is not None:
            raise ProbeError("locator-userinfo-present")
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or "#" in url or parsed.port not in (None,80,443,8000,8080,9999)
            or re.search(r'[\s\\"]', url)):
            raise ProbeError("locator-scheme-port-or-fragment")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if same_origin is not None and (parsed.scheme,host,port)!=same_origin:
            raise ProbeError("child-cross-origin")
        resolved = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        addresses = sorted({row[4][0] for row in resolved if is_public(row[4][0])})
        if not addresses:
            raise ProbeError("destination-without-global-ipv4")
        return host, port, addresses[0]
    except (OSError, ValueError):
        raise ProbeError("destination-unverified") from None


def origin(url):
    parsed=urlsplit(url)
    return parsed.scheme,parsed.hostname,parsed.port or (443 if parsed.scheme=="https" else 80)


youtube_transport = local()


def googlevideo(url):
    value = urlsplit(url)
    host = value.hostname or ""
    return (value.scheme == "https" and value.port is None and value.username is None
            and value.password is None and not value.fragment
            and (host == "googlevideo.com" or host.endswith(".googlevideo.com")))


def get_youtube(url, cap, stage):
    if stage in ("variant-manifest", "audio-manifest"):
        cap = max(cap, 2_000_000)
    for _ in range(4):
        if not googlevideo(url):
            raise ProbeError("unexpected-youtube-cdn")
        # The watch extractor supplies short-lived public locators. Do not put
        # them in process arguments, output, cached catalogue, or error text.
        host, port, addr = public_route(url, same_origin=origin(url))
        try:
            result = subprocess.run(
                ["/usr/bin/curl", "--disable", "--globoff", "--silent", "--noproxy", "*", "--compressed",
                 "--max-time", "7", "--max-filesize", str(cap), "--proto", "=https",
                 "--resolve", f"{host}:{port}:{addr}", "--write-out",
                 "\nZAPPER_HTTP_STATUS\n%{response_code}\n%{redirect_url}", "--config", "-"],
                input=f'url = "{url}"\n'.encode(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=SUBPROCESS_ENV, timeout=8)
        except subprocess.TimeoutExpired:
            raise ProbeError("youtube-cdn-timeout") from None
        body, delimiter, tail = result.stdout.rpartition(b"\nZAPPER_HTTP_STATUS\n")
        if result.returncode or not delimiter or len(body) > cap:
            raise ProbeError("youtube-cdn-transfer")
        code, _, target = tail.decode("iso-8859-1").partition("\n")
        if code in ("200", "206"):
            return url, body
        if code not in ("301", "302", "303", "307", "308") or not target:
            raise ProbeError("youtube-cdn-response")
        url = urljoin(url, target)
    raise ProbeError("youtube-cdn-redirect-limit")


def get(url, *, cap=6_000_000, stage="segment", parent=None, pluto_id=None):
    if getattr(youtube_transport, "active", False):
        return get_youtube(url, cap, stage)
    for hop in range(4):
        try:
            host, port, addr = public_route(url,same_origin=origin(parent) if parent else None,
                                            pluto_id=pluto_id if hop else None)
        except ProbeError as exc:
            raise ProbeError(("redirect" if hop else stage) + ":" + str(exc)) from None
        try:
            run = subprocess.run(["/usr/bin/curl","--disable","--globoff","--silent","--noproxy","*","--max-time","7",
                                  "--max-filesize",str(cap),"--proto","=http,https",
                                  "--resolve",f"{host}:{port}:{addr}","--include","--config","-"],
                                 input=f'url = "{url}"\n'.encode(),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=SUBPROCESS_ENV,timeout=8)
        except subprocess.TimeoutExpired:
            raise ProbeError("request-timeout") from None
        if run.returncode != 0:
            raise ProbeError("request-failed-or-byte-limit")
        head, delimiter, body = run.stdout.partition(b"\r\n\r\n")
        if not delimiter:
            raise ProbeError("invalid-http-response")
        lines = head.decode("iso-8859-1").splitlines()
        code = lines[0].split()[1] if len(lines[0].split()) >= 2 else ""
        if code in {"200","206"}:
            return url, body
        if code in {"301","302","303","307","308"}:
            target = next((line.split(":",1)[1].strip() for line in lines[1:] if line.lower().startswith("location:")), "")
            if not target:
                raise ProbeError("redirect-without-location")
            url = urljoin(url,target)
            continue
        raise ProbeError("http-" + code if code.isdecimal() else "http-error")
    raise ProbeError("redirect-limit")


def attrs(line):
    return {key:quoted or plain for key,quoted,plain in ATTRIBUTES.findall(line)}


def playlist(url, stage="manifest", parent=None, pluto_id=None):
    url, data = get(url,cap=500_000,stage=stage,parent=parent,pluto_id=pluto_id)
    lines = [line.strip() for line in data.decode("utf-8-sig",errors="replace").splitlines() if line.strip()]
    if not lines or lines[0] != "#EXTM3U":
        raise ProbeError("not-hls")
    return url,lines


def choose_media(url, pluto_id=None):
    url,lines = playlist(url,pluto_id=pluto_id)
    streams = []
    for index,line in enumerate(lines[:-1]):
        if not line.startswith("#EXT-X-STREAM-INF:") or lines[index+1].startswith("#"):
            continue
        metadata = attrs(line.partition(":")[2])
        resolution = re.search(r"x(\d+)$",metadata.get("RESOLUTION",""))
        codecs = [codec.strip().lower() for codec in metadata.get("CODECS", "").split(",") if codec.strip()]
        if not resolution and codecs and all(codec.startswith(("mp4a", "ac-3", "ec-3", "opus", "flac"))
                                             for codec in codecs):
            continue
        height = int(resolution[1]) if resolution else 360
        bandwidth = metadata.get("BANDWIDTH", "")
        cost = int(bandwidth) if bandwidth.isdecimal() else float("inf")
        streams.append((abs(height-360), cost, index, metadata, lines[index+1]))
    if not streams:
        return (url,lines),None
    _, _, _, data, relative = min(streams)
    primary = playlist(urljoin(url,relative),stage="variant-manifest",parent=url)
    tracks = [attrs(line.partition(":")[2]) for line in lines if line.startswith("#EXT-X-MEDIA:")]
    audio = [row for row in tracks if row.get("TYPE")=="AUDIO" and row.get("GROUP-ID")==data.get("AUDIO")]
    if audio:
        selected = next((row for row in audio if row.get("DEFAULT")=="YES"),audio[0])
        if selected.get("URI"):
            return primary,playlist(urljoin(url,selected["URI"]),stage="audio-manifest",parent=url)
    return primary,None


def segment_parent(manifest, locator, reviewed_origins):
    if reviewed_origins is not None and (origin(manifest), origin(locator)) == reviewed_origins:
        target = urlsplit(locator)
        if target.path.endswith(".ts") and target.username is None and target.password is None and not target.fragment:
            # Keep get()'s public-IP checks, HTTPS transport and same-origin redirects.
            return locator
    return manifest


def materialize(source, directory, prefix, *, segments=SEGMENTS, reviewed_origins=None):
    url,lines = source
    rows = []
    latest_map = None
    date = None
    duration = None
    discontinuity = False
    target = next((line for line in lines if line.startswith("#EXT-X-TARGETDURATION:")), "#EXT-X-TARGETDURATION:20")
    for line in lines:
        if line.startswith("#EXT-X-KEY:") and attrs(line.partition(":")[2]).get("METHOD")!="NONE":
            raise ProbeError("encrypted-playlist")
        if line.startswith("#EXT-X-BYTERANGE:"):
            raise ProbeError("byte-range-playlist")
        if line.startswith("#EXT-X-MAP:"):
            attributes = attrs(line.partition(":")[2])
            if attributes.get("BYTERANGE") or not attributes.get("URI"):
                raise ProbeError("unsupported-init-map")
            latest_map = urljoin(url,attributes["URI"])
        if line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            date = line.partition(":")[2]
        if line == "#EXT-X-DISCONTINUITY":
            discontinuity = True
        if line.startswith("#EXTINF:"):
            duration = line
        if not line.startswith("#") and duration:
            rows.append((urljoin(url,line),duration,latest_map,discontinuity,date))
            duration,date,discontinuity = None,None,False
    if not rows:
        raise ProbeError("no-media-segments")
    output = ["#EXTM3U","#EXT-X-VERSION:7",target,"#EXT-X-MEDIA-SEQUENCE:0"]
    previous_map = None
    times = []
    files = 0
    for index,(segment,period,init,discontinuity,date) in enumerate(rows[-segments:]):
        if discontinuity and index:
            output.append("#EXT-X-DISCONTINUITY")
        if init and init != previous_map:
            _,bytes_ = get(init,cap=1_000_000,stage="init",parent=url)
            name=f"{prefix}-init-{index}.mp4"
            (directory/name).write_bytes(bytes_)
            output.append(f'#EXT-X-MAP:URI="{name}"')
            previous_map=init
        _,data=get(segment,parent=segment_parent(url, segment, reviewed_origins))
        name=f"{prefix}-{index}.{'m4s' if init else 'ts'}"
        (directory/name).write_bytes(data)
        output.extend((period,name))
        if date:
            times.append(date)
        files += 1
    output.append("#EXT-X-ENDLIST")
    local = directory/f"{prefix}.m3u8"
    local.write_text("\n".join(output)+"\n")
    return local,files,times


def rolling_rows(lines):
    if any(line.startswith(("#EXT-X-KEY:", "#EXT-X-MAP:", "#EXT-X-BYTERANGE:",
                            "#EXT-X-DISCONTINUITY", "#EXT-X-ENDLIST")) for line in lines):
        raise ProbeError("unsupported-rolling-playlist")
    seq = next((line.partition(":")[2] for line in lines if line.startswith("#EXT-X-MEDIA-SEQUENCE:")), "")
    duration = next((line.partition(":")[2] for line in lines if line.startswith("#EXT-X-TARGETDURATION:")), "")
    if not seq.isdecimal() or not duration.isdecimal() or not 1 <= int(duration) <= 10:
        raise ProbeError("invalid-rolling-playlist")
    rows = []
    for index, line in enumerate(lines[:-1]):
        if not line.startswith("#EXTINF:") or lines[index + 1].startswith("#"):
            continue
        try:
            length = float(line.partition(":")[2].partition(",")[0])
        except ValueError:
            raise ProbeError("invalid-rolling-duration") from None
        if not 0 < length <= int(duration) + 0.5:
            raise ProbeError("invalid-rolling-duration")
        rows.append((int(seq) + len(rows), length, lines[index + 1]))
    if not rows:
        raise ProbeError("no-rolling-segments")
    return int(duration), rows


def materialize_rolling(source, directory, prefix):
    url, lines = source
    target, rows = rolling_rows(lines)
    last = rows[-7][0] - 1 if len(rows) >= 7 else rows[0][0] - 1
    output = ["#EXTM3U", "#EXT-X-VERSION:3", f"#EXT-X-TARGETDURATION:{target}",
              "#EXT-X-MEDIA-SEQUENCE:0"]
    count, seconds, total_bytes = 0, 0.0, 0
    deadline = monotonic() + 39
    while monotonic() < deadline and count < 32 and seconds < 26:
        for seq, length, locator in rows:
            if seq <= last:
                continue
            if seq != last + 1 or monotonic() >= deadline:
                raise ProbeError("rolling-window-gap-or-timeout")
            _, data = get(urljoin(url, locator), cap=2_000_000, parent=url)
            total_bytes += len(data)
            if total_bytes > 16_000_000:
                raise ProbeError("rolling-byte-limit")
            name = f"{prefix}-{seq}.ts"
            (directory / name).write_bytes(data)
            output.extend((f"#EXTINF:{length},", name))
            seconds += length
            count += 1
            last = seq
            if count >= 32 or seconds >= 26:
                break
        if count >= 32 or seconds >= 26:
            break
        sleep(min(2, max(0.75, target / 2)))
        _, lines = playlist(url, parent=url)
        _, rows = rolling_rows(lines)
    if seconds < 10:
        raise ProbeError("rolling-window-too-short")
    output.append("#EXT-X-ENDLIST")
    local = directory / f"{prefix}.m3u8"
    local.write_text("\n".join(output) + "\n")
    return local, count, []



def decode(path, mode):
    options = (["-map","0:v:0","-vf","fps=1,scale=64:36:flags=area,crop=56:28:4:4",
                "-pix_fmt","gray","-threads:v","1","-f","rawvideo"] if mode=="video" else
               ["-map","0:a:0","-ac","1","-ar","8000","-c:a","pcm_s16le","-f","s16le"])
    try:
        run=subprocess.run([FFMPEG,"-nostdin","-loglevel","error","-threads","1","-filter_threads","1",
                            "-protocol_whitelist","file","-allowed_extensions","ALL","-i",str(path),
                            "-t","90",*options,"pipe:1"], stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL,env=SUBPROCESS_ENV,timeout=20)
        return run.stdout if run.returncode==0 else b""
    except subprocess.TimeoutExpired:
        return b""


def rms(chunk):
    samples = array("h", chunk)
    if sys.byteorder != "little":
        samples.byteswap()
    return math.isqrt(sum(value * value for value in samples) // len(samples))


def sample(url, label, pluto_id=None, *, rolling=False, segments=SEGMENTS, reviewed_origins=None):
    info={"route":label}
    if not FFMPEG:
        return {**info, "status": "ffmpeg-unavailable"}, [], []
    started=datetime.now(timezone.utc)
    try:
        with TemporaryDirectory(prefix="zapper-hls-sample-") as tmp:
            path = Path(tmp)
            video_source,audio_source=choose_media(url,pluto_id)
            info["manifestDiscoveryMs"]=round((datetime.now(timezone.utc)-started).total_seconds()*1000)
            if rolling and audio_source:
                raise ProbeError("rolling-separate-audio-unreviewed")
            if rolling:
                p, segment_count, times = materialize_rolling(video_source, path, "video")
            else:
                p, segment_count, times = materialize(
                    video_source, path, "video", segments=segments, reviewed_origins=reviewed_origins)
            audio_path=p
            if audio_source:
                audio_path,_,_=materialize(audio_source,path,"audio",segments=segments)
            v,a=decode(p,"video"),decode(audio_path,"audio")
            frames=[v[i:i+1568] for i in range(0,len(v)-1567,1568)]
            levels=[rms(a[i:i+1600]) for i in range(0,len(a)-1599,1600)]
            info.update(videoFrames=len(frames),decodedAudioSeconds=round(len(a)/16000,3),
                        mediaSegments=segment_count,programmeDateTime=times,status="decoded-av" if frames and levels else "incomplete-media",
                        sampleWallMs=round((datetime.now(timezone.utc)-started).total_seconds()*1000))
            return info,frames,levels
    except ProbeError as exc:
        return {**info,"status":str(exc)},[],[]


def difference(a,b):
    return sum(abs(x-y) for x,y in zip(a,b))/len(a)


def correlation(left,right):
    if len(left)<75:
        return None
    x,y=sum(left)/len(left),sum(right)/len(right)
    dx,dy=[a-x for a in left],[a-y for a in right]
    denominator=math.sqrt(sum(a*a for a in dx)*sum(b*b for b in dy))
    return sum(a*b for a,b in zip(dx,dy))/denominator if denominator>0 else None


def align(left,right,left_audio,right_audio):
    candidates=[]
    for lag in range(-len(left)+8,len(right)-7):
        pairs=[(x,x+lag) for x in range(len(left)) if 0<=x+lag<len(right)]
        distances=[difference(left[x],right[y]) for x,y in pairs]
        run=0
        longest=0
        start=None
        for index,value in enumerate(distances):
            if value<=13:
                run+=1
                if run>longest:
                    longest=run
                    start=index+1-run
            else:
                run=0
        if start is not None:
            sequence=pairs[start:start+longest]
            diversity=sum(difference(left[x],left[x2])>=2 for (x,_),(x2,_) in zip(sequence,sequence[1:]))
            candidates.append((longest,diversity,lag,round(sum(distances[start:start+longest])/longest,2),sequence))
    if not candidates:
        return {"videoMatchSeconds":0}
    length,diversity,lag,avg,sequence=max(candidates,key=lambda x:(x[0],x[1],-x[3]))
    best_audio=None
    for tweak in range(-13,14):
        audio_lag=lag*10+tweak
        start=sequence[0][0]*10
        last=(sequence[-1][0]+1)*10
        positions=[x for x in range(start,last) if x<len(left_audio) and 0<=x+audio_lag<len(right_audio)]
        r=correlation([left_audio[x] for x in positions],[right_audio[x+audio_lag] for x in positions])
        if r is not None and (best_audio is None or r>best_audio):
            best_audio=r
    return dict(videoMatchSeconds=length,differentAdjacentFrames=diversity,offsetSeconds=lag,
                averageVideoPixelDifference=avg,bestAudioLevelCorrelation=round(best_audio,3) if best_audio is not None else None)



def match_samples(left, right):
    current, lv, la = left
    alternative, rv, ra = right
    if current["status"] != "decoded-av" or alternative["status"] != "decoded-av":
        return False
    score = align(lv, rv, la, ra)
    return (score.get("videoMatchSeconds", 0) >= 10
            and score.get("differentAdjacentFrames", 0) >= 4
            and (score.get("bestAudioLevelCorrelation") or 0) >= 0.78)


def youtube_sample(master, segments=8):
    if not googlevideo(master) or not urlsplit(master).path.startswith("/api/manifest/hls_variant/"):
        return {"status": "unexpected-youtube-master"}, [], []
    youtube_transport.active = True
    try:
        return sample(master, "youtube", segments=segments)
    finally:
        youtube_transport.active = False


def matches_youtube(master, candidate, *, youtube_segments=8, hls_segments=8):
    with ThreadPoolExecutor(max_workers=2) as pool:
        current = pool.submit(youtube_sample, master, youtube_segments)
        alternate = pool.submit(sample, candidate, "alternate", segments=hls_segments)
        return match_samples(current.result(), alternate.result())


def matches(primary, candidate):
    identity = tuple(hashlib.sha256(url.encode()).hexdigest() for url in (primary, candidate))
    options = {"rolling": True} if identity in REVIEWED_ROLLING_PAIRS else {}
    reviewed_origins = REVIEWED_SEGMENT_ORIGINS.get(identity)
    if reviewed_origins is not None:
        options = {"segments": 10}
    with ThreadPoolExecutor(max_workers=2) as pool:
        primary_options = {**options, "reviewed_origins": reviewed_origins} if reviewed_origins else options
        left = pool.submit(sample, primary, "current", **primary_options)
        right = pool.submit(sample, candidate, "candidate", **options)
        return match_samples(left.result(), right.result())


def both_decode_av(primary, candidate):
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(sample, primary, "current")
        second = pool.submit(sample, candidate, "candidate")
        return all(future.result()[0]["status"] == "decoded-av" for future in (first, second))


REVIEWED_MASTERS = {
    ("OromarTV.ec@SD", "oromar-master-variant"): (
        'https://stream.oromar.tv/hls/oromartv_hi/index.m3u8', "https://stream.oromar.tv/hls/oromartv.m3u8"),

}


def master_primary(feed, authority, alternate):
    original, master = REVIEWED_MASTERS.get((feed, authority), (None, None))
    return original if alternate == master else None


def matches_declared_master(primary, candidate):
    if (primary, candidate) not in REVIEWED_MASTERS.values() or origin(primary) != origin(candidate):
        return False
    locator, lines = playlist(candidate)
    if origin(locator) != origin(candidate):
        return False
    declared = any(line.startswith("#EXT-X-STREAM-INF:") and urljoin(locator, lines[i+1]) == primary
                   for i, line in enumerate(lines[:-1]))
    return declared and both_decode_av(primary, candidate)


# Evidence is bound to both reviewed locators and one exact edition. Changing
# either endpoint requires a new identity review.
REVIEWED_RECOVERIES = {
    ("AdultSwimLatinAmerica.us@Brazil", "adult-swim-brazil-editorial"): (
        "204c450de10756585f83054347acf9657bd9f2428cf2935c1eea1b41b5128cc1",
        "8aa59f2b856bf2c68d7b55cbe134a69e8c197ca6b63ac24d2fca92c152cee9d1",
        frozenset({"por", "pt", "pt-br"}),
    ),
}


def recovery_languages(feed, authority, original, alternate):
    reviewed = REVIEWED_RECOVERIES.get((feed, authority))
    if reviewed is None:
        return frozenset()
    actual = tuple(hashlib.sha256(locator.encode()).hexdigest() for locator in (original, alternate))
    return reviewed[2] if actual == reviewed[:2] else frozenset()


def primary_audio_language(path):
    output = subprocess.run(
        [FFPROBE, "-v", "error", "-protocol_whitelist", "file,crypto,data",
         "-allowed_extensions", "ALL", "-select_streams", "a:0",
         "-show_entries", "stream_tags=language", "-of", "json", str(path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        timeout=12, env=SUBPROCESS_ENV, check=True,
    )
    stream, = json.loads(output.stdout)["streams"]
    return stream.get("tags", {}).get("language", "").lower()


def matches_reviewed_recovery(alternate, languages):
    if not (FFMPEG and FFPROBE and languages):
        return False
    try:
        with TemporaryDirectory(prefix="zapper-recovery-sample-") as temporary:
            directory = Path(temporary)
            video, audio = choose_media(alternate)
            picture, _, _ = materialize(video, directory, "video", segments=2)
            sound = picture
            if audio is not None:
                sound, _, _ = materialize(audio, directory, "audio", segments=2)
            return (primary_audio_language(sound) in languages
                    and len(decode(picture, "video")) >= 2 * 56 * 28
                    and len(decode(sound, "audio")) >= 16_000)
    except (OSError, ProbeError, subprocess.SubprocessError, ValueError, KeyError):
        return False
