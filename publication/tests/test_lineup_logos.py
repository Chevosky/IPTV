from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from order_lineup import with_missing_logo


class LineupLogoTests(unittest.TestCase):
    def setUp(self):
        self.missing = '#EXTINF:-1 tvg-id="test" group-title="General",A, B'
        self.empty = '#EXTINF:-1 tvg-id="test" tvg-logo="" group-title="General",A, B'
        self.logo = "https://example.org/channel.png?width=120&height=40"

    def test_inserts_or_fills_without_changing_other_fields(self):
        expected = self.empty.replace('tvg-logo=""', f'tvg-logo="{self.logo}"')
        self.assertEqual(with_missing_logo(self.missing, self.logo), expected)
        self.assertEqual(with_missing_logo(self.empty, self.logo), expected)

    def test_preserves_existing_even_if_audited_alternative_exists(self):
        existing = self.empty.replace('tvg-logo=""', 'tvg-logo="http://existing.example/old.png"')
        self.assertEqual(with_missing_logo(existing, self.logo), existing)

    def test_rejects_insecure_or_malformed_url(self):
        for candidate in ("http://example.org/channel.png", "https://user@host/channel.png",
                          "https://example.org:8000/a.png", "https://example.org/a.png#part",
                          'https://example.org/a.png" group-title="oops', "https://"):
            self.assertEqual(with_missing_logo(self.empty, candidate), self.empty)

    def test_replaces_only_exact_reviewed_historical_url(self):
        historical = "https://old.example.org/channel.png"
        existing = self.empty.replace('tvg-logo=""', f'tvg-logo="{historical}"')
        expected = self.empty.replace('tvg-logo=""', f'tvg-logo="{self.logo}"')
        self.assertEqual(with_missing_logo(existing, self.logo, replaces=historical), expected)
        self.assertEqual(with_missing_logo(existing, self.logo, replaces=historical + "?changed"), existing)
