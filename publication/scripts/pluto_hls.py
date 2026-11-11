"""Verify ordinary public Pluto HLS against its exact original channel."""
from concurrent.futures import ThreadPoolExecutor
from ctypes import CDLL, POINTER, byref, c_int, c_size_t, c_uint32, c_void_p, create_string_buffer
from ctypes.util import find_library
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from urllib.parse import urljoin, urlsplit

import public_hls as hls

ADAPTER = "pluto.public-redirect"
WORKER_HOST = "zapper-pluto-flex.chevosky.workers.dev"


@lru_cache(maxsize=1)
def common_crypto():
    operation = CDLL("/usr/lib/libSystem.B.dylib").CCCrypt
    operation.restype = c_int
    operation.argtypes = [c_uint32, c_uint32, c_uint32, c_void_p, c_size_t, c_void_p,
                          c_void_p, c_size_t, c_void_p, c_size_t, POINTER(c_size_t)]
    return operation


@lru_cache(maxsize=1)
def openssl():
    library = find_library("crypto")
    if not library:
        raise hls.ProbeError("libcrypto-unavailable")
    crypto = CDLL(library)
    signatures = {
        "EVP_CIPHER_CTX_new": (c_void_p, []),
        "EVP_CIPHER_CTX_free": (None, [c_void_p]),
        "EVP_aes_128_cbc": (c_void_p, []),
        "EVP_DecryptInit_ex2": (c_int, [c_void_p, c_void_p, c_void_p, c_void_p, c_void_p]),
        "EVP_DecryptUpdate": (c_int, [c_void_p, c_void_p, POINTER(c_int), c_void_p, c_int]),
        "EVP_DecryptFinal_ex": (c_int, [c_void_p, c_void_p, POINTER(c_int)]),
    }
    for name, (result, arguments) in signatures.items():
        function = getattr(crypto, name)
        function.restype = result
        function.argtypes = arguments
    return crypto


def decrypt(content, key, iv):
    if sys.platform == "darwin":
        out = create_string_buffer(len(content))
        moved = c_size_t(0)
        if common_crypto()(1, 0, 1, key, len(key), iv, content, len(content), out, len(out), byref(moved)) == 0:
            return out.raw[:moved.value]
        raise hls.ProbeError("aes-decode-failed")

    crypto = openssl()
    ctx = crypto.EVP_CIPHER_CTX_new()
    if not ctx:
        raise hls.ProbeError("aes-context-unavailable")
    try:
        out = create_string_buffer(len(content) + 16)
        moved, final = c_int(0), c_int(0)
        if (crypto.EVP_DecryptInit_ex2(ctx, crypto.EVP_aes_128_cbc(), key, iv, None) == 1
                and crypto.EVP_DecryptUpdate(ctx, out, byref(moved), content, len(content)) == 1
                and crypto.EVP_DecryptFinal_ex(ctx, byref(out, moved.value), byref(final)) == 1):
            return out.raw[:moved.value + final.value]
        raise hls.ProbeError("aes-decode-failed")
    finally:
        crypto.EVP_CIPHER_CTX_free(ctx)


def exact_id(locator, *, fallback):
    parts = urlsplit(locator)
    host = "jmp2.uk" if fallback else WORKER_HOST
    path = r"/plu-([0-9a-f]{24})\.m3u8" if fallback else r"/pluto/([0-9a-f]{24})\.m3u8"
    found = re.fullmatch(path, parts.path)
    if (parts.scheme == "https" and parts.hostname == host and parts.port in (None, 443)
            and not (parts.username or parts.password or parts.query or parts.fragment) and found):
        return found[1]
    return None


def known_public_pluto(url):
    parts = urlsplit(url)
    hostname = parts.hostname or ""
    return (parts.scheme == "https" and (hostname == "pluto.tv" or hostname.endswith(".pluto.tv")
            or hostname == "siloh-ns1.plutotv.net") and parts.port in (443, None)
            and parts.username is None and parts.password is None and not parts.fragment)


def get_child(uri, parent, *, key=False):
    child = urljoin(parent, uri)
    if not known_public_pluto(parent) or not known_public_pluto(child):
        raise hls.ProbeError("offsite-pluto-child")
    cross = hls.origin(child) != hls.origin(parent)
    if key and urlsplit(child).hostname != "siloh-ns1.plutotv.net":
        raise hls.ProbeError("unapproved-key-cdn")
    if cross and urlsplit(child).query:
        raise hls.ProbeError("cross-origin-query-refused")
    _, body = hls.get(child, cap=16 if key else 7_000_000, stage="key" if key else "segment",
                      parent=child if cross else parent)
    if key and len(body) != 16:
        raise hls.ProbeError("unexpected-public-key-size")
    return body


def collect(source, directory, label):
    url, lines = source
    sequence = int(next((line.partition(":")[2] for line in lines
                         if line.startswith("#EXT-X-MEDIA-SEQUENCE:")), "0"))
    target = next((line for line in lines if line.startswith("#EXT-X-TARGETDURATION:")), "#EXT-X-TARGETDURATION:20")
    duration, key, rows, discontinuity = None, None, [], False
    for line in lines:
        if line.startswith(("#EXT-X-BYTERANGE:", "#EXT-X-MAP:")):
            raise hls.ProbeError("unsupported-pluto-playlist-layout")
        if line.startswith("#EXT-X-KEY:"):
            entry = hls.attrs(line.partition(":")[2])
            if entry.get("METHOD") == "NONE":
                key = None
            elif (entry.get("METHOD") == "AES-128" and entry.get("KEYFORMAT", "identity") == "identity"
                    and entry.get("URI") and (not entry.get("IV") or re.fullmatch(r"0x[\da-fA-F]{32}", entry["IV"]))):
                key = entry
            else:
                raise hls.ProbeError("unsupported-encryption")
        if line == "#EXT-X-DISCONTINUITY":
            discontinuity = True
        if line.startswith("#EXTINF:"):
            duration = line
        if not line.startswith("#") and duration:
            rows.append((line, duration, key, sequence + len(rows), discontinuity))
            duration, discontinuity = None, False
    if not rows:
        raise hls.ProbeError("no-media-segments")
    output = ["#EXTM3U", "#EXT-X-VERSION:3", target]
    keys = {}
    encrypted = 0
    for index, (uri, duration, encryption, number, gap) in enumerate(rows[-5:]):
        body = get_child(uri, url)
        if encryption:
            uri = encryption["URI"]
            if uri not in keys:
                keys[uri] = get_child(uri, url, key=True)
            iv = bytes.fromhex(encryption["IV"][2:]) if encryption.get("IV") else number.to_bytes(16, "big")
            body = decrypt(body, keys[uri], iv)
            encrypted += 1
        if gap and index:
            output.append("#EXT-X-DISCONTINUITY")
        path = directory / f"{label}-{index}.ts"
        path.write_bytes(body)
        output.extend((duration, path.name))
    local = directory / f"{label}.m3u8"
    local.write_text("\n".join([*output, "#EXT-X-ENDLIST"]) + "\n")
    return local, encrypted


def sample(locator, pluto_id):
    started = datetime.now(timezone.utc)
    try:
        video, audio = hls.choose_media(locator, pluto_id)
        manifest_ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        with TemporaryDirectory(prefix="zapper-pluto-av-") as tmp:
            path = Path(tmp)
            local, encrypted = collect(video, path, "video")
            audio_path = local if audio is None else collect(audio, path, "audio")[0]
            video_raw, audio_raw = hls.decode(local, "video"), hls.decode(audio_path, "audio")
            frames = [video_raw[i:i+1568] for i in range(0, len(video_raw)-1567, 1568)]
            levels = [hls.rms(audio_raw[i:i+1600]) for i in range(0, len(audio_raw)-1599, 1600)]
        return ({"status": "decoded-av" if frames and levels else "incomplete-media",
                 "manifestDiscoveryMs": manifest_ms,
                 "sampleWallMs": round((datetime.now(timezone.utc) - started).total_seconds() * 1000),
                 "encryptedSegmentsDecoded": encrypted, "videoFrames": len(frames),
                 "audioSeconds": round(len(audio_raw)/16000, 3)}, frames, levels)
    except (hls.ProbeError, OSError, ValueError, AttributeError) as error:
        return {"status": str(error)}, [], []



def matches(primary, candidate):
    id = exact_id(primary, fallback=False)
    if id is None or id != exact_id(candidate, fallback=True):
        return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(sample, primary, id)
        second = pool.submit(sample, candidate, id)
        current, cv, ca = first.result()
        fallback, fv, fa = second.result()
    if current["status"] != "decoded-av" or fallback["status"] != "decoded-av":
        return False
    score = hls.align(cv, fv, ca, fa)
    return (score.get("videoMatchSeconds", 0) >= 10
            and score.get("differentAdjacentFrames", 0) >= 4
            and (score.get("bestAudioLevelCorrelation") or 0) >= 0.78)
