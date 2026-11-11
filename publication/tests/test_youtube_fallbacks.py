from datetime import datetime, timedelta, timezone
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from public_fallbacks import LIFETIME, refresh_youtube, stamp, youtube_source_id
import public_hls
import public_fallbacks

PUBLISHER = "UC" + "p"*22
VIDEO = "abcdefghijk"
NEW = "abcdefghij2"
NOW = datetime(2026, 11, 8, 8, tzinfo=timezone.utc)
URL = "https://live.example.org/news.m3u8"
MASTER = "https://manifest.googlevideo.com/api/manifest/hls_variant/example"


def record(**overrides):
    return {"publisherID": PUBLISHER, "videoID": VIDEO, "url": URL,
            "id": youtube_source_id(PUBLISHER, URL), "observedAt": stamp(NOW),
            "validUntil": stamp(NOW + LIFETIME), **overrides}


def metadata(video, publisher=PUBLISHER):
    return {"channel_id": publisher, "id": video, "live_status": "is_live",
            "formats": [{"protocol": "m3u8_native", "manifest_url": MASTER,
                         "vcodec": "avc1", "acodec": "aac"}]}


class YouTubeFallbackTests(unittest.TestCase):
    def test_new_reviewed_route_enters_publisher_inventory(self):
        initial = {"youtubeAlternatives": [record()]}
        youtube = [{"publisherID": PUBLISHER, "videoID": VIDEO}]
        routes, report = refresh_youtube(initial, {}, youtube, NOW)
        self.assertEqual(routes, [record()])
        self.assertEqual(report["attempted"], 0)

    def test_rotation_requires_new_av_and_advances_expiry(self):
        seed = {"youtubeAlternatives": [record()]}
        youtube = [{"publisherID": PUBLISHER, "videoID": NEW}]
        later = NOW + timedelta(hours=6)
        failed, report = refresh_youtube(seed, seed, youtube, later, fetch=lambda _, v: metadata(v),
                                         probe=lambda *_: False)
        self.assertEqual(failed, [record()])
        self.assertEqual(report["awaitingCurrentVideo"], 1)
        with patch.object(public_fallbacks, "sleep"), patch(
                "public_fallbacks.directos_youtube_anonimos.native_master", wraps=public_fallbacks.directos_youtube_anonimos.native_master) as master:
            passed, report = refresh_youtube(seed, seed, youtube, later, fetch=lambda _, v: metadata(v),
                                             probe=lambda link, candidate: (link, candidate) == (MASTER, URL))
            self.assertEqual(master.call_count, 2)
            outcomes = iter((True, False))
            partial, _ = refresh_youtube(seed, seed, youtube, later, fetch=lambda _, v: metadata(v),
                                         probe=lambda *_: next(outcomes))
            self.assertEqual(partial, [record()])
        self.assertEqual(passed, [record(videoID=NEW, observedAt=stamp(later), validUntil=stamp(later + LIFETIME))])
        self.assertEqual(report, {"inventory": 1, "attempted": 1, "renewed": 1, "awaitingCurrentVideo": 0})
        carried, _ = refresh_youtube(seed, {"youtubeAlternatives": passed}, youtube, later + timedelta(hours=6))
        self.assertEqual(carried, passed)

    def test_wrong_publisher_ended_or_ambiguous_master_keeps_previous(self):
        seed = {"youtubeAlternatives": [record()]}
        youtube = [{"publisherID": PUBLISHER, "videoID": NEW}]
        invalid = (metadata(NEW, "UC"+"x"*22),
                   dict(metadata(NEW), live_status="was_live"),
                   dict(metadata(NEW), formats=metadata(NEW)["formats"]+
                        [dict(metadata(NEW)["formats"][0],manifest_url=MASTER+"2")]))
        for reply in invalid:
            out, r = refresh_youtube(seed, seed, youtube, NOW+timedelta(hours=6),
                                     fetch=lambda *_:reply, probe=lambda *_: self.fail("untrusted master"))
            self.assertEqual(out, [record()])
            self.assertEqual(r["awaitingCurrentVideo"], 1)

    def test_reject_unreviewed_duplicate_and_query_route(self):
        youtube = [{"publisherID": PUBLISHER, "videoID": VIDEO}]
        for seed, prior in (({}, {"youtubeAlternatives": [record()]}),
                            ({"youtubeAlternatives": [record(), record()]}, {}),
                            ({"youtubeAlternatives": [record(url=URL+"?token=a")]}, {})):
            with self.assertRaises(ValueError):
                refresh_youtube(seed, prior, youtube, NOW)

    def test_complete_release_rotates_revalidates_and_preserves_two_history_cycles(self):
        import base64
        import hashlib
        import json
        from tempfile import TemporaryDirectory
        from xml.etree import ElementTree as ET
        from filter_expansion import EPG_HEADER, unpack
        from guide_history import RETAINED
        from test_expansion_refresh import programme
        import refresh_expansion

        base, extra = "Base.ar@SD", "Extra.ar@SD"
        native_publisher = "UC" + "q"*22
        original_hls = "https://example.com/extra.m3u8"
        native_record = {"feed": extra, "primarySourceID": "extra-source",
                         "primarySHA256": hashlib.sha256(original_hls.encode()).hexdigest(),
                         "publisherID": native_publisher, "videoID": "nnnnnnnnnnn",
                         "observedAt": stamp(NOW), "validUntil": stamp(NOW + LIFETIME)}
        canonical = [{"id": "test-base", "tvgId": base, "country": "AR", "name": "Base",
                      "sources": [{"url": "https://example.com/base.m3u8"}]}]
        expansion = [{"id": "test-extra", "tvgID": extra, "name": "Extra", "category": "General",
                      "sources": [{"id": "extra-source", "url": "https://example.com/extra.m3u8"}]}]

        def playlist(feeds):
            return f'#EXTM3U x-tvg-url="{EPG_HEADER}"\n' + "".join(
                f'#EXTINF:-1 tvg-id="{feed}" group-title="General",{feed}\n'
                f'https://example.com/{feed}.m3u8\n' for feed in feeds)

        def video_item(video):
            return {"id": video, "snippet": {"channelId": PUBLISHER, "liveBroadcastContent": "live", "title": "News Live"},
                    "status": {"privacyStatus": "public", "embeddable": True},
                    "liveStreamingDetails": {"actualStartTime": "2026-11-08T14:00:00Z"}}

        def api(endpoint, params):
            video = VIDEO if when == NOW else NEW
            if endpoint == "search":
                return {"items": [{"id": {"videoId": video}, "snippet": video_item(video)["snippet"]}]}
            return {"items": [video_item(video)] if params["id"] == video else []}

        when = NOW

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return when.astimezone(tz)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = root / "publication/epg"
            settings.mkdir(parents=True)
            baseline = ET.Element("tv")
            ET.SubElement(baseline, "channel", id=base)
            baseline.append(programme(base, "20261108133000 +0000", "20261108153000 +0000"))
            base_xml = ET.tostring(baseline, encoding="unicode")
            ET.SubElement(baseline, "channel", id=extra)
            first = programme(extra, "20261108073000 +0000", "20261108093000 +0000", "Morning edition")
            second = programme(extra, "20261108133000 +0000", "20261108153000 +0000", "Afternoon edition")
            third = programme(extra, "20261108193000 +0000", "20261108213000 +0000", "Evening edition")
            payload = {"catalog": canonical, "expansion": expansion, "alternatives": [],
                       "youtube": [{"publisherID": PUBLISHER, "videoID": VIDEO, "number": 1001, "liveTitle": "News Live"}],
                       "youtubeAlternatives": [record()], "nativeYouTubeFallbacks": [native_record],
                       "playlist": playlist((base, extra)),
                       "flexXML": ET.tostring(baseline, encoding="unicode"), "unavailableProgrammeIDs": []}
            raw = json.dumps(payload).encode()
            (settings / "expansion-seed.json").write_text(json.dumps({
                "sha256": hashlib.sha256(raw).hexdigest(), "payload": base64.b64encode(raw).decode()}))
            (settings / "expansion-iptvorg.channels.xml").write_text("<channels/>")
            (root / "catalog-flex.json").write_text(json.dumps({"channels": canonical}))
            (root / "guide-sports-news.xml").write_text("<tv/>")
            m3u, xml = root / "base.m3u", root / "base.xml"
            m3u.write_text(playlist((base,)))
            xml.write_text(base_xml)
            with patch.object(public_fallbacks, "sleep"), patch.object(refresh_expansion, "datetime", Clock), \
                    patch.object(refresh_expansion.subprocess, "check_output", return_value="test-revision"), \
                    patch.object(refresh_expansion.guia_expansion, "programmes_for_expansion") as schedules:

                def release(name, fresh, probe):
                    schedules.return_value = {extra: [fresh]}
                    output = root / name
                    report = refresh_expansion.build(root, m3u, xml, root / "no-batches", output,
                        fetch_official=False, official_snapshot=root, youtube_fetch=api,
                        youtube_public_fetch=lambda kind, v: metadata(native_record["videoID"], v) if kind == "live" else metadata(v),
                        youtube_fallback_probe=probe, native_youtube_fallback_probe=probe)
                    content, _ = unpack(output / "coordinated-generation.json")
                    return output, content, report

                prior = root / "coordinated-generation.json"
                initial, _, _ = release("initial", first, lambda *_: False)
                prior.write_bytes((initial / prior.name).read_bytes())
                when = NOW + timedelta(hours=6)
                unverified, a, report_a = release("unverified", second, lambda *_: False)
                verified, b, report_b = release("verified", second, lambda *_: True)
                self.assertEqual(a["youtube"][0]["videoID"], NEW)
                self.assertEqual(a["youtube"], b["youtube"])
                self.assertEqual(a["youtubeAlternatives"][0]["videoID"], VIDEO)
                self.assertEqual(b["youtubeAlternatives"][0]["videoID"], NEW)
                self.assertEqual([r["youtubeFallbackRefresh"]["renewed"] for r in (report_a, report_b)], [0, 1])
                for name in ("zapper-flex.m3u", "guide-flex.xml", "guide-flex-20261003.xml"):
                    self.assertEqual((unverified / name).read_bytes(), (verified / name).read_bytes())
                self.assertEqual(a["nativeYouTubeFallbacks"], [native_record])
                self.assertEqual(b["nativeYouTubeFallbacks"], [{**native_record, "observedAt": stamp(when),
                                                                "validUntil": stamp(when + LIFETIME)}])
                self.assertEqual([r["nativeYouTubeFallbackRefresh"]["renewed"] for r in (report_a, report_b)], [0, 1])
                self.assertEqual(a["expansion"], b["expansion"])
                self.assertEqual(a["sportsXML"], b["sportsXML"])
                self.assertEqual(report_b["retainedHistoricalProgrammes"], 1)

                prior.write_bytes((verified / prior.name).read_bytes())
                when = NOW + timedelta(hours=12)
                cycle, later, report = release("cycle-3", third, lambda *_: False)
                self.assertEqual(later["youtubeAlternatives"], b["youtubeAlternatives"])
                self.assertEqual(later["nativeYouTubeFallbacks"], b["nativeYouTubeFallbacks"])
                self.assertEqual(report["retainedHistoricalProgrammes"], 2)
                for name in ("guide-flex.xml", "guide-flex-20261003.xml"):
                    rows = ET.parse(cycle / name).getroot().findall(f'programme[@channel="{extra}"]')
                    self.assertEqual({row.findtext("title"): row.get(RETAINED) for row in rows},
                                     {"Morning edition": "1", "Afternoon edition": "1", "Evening edition": None})

    def test_signed_googlevideo_redirect_fails_closed(self):
        response = b"\nZAPPER_HTTP_STATUS\n302\nhttps://example.org/signed"
        with patch.object(public_hls, "public_route", return_value=("manifest.googlevideo.com", 443, "8.8.8.8")), \
                patch.object(public_hls.subprocess, "run", return_value=CompletedProcess([], 0, stdout=response)) as curl:
            with self.assertRaisesRegex(public_hls.ProbeError, "unexpected-youtube-cdn"):
                public_hls.get_youtube(MASTER, 1000, "manifest")
            self.assertEqual(curl.call_count, 1)
            self.assertNotIn(MASTER, curl.call_args.args[0])
            self.assertIn(MASTER.encode(), curl.call_args.kwargs["input"])


if __name__ == "__main__":
    unittest.main()
