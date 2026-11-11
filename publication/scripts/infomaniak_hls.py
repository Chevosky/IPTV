"""Revalidate the exact public Infomaniak livecast used by Africa24 Sport."""
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import json
from urllib.parse import parse_qsl, urlsplit

import public_hls as hls

FEED = "Africa24Sport.fr@SD"
AUTHORITY = "infomaniak-africa24sport"
LIVE = "https://africa24tv.com/africa24-sport-en-direct/"
PLAYER = "https://player.infomaniak.com"
CHANNEL_ID = 72188
PLAYER_ID = 11778
STREAM = "africa24sport"
TITLE = "AFRICA 24 SPORT"
PATH = f"/livecast/ik:{STREAM}/manifest.m3u8"
PRIMARY = "https://africa24.vedge.infomaniak.com" + PATH
ALTERNATIVE = "https://africa24sport.vedge.infomaniak.com" + PATH


class LivePlayer(HTMLParser):
    found = False

    def handle_starttag(self, tag, attributes):
        if tag != "iframe":
            return
        url = urlsplit(dict(attributes).get("src") or "")
        identity = sorted(parse_qsl(url.query, keep_blank_values=True))
        if (url.scheme, url.netloc, url.path, url.fragment) == (
                "https", "player.infomaniak.com", "/", ""):
            self.found |= identity == [("channel", str(CHANNEL_ID)), ("player", str(PLAYER_ID))]


def reviewed(feed, authority, candidate):
    return (feed, authority, candidate) == (FEED, AUTHORITY, ALTERNATIVE)


def declares_route(body):
    if not isinstance(body, dict) or body.get("result") != "success":
        return False
    player = body.get("data")
    if not isinstance(player, dict) or player.get("player_id") != PLAYER_ID or player.get("title") != TITLE:
        return False
    channel = player.get("channel")
    rows = player.get("integrations")
    if (not isinstance(channel, dict) or channel.get("channel_id") != CHANNEL_ID
            or channel.get("name") != TITLE or not isinstance(rows, list)):
        return False
    routes = [row for row in rows if isinstance(row, dict)]
    return (any(row.get("type") == "hls" and row.get("constant_url") is True
                and row.get("url") == ALTERNATIVE for row in routes)
            and any(row.get("type") == "rtmp" and row.get("stream_id") == STREAM for row in routes))


def matches(primary, candidate):
    if (primary, candidate) != (PRIMARY, ALTERNATIVE):
        return False
    _, page = hls.get(LIVE, cap=500_000, stage="publisher-page", parent=LIVE)
    embed = LivePlayer()
    embed.feed(page.decode("utf-8", errors="replace"))
    if not embed.found:
        return False
    endpoint = f"{PLAYER}/playerConfig.php?channel={CHANNEL_ID}&player={PLAYER_ID}"
    _, body = hls.get(endpoint, cap=500_000, stage="publisher-player", parent=endpoint)
    if not declares_route(json.loads(body)):
        return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        samples = [pool.submit(hls.sample, url, label, segments=1)
                   for url, label in ((primary, "catalog"), (candidate, "publisher"))]
        return all(sample.result()[0]["status"] == "decoded-av" for sample in samples)
