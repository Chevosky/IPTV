"""Revalidate the broadcaster-declared two QTV1 routes independently of their delivery delays."""
from dataclasses import dataclass
import json

import public_hls as hls

FEED = "QatarTelevision.qa@SD"
AUTHORITY = "tabie-qtv1"
PRIMARY = "https://live.kwikmotion.com/qtv1live/qtv1.smil/playlist.m3u8"
ALTERNATIVE = "https://qatartv.akamaized.net/hls/live/20000609/qtv1/master.m3u8"


@dataclass(frozen=True)
class Binding:
    feed: str
    authority: str
    provider_id: int
    provider_name: str
    primary: str
    alternate: str


BINDINGS = (
    Binding(FEED, AUTHORITY, 23, "QTV 1", PRIMARY, ALTERNATIVE),
    Binding('AlArabyTV2.qa@SD', 'tabie-araby2', 3, 'Arabic_TV2', 'https://live.kwikmotion.com/alaraby2live/alaraby2.smil/playlist.m3u8', 'https://live.kwikmotion.com/alaraby2live/alaraby2.smil/alaraby2publish/alaraby2_source/chunks.m3u8'),
    Binding('AlArabyTV.qa@SD', 'tabie-araby1', 4, 'Arabic_TV1', 'https://live.kwikmotion.com/alaraby1live/alaraby_abr/playlist.m3u8', 'https://live.kwikmotion.com/alaraby1live/alaraby_abr/alaraby1publish/alaraby1_source/chunks.m3u8'),

)


def reviewed(feed, authority, alternate):
    return next((rule for rule in BINDINGS if (rule.feed, rule.authority, rule.alternate)
                == (feed, authority, alternate)), None)


def binding(primary, candidate):
    return next((rule for rule in BINDINGS if (rule.primary, rule.alternate) == (primary, candidate)), None)


def declares_routes(body, primary, candidate):
    rule = binding(primary, candidate)
    return (rule is not None and isinstance(body, dict) and body.get("id") == rule.provider_id
            and body.get("success") is True and body.get("isRadio") is False
            and body.get("name") == rule.provider_name
            and {body.get("streamUrl"), body.get("backupStreamUrl")} == {primary, candidate})


def matches(primary, candidate):
    rule = binding(primary, candidate)
    if rule is None:
        return False
    endpoint = f"https://tabie.net/Live/GetChannelPlaybackData?channelId={rule.provider_id}"
    _, response = hls.get(endpoint, cap=500_000, stage="official-channel", parent=endpoint)
    if not declares_routes(json.loads(response), primary, candidate):
        return False
    return hls.both_decode_av(primary, candidate)
