from datetime import datetime, timedelta, timezone
import copy
import json
from pathlib import Path
import sys
import unittest
import urllib.parse
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build_sports_guide as guide

class MovistarESPNTests(unittest.TestCase):
    def row(self, **changes):
        row = {'LiveChannelPid': 'LCH2340', 'ChannelName': 'ESPN 7 HD',
               'Start': 1793987400, 'End': 1793994600,
               'Title': 'Fútbol UEFA Europa League : AC Milan vs. Ferencváros',
               'Description': 'Provider description', 'Pid': 'LSC642796734'}
        return dict(row, **changes)

    def parse(self, row, exclusions=()):
        return guide.parse_movistar(json.dumps({'Content': [row]}).encode(),
                                    'lch2340', 'ESPN 7 HD', exclusions)

    def test_full_match_title_and_provider_instants_preserved(self):
        result = self.parse(self.row())[0]
        self.assertEqual(result['title'], self.row()['Title'])
        self.assertEqual(int(result['start'].timestamp()), self.row()['Start'])
        self.assertEqual(int(result['stop'].timestamp()), self.row()['End'])
        self.assertEqual(result['providerProgrammeID'], 'LSC642796734')

    def test_foreign_channel_never_silently_filtered_or_relabelled(self):
        for row in (self.row(LiveChannelPid='LCH2729'), self.row(ChannelName='ESPN 6 HD')):
            with self.assertRaisesRegex(ValueError, 'channel identity'):
                self.parse(row)

    def test_epoch_units_and_bool_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'epoch seconds'):
            self.parse(self.row(Start=True))
        with self.assertRaises((ValueError, OverflowError)):
            self.parse(self.row(Start=1793987400000))

    def test_exclusion_is_dated_and_does_not_rename_programmes(self):
        rule = {'startUTC': '2026-11-06T17:50:00Z', 'stopUTC': '2026-11-06T19:50:00Z',
                'expectedProviderTitle': self.row()['Title']}
        self.assertEqual(self.parse(self.row(), [rule]), [])
        tomorrow = self.row(Start=self.row()['Start'] + 86400, End=self.row()['End'] + 86400)
        self.assertEqual(len(self.parse(tomorrow, [rule])), 1)
        corrected = self.row(Title='Fútbol UEFA Europa League : Crystal Palace vs. Hoffenheim')
        self.assertEqual(self.parse(corrected, [rule])[0]['title'], corrected['Title'])

    def binding(self):
        return {'sourceID': 'src_79064a91d1c6e4c5', 'schedule': {
            'provider': 'movistar', 'region': 'PE', 'language': 'es', 'key': 'lch2341',
            'providerName': 'ESPN 6 HD', 'providerChannelID': 'movistar.pe:LCH2341'}}

    def test_region_or_alternative_source_cannot_inherit_primary_grid(self):
        for change in ('region', 'source'):
            binding = self.binding()
            if change == 'region':
                binding['schedule']['region'] = 'CO'
            else:
                binding['sourceID'] = 'a-different-preserved-alternative'
            with self.assertRaisesRegex(ValueError, 'source/edition'):
                guide.movistar_schedule(binding, datetime.now(timezone.utc),
                                        lambda _: self.fail('Wrong identity must not fetch'))

    def test_official_channel_metadata_drift_fails_closed(self):
        def read(_):
            return json.dumps({'Content': {'List': [{'Pid': 'LCH2341', 'Name': 'Other edition'}]}}).encode()
        with self.assertRaisesRegex(ValueError, 'metadata changed'):
            guide.movistar_schedule(self.binding(), datetime.now(timezone.utc), read)

    def test_each_day_keeps_its_actual_source_url(self):
        now = datetime(2026, 11, 6, 19, 40, tzinfo=timezone.utc)
        def read(url):
            if 'contents/all?' in url:
                return json.dumps({'Content': {'List': [{'Pid': 'LCH2341', 'Name': 'ESPN 6 HD'}]}}).encode()
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            start = int(query['starttime'][0]) + 19 * 3600
            row = self.row(LiveChannelPid='LCH2341', ChannelName='ESPN 6 HD',
                           Start=start, End=start + 7200, Title='Actual provider programme')
            return json.dumps({'Content': [row]}).encode()
        rows, urls = guide.movistar_schedule(self.binding(), now, read)
        self.assertEqual(len(urls), 2)
        for row in rows:
            requested_day = int(urllib.parse.parse_qs(urllib.parse.urlsplit(row['provenance']).query)['starttime'][0])
            self.assertEqual(row['start'].date(), datetime.fromtimestamp(requested_day, timezone.utc).date())

if __name__ == '__main__':
    unittest.main()
