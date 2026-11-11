"""Preserve real past XMLTV slots without changing the new programme schedule."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import unicodedata
from xml.etree import ElementTree as ET

RETAINED = "zapper-retained-history"

EMPTY = {"sin programación", "sem programação", "no information",
         "no program information", "no programme information", "programming unavailable"}


def normalized(value):
    return "".join(character for character in unicodedata.normalize("NFKD", value.casefold())
                   if character.isalnum())


def instant(value):
    if not value:
        raise ValueError("missing XMLTV time")
    try:
        return datetime.strptime(value, "%Y%m%d%H%M%S %z").astimezone(timezone.utc)
    except ValueError:
        return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


def without_history(xml, serialize):
    """Keep retained history out of freshness and fallback selection."""
    if RETAINED not in xml:
        return xml
    root = ET.fromstring(xml)
    for row in root.findall(f'programme[@{RETAINED}="1"]'):
        root.remove(row)
    return serialize(root).decode()


def stable_bindings(previous_payload, current_payload):
    """Require the same stable identity and at least one unchanged source locator."""
    def index(payload):
        result = {}
        for collection, id_field in (("catalog", "tvgId"), ("expansion", "tvgID")):
            for item in payload.get(collection, []):
                feed, identity = item.get(id_field), item.get("id")
                if not feed or not identity or feed in result:
                    raise ValueError("ambiguous catalogue identity")
                locators = {(source.get("type"), source.get("url") or source.get("channelId"))
                            for source in item.get("sources", [])
                            if source.get("url") or source.get("channelId")}
                scope = (tuple(sorted(item.get("broadcastArea") or [])), item.get("country"),
                         tuple(sorted(item.get("languages") or [])), item.get("language"))
                result[feed] = (collection, identity, scope, locators)
        return result

    old, new = index(previous_payload), index(current_payload)
    return {feed for feed, (collection, identity, scope, locators) in new.items()
            if feed in old and old[feed][:3] == (collection, identity, scope)
            and bool(locators & old[feed][3])}


def preserve(current_xml, previous_xml, now, serialize, *, allowed_ids,
             unavailable_ids=frozenset(), programme_allowed=None, max_bytes=19_000_000):
    """Prefer new XML; retain compatible historical records up to consumer limits.

    History is never evidence that a channel is currently on air. The caller
    must apply this only after deciding which channels belong to the release.
    """
    if now.tzinfo is None:
        raise ValueError("timezone-aware clock required")
    if b"<!DOCTYPE" in previous_xml.upper() or b"<!ENTITY" in previous_xml.upper():
        return current_xml, 0
    current = ET.fromstring(current_xml)
    if current.tag != "tv":
        raise ValueError("expected XMLTV")
    try:
        previous = ET.fromstring(previous_xml)
    except ET.ParseError:
        return current_xml, 0
    if previous.tag != "tv":
        return current_xml, 0
    ids = {node.get("id") for node in current.findall("channel")}
    names = {node.get("id"): normalized(node.findtext("display-name") or "")
             for node in current.findall("channel")}
    old_ids = {node.get("id") for node in previous.findall("channel")}
    if None in ids or len(ids) != len(current.findall("channel")):
        raise ValueError("invalid channel identities")
    spans = defaultdict(list)
    for node in current.findall("programme"):
        feed = node.get("channel")
        if feed not in ids:
            raise ValueError("unknown programme channel")
        start, stop = instant(node.get("start")), instant(node.get("stop"))
        if not start < stop:
            raise ValueError("invalid current interval")
        spans[feed].append((start, stop))
    for row in spans.values():
        row.sort()
    candidates = []
    for node in previous.findall("programme"):
        feed = node.get("channel")
        title = (node.findtext("title") or "").strip()
        if (feed not in ids or feed not in old_ids or feed not in allowed_ids
                or feed in unavailable_ids or not title or title.casefold() in EMPTY
                or (not node.get("zapper-provider-id") and names.get(feed)
                    and normalized(title) == names[feed])):
            continue
        if programme_allowed is not None and not programme_allowed(node):
            continue
        try:
            start, stop = instant(node.get("start")), instant(node.get("stop"))
        except ValueError:
            continue
        # The old producer emitted unsourced 48-hour channel-name placeholders.
        # Their exact programme is unknown, so do not preserve them as history.
        if stop - start == timedelta(hours=48) and not node.get("zapper-provider-id"):
            continue
        if not (now - timedelta(days=29) <= start < stop <= now
                and stop - start <= timedelta(days=7)):
            continue
        candidates.append((stop, start, feed, node))
    accepted = []
    for stop, start, feed, node in sorted(candidates, key=lambda item: item[0], reverse=True):
        row = spans[feed]
        if any(start < previous_stop and previous_start < stop
               for previous_start, previous_stop in row):
            continue
        row.append((start, stop))
        node.set(RETAINED, "1")
        current.append(node)
        accepted.append(node)
    if not accepted:
        return current_xml, 0
    result = serialize(current)
    # Respect the existing 20 MB guide limit, preserving the newest history first.
    if len(result) <= max_bytes:
        return result, len(accepted)
    for node in accepted:
        current.remove(node)
    result = serialize(current)
    if len(result) > max_bytes:
        return current_xml, 0
    low, high = 0, len(accepted)
    while low < high:
        middle = (low + high + 1) // 2
        current.extend(accepted[:middle])
        candidate = serialize(current)
        for node in accepted[:middle]:
            current.remove(node)
        if len(candidate) <= max_bytes:
            low = middle
            result = candidate
        else:
            high = middle - 1
    return (result, low) if low else (current_xml, 0)
