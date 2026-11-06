import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from channel_policy import excluded, load_policy, active_channels, filter_playlist
import json

ROOT = Path(__file__).resolve().parents[2]


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy(ROOT)

    def test_current_variants(self):
        catalog = json.loads((ROOT / "catalog-flex.json").read_text())
        active = active_channels(catalog, self.policy)
        self.assertEqual(len(catalog["channels"])-len(active),11)
        self.assertEqual(len(active),226)
        self.assertTrue(all(not excluded(c["name"],c["tvgId"],self.policy) for c in active))

    def test_future_variant(self):
        for name in ["Dating Naked Argentina", "Paradise Hotel: Nueva temporada", "DATING-NAKED UK"]:
            self.assertTrue(excluded(name,"future-id",self.policy))

    def test_known_id_stays_excluded_if_renamed(self):
        self.assertTrue(excluded("New upstream label",self.policy["excludedTvgIDs"][0],self.policy))

    def test_other_channels_unchanged(self):
        for name in ["Telefe", "History", "Discovery", "ESPN 1", "The Pet Collective"]:
            self.assertFalse(excluded(name,"unrelated-id",self.policy))

    def test_export_keeps_unrelated_bytes_and_removes_entire_excluded_blocks(self):
        keep='#EXTINF:-1 tvg-id="other",ESPN 1\n#EXTVLCOPT:http-referrer=https://example.org/\nhttps://example.org/sports.m3u8\n'
        remove='#EXTINF:-1 tvg-id="future",Paradise Hotel Argentina\n#EXTVLCOPT:http-referrer=https://example.org/\nhttps://example.org/excluded.m3u8\n'
        self.assertEqual(filter_playlist('#EXTM3U\n'+keep+remove,self.policy),'#EXTM3U\n'+keep)


if __name__ == "__main__":
    unittest.main()
