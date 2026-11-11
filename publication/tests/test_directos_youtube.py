import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from directos_youtube import MAX_SEARCHES, refresh
from directos_youtube_anonimos import MAX_TAB_ENTRIES, refresh as refresh_public

NOW = datetime(2026, 11, 7, 23, 40, tzinfo=timezone.utc)
ABC = "UCBi2mrWuNuyYy4gbM6fU18Q"
OLD = "fcQXGWwEbMo"
NEW = "KQhNZQ_Uag8"
LIVE_TITLE = "LIVE: ABC News Live - 24/7 news, context and analysis"


def row(number=1, publisher=ABC, video=OLD, title="LIVE: ABC News Live - Saturday, November 7", **extra):
    return {"publisherID": publisher, "number": number, "videoID": video, "name": f"Channel {number}",
            "liveTitle": title, "verifiedAt": "2026-11-07T17:24:00Z", "languages": ["eng"],
            "category": "01 - News", **extra}


def video_item(video, publisher, title, *, is_live=True, public=True, embeddable=True):
    return {"id": video,
            "snippet": {"channelId": publisher, "title": title,
                        "liveBroadcastContent": "live" if is_live else "none"},
            "status": {"privacyStatus": "public" if public else "unlisted", "embeddable": embeddable},
            "liveStreamingDetails": {"actualStartTime": "2026-11-07T20:00:00Z",
                                     **({} if is_live else {"actualEndTime": "2026-11-07T23:00:00Z"})}}


def search_item(video, publisher, title):
    return {"id": {"videoId": video}, "snippet": {"channelId": publisher, "title": title}}


class YouTubeRefreshTests(unittest.TestCase):
    def test_same_feed_rotation_preserves_identity_and_fields(self):
        source = row()
        other = row(2, "UC" + "x" * 22, "AAAAAAAAAAA", "Different station")
        calls = []

        def fetch(endpoint, params):
            calls.append((endpoint, params))
            if endpoint == "search":
                self.assertEqual(params["channelId"], ABC)
                self.assertEqual(params["eventType"], "live")
                return {"items": [search_item(NEW, ABC, LIVE_TITLE),
                                  search_item("BBBBBBBBBBB", ABC, "Breaking news special")]}
            items = {OLD: video_item(OLD, ABC, source["liveTitle"], is_live=False),
                     NEW: video_item(NEW, ABC, LIVE_TITLE),
                     other["videoID"]: video_item(other["videoID"], other["publisherID"], other["liveTitle"])}
            return {"items": [items[key] for key in params["id"].split(",") if key in items]}

        result, report = refresh([other, source], fetch, NOW)
        self.assertEqual([x["publisherID"] for x in result], [other["publisherID"], ABC])
        self.assertEqual(result[0], other)
        self.assertEqual(result[1]["videoID"], NEW)
        self.assertEqual(result[1]["liveTitle"], LIVE_TITLE)
        for key in ("publisherID", "number", "name", "languages", "category"):
            self.assertEqual(result[1][key], source[key])
        self.assertEqual(report, {"status": "ok", "stillLive": 1, "searches": 1, "rotated": 1})
        self.assertEqual([x[0] for x in calls], ["videos", "search", "videos"])

    def test_replacement_must_be_unique_public_current_and_same_publisher(self):
        source = row()
        rival = "UC" + "y" * 22
        other_video = "BBBBBBBBBBB"

        def simulate(first, second=None, paginated=False):
            def fetch(endpoint, params):
                if endpoint == "search":
                    return {"items": [search_item(NEW, ABC, LIVE_TITLE), search_item(other_video, ABC, LIVE_TITLE)],
                            **({"nextPageToken": "next"} if paginated else {})}
                if params["id"] == OLD:
                    return {"items": []}
                return {"items": [x for x in (first, second) if x]}
            result, status = refresh([source], fetch, NOW)
            self.assertEqual(result[0]["videoID"], OLD)
            self.assertEqual({k: v for k, v in result[0].items() if k != "lastSearchAt"}, source)
            self.assertEqual(status["priorLocatorRetained"], 1)

        simulate(video_item(NEW, ABC, LIVE_TITLE), video_item(other_video, ABC, LIVE_TITLE))
        simulate(video_item(NEW, rival, LIVE_TITLE))
        simulate(video_item(NEW, ABC, LIVE_TITLE, is_live=False))
        simulate(video_item(NEW, ABC, LIVE_TITLE, public=False))
        simulate(video_item(NEW, ABC, LIVE_TITLE, embeddable=False))
        simulate(video_item(NEW, ABC, LIVE_TITLE), paginated=True)

    def test_provider_and_search_outages_preserve_the_exact_previous_entries(self):
        entries = [row(), row(2, "UC" + "x" * 22, "AAAAAAAAAAA", "Another station", available=False)]

        def provider_outage(*_):
            raise OSError("temporary")

        def search_outage(endpoint, _):
            if endpoint == "search":
                raise OSError("temporary")
            return {"items": []}

        self.assertEqual(refresh(entries, provider_outage, NOW), (entries, {"status": "provider-unavailable"}))
        result, report = refresh(entries, search_outage, NOW)
        self.assertEqual(result, entries)
        self.assertEqual(report, {"status": "search-unavailable", "searches": 1, "priorLocatorRetained": 2})

    def test_limits_searches_and_honors_cooldown(self):
        entries = [row(i, f"UC{i:022}", f"{i:011}", "Station", **(
            {"lastSearchAt": "2026-11-07T22:40:00Z"} if i == 0 else {}))
                   for i in range(MAX_SEARCHES + 3)]
        requests = []

        def fetch(endpoint, params):
            if endpoint == "search":
                requests.append(params["channelId"])
            return {"items": []}

        result, report = refresh(entries, fetch, NOW)
        self.assertEqual(len(requests), MAX_SEARCHES)
        self.assertNotIn(entries[0]["publisherID"], requests)
        self.assertEqual(result[0], entries[0])
        self.assertEqual([r["videoID"] for r in result], [r["videoID"] for r in entries])
        self.assertEqual(report["priorLocatorRetained"], len(entries))

    def test_public_resolver_rotates_unique_feed_with_native_media(self):
        source = row()
        formats = [{"protocol": "m3u8_native",
                    "manifest_url": "https://test.googlevideo.com/api/manifest/hls_variant/fixture",
                    "acodec": "aac", "vcodec": "avc1"}]
        selected = {"id": NEW, "channel_id": ABC, "live_status": "is_live",
                    "title": LIVE_TITLE + " 2026-11-07 20:51", "formats": formats}
        live = {"id": NEW, "live_status": "is_live", "title": LIVE_TITLE}
        ended = {"id": OLD, "live_status": "was_live", "title": source["liveTitle"]}
        requests = []

        def fetch(kind, identifier):
            requests.append((kind, identifier))
            if kind == "video":
                return {"id": OLD, "channel_id": ABC, "live_status": "was_live"}
            if kind == "live":
                return selected
            return {"channel_id": ABC, "entries": [live, ended]}

        result, report = refresh_public([source], fetch, NOW)
        self.assertEqual(requests, [("video", OLD), ("live", ABC), ("streams", ABC)])
        self.assertEqual(result[0]["videoID"], NEW)
        self.assertEqual(result[0]["liveTitle"], LIVE_TITLE)
        self.assertEqual(result[0]["publisherID"], ABC)
        self.assertEqual(report, {"status": "ok", "method": "yt-dlp", "searches": 1, "rotated": 1})

    def test_public_resolver_keeps_prior_on_error_unavailable_or_ambiguous(self):
        source = row()
        formats = [{"protocol": "m3u8_native",
                    "manifest_url": "https://test.googlevideo.com/api/manifest/hls_variant/fixture",
                    "acodec": "aac", "vcodec": "avc1"}]
        valid = {"id": NEW, "channel_id": ABC, "live_status": "is_live", "title": LIVE_TITLE, "formats": formats}
        live = {"id": NEW, "live_status": "is_live", "title": LIVE_TITLE}
        second = {**live, "id": "BBBBBBBBBBB"}
        archive = {"id": OLD, "live_status": "was_live", "title": source["liveTitle"]}
        ended = {"id": OLD, "channel_id": ABC, "live_status": "was_live"}

        for old, selected, streams in (
            (None, valid, {"channel_id": ABC, "entries": [live]}),
            (ended, None, None),
            (ended, {**valid, "channel_id": "UC" + "x" * 22}, None),
            (ended, {**valid, "formats": []}, None),
            (ended, {**valid, "formats": [{"protocol": 1, "manifest_url": "https://test.example/"}]}, None),
            (ended, {**valid, "title": "Different live special"}, {"channel_id": ABC, "entries": [live]}),
            (ended, valid, None),
            (ended, valid, {"channel_id": "UC" + "x" * 22, "entries": [live]}),
            (ended, valid, {"channel_id": ABC, "entries": [live, second]}),
            (ended, valid, {"channel_id": ABC, "entries": [live, {**second, "title": None}]}),
            (ended, valid, {"channel_id": ABC, "entries": [live, {**second, "id": "bad-id"}]}),
            (ended, valid, {"channel_id": ABC, "entries": [live, {**second, "channel_id": "UC" + "x" * 22}]}),
            (ended, valid, {"channel_id": ABC, "entries": [live, archive] + [second] * MAX_TAB_ENTRIES}),
        ):
            with self.subTest(old=old, selected=bool(selected), streams=bool(streams)):
                result, report = refresh_public([source],
                    lambda kind, _: {"video": old, "live": selected, "streams": streams}[kind], NOW)
                self.assertEqual(result, [source])
                self.assertNotIn("rotated", report)


if __name__ == "__main__":
    unittest.main()
