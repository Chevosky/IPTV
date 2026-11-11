from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import hashlib
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from public_fallbacks import refresh_native_youtube, stamp, LIFETIME

NOW = datetime(2026, 11, 8, 8, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=6)
FEED = "Channel.es@SD"
URL = "https://live.example.org/edition.m3u8"
PUB = "UC" + "p" * 22
VIDEO = "abcdefghijk"
ROTATED = "abcdefghij2"
MASTER = "https://manifest.googlevideo.com/api/manifest/hls_variant/example"
EXPANSION = [{"tvgID": FEED, "sources": [{"id": "original", "url": URL}]}]


def record(**overrides):
    return {"feed": FEED, "primarySourceID": "original", "primarySHA256": hashlib.sha256(URL.encode()).hexdigest(),
            "publisherID": PUB, "videoID": VIDEO, "observedAt": stamp(NOW),
            "validUntil": stamp(NOW + LIFETIME), **overrides}


def fixture(video=VIDEO, **overrides):
    return {"id": video, "channel_id": PUB, "live_status": "is_live",
            "formats": [{"protocol": "m3u8_native", "manifest_url": MASTER,
                         "vcodec": "avc1", "acodec": "aac"}], **overrides}


def envelope(*records):
    return {"nativeYouTubeFallbacks": list(records)}


class NativeYouTubeFallbackTests(unittest.TestCase):
    def test_repeat_renews_only_a_verified_live_and_carries_without_refresh(self):
        self.assertEqual(refresh_native_youtube(envelope(record()), {}, EXPANSION, NOW)[0], [record()])
        def fetch(kind, publisher):
            self.assertEqual((kind, publisher), ("live", PUB))
            return fixture()
        result, report = refresh_native_youtube(envelope(record()), {}, EXPANSION, LATER,
            fetch=fetch, probe=lambda master, url: (master, url) == (MASTER, URL))
        self.assertEqual(result, [record(observedAt=stamp(LATER), validUntil=stamp(LATER + LIFETIME))])
        self.assertEqual(report, {"inventory": 1, "attempted": 1, "renewed": 1})
        self.assertEqual(refresh_native_youtube(envelope(record()), envelope(*result), EXPANSION, LATER)[0], result)

    def test_rotation_requires_two_distinct_windows_for_the_same_new_video(self):
        for second, result in ((fixture(ROTATED), ROTATED), (fixture(VIDEO), VIDEO), (None, VIDEO)):
            checks = iter((fixture(ROTATED), second))
            with patch("public_fallbacks.sleep") as delay:
                rows, _ = refresh_native_youtube(envelope(record()), {}, EXPANSION, LATER,
                    fetch=lambda *_: next(checks), probe=lambda *_: True)
                delay.assert_called_once_with(90)
            self.assertEqual(rows[0]["videoID"], result)
            self.assertEqual(rows[0]["observedAt"], stamp(LATER if result == ROTATED else NOW))

    def test_timeout_or_wrong_identity_never_renews(self):
        for invalid in (None, fixture(channel_id="UC" + "q" * 22), fixture(live_status="was_live"),
                        fixture(formats=[])):
            rows, report = refresh_native_youtube(envelope(record()), {}, EXPANSION, LATER,
                fetch=lambda *_: invalid, probe=lambda *_: self.fail("unverified media"))
            self.assertEqual(rows, [record()])
            self.assertEqual(report["renewed"], 0)
        rows, report = refresh_native_youtube(envelope(record()), {}, EXPANSION, LATER,
            fetch=lambda *_: fixture(), probe=lambda *_: False)
        self.assertEqual(rows, [record()])
        self.assertEqual(report["renewed"], 0)

    def test_reject_wrong_feed_primary_hash_duplicate_or_unreviewed_record(self):
        for seed, prior in ((envelope(record(feed="Wrong.es@SD")), {}), (envelope(record(primarySHA256="0"*64)), {}),
                            (envelope(record(), record()), {}), ({}, envelope(record())),
                            (envelope(record()), envelope(record(publisherID="UC"+"q"*22)))):
            with self.assertRaises(ValueError):
                refresh_native_youtube(seed, prior, EXPANSION, NOW)
