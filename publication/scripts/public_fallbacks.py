"""Renew only an editorially approved public route, using its exact original signal."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from time import sleep
from urllib.parse import urlsplit

import directos_youtube_anonimos
from directos_youtube import PUBLISHER, VIDEO
import public_hls
import pluto_hls
import qatar_hls
import infomaniak_hls

PREFIX = "audit-public-"
LIFETIME = timedelta(hours=8)


def source_id(url):
    return PREFIX + hashlib.sha256(url.encode()).hexdigest()[:20]


def stamp(now):
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def public_url(url):
    parsed = urlsplit(url)
    return (parsed.scheme in ("http", "https") and parsed.hostname and parsed.username is None
            and parsed.password is None and "?" not in url and "#" not in url)


def approved(route):
    return route["id"].startswith(PREFIX)


def carry(seed, prior, *, timestamp, expiry):
    """Preserve earlier attestations and validate that only their timestamps can change."""
    seed_by_id = {route["id"]: route for route in seed}
    if len(seed_by_id) != len(seed) or len({route["id"] for route in prior}) != len(prior):
        raise ValueError("Repeated source binding")
    prior_ids = [route["id"] for route in prior]
    prior_id_set = set(prior_ids)
    if [route["id"] for route in seed if route["id"] in prior_id_set] != prior_ids:
        raise ValueError("Previous sources removed or reordered")
    carried = deepcopy(seed)
    by_id = {route["id"]: route for route in carried}
    for route in seed:
        if not approved(route):
            continue
        proof = expiry(route)
        start = parse_stamp(proof[timestamp])
        end = parse_stamp(proof["validUntil"])
        if (not public_url(route["url"]) or route.get("headers") or route["id"] != source_id(route["url"])
                or ("adapter" in route and (route["adapter"] != pluto_hls.ADAPTER
                    or pluto_hls.exact_id(route["url"], fallback=True) is None))
                or not start < end <= start + LIFETIME):
            raise ValueError("Invalid public fallback review")
    for old in prior:
        original = seed_by_id[old["id"]]
        comparable = deepcopy(old)
        if approved(original):
            old_proof = expiry(old)
            old_start = parse_stamp(old_proof[timestamp])
            old_end = parse_stamp(old_proof["validUntil"])
            if not old_start < old_end <= old_start + LIFETIME:
                raise ValueError("Invalid published fallback review")
            original_proof = expiry(original)
            if old_end > parse_stamp(original_proof["validUntil"]):
                expiry(by_id[old["id"]]).update({timestamp: old_proof[timestamp], "validUntil": old_proof["validUntil"]})
            expiry(comparable).update({timestamp: original_proof[timestamp], "validUntil": original_proof["validUntil"]})
        if comparable != original:
            raise ValueError("Previously published source differs from reviewed inventory")
    return carried


def refresh(seed, prior, now, primary_by_feed, *, probe=None, pluto_probe=None, qatar_probe=None, master_probe=None, recovery_probe=None,
            infomaniak_probe=None):
    """Carry immutable inventory forward. An inconclusive refresh preserves its prior deadline."""
    old_expansion = {row["tvgID"]: row for row in prior["expansion"]}
    expansion = []
    tasks = []
    passthrough = lambda route: route
    evidence = lambda route: route["verification"]
    for row in seed["expansion"]:
        feed = row["tvgID"]
        old = old_expansion.pop(feed, row)
        if ({key: value for key, value in row.items() if key != "sources"}
                != {key: value for key, value in old.items() if key != "sources"}):
            raise ValueError("Previously published channel differs from reviewed inventory")
        sources = carry(row["sources"], old["sources"], timestamp="checkedAt", expiry=evidence)
        if any("adapter" in route for route in sources):
            raise ValueError("Pluto alternatives require a canonical core binding")
        for route in sources:
            authority = route.get("verification", {}).get("identityAuthority")
            if authority is not None and (not approved(route) or not (
                    qatar_hls.reviewed(feed, authority, route["url"])
                    or public_hls.master_primary(feed, authority, route["url"])
                    or infomaniak_hls.reviewed(feed, authority, route["url"]))):
                raise ValueError("Unrecognized broadcaster identity authority")
        expansion.append({**row, "sources": sources})
        tasks.extend((feed, route, "checkedAt", evidence(route)) for route in sources if approved(route))
    if old_expansion:
        raise ValueError("Previously published expansion rows are missing")
    alternatives = carry(seed["alternatives"], prior["alternatives"], timestamp="observedAt", expiry=passthrough)
    for route in alternatives:
        authority = route.get("identityAuthority")
        if authority is not None and (not approved(route) or route.get("adapter") is not None
                                      or not public_hls.recovery_languages(
                                          route["feed"], authority, primary_by_feed.get(route["feed"], ""), route["url"])):
            raise ValueError("Unrecognized canonical recovery identity")
    tasks.extend((route["feed"], route, "observedAt", route) for route in alternatives if approved(route))
    report = {"inventory": len(tasks), "attempted": 0, "renewed": 0}
    if all(check is None for check in (probe, pluto_probe, qatar_probe, master_probe, recovery_probe,
                                       infomaniak_probe)):
        return expansion, alternatives, report

    def verify(task):
        feed, route, _, proof = task
        primary = primary_by_feed.get(feed, "")
        if not public_url(primary) or primary == route["url"]:
            return False
        recovery = route.get("identityAuthority")
        if recovery is not None:
            languages = public_hls.recovery_languages(feed, recovery, primary, route["url"])
            if recovery_probe is None or not languages:
                return False
            try:
                return recovery_probe(route["url"], languages)
            except (OSError, ValueError, public_hls.ProbeError):
                return False
        resolver = probe
        if route.get("adapter") == pluto_hls.ADAPTER:
            if pluto_hls.exact_id(primary, fallback=False) != pluto_hls.exact_id(route["url"], fallback=True):
                return False
            resolver = pluto_probe
        authority = proof.get("identityAuthority")
        if authority is not None:
            rule = qatar_hls.reviewed(feed, authority, route["url"])
            approved_master = public_hls.master_primary(feed, authority, route["url"])
            if rule is not None and primary == rule.primary:
                resolver = qatar_probe
            elif approved_master == primary:
                resolver = master_probe
            elif infomaniak_hls.reviewed(feed, authority, route["url"]) and primary == infomaniak_hls.PRIMARY:
                resolver = infomaniak_probe
            else:
                return False
        if resolver is None:
            return False
        try:
            return resolver(primary, route["url"])
        except (OSError, ValueError, public_hls.ProbeError):
            return False

    # At most six route fetches run concurrently, including both sides of each comparison.
    with ThreadPoolExecutor(max_workers=3) as pool:
        for (_, _, timestamp, proof), match in zip(tasks, pool.map(verify, tasks)):
            report["attempted"] += 1
            if match:
                proof.update({timestamp: stamp(now), "validUntil": stamp(now + LIFETIME)})
                report["renewed"] += 1
    return expansion, alternatives, report


def project_reviewed_recoveries(playlist_blocks, alternatives, primary_by_feed, now):
    """Select only a currently attested, explicitly reviewed recovery for Flex."""
    projected = dict(playlist_blocks)
    for route in alternatives:
        feed = route["feed"]
        authority = route.get("identityAuthority")
        if not (authority and route.get("preferInPlaylist")
                and parse_stamp(route["observedAt"]) <= now < parse_stamp(route["validUntil"])):
            continue
        primary = primary_by_feed.get(feed, "")
        if not public_hls.recovery_languages(feed, authority, primary, route["url"]):
            continue
        original = projected[feed]
        lines = original.splitlines(keepends=True)
        matches = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == primary]
        if len(matches) != 1:
            raise ValueError("Reviewed recovery did not match exactly one original playback locator")
        index, = matches
        lines[index] = route["url"] + lines[index][len(primary):]
        projected[feed] = "".join(lines)
    return projected


def youtube_source_id(publisher, locator):
    return source_id(publisher + "\n" + locator)


def valid_youtube_binding(record, publishers, now):
    fields = {"id", "publisherID", "videoID", "url", "observedAt", "validUntil"}
    if set(record) != fields or not all(isinstance(value, str) for value in record.values()):
        return False
    try:
        parsed = urlsplit(record["url"])
        observed, expires = (parse_stamp(record[k]) for k in ("observedAt", "validUntil"))
        publisher = record["publisherID"]
        host = (parsed.hostname or "").lower().rstrip(".")
        youtube_hosts = ("youtube.com", "youtube-nocookie.com", "youtu.be", "googlevideo.com")
        return (publisher in publishers and PUBLISHER.fullmatch(publisher) and VIDEO.fullmatch(record["videoID"])
                and public_url(record["url"]) and parsed.scheme == "https" and parsed.port is None
                and parsed.path.lower().endswith(".m3u8")
                and not any(host == suffix or host.endswith("." + suffix) for suffix in youtube_hosts)
                and (record["observedAt"], record["validUntil"]) == (stamp(observed), stamp(expires))
                and record["id"] == youtube_source_id(publisher, record["url"])
                and observed <= now and observed < expires <= observed + LIFETIME)
    except (TypeError, ValueError):
        return False


def refresh_youtube(seed, prior, youtube, now, *, fetch=None, probe=None):
    """Retain reviewed identity and renew only against the currently selected video."""
    publishers = {row["publisherID"]: row for row in youtube}
    original = seed.get("youtubeAlternatives", [])
    published = prior.get("youtubeAlternatives", [])
    for records in (original, published):
        if (not all(valid_youtube_binding(r, publishers, now) for r in records)
                or len({r["id"] for r in records}) != len(records)
                or len({r["url"] for r in records}) != len(records)):
            raise ValueError("Invalid reviewed YouTube fallback inventory")
    reviewed = {record["id"]: record for record in original}
    carried = dict(reviewed)
    for previous in published:
        identity = reviewed.get(previous["id"])
        if identity is None or any(previous[k] != identity[k] for k in ("url", "publisherID")):
            raise ValueError("Published YouTube fallback differs from review")
        if parse_stamp(previous["observedAt"]) > parse_stamp(identity["observedAt"]):
            carried[previous["id"]] = previous
    report = {"inventory": len(carried), "attempted": 0, "renewed": 0,
              "awaitingCurrentVideo": sum(r["videoID"] != publishers[r["publisherID"]]["videoID"] for r in carried.values())}
    if fetch is None or probe is None:
        return list(carried.values()), report

    def verify(record):
        row = publishers[record["publisherID"]]
        publisher, video = row["publisherID"], row["videoID"]

        def sample_current():
            metadata = fetch("video", video)
            if (not directos_youtube_anonimos.identity(metadata, publisher, video)
                    or metadata.get("live_status") != "is_live"):
                return False
            master = directos_youtube_anonimos.native_master(metadata)
            return bool(master and probe(master, record["url"]))

        try:
            if not sample_current():
                return None
            if record["videoID"] != video:
                # A new video can be a different edition from the same account.
                # The decoder compares at most 90 s. Separate the windows.
                sleep(90)
                if not sample_current():
                    return None
            return video
        except (OSError, ValueError, public_hls.ProbeError):
            return None

    observation = parse_stamp(stamp(now))
    with ThreadPoolExecutor(max_workers=2) as pool:
        for (route_id, record), verified_video in zip(carried.items(), pool.map(verify, carried.values())):
            report["attempted"] += 1
            if verified_video and observation > parse_stamp(record["observedAt"]):
                carried[route_id] = {**record, "videoID": verified_video,
                                    "observedAt": stamp(now), "validUntil": stamp(now + LIFETIME)}
                report["renewed"] += 1
    report["awaitingCurrentVideo"] = sum(r["videoID"] != publishers[r["publisherID"]]["videoID"]
                                        for r in carried.values())
    return list(carried.values()), report


def refresh_native_youtube(seed, prior, expansion, now, *, fetch=None, probe=None):
    """Renew a broadcaster-reviewed YouTube fallback for an existing HLS row."""
    reviewed = seed.get("nativeYouTubeFallbacks", [])
    published = prior.get("nativeYouTubeFallbacks", [])
    field_names = {"feed", "primarySourceID", "primarySHA256", "publisherID", "videoID", "observedAt", "validUntil"}
    immutable = ("feed", "primarySourceID", "primarySHA256", "publisherID")
    native_rows = {row["tvgID"]: row for row in expansion}
    rows = {}

    def primary(record):
        if set(record) != field_names:
            raise ValueError("Invalid native YouTube fallback fields")
        feed = record["feed"]
        candidates = native_rows.get(feed, {}).get("sources", [])
        original = next((src for src in candidates if src["id"] == record["primarySourceID"]), None)
        if (not original or original.get("headers") or approved(original) or not public_url(original["url"])
                or hashlib.sha256(original["url"].encode()).hexdigest() != record["primarySHA256"]
                or not PUBLISHER.fullmatch(record["publisherID"]) or not VIDEO.fullmatch(record["videoID"])):
            raise ValueError("Invalid native YouTube fallback binding")
        observed, expires = parse_stamp(record["observedAt"]), parse_stamp(record["validUntil"])
        if (record["observedAt"], record["validUntil"]) != (stamp(observed), stamp(expires)) or not (
                observed <= now and observed < expires <= observed + LIFETIME):
            raise ValueError("Invalid native YouTube fallback attestation")
        return original["url"]

    for record in reviewed:
        locator = primary(record)
        if record["feed"] in rows or any(item[0]["publisherID"] == record["publisherID"] for item in rows.values()):
            raise ValueError("Duplicate native YouTube fallback")
        rows[record["feed"]] = (record, locator)
    seen = set()
    for record in published:
        primary(record)
        feed = record["feed"]
        if feed in seen or feed not in rows:
            raise ValueError("Unexpected published native YouTube fallback")
        seen.add(feed)
        old, locator = rows[feed]
        if any(old[key] != record[key] for key in immutable):
            raise ValueError("Native YouTube fallback differs from review")
        if parse_stamp(record["observedAt"]) > parse_stamp(old["observedAt"]):
            rows[feed] = (record, locator)

    report = {"inventory": len(rows), "attempted": 0, "renewed": 0}
    if fetch is None or probe is None:
        return [record for record, _ in rows.values()], report

    def check(record, locator):
        publisher = record["publisherID"]
        info = fetch("live", publisher)
        if not directos_youtube_anonimos.identity(info, publisher) or info.get("live_status") != "is_live":
            return None
        master = directos_youtube_anonimos.native_master(info)
        return info["id"] if master and probe(master, locator) else None

    refreshed = []
    for record, locator in rows.values():
        report["attempted"] += 1
        try:
            video = check(record, locator)
            if video and video != record["videoID"]:
                # Separate the proof windows by more than the longest sample.
                sleep(90)
                if check(record, locator) != video:
                    video = None
        except (OSError, ValueError, public_hls.ProbeError):
            video = None
        if video and parse_stamp(stamp(now)) > parse_stamp(record["observedAt"]):
            record = {**record, "videoID": video, "observedAt": stamp(now),
                      "validUntil": stamp(now + LIFETIME)}
            report["renewed"] += 1
        refreshed.append(record)
    return refreshed, report
