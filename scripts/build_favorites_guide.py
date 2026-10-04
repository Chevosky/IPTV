#!/usr/bin/env python3
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAYLIST = ROOT / "favorites-test.m3u"
SOURCE = ROOT / "guide.xml"
EXACT_SOURCE = ROOT / "guide-iptvorg-exact.xml"
ALIASES_FILE = ROOT / "epg-aliases.json"
FORCE_ALIASES_FILE = ROOT / "epg-force-aliases.json"
OUT = ROOT / "guide-favorites.xml"
REPORT = ROOT / "coverage-favorites.json"
WINDOW_HOURS = 24
IPTVORG_CHANNELS_API = "https://iptv-org.github.io/api/channels.json"
IPTVORG_FEEDS_API = "https://iptv-org.github.io/api/feeds.json"
IPTVORG_LANGUAGES_API = "https://iptv-org.github.io/api/languages.json"

LANGUAGE_LABELS = {
    "spa": "Español",
    "eng": "English",
    "por": "Português",
    "ita": "Italiano",
    "fra": "Français",
    "deu": "Deutsch",
}

SYNTHETIC_NAME_OVERRIDES = {
    "ComediaalobestiadePlutoTV.de": "Comedia a lo bestia",
    "CCPlutoTV.de": "Comedy Central",
}


def parse_playlist(path: Path):
    out = {}
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("#EXTINF:"):
            continue
        m = re.search(r'tvg-id="([^"]+)"', line)
        if not m:
            continue
        cid = m.group(1).strip()
        name = line[line.rfind(",")+1:].strip()
        url = lines[i+1].strip() if i+1 < len(lines) else ""
        if cid:
            out[cid] = {"name": name, "url": url}
    return out

def parse_xmltv_dt(value):
    if not value:
        return None
    value = value.strip()
    main = value[:14]
    tz = value[14:].strip()
    try:
        dt = datetime.strptime(main, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    if tz and len(tz) >= 5 and tz[0] in "+-" and tz[1:5].isdigit():
        sign = 1 if tz[0] == "+" else -1
        off = timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5])) * sign
        dt = dt.replace(tzinfo=timezone(off)).astimezone(timezone.utc)
    else:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def iso_to_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z","+00:00")).astimezone(timezone.utc)
    except Exception:
        return None

def dt_to_xmltv(dt):
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S +0000")

def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent":"Chevosky-IPTV-EPG/2.2"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))

def clean_synthetic_name(value):
    value = (value or "").strip()

    # Stream-specific decorations are useful in the M3U but noisy as a
    # placeholder programme title.
    value = re.sub(
        r'\s*\((?:2160p|1440p|1080p|720p|576p|540p|480p|360p|240p|4K|UHD|FHD|HD|SD)\)\s*',
        ' ',
        value,
        flags=re.I,
    )
    value = re.sub(
        r'\s*\[(?:Geo-blocked|Not 24/7)\]\s*',
        ' ',
        value,
        flags=re.I,
    )

    # These are platform/collection labels, not useful programme information
    # when the guide has no real schedule.
    value = re.sub(r'\bBest\s+of\s+', '', value, flags=re.I)
    value = re.sub(r'\b(?:by|de)\s+Pluto\s+TV\b', '', value, flags=re.I)
    value = re.sub(r'\bPluto\s+TV\b', '', value, flags=re.I)

    value = re.sub(r'\s{2,}', ' ', value)
    value = re.sub(r'\s+([:;,.!?])', r'\1', value)
    return value.strip(' -–—')

def split_tvg_id(tvg_id):
    if "@" in tvg_id:
        channel_id, feed_id = tvg_id.rsplit("@", 1)
        return channel_id, feed_id
    return tvg_id, None

def load_iptvorg_metadata():
    try:
        channels_data = fetch_json(IPTVORG_CHANNELS_API)
        feeds_data = fetch_json(IPTVORG_FEEDS_API)
        languages_data = fetch_json(IPTVORG_LANGUAGES_API)
    except Exception as e:
        print(f"iptv-org metadata: {e}")
        return {}, {}, {}

    channels = {
        item.get("id"): item
        for item in channels_data
        if item.get("id")
    }

    feeds = {}
    for item in feeds_data:
        channel_id = item.get("channel")
        if channel_id:
            feeds.setdefault(channel_id, []).append(item)

    language_names = {
        item.get("code"): item.get("name")
        for item in languages_data
        if item.get("code") and item.get("name")
    }

    return channels, feeds, language_names

def synthetic_language(tvg_id, feeds_by_channel, language_names):
    channel_id, feed_id = split_tvg_id(tvg_id)
    candidates = feeds_by_channel.get(channel_id, [])

    chosen = None
    if feed_id:
        exact = [item for item in candidates if item.get("id") == feed_id]
        if len(exact) == 1:
            chosen = exact[0]
    else:
        main = [item for item in candidates if item.get("is_main")]
        if len(main) == 1:
            chosen = main[0]
        elif len(candidates) == 1:
            chosen = candidates[0]

    if not chosen:
        return None

    codes = list(dict.fromkeys(chosen.get("languages") or []))
    if len(codes) != 1:
        return None

    code = codes[0]
    return LANGUAGE_LABELS.get(code) or language_names.get(code)

def synthetic_title(tvg_id, playlist_name, channels, feeds_by_channel, language_names):
    channel_id, _ = split_tvg_id(tvg_id)
    canonical = (channels.get(channel_id) or {}).get("name")
    title = SYNTHETIC_NAME_OVERRIDES.get(
        channel_id,
        clean_synthetic_name(canonical or playlist_name),
    )

    language = synthetic_language(
        tvg_id,
        feeds_by_channel,
        language_names,
    )
    if language:
        title = f"{title} - {language}"

    return title

playlist = parse_playlist(PLAYLIST)
wanted = set(playlist)
aliases = {}
if ALIASES_FILE.exists():
    aliases = json.loads(ALIASES_FILE.read_text(encoding="utf-8"))
force_aliases = {}
if FORCE_ALIASES_FILE.exists():
    force_aliases = json.loads(FORCE_ALIASES_FILE.read_text(encoding="utf-8"))

if not wanted:
    raise SystemExit("No tvg-id values found in favorites-test.m3u")
if not SOURCE.exists():
    raise SystemExit("guide.xml is missing")

# Read the consolidated guide plus an optional exact-ID guide generated from iptv-org/epg.
guide_channels = {}
guide_programmes = {}
needed_source_ids = set(wanted) | set(aliases.values()) | set(force_aliases.values())

def load_guide(path, replace=False):
    local_channels = {}
    local_programmes = {}
    for _, elem in ET.iterparse(path, events=("end",)):
        if elem.tag == "channel":
            cid = elem.get("id")
            if cid in needed_source_ids:
                local_channels[cid] = ET.fromstring(ET.tostring(elem, encoding="utf-8"))
            elem.clear()
        elif elem.tag == "programme":
            cid = elem.get("channel")
            if cid in needed_source_ids:
                local_programmes.setdefault(cid, []).append(
                    ET.fromstring(ET.tostring(elem, encoding="utf-8"))
                )
            elem.clear()
    for cid, ch in local_channels.items():
        if replace or cid not in guide_channels:
            guide_channels[cid] = ch
    for cid, ps in local_programmes.items():
        if replace or cid not in guide_programmes:
            guide_programmes[cid] = ps

load_guide(SOURCE, replace=False)
if EXACT_SOURCE.exists():
    load_guide(EXACT_SOURCE, replace=True)

out_channels = {}
out_programmes = []
coverage_source = {}

# Forced aliases take precedence over exact tvg-id matches when the exact id is
# known to point at the wrong regional schedule.
for target_id, source_id in force_aliases.items():
    if target_id not in wanted:
        continue
    ps = guide_programmes.get(source_id, [])
    if not ps:
        continue
    source_ch = guide_channels.get(source_id)
    ch = ET.fromstring(ET.tostring(source_ch, encoding="utf-8")) if source_ch is not None else ET.Element("channel")
    ch.set("id", target_id)
    out_channels[target_id] = ch
    for source_p in ps:
        p = ET.fromstring(ET.tostring(source_p, encoding="utf-8"))
        p.set("channel", target_id)
        out_programmes.append(p)
    coverage_source[target_id] = {"type":"forced-alias","source_id":source_id}

# Direct ID matches.
for target_id in wanted:
    if target_id in coverage_source:
        continue
    ps = guide_programmes.get(target_id, [])
    if not ps:
        continue
    ch = guide_channels.get(target_id)
    if ch is not None:
        ch.set("id", target_id)
        out_channels[target_id] = ch
    for p in ps:
        p.set("channel", target_id)
        out_programmes.append(p)
    coverage_source[target_id] = {"type":"direct","source_id":target_id}

# Explicit aliases for known same-channel IDs.
for target_id, source_id in aliases.items():
    if target_id in coverage_source or target_id not in wanted:
        continue
    ps = guide_programmes.get(source_id, [])
    if not ps:
        continue
    source_ch = guide_channels.get(source_id)
    if source_ch is not None:
        ch = ET.fromstring(ET.tostring(source_ch, encoding="utf-8"))
    else:
        ch = ET.Element("channel")
    ch.set("id", target_id)
    dns = ch.findall("display-name")
    if not dns:
        dn = ET.SubElement(ch, "display-name")
        dn.text = playlist[target_id]["name"]
    out_channels[target_id] = ch
    for source_p in ps:
        p = ET.fromstring(ET.tostring(source_p, encoding="utf-8"))
        p.set("channel", target_id)
        out_programmes.append(p)
    coverage_source[target_id] = {"type":"alias","source_id":source_id}

# Pluto: derive the real Pluto channel ID from the stream URL and query its EPG directly.
now = datetime.now(timezone.utc)
end = now + timedelta(hours=WINDOW_HOURS)
for target_id, info in playlist.items():
    if target_id in coverage_source:
        continue
    m = re.search(r'(?:plu-|channels/)([0-9a-f]{16,32})', info["url"], re.I)
    if not m:
        continue
    site_id = m.group(1)
    params = urllib.parse.urlencode({
        "start": now.isoformat().replace("+00:00","Z"),
        "stop": end.isoformat().replace("+00:00","Z")
    })
    api = f"https://api.pluto.tv/v2/channels/{site_id}?{params}"
    try:
        data = fetch_json(api)
    except Exception as e:
        print(f"Pluto {target_id}: {e}")
        continue
    timelines = data.get("timelines") or []
    kept = 0
    for item in timelines:
        start = iso_to_dt(item.get("start"))
        stop = iso_to_dt(item.get("stop"))
        if start is None or start > end or (stop is not None and stop < now):
            continue
        p = ET.Element("programme", {
            "channel": target_id,
            "start": dt_to_xmltv(start),
            "stop": dt_to_xmltv(stop or (start + timedelta(hours=1)))
        })
        title = ET.SubElement(p, "title")
        title.text = item.get("title") or info["name"]
        ep = item.get("episode") or {}
        if ep.get("name"):
            ET.SubElement(p, "sub-title").text = ep["name"]
        if ep.get("description"):
            ET.SubElement(p, "desc").text = ep["description"]
        for cat in [ep.get("genre"), ep.get("subGenre")]:
            if cat:
                ET.SubElement(p, "category").text = cat
        out_programmes.append(p)
        kept += 1
    if kept:
        ch = ET.Element("channel", {"id":target_id})
        ET.SubElement(ch, "display-name").text = info["name"]
        out_channels[target_id] = ch
        coverage_source[target_id] = {"type":"pluto-api","site_id":site_id}

# Preserve the distinction between real EPG coverage and synthetic fallback.
real_covered = set(coverage_source)
missing = sorted(wanted - real_covered)

# For channels that still have no schedule, add one rolling synthetic
# programme so Flex shows a useful channel label instead of an empty guide.
channels_meta, feeds_meta, language_names = load_iptvorg_metadata()
synthetic_start = now - timedelta(hours=12)
synthetic_stop = now + timedelta(hours=36)

for target_id in missing:
    info = playlist[target_id]

    ch = ET.Element("channel", {"id": target_id})
    ET.SubElement(ch, "display-name").text = info["name"]
    out_channels[target_id] = ch

    p = ET.Element("programme", {
        "channel": target_id,
        "start": dt_to_xmltv(synthetic_start),
        "stop": dt_to_xmltv(synthetic_stop),
    })

    ET.SubElement(p, "title").text = synthetic_title(
        target_id,
        info["name"],
        channels_meta,
        feeds_meta,
        language_names,
    )

    out_programmes.append(p)
    coverage_source[target_id] = {
        "type": "synthetic",
        "source_id": target_id,
    }

covered = set(coverage_source)

root = ET.Element("tv", {
    "generator-info-name":"Chevosky/IPTV curated guide",
    "generator-info-url":"https://github.com/Chevosky/IPTV"
})
for cid in sorted(covered):
    ch = out_channels.get(cid)
    if ch is None:
        ch = ET.Element("channel", {"id":cid})
    else:
        ch = ET.fromstring(ET.tostring(ch, encoding="utf-8"))
        ch.set("id", cid)
    # Flex IPTV can be picky about XMLTV channel-name matching even when ids match.
    # Force the XMLTV display name to be exactly the M3U channel name.
    for dn in list(ch.findall("display-name")):
        ch.remove(dn)
    dn = ET.Element("display-name")
    dn.text = playlist[cid]["name"]
    ch.insert(0, dn)
    root.append(ch)

out_programmes.sort(key=lambda p: (p.get("channel") or "", p.get("start") or ""))
for p in out_programmes:
    root.append(p)

ET.indent(root, space="  ")
ET.ElementTree(root).write(OUT, encoding="utf-8", xml_declaration=True)

report = {
    "playlist_channels": len(wanted),
    "covered_channels": len(real_covered),
    "coverage_percent": round((len(real_covered)/len(wanted)*100) if wanted else 0, 1),
    "guide_channels": len(covered),
    "synthetic_channels": len(missing),
    "programmes": len(out_programmes),
    "output_bytes": OUT.stat().st_size,
    "coverage_sources": coverage_source,
    "missing": [{"tvg_id":cid,"name":playlist[cid]["name"],"url":playlist[cid]["url"]} for cid in missing]
}
REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")

print(f"Playlist channels: {len(wanted)}")
print(f"Real EPG channels: {len(real_covered)} ({report['coverage_percent']}%)")
print(f"Synthetic fallback: {len(missing)}")
print(f"Guide channels: {len(covered)}")
print(f"  forced aliases: {sum(1 for x in coverage_source.values() if x['type']=='forced-alias')}")
print(f"  direct: {sum(1 for x in coverage_source.values() if x['type']=='direct')}")
print(f"  aliases: {sum(1 for x in coverage_source.values() if x['type']=='alias')}")
print(f"  pluto-api: {sum(1 for x in coverage_source.values() if x['type']=='pluto-api')}")
print(f"  synthetic: {sum(1 for x in coverage_source.values() if x['type']=='synthetic')}")
print(f"Programmes: {len(out_programmes)}")
print(f"Output: {OUT.stat().st_size/1024/1024:.2f} MB")
print(f"Missing: {len(missing)}")
