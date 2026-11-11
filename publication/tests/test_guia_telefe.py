from datetime import datetime
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from guia_telefe import CHANNEL, DATE, PROVIDER, overlay, slots


NOW = datetime.fromisoformat("2026-11-08T07:30:00+00:00")


def pane(day, cards):
    rows = [
        '<div class="schedule-item-row" data-schedule-target="item">'
        + (f'<span class="schedule-time-text">{at}</span>' if at else '')
        + f'<h3 class="schedule-program-title text-uppercase">{title}</h3></div>'
        for at, title in cards
    ]
    return f'<div role="tabpanel" id="tab-pane-{day}">{"".join(rows)}</div>'


def page(*panes):
    return ('<section data-controller="schedule"><h2>Grilla de horarios nacional</h2>'
            + ''.join(panes) + '</section>')


class TelefeSchedule(unittest.TestCase):
    def test_national_signal_midnight_and_independent_dated_panes(self):
        document = page(
            pane('20261108', [('06:00', 'Disney Junior'), ('07:30', 'El Chavo'),
                              ('13:00', 'La Peña'), ('20:00', 'Pasapalabra &amp; amigos'), ('00:15', 'Repetición')]),
            pane('20261109', [('06:00', 'Disney Junior'), ('07:30', 'Buen Telefe')]),
        )
        rows = slots(document, NOW)
        self.assertEqual([(r.get('start'), r.get('stop'), r.findtext('title')) for r in rows], [
            ('20261108090000 +0000', '20261108103000 +0000', 'Disney Junior'),
            ('20261108103000 +0000', '20261108160000 +0000', 'El Chavo'),
            ('20261108160000 +0000', '20261108230000 +0000', 'La Peña'),
            ('20261108230000 +0000', '20261109031500 +0000', 'Pasapalabra & amigos'),
            ('20261109090000 +0000', '20261109103000 +0000', 'Disney Junior'),
        ])
        self.assertTrue(all(r.get('channel') == CHANNEL and
                            r.get('zapper-provider-id') == PROVIDER for r in rows))

    def test_missing_time_cannot_close_previous_programme(self):
        document = page(pane('20261108', [('06:00', 'Disney Junior'), ('', 'Sin hora'),
                                         ('08:00', 'Magazine'), ('09:00', 'Cine')]))
        self.assertEqual([r.findtext('title') for r in slots(document, NOW)], ['Magazine'])

    def test_wrong_section_and_non_monotonic_day_fail_closed(self):
        with self.assertRaises(ValueError):
            slots('<section data-controller="schedule">Telefe internacional</section>', NOW)
        with self.assertRaises(ValueError):
            slots(page(pane('20261108', [('08:00', 'A'), ('07:30', 'B')])), NOW)

    def test_fresh_schedule_preserves_nonoverlap_and_other_telefe(self):
        def programme(channel, start, stop):
            return ET.Element('programme', channel=channel,
                              start=f'20261108{start}0000 +0000', stop=f'20261108{stop}0000 +0000')
        def parse(raw):
            return datetime.strptime(raw, DATE)
        fresh = programme(CHANNEL, '10', '11')
        contradicted = programme(CHANNEL, '10', '12')
        omitted = programme(CHANNEL, '08', '10')
        different = programme('Telefe.ar@SD', '10', '12')
        self.assertEqual(overlay([fresh], [contradicted, omitted, different], parse),
                         [omitted, different, fresh])
