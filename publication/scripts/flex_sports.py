"""Project the reviewed, portable subset of the Sports atlas into Flex outputs."""
from collections import defaultdict
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
import json
import re
from urllib.parse import parse_qsl, urlsplit
from xml.etree import ElementTree as ET

from guide_history import instant
from order_lineup import GROUP_TITLE, lineup_key, load_lineup_settings, with_missing_logo
from xmltv_format import xmltv_bytes

APPROVED = {
    'source1:content:content_c702e1fa113974b1': ('src_75641332c9ce38a3', '75641332c9ce38a37acfe465fd5946d5063dd3fca96c91a32619ba3a4c7fa977', 'direct'),
    'source1:content:content_acd7fbf7106dca11': ('src_3fd8f2a87bebca3c', '3fd8f2a87bebca3cec5f7cd684b969c5c7b0131c7e4fa68e0c970e8ddf695743', 'direct'),
    'source1:content:content_a3ed6c4947f23a9e': ('src_40227f339b60b59a', '40227f339b60b59aa19d2f8467a6bf76e856f4f2ec8673f345e0f991d957eb5e', 'direct'),
    'source1:content:content_21d43c2a855c7820': ('src_fb98b6bfbf1ce918', 'fb98b6bfbf1ce918044b8d0750b4a5c4ee0e72e593cae7b98698838a5761ef5e', 'direct'),
    'source1:content:content_50811e764efa22c0': ('src_114fe12ab1dd9a16', '114fe12ab1dd9a16f2dba1e91f92cc92da5e893851760431f73edb395902ba1e', 'reviewed-fifa-hls'),
}

LIST_FIELDS = {"broadcastArea", "languages"}
TEXT_FIELDS = {"id", "sourceID", "name", "group", "url", "providerChannelID", "mode", "logoURL"}
GROUPS = {"Noticias", "Deportes"}


def validate_routes(entries):
    if not isinstance(entries, list) or any(not isinstance(row, dict) for row in entries):
        raise ValueError("Invalid Flex Sports inventory")
    if any(set(row) != TEXT_FIELDS | LIST_FIELDS
           or any(not isinstance(row[key], str) or not row[key] for key in TEXT_FIELDS)
           or any(not isinstance(row[key], list) or not row[key]
                  or any(not isinstance(item, str) or not item for item in row[key]) for key in LIST_FIELDS)
           for row in entries):
        raise ValueError("Invalid Flex Sports record")
    if len(entries) != len(APPROVED) or {row["id"] for row in entries} != set(APPROVED):
        raise ValueError("Missing, duplicate or unknown Flex Sports identity")
    if len({row["url"] for row in entries}) != len(entries):
        raise ValueError("Duplicate Flex Sports locator")
    for row in entries:
        if (not row["id"].startswith("source1:content:") or row["group"] not in GROUPS
                or any(c in row["name"] for c in ('"', "\r", "\n"))):
            raise ValueError("Unsafe Flex Sports metadata")
        approved = APPROVED[row["id"]]
        if (row["sourceID"], sha256(row["url"].encode()).hexdigest(), row["mode"]) != approved:
            raise ValueError("Unreviewed Flex Sports locator")
        url = urlsplit(row["url"])
        query = parse_qsl(url.query, keep_blank_values=True)
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.fragment or url.port is not None or not url.path.lower().endswith(".m3u8")
                or (query and not (len(query) == 1 and url.hostname == "d3f4oii5n0oeqi.cloudfront.net"
                                  and query[0][0] == "ads.xumo_channelId" and re.fullmatch(r"[0-9]{8}", query[0][1])))):
            raise ValueError("Nonportable Flex Sports locator")
    return entries


def load_review(path, bindings_path):
    entries = validate_routes(json.loads(path.read_text()))
    bindings = json.loads(bindings_path.read_text())["channels"]
    by_id = {row["id"]: row for row in bindings}
    if len(by_id) != len(bindings):
        raise ValueError("Duplicate Sports schedule binding")
    for row in entries:
        match = by_id.get(row["id"], {})
        if (row["sourceID"] not in match.get("sourceIDs", []) or match.get("name") != row["name"]
                or (match.get("schedule") or {}).get("providerChannelID") != row["providerChannelID"]
                or match.get("unavailableReason")):
            raise ValueError("Flex Sports identity or guide binding changed")
    return entries


def project(payload, parse_playlist, *, require_fresh=False):
    """Pure derivation used both by the writer and the publication validator."""
    if "flexSports" not in payload:
        return payload["playlist"], payload["flexXML"].encode()
    entries = validate_routes(payload["flexSports"])
    sports_ids = {row["id"] for row in entries}
    if sports_ids.intersection(payload.get("editorialWithheldTvgIDs", [])):
        raise ValueError("Flex Sports identity is editorially excluded")
    playlist_head, blocks = parse_playlist(payload["playlist"])
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    guide = ET.fromstring(payload["flexXML"], parser=parser)
    sports = ET.fromstring(payload["sportsXML"])
    if guide.tag != "tv" or sports.tag != "tv":
        raise ValueError("Invalid XMLTV root")
    existing_ids = [row.get("id") for row in guide.findall("channel")]
    if len(existing_ids) != len(set(existing_ids)) or set(existing_ids) != set(blocks):
        raise ValueError("Existing Flex XMLTV and M3U differ")
    if sports_ids.intersection(existing_ids):
        raise ValueError("Duplicate projected Flex Sports identity")
    now = instant_from_publication(payload)
    sports_channels = defaultdict(list)
    sports_programmes = defaultdict(list)
    for row in sports.findall("channel"):
        sports_channels[row.get("id")].append(row)
    for row in sports.findall("programme"):
        if row.get("channel") in sports_ids:
            start, stop = instant(row.get("start")), instant(row.get("stop"))
            if start >= stop or not row.findtext("title", "").strip():
                raise ValueError("Invalid Flex Sports schedule")
            sports_programmes[row.get("channel")].append((start, stop, row))
    channels = {channel.get("id"): channel for channel in guide.findall("channel")}
    programmes = guide.findall("programme")
    facets, overrides = load_lineup_settings()
    metadata = {row.get("tvgId") or row.get("tvgID"): row
                for row in (*payload.get("catalog", ()), *payload.get("expansion", ()))}
    additions = {}
    for row in entries:
        key, name = row["id"], row["name"]
        declared = sports_channels[key]
        if len(declared) != 1 or declared[0].findtext("display-name") != name:
            raise ValueError("Missing or mismatched Flex Sports guide identity")
        ordered = sorted(sports_programmes[key], key=lambda item: item[:2])
        if not ordered or (require_fresh and not any(
            now < stop and start <= now + timedelta(hours=36) for start, stop, _ in ordered
        )):
            raise ValueError("No viable Flex Sports schedule")
        if any(first[1] > second[0] for first, second in zip(ordered, ordered[1:])):
            raise ValueError("Overlapping Flex Sports schedule")
        channel = deepcopy(declared[0])
        icon = ET.Element("icon", src=row["logoURL"])
        for existing in channel.findall("icon"):
            if existing.get("src") == row["logoURL"]:
                channel.remove(existing)
        channel.insert(len(channel.findall("display-name")), icon)
        channels[key] = channel
        programmes.extend(record for _, _, record in ordered)
        group = row["group"]
        entry = f'#EXTINF:-1 tvg-id="{key}" tvg-name="{name}" group-title="{group}",{name}'
        labelled = with_missing_logo(entry, row["logoURL"])
        if labelled == entry:
            raise ValueError("Invalid canonical Flex Sports logo")
        additions[key] = f'{labelled}\n{row["url"]}\n'
        metadata[key] = row

    def sort_key(feed, block):
        group = GROUP_TITLE.search(block.splitlines()[0])
        if not group:
            raise ValueError("Missing Flex lineup category")
        return lineup_key(feed, metadata.get(feed, {}), facets, overrides, group[1])[0]

    # Merge into the existing sorted lineup without rewriting any prior channel block.
    pending = iter(sorted((sort_key(feed, block), feed) for feed, block in additions.items()))
    next_row = next(pending, None)
    ordered = []
    for feed, block in blocks.items():
        key = sort_key(feed, block)
        while next_row and next_row[0] < key:
            ordered.append(next_row[1])
            next_row = next(pending, None)
        ordered.append(feed)
    if next_row:
        ordered.append(next_row[1])
        ordered.extend(feed for _, feed in pending)
    rank = {feed: index for index, feed in enumerate(ordered)}

    # XMLTV declares all channels before programmes. Preserve annotations and
    # order programme records by the same channel lineup, then by air time.
    guide[:] = [child for child in guide if child.tag not in ("channel", "programme")]
    guide.extend(channels[feed] for feed in ordered)
    guide.extend(sorted(programmes, key=lambda row: (rank[row.get("channel")], instant(row.get("start")))))
    blocks.update(additions)
    xml = xmltv_bytes(guide)
    if len(xml) > 20_000_000:
        raise ValueError("Projected Flex guide exceeds the allowed size")
    return playlist_head + "".join(blocks[feed] for feed in ordered), xml


def instant_from_publication(payload):
    from datetime import datetime, timezone
    timestamp = payload["publication"]["generatedAt"]
    if not isinstance(timestamp, int) or timestamp <= 0:
        raise ValueError("Missing publication clock")
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)
