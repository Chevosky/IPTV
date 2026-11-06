import sys
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from epg_freshness import prefer_incoming


class FreshnessTests(unittest.TestCase):
    now = datetime(2026, 11, 6, 14, 0, tzinfo=timezone.utc)

    def programme(self, start, stop):
        return [ET.Element("programme", {"channel": "exact-region", "start": start, "stop": stop})]

    def test_expired_exact_does_not_replace_current_consolidated(self):
        self.assertFalse(prefer_incoming(self.programme("20261106130000 +0000", "20261106150000 +0000"), self.programme("20261105000000 +0000", "20261106060000 +0000"), self.now))

    def test_current_exact_recovers_expired_consolidated(self):
        self.assertTrue(prefer_incoming(self.programme("20261105000000 +0000", "20261106060000 +0000"), self.programme("20261106130000 +0000", "20261106150000 +0000"), self.now))

    def test_exact_priority_preserved_when_both_current(self):
        current = self.programme("20261106130000 +0000", "20261106150000 +0000")
        self.assertTrue(prefer_incoming(current, current, self.now))

    def test_timezones_are_compared_as_instants(self):
        current = self.programme("20261106100000 -0300", "20261106120000 -0300")
        expired = self.programme("20261106100000 +0000", "20261106120000 +0000")
        self.assertFalse(prefer_incoming(current, expired, self.now))

    def test_future_schedule_not_replaced_by_expired_schedule(self):
        future = self.programme("20261106160000 +0000", "20261106170000 +0000")
        old = self.programme("20261105000000 +0000", "20261106060000 +0000")
        self.assertFalse(prefer_incoming(future, old, self.now))

    def test_invalid_programmes_cannot_replace_current(self):
        current = self.programme("20261106130000 +0000", "20261106150000 +0000")
        self.assertFalse(prefer_incoming(current, self.programme("invalid", "invalid"), self.now))

    def test_no_artificial_time_shift(self):
        incoming = self.programme("20261106100000 -0300", "20261106120000 -0300")
        original = ET.tostring(incoming[0])
        prefer_incoming([], incoming, self.now)
        self.assertEqual(ET.tostring(incoming[0]), original)


if __name__ == "__main__":
    unittest.main()
