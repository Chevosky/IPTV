"""Build the playlist and both guide URLs from the audited expansion inventory."""
import json
import os
import shutil
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
import subprocess
import xml.etree.ElementTree as ET

import filter_expansion as projection
import directos_youtube
import directos_youtube_anonimos
import guia_elcinema
import guia_expansion
import guia_istv
import guide_continuity
import public_fallbacks
import public_hls
import pluto_hls
import qatar_hls
import infomaniak_hls
from guide_history import without_history
from xmltv_format import write_xmltv_clean, xmltv_bytes

UPSTREAM = "ae9cc64f9497c602ec2f6e17857ae38b3aa1ff05"
EMPTY_TITLES = {
    "جدول البرامج غير متاح", "no information", "no program information",
    "no programme information", "no programming information", "sin programación",
    "sem programação", "programming unavailable",
}
REJECTED_BINDINGS = {
    "AsharqDiscovery.sa@SD": "iptv-org:shahid.mbc.net:1001845",
    "AsharqDocumentary.sa@SD": "iptv-org:shahid.mbc.net:997605",
}


def schedule_allowed(programme):
    if programme.get("channel") == guia_expansion.RAI_ASIA_AUSTRALIA_ID:
        return guia_expansion.rai_asia_australia_provenance(programme)
    rejected = REJECTED_BINDINGS.get(programme.get("channel"))
    return rejected is None or programme.get("zapper-provider-id") != rejected


def programmes_by_feed(xml):
    result = defaultdict(list)
    for programme in ET.fromstring(xml).findall("programme"):
        result[programme.attrib["channel"]].append(programme)
    return result


def separate_conflicting_slots(rows):
    """Return uncontested rows and the windows where a provider disagrees with itself."""
    accepted, disputed = [], []
    group = []
    for start, stop, title in sorted(rows):
        if group and start >= group_end:
            if len(group) == 1:
                accepted.append(rows[group[0]])
            else:
                disputed.append((group[0][0], group_end))
            group = []
        group_end = max(group_end, stop) if group else stop
        group.append((start, stop, title))
    if len(group) == 1:
        accepted.append(rows[group[0]])
    elif group:
        disputed.append((group[0][0], group_end))
    return accepted, disputed


def exclude_disputed_slots(prior, disputed):
    """A contradiction is different from an omission of a previously verified slot."""
    for feed, (provider, windows) in disputed.items():
        def eligible(row):
            if row.get("zapper-provider-id") != provider:
                return True
            start, stop = (datetime.strptime(row.attrib[key], projection.XMLTV_DATE)
                           for key in ("start", "stop"))
            return all(stop <= begin or start >= end for begin, end in windows)
        prior[feed] = [row for row in prior[feed] if eligible(row)]


def reviewed_programmes(batches, bindings, now):
    approved = defaultdict(dict)
    for node in ET.parse(bindings).getroot().findall("channel"):
        feed = node.attrib["xmltv_id"]
        site = node.attrib["site"]
        if any(feed in feeds for feeds in approved.values()):
            raise ValueError(f"Duplicate provider binding: {feed}")
        approved[site][feed] = node.attrib["site_id"]
    programmes, disputed = {}, {}
    for site, feeds in approved.items():
        batch = batches / f"{site}.xml"
        if not batch.exists():
            continue
        guide = ET.parse(batch).getroot()
        by_feed = defaultdict(dict)
        if guide.tag != "tv":
            raise ValueError(f"Invalid provider output: {site}")
        for programme in guide.findall("programme"):
            feed = programme.attrib["channel"]
            if feed not in feeds:
                raise ValueError(f"Unexpected provider binding: {site}/{feed}")
            title = programme.findtext("title", "").strip()
            if not title or title.casefold() in EMPTY_TITLES or (
                    site == "elcinema.com" and title in guia_elcinema.ELCINEMA_EMPTY):
                continue
            start, stop = (datetime.strptime(programme.attrib[key], projection.XMLTV_DATE)
                           for key in ("start", "stop"))
            if not now - timedelta(hours=2) < stop <= now + timedelta(hours=36):
                continue
            if not start < stop <= start + timedelta(hours=12):
                continue
            programme.set("zapper-provider-id", f"iptv-org:{site}:{feeds[feed]}")
            programme.set("zapper-provenance",
                          f"https://github.com/iptv-org/epg/tree/{UPSTREAM}/sites/{site}")
            if schedule_allowed(programme):
                by_feed[feed][(start, stop, title)] = programme
        for feed, rows in by_feed.items():
            accepted, conflicts = separate_conflicting_slots(rows)
            programmes[feed] = accepted
            if conflicts:
                disputed[feed] = (f"iptv-org:{site}:{feeds[feed]}", conflicts)
    return programmes, disputed


def preserve_uncontradicted_slots(fresh, cached, now):
    """Keep eligible prior rows omitted by a partial refresh of the same source."""
    def identity(row):
        locator = urlsplit(row.get("zapper-provenance", ""))
        provider = row.get("zapper-provider-id")
        host = locator.hostname
        # RTVE's short broadcast API and paired Star schedule are one verified edition.
        if provider == "rtve-star:11186" and locator.scheme == "https" and (
            (host, locator.path) in {("api.rtve.es", "/api/lives/broadcasts/11186.json"),
                                     ("www.rtve.es", "/play/tve-internacional/argentina/")}
        ):
            host = "rtve.es"
        if provider == "apunt:internet" and locator.scheme == "https" and (
            (host, locator.path) in {
                ("www.apuntmedia.es", "/directe/directe-tv_136_1392524.html"),
                ("api.apuntmedia.es", "/programacio/internet/hui"),
                ("api.apuntmedia.es", "/programacio/internet/dema"),
            }
        ):
            host = "verified-apunt-web-edition"
        return provider, locator.scheme, host

    merged = dict(fresh)
    for feed, rows in fresh.items():
        sources = {identity(row) for row in rows}
        if len(sources) != 1 or projection.programme_state(rows, now) not in projection.VERIFIED_STATES:
            continue
        source, = sources
        windows = [(datetime.strptime(row.attrib["start"], projection.XMLTV_DATE),
                    datetime.strptime(row.attrib["stop"], projection.XMLTV_DATE)) for row in rows]
        retained = []
        for row in cached.get(feed, []):
            if (identity(row) != source or not schedule_allowed(row)
                    or not row.findtext("title", "").strip()):
                continue
            start, stop = (datetime.strptime(row.attrib[key], projection.XMLTV_DATE) for key in ("start", "stop"))
            if (now < stop <= now + timedelta(days=3) and start < stop <= start + timedelta(hours=12)
                    and all(stop <= new_start or start >= new_stop for new_start, new_stop in windows)):
                retained.append(row)
        combined = [*rows, *retained]
        merged[feed] = combined if projection.programme_state(combined, now) in projection.VERIFIED_STATES else rows
    return merged


def write_guide(path, programmes):
    guide = ET.Element("tv")
    for rows in programmes.values():
        guide.extend(row for row in rows if schedule_allowed(row))
    write_xmltv_clean(guide, path)


def remove_unusable_programmes(data, previously_unavailable=()):
    """Keep channel identities when a provider has only an explicitly unusable listing."""
    root = ET.fromstring(data)
    rules = {
        guia_expansion.RAI_ASIA_AUSTRALIA_ID: lambda node: not schedule_allowed(node),
        "CineWestern.es@SD": lambda node: (node.findtext("title") or "").strip().casefold()
            in {"cine western", "cine western (spain)"},
        "PlayTV.br@SD": lambda node: "canal substituído por tv wa" in (node.findtext("title") or "").casefold(),
        "AtacamaTV.cl@SD": lambda node: node.findtext("title") == "Atacama TV - Español"
            and (datetime.strptime(node.attrib["stop"], projection.XMLTV_DATE)
                 - datetime.strptime(node.attrib["start"], projection.XMLTV_DATE)) == timedelta(days=2),
    }
    removed = False
    known_missing = set(previously_unavailable)
    declared = {node.get("id") for node in root.findall("channel")}
    unavailable = []
    for feed, discard in rules.items():
        programmes = [programme for programme in root.findall("programme")
                      if programme.get("channel") == feed]
        unusable = [programme for programme in programmes if discard(programme)]
        for programme in unusable:
            root.remove(programme)
        if unusable:
            removed = True
        if len(unusable) == len(programmes) and (unusable or feed in known_missing & declared):
            unavailable.append(feed)
    return (xmltv_bytes(root).decode() if removed else data), unavailable


def build(root, baseline_playlist, baseline_guide, batches, output, *, fetch_official=True, official_snapshot=None,
          official_batches=(),
          youtube_fetch=None, youtube_public_fetch=None, fallback_probe=None, pluto_fallback_probe=None, qatar_fallback_probe=None, master_fallback_probe=None, recovery_probe=None, youtube_fallback_probe=None,
          native_youtube_fallback_probe=None):
    now = datetime.now(timezone.utc)
    settings = root / "publication/epg"
    seed_path = settings / "expansion-seed.json"
    seed, seed_sha = projection.unpack(seed_path)
    prior_path = root / "coordinated-generation.json"
    previous_release = projection.unpack(prior_path)[0] if prior_path.exists() else None
    prior = previous_release if previous_release is not None else seed
    prior_schedule = without_history(prior["flexXML"], xmltv_bytes)

    catalog = json.loads((root / "catalog-flex.json").read_text())["channels"]
    if catalog != seed["catalog"] or prior["catalog"] != catalog:
        raise ValueError("The reviewed expansion and canonical catalog differ")
    header, baseline = projection.playlist_rows(baseline_playlist.read_text())
    _, reviewed = projection.playlist_rows(seed["playlist"])
    # Review against the original playlist source, never the alternative itself.
    primaries = {feed: next((line for line in record.splitlines()
                             if line.startswith(("https://", "http://"))), "")
                 for feed, record in reviewed.items()}
    expansion, alternatives, fallback_report = public_fallbacks.refresh(
        seed, prior, now, primaries,
        probe=fallback_probe or (public_hls.matches if fetch_official else None),
        pluto_probe=pluto_fallback_probe or (pluto_hls.matches if fetch_official else None),
        qatar_probe=qatar_fallback_probe or (qatar_hls.matches if fetch_official else None),
        master_probe=master_fallback_probe or (public_hls.matches_declared_master if fetch_official else None),
        recovery_probe=recovery_probe or (public_hls.matches_reviewed_recovery if fetch_official else None),
        infomaniak_probe=infomaniak_hls.matches if fetch_official else None,
    )
    by_id = {row["tvgID"]: row for row in expansion}
    if set(baseline) | set(by_id) != set(reviewed) or set(baseline) & set(by_id):
        raise ValueError("The reviewed playlist and canonical inventory differ")
    projected = public_fallbacks.project_reviewed_recoveries(reviewed, alternatives, primaries, now)
    playlist = header + "".join(projected[feed] for feed in (*baseline, *by_id))

    base_xml = baseline_guide.read_text()
    if fetch_official:
        try:
            istv = guia_istv.weekly_programmes(guia_istv.fetch_days(), now)
        except (OSError, ValueError):
            istv = []
    else:
        istv = []
    istv = istv or guia_istv.retained_programmes(prior_schedule, now)
    base_xml, unavailable = remove_unusable_programmes(
        guia_istv.overlay(base_xml, istv), prior["unavailableProgrammeIDs"])
    guide = ET.fromstring(base_xml)
    cached = programmes_by_feed(prior_schedule)
    if fetch_official or official_snapshot is not None:
        for feed in guide_continuity.BASE_FEEDS:
            sources = guide_continuity.BASE_SOURCES.get(feed)
            if feed not in baseline or (sources and primaries.get(feed) not in sources):
                continue
            current = guide.findall(f'programme[@channel="{feed}"]')
            renewed = guide_continuity.renew(feed, current, cached.get(feed, ()), now, official_snapshot)
            if renewed == current:
                continue
            for row in current:
                guide.remove(row)
            guide.extend(renewed)
            if renewed and feed in unavailable:
                unavailable.remove(feed)
            if not renewed and feed not in unavailable:
                unavailable.append(feed)
    first_programme = next(index for index, node in enumerate(guide) if node.tag == "programme")
    for index, (feed, row) in enumerate(by_id.items()):
        channel = ET.Element("channel", id=feed)
        ET.SubElement(channel, "display-name").text = row["name"]
        guide.insert(first_programme + index, channel)

    reviewed_seed = programmes_by_feed(seed["flexXML"])
    bindings_path = settings / "expansion-iptvorg.channels.xml"
    batches_programmes, disputed = reviewed_programmes(batches, bindings_path, now)
    pbs = guide_continuity.PBS_KIDS
    if pbs in by_id and (fetch_official or official_snapshot is not None):
        reviewed_pbs = batches_programmes.get(pbs, ())
        renewed = guide_continuity.renew(pbs, reviewed_pbs, (), now, official_snapshot)
        if renewed:
            batches_programmes[pbs] = renewed
            if pbs in disputed:
                exclude_disputed_slots(batches_programmes, {pbs: disputed[pbs]})
    exclude_disputed_slots(reviewed_seed, disputed)
    exclude_disputed_slots(cached, disputed)
    fresh = {feed: rows for feed, rows in batches_programmes.items()
             if projection.programme_state(rows, now) in projection.VERIFIED_STATES}
    if fetch_official or official_snapshot is not None:
        elcinema_bindings = {channel.attrib["xmltv_id"]: channel.attrib["site_id"]
                             for channel in ET.parse(bindings_path).findall("channel")
                             if channel.get("site") == "elcinema.com" and channel.get("xmltv_id") not in fresh}
        fresh.update(guia_elcinema.elcinema_programmes(elcinema_bindings, now, official_snapshot))
    # Previously published rows can preserve coverage, but cannot establish a successful refresh.
    official = guia_expansion.programmes_for_expansion(
        seed["expansion"], prior_schedule if fresh else None, now,
        fetch=fetch_official, snapshot=official_snapshot)
    # A local collection can be split into bounded provider jobs. Accept only
    # complete, current schedules for known expansion identities.
    expected_official = {
        entry["tvgID"]: ":".join(resolved[:2])
        for entry in seed["expansion"] if (resolved := guia_expansion.binding(entry))
    }
    for batch in official_batches:
        rows_by_feed = programmes_by_feed(Path(batch).read_bytes())
        for feed, rows in rows_by_feed.items():
            if (feed in by_id and expected_official.get(feed)
                    and all(row.get("zapper-provider-id") == expected_official[feed] for row in rows)
                    and projection.programme_state(rows, now) in projection.VERIFIED_STATES):
                official[feed] = rows
    official_only_channels = 0
    if not fresh:
        official = {
            feed: [row for row in rows if schedule_allowed(row)
                   and row.findtext("title", "").strip().casefold() not in EMPTY_TITLES]
            for feed, rows in official.items() if feed in by_id
        }
        official = {feed: rows for feed, rows in official.items()
                    if projection.programme_state(rows, now) in projection.VERIFIED_STATES}
        if not official:
            raise ValueError("No reviewed batch or direct provider delivered programming within the verification window")
        official_only_channels = len(official)
    guides = output.parent
    write_guide(guides / "seed-expansion.xml", {feed: reviewed_seed[feed] for feed in by_id})
    write_guide(guides / "cached-expansion.xml", {feed: cached[feed] for feed in by_id})
    write_guide(guides / "fresh-expansion.xml", preserve_uncontradicted_slots(fresh, cached, now))
    cached_official = {feed: [row for row in cached[feed]
                              if row.get("zapper-provider-id", "").startswith("official:")]
                       for feed in by_id}
    write_guide(guides / "cached-official-expansion.xml", cached_official)
    write_guide(guides / "official-expansion.xml", preserve_uncontradicted_slots(official, cached, now))
    youtube = prior.get("youtube", seed.get("youtube", []))
    youtube_refresh = {"status": "not-requested"}
    executable = shutil.which(os.environ.get("ZAPPER_YOUTUBE_YTDLP", "")) if fetch_official else None
    public_fetch = youtube_public_fetch or (directos_youtube_anonimos.client(executable) if executable else None)
    if youtube and (fetch_official or youtube_fetch is not None or youtube_public_fetch is not None):
        key = os.environ.get("ZAPPER_YOUTUBE_DATA_API_KEY", "").strip()
        fetch = youtube_fetch or (directos_youtube.client(key) if key else None)
        if fetch is not None:
            youtube, youtube_refresh = directos_youtube.refresh(youtube, fetch, now)
        else:
            if public_fetch is not None:
                youtube, youtube_refresh = directos_youtube_anonimos.refresh(youtube, public_fetch, now)
            else:
                youtube_refresh = {"status": "resolver-not-configured"}
    youtube_alternatives, youtube_fallback_report = public_fallbacks.refresh_youtube(
        seed, prior, youtube, now, fetch=public_fetch,
        probe=youtube_fallback_probe or (public_hls.matches_youtube if fetch_official else None))
    native_youtube_fetch = youtube_public_fetch or (
        directos_youtube_anonimos.client(executable, force_ipv4=True) if executable else None)
    native_youtube_fallbacks, native_youtube_report = public_fallbacks.refresh_native_youtube(
        seed, prior, expansion, now, fetch=native_youtube_fetch,
        probe=native_youtube_fallback_probe or ((lambda master, url: public_hls.matches_youtube(
            master, url, youtube_segments=10, hls_segments=12)) if fetch_official else None))
    data = {**seed, "youtube": youtube, "expansion": expansion, "alternatives": alternatives,
            "playlist": playlist, "flexXML": xmltv_bytes(guide).decode(),
            "sportsXML": (root / "guide-sports-news.xml").read_text(),
            "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
            "unavailableProgrammeIDs": unavailable}
    if youtube_alternatives or "youtubeAlternatives" in seed:
        data["youtubeAlternatives"] = youtube_alternatives
    if native_youtube_fallbacks or "nativeYouTubeFallbacks" in seed:
        if any({record[key] for record in native_youtube_fallbacks} & {row[key] for row in youtube}
               for key in ("publisherID", "videoID")):
            raise ValueError("Native YouTube fallback duplicates a standalone YouTube signal")
        data["nativeYouTubeFallbacks"] = native_youtube_fallbacks
    flex_sports_review = root / "publication/sports/flex-direct-bindings.json"
    if flex_sports_review.exists():
        from flex_sports import load_review
        data["flexSports"] = load_review(flex_sports_review, flex_sports_review.with_name("guide-bindings.json"))
    elif "flexSports" in prior:
        raise ValueError("The published Flex Sports review is missing")
    editorial_path = settings / "editorial-review.json"
    editorial = json.loads(editorial_path.read_text()) if editorial_path.exists() else None
    verified_guides = [
        guides / "seed-expansion.xml", guides / "cached-expansion.xml", guides / "fresh-expansion.xml",
        guides / "cached-official-expansion.xml",
    ]
    reviewed_slots = settings / "expansion-reviewed.xml"
    if reviewed_slots.exists():
        # Dated broadcaster confirmations remain a fallback when the refreshed guide is unavailable.
        verified_guides.append(reviewed_slots)
    verified_guides.append(guides / "official-expansion.xml")
    projection.write_release(data, output, now, verified_guides, seed_sha, keep_inventory=True, editorial=editorial,
                             previous_payload=previous_release, history_programme_allowed=schedule_allowed)
    result = json.loads((output / "revision-epg.json").read_text())
    result["freshProviderChannels"] = len(fresh)
    result["officialOnlyChannels"] = official_only_channels
    result["youtubeRefresh"] = youtube_refresh
    result["youtubeFallbackRefresh"] = youtube_fallback_report
    result["nativeYouTubeFallbackRefresh"] = native_youtube_report
    result["publicFallbackRefresh"] = fallback_report
    return result
