from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

import guia_bbc_xumo as guide
from guia_expansion import normalized_programmes


class BBCXumoTests(TestCase):
    now = datetime(2026, 11, 8, 10, 3, tzinfo=timezone.utc)

    def fixture(self, directory, entries, assets):
        payload = {"channels": entries, "assets": assets}
        (directory / "bbc-xumo-exact-20261108-1.json").write_text(json.dumps(payload))

    def test_exact_id_and_valid_timezone(self):
        slot = {"assetId": "one", "start": "2026-11-08T10:01:00+0000", "end": "2026-11-08T10:58:00+0000"}
        exact = {"channelId": guide.CHANNEL, "schedule": [slot, {**slot, "assetId": "missing"},
            {**slot, "start": "2026-11-08T10:01:00+0100"}, {**slot, "end": "broken"}]}
        foreign = {"channelId": 99991174, "schedule": [{**slot, "assetId": "foreign"}]}
        assets = {"one": {"title": "Wild Africa", "episodeTitle": "Deserts",
                          "descriptions": {"medium": "Arid landscapes"}},
                  "foreign": {"title": "Best Pets of the Month"}}
        with TemporaryDirectory() as tmp:
            snapshot = Path(tmp)
            self.fixture(snapshot, [exact, foreign], assets)
            result = normalized_programmes(guide.FEED, guide.PROVIDER, guide.rows(self.now, snapshot), self.now)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].get("start"), "20261108100100 +0000")
            self.assertEqual(result[0].findtext("sub-title"), "Deserts")
            self.fixture(snapshot, [foreign], assets)
            self.assertEqual(list(guide.rows(self.now, snapshot)), [])

    def test_searches_offset_by_channel_identity_and_reuses_it(self):
        seen = []

        def find(day, slot, offset, snapshot):
            if day != "20261108":
                raise OSError("offline")
            seen.append((slot, offset))
            return ([], {}, guide.ENDPOINT) if offset == 250 else None

        with mock.patch.object(guide, "channel_page", side_effect=find):
            self.assertEqual(list(guide.rows(self.now, None)), [])
        self.assertEqual(seen, [(0, 200), (0, 150), (0, 250), (1, 250), (2, 250), (3, 250)])
