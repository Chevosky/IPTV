import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from datetime import datetime, timedelta, timezone
import json
from tempfile import TemporaryDirectory
import unittest
from xml.etree import ElementTree as ET

from guide_history import RETAINED, preserve as preserve_impl, stable_bindings, without_history
from filter_expansion import EPG_HEADER, unpack, write_release
from xmltv_format import xmltv_bytes
from validate_zapper_guide import stats

NOW = datetime(2026, 11, 8, 6, tzinfo=timezone.utc)


def guide(ids, programmes):
    root = ET.Element("tv")
    for feed in ids:
        ET.SubElement(root, "channel", id=feed)
    for feed, begin, end, title in programmes:
        node = ET.SubElement(root, "programme", channel=feed,
                             start=(NOW + timedelta(hours=begin)).strftime("%Y%m%d%H%M%S %z"),
                             stop=(NOW + timedelta(hours=end)).strftime("%Y%m%d%H%M%S %z"))
        ET.SubElement(node, "title").text = title
    return ET.tostring(root)


def serialize(root):
    return ET.tostring(root, encoding="utf-8")


def preserve(current, previous, now, encoder, **options):
    return preserve_impl(current, previous, now, encoder, allowed_ids={"A"}, **options)


class HistoryTests(unittest.TestCase):
    def test_retains_past_for_same_id_without_overwriting_new_schedule(self):
        current = guide(["A"], [("A", -1, 1, "Current"), ("A", 1, 2, "Next")])
        old = guide(["A", "B"], [("A", -4, -3, "Previous"),
                                  ("A", -2, 0, "Old overlap"),
                                  ("A", 1, 2, "Old future"),
                                  ("B", -4, -3, "Other channel")])
        result, count = preserve(current, old, NOW, serialize)
        titles = [node.findtext("title") for node in ET.fromstring(result).findall("programme")]
        self.assertEqual(count, 1)
        self.assertEqual(titles, ["Current", "Next", "Previous"])

    def test_excludes_placeholder_invalid_and_long_expired_records(self):
        current = guide(["A"], [("A", -1, 1, "Current")])
        old = guide(["A"], [("A", -4, -3, "No information"),
                            ("A", -24 * 31, -24 * 30, "Too old"), ("A", -3, -2, "Real")])
        result, count = preserve(current, old, NOW, serialize)
        self.assertEqual(count, 1)
        self.assertIn(b"Real", result)
        self.assertNotIn(b"No information", result)

    def test_budget_discards_oldest_history_not_new_programmes(self):
        current = guide(["A"], [("A", 0, 1, "Now")])
        old = guide(["A"], [("A", -4, -3, "Old"), ("A", -2, -1, "Recent")])
        result, count = preserve(current, old, NOW, serialize, max_bytes=len(current) + 1)
        self.assertEqual(count, 0)
        self.assertIn(b"Now", result)

    def test_no_untrusted_dtd(self):
        current = guide(["A"], [])
        self.assertEqual(preserve(current, b'<!DOCTYPE tv><tv/>', NOW, serialize), (current, 0))
        self.assertEqual(preserve(current, b'<tv>', NOW, serialize), (current, 0))

    def test_unavailable_feed_has_no_history(self):
        current = guide(["A"], [])
        old = guide(["A"], [("A", -4, -3, "Real")])
        result, count = preserve(current, old, NOW, serialize, unavailable_ids={"A"})
        self.assertEqual(count, 0)
        self.assertFalse(ET.fromstring(result).findall("programme"))

    def test_long_actual_programme_is_not_discarded(self):
        current = guide(["A"], [])
        old = guide(["A"], [("A", -16, -3, "Actual marathon")])
        result, count = preserve(current, old, NOW, serialize)
        self.assertEqual(count, 1)
        self.assertIn(b"Actual marathon", result)

    def test_unsourced_48_hour_placeholder_is_not_history(self):
        current = guide(["A"], [])
        old = guide(["A"], [("A", -60, -12, "Channel label")])
        result, count = preserve(current, old, NOW, serialize)
        self.assertEqual(count, 0)
        self.assertFalse(ET.fromstring(result).findall("programme"))

    def test_repeated_update_does_not_duplicate_history(self):
        current = guide(["A"], [("A", -1, 1, "Current")])
        old = guide(["A"], [("A", -4, -3, "Previous")])
        first, count = preserve(current, old, NOW, serialize)
        second, count_again = preserve(first, old, NOW, serialize)
        self.assertEqual(count, 1)
        self.assertEqual(count_again, 0)
        self.assertEqual(first, second)

    def test_channel_label_is_not_promoted_to_historical_programme(self):
        current = guide(["A"], [])
        root = ET.fromstring(current)
        ET.SubElement(root.find("channel"), "display-name").text = "Canal Música"
        old = guide(["A"], [("A", -4, -3, "Canal Musica")])
        result, count = preserve(serialize(root), old, NOW, serialize)
        self.assertEqual(count, 0)
        self.assertFalse(ET.fromstring(result).findall("programme"))

    def test_identity_requires_same_catalogue_id_and_source_locator(self):
        def payload(identity, locator):
            return {"catalog": [{"id": identity, "tvgId": "A", "sources": [{"url": locator}]}]}
        old = payload("same", "https://example.com/a")
        self.assertEqual(stable_bindings(old, payload("same", "https://example.com/a")), {"A"})
        self.assertEqual(stable_bindings(old, payload("different", "https://example.com/a")), set())
        self.assertEqual(stable_bindings(old, payload("same", "https://example.com/b")), set())
        changed_scope = payload("same", "https://example.com/a")
        changed_scope["catalog"][0]["country"] = "US"
        self.assertEqual(stable_bindings(old, changed_scope), set())

    def test_homonymous_programme_with_source_is_retained(self):
        current = guide(["A"], [])
        root = ET.fromstring(current)
        ET.SubElement(root.find("channel"), "display-name").text = "Canal Música"
        old = ET.fromstring(guide(["A"], [("A", -4, -3, "Canal Música")]))
        old.find("programme").set("zapper-provider-id", "reviewed:same-signal")
        _, count = preserve(serialize(root), serialize(old), NOW, serialize)
        self.assertEqual(count, 1)



    def test_archive_does_not_affect_projection_or_its_next_refresh(self):
        active, withheld = 'Example.ar@SD', 'Held.ar@SD'
        feeds = (active, withheld)
        expansion = [{"tvgID": feed, "id": f"iptv:{feed}", "name": feed,
                      "category": "00 - General", "broadcastArea": ["c/AR"],
                      "sources": [{"url": f"https://example.com/{feed}.m3u8"}]}
                     for feed in feeds]
        playlist = f'#EXTM3U x-tvg-url="{EPG_HEADER}"\n' + ''.join(
            f'#EXTINF:-1 tvg-id="{feed}" tvg-chno="{number}" group-title="00 - General",{feed}\n'
            f'https://example.com/{feed}.m3u8\n' for number, feed in enumerate(feeds, 1))
        current = ET.fromstring(guide(feeds, [(active, -1, 1, 'Current')]))
        programme = current.find('programme')
        programme.set('zapper-provider-id', 'test:current')
        programme.set('zapper-provenance', 'https://example.com/schedule')
        payload = {'catalog': [], 'expansion': expansion, 'playlist': playlist,
                   'flexXML': ET.tostring(current, encoding='unicode'), 'sportsXML': '<tv/>',
                   'unavailableProgrammeIDs': []}
        previous = dict(payload, flexXML=guide(feeds, [
            (active, -4, -3, 'Archived'), (active, -3, -2, 'Revoked'),
            (active, -2, 0, 'Disputed'), (withheld, -4, -3, 'Withheld history'),
        ]).decode())
        with TemporaryDirectory() as directory:
            output = Path(directory) / 'release'
            write_release(payload, output, NOW, [], keep_inventory=True,
                          previous_payload=previous,
                          history_programme_allowed=lambda p: p.findtext('title') != 'Revoked')
            envelope, _ = unpack(output / 'coordinated-generation.json')
            xml = envelope['flexXML']
            self.assertEqual(envelope['withheldExpansionIDs'], [withheld])
            self.assertEqual(envelope['sportsXML'], '<tv/>')
            for name in ('guide-flex.xml', 'guide-flex-20261003.xml'):
                self.assertEqual((output/name).read_text(), xml)
            report = json.loads((output/'revision-epg.json').read_text())
            self.assertEqual(report['retainedHistoricalProgrammes'], 1)
            programmes = ET.fromstring(xml).findall('programme')
            self.assertEqual([p.findtext('title') for p in programmes], ['Archived', 'Current'])
            self.assertEqual(programmes[0].get(RETAINED), '1')
            projected = ET.fromstring(without_history(xml, xmltv_bytes)).findall('programme')
            self.assertEqual([p.findtext('title') for p in projected], ['Current'])


    def test_flex_coverage_counts_schedule_without_archive(self):
        root = ET.fromstring(guide(['A'], [('A', -1, 1, 'Current'),
                                           ('A', 1, 5, 'Future'), ('A', -4, -3, 'Old')]))
        root.findall('programme')[-1].set(RETAINED, '1')
        root.insert(0, ET.Comment(' ' * 100_000))
        with TemporaryDirectory() as directory:
            output = Path(directory) / 'guide.xml'
            output.write_bytes(xmltv_bytes(root))
            self.assertEqual(stats(output, {'A'}, NOW, require_fresh=False)['programmes'], 2)
            root.findall('programme')[1].set(RETAINED, '1')
            output.write_bytes(xmltv_bytes(root))
            with self.assertRaisesRegex(ValueError, 'history cannot describe future'):
                stats(output, {'A'}, NOW, require_fresh=False)

if __name__ == "__main__":
    unittest.main()
