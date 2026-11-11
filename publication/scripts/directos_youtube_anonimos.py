"""Read public YouTube live metadata without downloading media or using a key."""

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import subprocess
from urllib.parse import urlsplit

from directos_youtube import MAX_SEARCHES, PUBLISHER, SEARCH_INTERVAL, VIDEO, matches_feed

MAX_TAB_ENTRIES = 24


def client(executable, runtime="node", environment=None, *, force_ipv4=False):
    def fetch(kind, identifier):
        if kind == "video" and VIDEO.fullmatch(identifier):
            locator = "https://www.youtube.com/watch?v=" + identifier
            options = ["--no-playlist"]
        elif kind in ("live", "streams") and PUBLISHER.fullmatch(identifier):
            locator = f"https://www.youtube.com/channel/{identifier}/{kind}"
            options = (["--flat-playlist", "--playlist-end", str(MAX_TAB_ENTRIES)]
                       if kind == "streams" else ["--no-playlist"])
        else:
            return None
        try:
            result = subprocess.run([
                executable, "--ignore-config", "--no-plugin-dirs", "--no-cache-dir", "--skip-download",
                "--no-cookies", "--no-cookies-from-browser", "--socket-timeout", "4", "--retries", "0",
                "--extractor-retries", "0", "--no-js-runtimes", "--js-runtimes", runtime,
                *(["--force-ipv4"] if force_ipv4 else []), "--dump-single-json", *options, locator,
            ], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=18, env=environment)
            if result.returncode or len(result.stdout) > 1_048_576:
                return None
            data = json.loads(result.stdout)
            return data if isinstance(data, dict) else None
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return None

    return fetch


def identity(info, publisher, video=None):
    if not isinstance(info, dict) or info.get("channel_id") != publisher:
        return False
    locator = info.get("id")
    return isinstance(locator, str) and bool(VIDEO.fullmatch(locator)) and (video is None or locator == video)


def native_master(info):
    masters = defaultdict(list)
    formats = info.get("formats")
    if not isinstance(formats, list):
        return None
    for variant in formats:
        if not isinstance(variant, dict):
            return False
        url = variant.get("manifest_url")
        protocol = variant.get("protocol")
        if not isinstance(url, str) or not isinstance(protocol, str) or not protocol.startswith("m3u8"):
            continue
        try:
            parsed = urlsplit(url)
            host = parsed.hostname or ""
            if (parsed.scheme == "https" and not parsed.username and not parsed.password and parsed.port is None
                and (host == "googlevideo.com" or host.endswith(".googlevideo.com"))
                and parsed.path.startswith("/api/manifest/hls_variant/") and not parsed.fragment):
                masters[url].append(variant)
        except ValueError:
            return None
    def audio(variant):
        codec = variant.get("acodec")
        if codec is not None:
            return codec != "none"
        return variant.get("vcodec") == "none" and variant.get("resolution") == "audio only"
    eligible = [url for url, variants in masters.items()
                if any(v.get("vcodec") not in (None, "none") for v in variants) and any(audio(v) for v in variants)]
    return eligible[0] if len(eligible) == 1 else None


def has_native_master(info):
    return native_master(info) is not None


def replacement(fetch, row):
    publisher = row["publisherID"]
    selected = fetch("live", publisher)
    if not identity(selected, publisher) or selected.get("live_status") != "is_live":
        return None, "channel-live-unconfirmed"
    if not matches_feed(row, selected.get("title")) or not has_native_master(selected):
        return None, "feed-unconfirmed"
    tab = fetch("streams", publisher)
    if not isinstance(tab, dict) or tab.get("channel_id") != publisher or not isinstance(tab.get("entries"), list):
        return None, "ambiguity-unchecked"
    entries = tab["entries"]
    if any(not isinstance(entry, dict) or entry.get("live_status") not in ("is_live", "was_live", "not_live", "is_upcoming", "post_live")
           for entry in entries):
        return None, "ambiguity-unchecked"
    # Do not adopt a match from a bounded page full of live/upcoming streams.
    if len(entries) >= MAX_TAB_ENTRIES and entries[-1].get("live_status") not in ("was_live", "not_live", "post_live"):
        return None, "ambiguity-unchecked"
    active = [entry for entry in entries if entry["live_status"] == "is_live"]
    for entry in active:
        video, title = entry.get("id"), entry.get("title")
        if not isinstance(video, str) or not VIDEO.fullmatch(video) or not isinstance(title, str) or not title.strip():
            return None, "ambiguity-unchecked"
        if entry.get("channel_id") not in (None, publisher):
            return None, "ambiguity-unchecked"
    matches = [entry for entry in active if matches_feed(row, entry["title"])]
    if len(matches) != 1 or matches[0].get("id") != selected["id"]:
        return None, "ambiguous-feed"
    return (selected["id"], matches[0]["title"]), "unique-live-feed"


def refresh(entries, fetch, now):
    if any(not PUBLISHER.fullmatch(row["publisherID"]) or not VIDEO.fullmatch(row["videoID"]) for row in entries):
        raise ValueError("Invalid identifiers in the adopted YouTube catalog")
    used = {row["videoID"] for row in entries}
    if len(used) != len(entries) or len({row["publisherID"] for row in entries}) != len(entries):
        raise ValueError("Duplicate identifiers in the adopted YouTube catalog")
    counts = Counter()
    output = {row["publisherID"]: row for row in entries}
    confirmed_ended = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = pool.map(lambda row: fetch("video", row["videoID"]), entries)
        for row, info in zip(entries, results):
            publisher = row["publisherID"]
            if not identity(info, publisher, row["videoID"]):
                counts["checkUnconfirmed"] += 1
            elif info.get("live_status") == "is_live" and has_native_master(info):
                counts["stillLive"] += 1
                output[publisher] = {key: value for key, value in row.items() if key != "available"}
            elif info.get("live_status") in ("was_live", "not_live", "post_live"):
                confirmed_ended.append(row)
            else:
                counts["checkUnconfirmed"] += 1

    stamp = now.isoformat(timespec="milliseconds")
    candidates = sorted(confirmed_ended, key=lambda r: r["number"])
    if candidates:
        start = (int(now.timestamp() // SEARCH_INTERVAL.total_seconds()) * MAX_SEARCHES) % len(candidates)
        candidates = candidates[start:] + candidates[:start]
    for row in candidates:
        checked = row.get("lastSearchAt")
        if checked and now - datetime.fromisoformat(checked.replace("Z", "+00:00")) < SEARCH_INTERVAL:
            counts["cooldown"] += 1
            continue
        if counts["searches"] >= MAX_SEARCHES:
            counts["searchLimit"] += 1
            continue
        counts["searches"] += 1
        found, reason = replacement(fetch, row)
        if found is None or found[0] in used:
            counts[reason if found is None else "ambiguous-feed"] += 1
            continue
        video, title = found
        used.add(video)
        output[row["publisherID"]] = {**{key: value for key, value in row.items() if key != "available"},
                                      "videoID": video, "liveTitle": title, "verifiedAt": stamp, "lastSearchAt": stamp}
        counts["rotated"] += 1
    return [output[row["publisherID"]] for row in entries], {"status": "ok", "method": "yt-dlp", **counts}
