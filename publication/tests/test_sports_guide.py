import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build_sports_guide as guide

class SportsGuideTests(unittest.TestCase):
    def test_midnight_rollover_and_no_fabricated_final_end(self):
        data = {'day': [{'contenido': {'noche': {'item': [
            {'hora': '2026-11-06T23:00:00.000Z', 'programa': {'content': 'Late'}},
            {'hora': '2026-11-06T01:00:00.000Z', 'programa': {'content': 'After midnight'}}]} } }],
            'next': [{'contenido': {'manana': {'item': [
            {'hora': '2026-11-07T06:00:00.000Z', 'programa': {'content': 'Morning'}}]}}}]}
        result = guide.parse_realmadrid(json.dumps(data).encode())
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['stop'], datetime(2026, 11, 7, 1, tzinfo=timezone.utc))
        self.assertEqual(result[1]['start'], result[0]['stop'])
        self.assertEqual(result[1]['stop'], datetime(2026, 11, 7, 6, tzinfo=timezone.utc))

    def test_plex_uses_utc_and_keeps_separate_media_intervals(self):
        payload = {'MediaContainer': {'Metadata': [{'grandparentTitle': 'Series', 'title': 'Episode',
            'Media': [{'beginsAt': 1793980800, 'endsAt': 1793984400},
                      {'beginsAt': 1793991600, 'endsAt': 1793995200}]}]}}
        result = guide.parse_plex(json.dumps(payload).encode())
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['start'].utcoffset(), timedelta(0))
        self.assertLess(result[0]['stop'], result[1]['start'])
        self.assertEqual(result[0]['subtitle'], 'Episode')

    def test_wrong_regional_edition_rejected_before_fetch(self):
        binding = {'name': 'FIFA+', 'schedule': {'provider': 'plex', 'key': 'exact',
            'language': 'en', 'providerChannelID': 'FIFAPlus.uk@UnitedStates'}}
        wrong = ET.fromstring('<channels><channel site_id="exact" lang="es" xmltv_id="FIFAPlus.uk@Spain">FIFA+</channel></channels>')
        with self.assertRaisesRegex(ValueError, 'edition'):
            guide.provider_schedule(binding, datetime.now(timezone.utc), wrong, lambda _: self.fail('must not fetch wrong feed'))

    def test_overlap_and_expired_schedule_rejected(self):
        now = datetime.now(timezone.utc)
        row = {'start': now, 'stop': now + timedelta(hours=1), 'title': 'A'}
        with self.assertRaisesRegex(ValueError, 'Overlapping'):
            guide.normalize([row, dict(row, title='Different programme')], now)
        with self.assertRaisesRegex(ValueError, 'No current'):
            guide.normalize([dict(row, start=now - timedelta(days=2), stop=now - timedelta(days=1))], now)

    def test_source_mismatch_rejected_and_unknown_not_promoted(self):
        root = Path(__file__).resolve().parents[2]
        manifest = json.loads((root / 'publication/sports/guide-bindings.json').read_text())
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / 'publication/sports').mkdir(parents=True)
            (directory / 'publication/sports/guide-bindings.json').write_text(json.dumps(manifest))
            now = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
            mapping = ET.Element('channels')
            for b in manifest['channels']:
                s = b['schedule']
                if s and s['provider'] == 'plex':
                    ET.SubElement(mapping, 'channel', {'site_id': s['key'], 'lang': s['language'], 'xmltv_id': s['providerChannelID']}).text = b['name']
            def read(url):
                if url == guide.PLEX_MAP:
                    return ET.tostring(mapping)
                if 'plex.tv/grid' in url:
                    return json.dumps({'MediaContainer': {'Metadata': [{'title': 'Real provider record', 'Media': [
                        {'beginsAt': int(now.timestamp()) - 3600, 'endsAt': int(now.timestamp()) + 3600}]}]}}).encode()
                raise OSError('Real Madrid temporarily unavailable')
            guide.build(directory, read=read, now=now)
            candidate = directory / 'guide-sports-news-candidate.xml'
            tree = ET.parse(candidate).getroot()
            unknown = next(e for e in tree.findall('channel') if e.get('id') == manifest['channels'][1]['id'])
            self.assertEqual(unknown.get('zapper-verification-status'), 'UNKNOWN')
            self.assertEqual(len(tree.findall('programme')), 3)
            tree.find('channel').set('zapper-source-id', 'wrong-source')
            with self.assertRaisesRegex(ValueError, 'binding'):
                guide.validate(tree, manifest, now)
            # An upstream outage preserves the original verification dates and valid bytes.
            original = candidate.read_bytes()
            (directory / 'guide-sports-news.xml').write_bytes(original)
            def unavailable(_):
                raise OSError('provider unavailable')
            report = guide.build(directory, read=unavailable, now=now + timedelta(minutes=5))
            self.assertEqual(sum(c['status'] == 'RETAINED_LAST_VALID' for c in report['channels']), 3)
            retained = ET.parse(candidate).getroot()
            self.assertEqual(retained.find('programme').get('zapper-last-verified-utc'), guide.iso(now))

if __name__ == '__main__':
    unittest.main()
