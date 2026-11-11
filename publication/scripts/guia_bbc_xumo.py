"""Read only the Xumo US BBC Earth grid bound to its exact distribution feed."""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
import json

from guia_expansion import read_response

FEED = "BBCEarth.uk@US"
PROVIDER = "xumo-us:99951231"
CHANNEL = 99951231
ENDPOINT = "https://valencia-app-mds.xumo.com/v2/epg/10006"
SOURCES = frozenset({
    "https://amg00793-amg00793c6-xumo-us-2669.playouts.now.amagi.tv/BBCStudios-BBCEarthA-hls/playlist.m3u8",
})
OFFSETS = (200, 150, 250, 100, 300, 50, 350, 0, 400, 450)


def channel_page(day, slot, offset, snapshot):
    params = urlencode([("f", "asset.title"), ("f", "asset.descriptions"),
                        ("limit", 50), ("offset", offset)])
    url = f"{ENDPOINT}/{day}/{slot}.json?{params}"
    data = json.loads(read_response(url, snapshot, f"bbc-xumo-exact-{day}-{slot}.json",
                                    maximum=8_000_000))
    exact = [item for item in data["channels"] if item.get("channelId") == CHANNEL]
    return (exact[0]["schedule"], data.get("assets", {}), url) if len(exact) == 1 else None


def rows(now, snapshot):
    first_day = now.astimezone(timezone.utc).date()
    for offset_day in (0, 1):
        day = (first_day + timedelta(days=offset_day)).strftime("%Y%m%d")
        offsets = OFFSETS if snapshot is None else OFFSETS[:1]
        preferred = offsets[0]
        for slot in range(4):
            page = None
            for offset in (preferred, *(value for value in offsets if value != preferred)):
                try:
                    page = channel_page(day, slot, offset, snapshot)
                except (OSError, ValueError, TypeError, KeyError):
                    break
                if page is not None:
                    preferred = offset
                    break
            else:
                break
            if not page:
                continue
            schedule, assets, url = page
            for item in schedule:
                asset = assets.get(str(item.get("assetId")), {})
                title = asset.get("title", "")
                if not isinstance(title, str) or not title.strip() or title.strip().casefold() == "bbc earth":
                    continue
                try:
                    start = datetime.strptime(item["start"], "%Y-%m-%dT%H:%M:%S%z")
                    stop = datetime.strptime(item["end"], "%Y-%m-%dT%H:%M:%S%z")
                except (KeyError, ValueError, TypeError):
                    continue
                if any(value.tzinfo is None or value.utcoffset() != timedelta() for value in (start, stop)):
                    continue
                description = asset.get("descriptions") or {}
                synopsis = description.get("medium") or description.get("small") or description.get("tiny")
                yield start, stop, title, asset.get("episodeTitle"), synopsis, url
