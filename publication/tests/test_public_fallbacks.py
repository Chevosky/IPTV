from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
from unittest.mock import patch
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import public_fallbacks as fallback
import public_hls
import infomaniak_hls as infomaniak


class PublicFallbackTests(unittest.TestCase):
    def setUp(self):
        self.then = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.now = self.then + timedelta(hours=6)
        self.primary = "https://example.com/primary.m3u8"
        self.secondary = "https://example.org/fallback.m3u8"
        self.proof = {"checkedAt": fallback.stamp(self.then),
                      "validUntil": fallback.stamp(self.then + fallback.LIFETIME),
                      "audioDecoded": True, "videoDecoded": True, "method": "ffmpeg"}
        self.route = {"id": fallback.source_id(self.secondary), "url": self.secondary,
                      "headers": {}, "verification": self.proof}
        self.original = {"id": "original", "url": self.primary}
        self.row = {"tvgID": "demo", "sources": [self.original, self.route], "name": "Demo"}
        self.prior = {"expansion": [{**self.row, "sources": [self.original]}], "alternatives": []}
        self.seed = {"expansion": [self.row], "alternatives": []}

    def refresh(self, seed, prior, probe=None):
        return fallback.refresh(seed, prior, self.now, {"demo": self.primary}, probe=probe)

    def test_appends_review_and_renews_only_on_matching_sample(self):
        rows, _, count = self.refresh(self.seed, self.prior, lambda x, y: (x, y) == (self.primary, self.secondary))
        self.assertEqual(count, {"inventory": 1, "attempted": 1, "renewed": 1})
        self.assertEqual(rows[0]["sources"][0], self.original)
        self.assertEqual(rows[0]["sources"][1]["verification"]["validUntil"],
                         fallback.stamp(self.now + fallback.LIFETIME))
        self.assertEqual(self.route["verification"]["checkedAt"], fallback.stamp(self.then))

    def test_inconclusive_and_offline_preserve_last_attestation(self):
        renewed, _, _ = self.refresh(self.seed, self.prior, lambda *_: True)
        prior = {"expansion": renewed, "alternatives": []}
        for probe in (None, lambda *_: False):
            with self.subTest(probe=probe):
                rows, _, count = self.refresh(self.seed, prior, probe)
                self.assertEqual(rows, renewed)
                self.assertEqual(count["renewed"], 0)

    def test_infomaniak_requires_exact_broadcaster_identity_and_both_decodes(self):
        i = infomaniak
        iframe = f'<iframe src="{i.PLAYER}/?player={i.PLAYER_ID}&amp;channel={i.CHANNEL_ID}"></iframe>'
        body = {"result": "success", "data": {"player_id": i.PLAYER_ID, "title": i.TITLE,
                "channel": {"channel_id": i.CHANNEL_ID, "name": i.TITLE},
                "integrations": [{"type": "hls", "constant_url": True, "url": i.ALTERNATIVE},
                                 {"type": "rtmp", "stream_id": i.STREAM}]}}
        def download(locator, **kwargs):
            self.assertEqual(locator, kwargs["parent"])
            if locator == i.LIVE:
                return locator, iframe.encode()
            return locator, json.dumps(body).encode()

        with patch.object(i.hls, "get", side_effect=download), patch.object(
                i.hls, "sample", return_value=({"status": "decoded-av"}, [], [])) as sample:
            self.assertTrue(i.matches(i.PRIMARY, i.ALTERNATIVE))
            self.assertEqual(sample.call_count, 2)
            self.assertTrue(all(call.kwargs["segments"] == 1 for call in sample.call_args_list))
            sample.return_value = ({"status": "incomplete-media"}, [], [])
            self.assertFalse(i.matches(i.PRIMARY, i.ALTERNATIVE))
            sample.reset_mock()
            body["data"]["channel"]["channel_id"] += 1
            self.assertFalse(i.matches(i.PRIMARY, i.ALTERNATIVE))
            self.assertFalse(i.matches(i.PRIMARY + "?mismatch=1", i.ALTERNATIVE))
            body["data"]["channel"]["channel_id"] = i.CHANNEL_ID
            iframe = iframe.replace(i.PLAYER, "https://invalid.example")
            self.assertFalse(i.matches(i.PRIMARY, i.ALTERNATIVE))
            sample.assert_not_called()

    def test_infomaniak_alternate_renews_without_removing_original_or_extending_failures(self):
        i = infomaniak
        reviewed = {"id": fallback.source_id(i.ALTERNATIVE), "url": i.ALTERNATIVE,
                    "verification": {**self.proof, "identityAuthority": i.AUTHORITY}}
        original = {"id": "original", "url": i.PRIMARY}
        feed = {"tvgID": i.FEED, "sources": [original]}
        prior = {"expansion": [feed], "alternatives": []}
        seed = {"expansion": [{**feed, "sources": [original, reviewed]}], "alternatives": []}
        rows, _, report = fallback.refresh(
            seed, prior, self.now, {i.FEED: i.PRIMARY},
            infomaniak_probe=lambda a, b: (a, b) == (i.PRIMARY, i.ALTERNATIVE))
        self.assertEqual(report, {"inventory": 1, "attempted": 1, "renewed": 1})
        self.assertEqual(rows[0]["sources"][0], original)
        previous = {"expansion": rows, "alternatives": []}
        for probe in (None, lambda *_: False):
            result, _, report = fallback.refresh(
                seed, previous, self.now + timedelta(hours=6), {i.FEED: i.PRIMARY}, infomaniak_probe=probe)
            self.assertEqual(result, rows)
            self.assertEqual(report["renewed"], 0)
        wrong_feed = deepcopy(seed)
        wrong_feed["expansion"][0]["tvgID"] = "another-edition"
        with self.assertRaises(ValueError):
            fallback.refresh(wrong_feed, {"expansion": [], "alternatives": []}, self.now, {})

    def test_rejects_locator_tamper_and_removed_previous_source(self):
        renewed, _, _ = self.refresh(self.seed, self.prior, lambda *_: True)
        prior = {"expansion": renewed, "alternatives": []}
        for field, value in (("url", "https://example.net/other.m3u8"), ("headers", {"Referer": "private"})):
            invalid = deepcopy(prior)
            invalid["expansion"][0]["sources"][1][field] = value
            with self.assertRaises(ValueError):
                self.refresh(self.seed, invalid)
        with self.assertRaises(ValueError):
            self.refresh(self.prior, prior)

    def test_core_uses_no_invented_startup_timing(self):
        route = {"id": self.route["id"], "url": self.secondary, "feed": "demo", "headers": {},
                 "observedAt": self.proof["checkedAt"], "validUntil": self.proof["validUntil"]}
        seed = {"expansion": [], "alternatives": [route]}
        _, alternatives, count = self.refresh(seed, {"expansion": [], "alternatives": []}, lambda *_: True)
        self.assertNotIn("typicalStartupMs", alternatives[0])
        self.assertEqual(alternatives[0]["observedAt"], fallback.stamp(self.now))
        self.assertEqual(count["renewed"], 1)

    def test_reviewed_core_recovery_selects_only_while_attested(self):
        identity = "test-editorial-recovery"
        languages = frozenset({"por"})
        expected = tuple(hashlib.sha256(url.encode()).hexdigest() for url in (self.primary, self.secondary))
        original = {"demo": f'#EXTINF:-1 tvg-id="demo",Demo\n{self.primary}\n'}
        primary_by_feed = {"demo": self.primary}
        route = {"id": fallback.source_id(self.secondary), "url": self.secondary, "feed": "demo",
                 "headers": {}, "observedAt": self.proof["checkedAt"], "validUntil": self.proof["validUntil"],
                 "identityAuthority": identity, "preferInPlaylist": True}
        seed = {"expansion": [], "alternatives": [route]}
        before = {"expansion": [], "alternatives": []}
        with patch.dict(public_hls.REVIEWED_RECOVERIES, {("demo", identity): (*expected, languages)}):
            _, renewed, summary = fallback.refresh(
                seed, before, self.now, primary_by_feed,
                recovery_probe=lambda url, tags: (url, tags) == (self.secondary, languages))
            self.assertEqual(summary["renewed"], 1)
            self.assertEqual(renewed[0]["observedAt"], fallback.stamp(self.now))
            chosen = fallback.project_reviewed_recoveries(original, renewed, primary_by_feed, self.now)
            self.assertEqual(chosen["demo"], original["demo"].replace(self.primary, self.secondary))
            expired = self.now + fallback.LIFETIME
            previous = {"expansion": [], "alternatives": renewed}
            _, stale, summary = fallback.refresh(seed, previous, expired, primary_by_feed, recovery_probe=lambda *_: False)
            self.assertEqual(stale, renewed)
            self.assertEqual(summary["renewed"], 0)
            self.assertEqual(fallback.project_reviewed_recoveries(original, stale, primary_by_feed, expired), original)
            with self.assertRaises(ValueError):
                fallback.refresh(seed, before, self.now, {"demo": "https://example.com/changed.m3u8"})

    def test_no_private_destinations_or_public_url_credentials(self):
        for address in ("127.0.0.1", "169.254.169.254", "10.1.1.1", "224.0.0.1", "240.0.0.1"):
            self.assertFalse(public_hls.is_public(address))
        for url in ("https://a:b@example.org/path", "https://example.org/a?sig=s", "http://example.org/a#x"):
            self.assertFalse(fallback.public_url(url))


if __name__ == "__main__":
    unittest.main()
