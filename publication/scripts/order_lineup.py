"""Order a Flex M3U and its XMLTV using the same channel identities."""

import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from xmltv_format import xmltv_bytes


GUIDE_GROUPS = (
    ("General", ("00 - General",)),
    ("Noticias", ("01 - News",)),
    ("Deportes", ("10 - Sports & Motor",)),
    ("Cine y series", ("03 - Movies", "04 - Series")),
    ("Entretenimiento", ("02 - Entertainment", "05 - Comedy", "99 - Reality TV")),
    ("Infantil", ("08 - Animation & Kids",)),
    ("Música", ("07 - Music",)),
    ("Cultura y documentales", ("06 - Documentary & Factual", "09 - Lifestyle & Culture")),
    ("Otras", ()),
)
GUIDE_CATEGORY = {
    category: (index, label)
    for index, (label, categories) in enumerate(GUIDE_GROUPS)
    for category in (label, *categories)
}
LANGUAGES = ("spa", "por", "eng", "fra", "ita")
LANGUAGE_ALIASES = dict(zip(("es", "pt", "en", "fr", "it"), LANGUAGES)) | {"fre": "fra"}
UNSPECIFIED_LANGUAGES = {"und", "mul", "mis", "zxx"}
ARGENTINA_GENERAL = (
    "AmericaTV.ar@SD",
    "TelefeBuenosAires.ar@SD",
    "Telefe.ar@SD",
    "TelefeInternacional.ar@SD",
)
ARGENTINA_PRIORITY = {feed: index for index, feed in enumerate(ARGENTINA_GENERAL)}
TVG_ID = re.compile(r'\btvg-id="([^"]+)"')
LOGO = re.compile(r'\btvg-logo="([^"]*)"')
GROUP_TITLE = re.compile(r'\bgroup-title="([^"]+)"')


def _country_code(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]{2}", value.strip()):
        return None
    value = value.strip().upper()
    return "GB" if value == "UK" else value


def _metadata(row, fallback, field, singular):
    declared = row.get(field)
    if declared is not None:
        return declared
    if row.get(singular):
        value = row[singular]
        if singular == "language":
            return (value,)
        if country := _country_code(value):
            return ("c/" + country,)
    return fallback.get(field, ())


def _country(feed, areas):
    if not any(area.startswith("r/") for area in areas):
        territories = set()
        for area in areas:
            namespace, _, value = area.partition("/")
            if namespace in {"s", "ct"}:
                value = value[:2]
            if namespace in {"c", "s", "ct"} and (code := _country_code(value)):
                territories.add(code)
        if len(territories) == 1:
            return next(iter(territories))
    origin = re.search(r"\.([A-Za-z]{2})$", feed.partition("@")[0])
    return _country_code(origin[1]) if origin else None


def _language_order(languages):
    codes = {value.strip().lower() for value in languages}
    codes = {LANGUAGE_ALIASES.get(code, code) for code in codes - UNSPECIFIED_LANGUAGES if code}
    return next((index for index, code in enumerate(LANGUAGES) if code in codes),
                len(LANGUAGES) + (not codes))


def _natural_name(name):
    # Mirror the candidate's case-, diacritic- and width-folded natural order.
    folded = "".join(character for character in unicodedata.normalize("NFKD", name.casefold())
                     if not unicodedata.combining(character))
    # Foundation's numeric comparison puts a digit run before ASCII punctuation.
    return tuple((-1, int(token)) if token.isdecimal() else (ord(token), 0)
                 for token in re.findall(r"\d+|[^\d]", folded))


def load_lineup_settings():
    publication = Path(__file__).resolve().parents[1]
    facets = json.loads((publication / "epg/lineup-facets.json").read_text())
    policy = json.loads((publication / "channel-policy.json").read_text())
    return facets, policy.get("categoryOverrides", {})


def _playlist_blocks(playlist):
    if not playlist.startswith("#EXTM3U") or not playlist.endswith("\n"):
        raise ValueError("Expected an extended M3U with complete lines")
    header, *blocks = re.split(r"(?m)(?=^#EXTINF:)", playlist)
    by_feed = {}
    categories = {}
    for block in blocks:
        entry = block.splitlines()[0]
        feed = TVG_ID.search(entry)
        category = GROUP_TITLE.search(entry)
        if not feed or not category or feed[1] in by_feed:
            raise ValueError("Each M3U block must have a unique tvg-id and a group-title")
        by_feed[feed[1]] = block
        categories[feed[1]] = category[1]
    if not by_feed:
        raise ValueError("Expected at least one M3U entry")
    return header, by_feed, categories


def load_logo_overrides():
    path = Path(__file__).resolve().parents[1] / "epg/channel-logo-overlay.json"
    data = json.loads(path.read_text())
    return data["sections"], data.get("replaces", {})


def with_missing_logo(entry, logo, *, replaces=None):
    if not isinstance(logo, str) or re.search(r'[\s"<>\\]', logo):
        return entry
    try:
        fields = urlsplit(logo)
        valid = (fields.scheme == "https" and fields.hostname and fields.port in (None, 443)
                 and fields.username is None and fields.password is None and not fields.fragment)
    except ValueError:
        return entry
    if not valid:
        return entry
    existing = LOGO.search(entry)
    if existing:
        if existing[1] and existing[1] != replaces:
            return entry
        return LOGO.sub(lambda _: f'tvg-logo="{logo}"', entry, count=1)
    identity = TVG_ID.search(entry)
    if identity is None:
        return entry
    return entry[:identity.end()] + f' tvg-logo="{logo}"' + entry[identity.end():]


def lineup_key(feed, row, facets, overrides, category):
    category = overrides.get(feed, {}).get("category") or row.get("category") or category
    group, label = GUIDE_CATEGORY.get(category, (len(GUIDE_GROUPS) - 1, "Otras"))
    fallback = facets.get(feed, {})
    country = _country(feed, _metadata(row, fallback, "broadcastArea", "country"))
    language = _language_order(_metadata(row, fallback, "languages", "language"))
    priority = ARGENTINA_PRIORITY.get(feed, len(ARGENTINA_PRIORITY)) if group == 0 and country == "AR" else len(ARGENTINA_PRIORITY)
    identity = "exp:" + row["id"] if "tvgID" in row and "id" in row else row.get("id", feed)
    return ((group, language, "" if country == "AR" else country or "~", priority,
             feed == "TopGear.uk@SD", _natural_name(row.get("name", feed)), identity), label)


def order_lineup(playlist, guide, catalog=(), expansion=()):
    """Project Zapper's guide groups and order without changing inventory or routes."""
    facets, overrides = load_lineup_settings()
    logos, logo_replacements = load_logo_overrides()
    metadata = {}
    for row in (*catalog, *expansion):
        feed = row.get("tvgId") or row.get("tvgID")
        if not feed or feed in metadata:
            raise ValueError("Expected unique tvgId/tvgID values in lineup metadata")
        metadata[feed] = row

    header, blocks, categories = _playlist_blocks(playlist)
    labels = {}

    def sort_key(feed):
        key, labels[feed] = lineup_key(feed, metadata.get(feed, {}), facets, overrides, categories[feed])
        return key

    ordered = sorted(blocks, key=sort_key)
    rank = {feed: index for index, feed in enumerate(ordered)}

    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    root = ET.fromstring(guide, parser=parser)
    if root.tag != "tv":
        raise ValueError("Expected an XMLTV guide")
    channels = root.findall("channel")
    programmes = root.findall("programme")
    ids = [channel.get("id") for channel in channels]
    if len(ids) != len(rank) or set(ids) != set(rank):
        raise ValueError("M3U and XMLTV channel IDs do not match")
    if any(programme.get("channel") not in rank for programme in programmes):
        raise ValueError("XMLTV programme refers to a channel outside the lineup")
    ordered_channels = iter(sorted(channels, key=lambda channel: rank[channel.get("id")]))
    ordered_programmes = iter(sorted(programmes, key=lambda programme: rank[programme.get("channel")]))
    for index, child in enumerate(root):
        if child.tag == "channel":
            root[index] = next(ordered_channels)
        elif child.tag == "programme":
            root[index] = next(ordered_programmes)

    def block_with_group(feed):
        entry, newline, route = blocks[feed].partition("\n")
        row = metadata.get(feed, {})
        logo = (logos["expansion"].get(row.get("id", "").lower()) if "tvgID" in row else
                logos["catalog"].get(row.get("id", "")))
        labelled = GROUP_TITLE.sub(lambda _: f'group-title="{labels[feed]}"', entry, count=1)
        return with_missing_logo(labelled, logo, replaces=logo_replacements.get(feed)) + newline + route

    return header + "".join(block_with_group(feed) for feed in ordered), xmltv_bytes(root)
