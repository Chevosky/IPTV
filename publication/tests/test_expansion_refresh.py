import sys
from base64 import b64encode
from hashlib import sha256
from datetime import datetime, timezone
import gzip
from html import escape
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from filter_expansion import EPG_HEADER, programme_state, unpack, write_release
from guia_elcinema import elcinema_programmes
from guia_expansion import APUNT_ACCOUNT, ayn_rows, cbs_rows, ct_shared_day, dmax_rows, programmes_for_expansion, rai_rows, rcntotal_rows, reduno_rows, binding, rtve_rows, space_toon_grid
import refresh_expansion
from refresh_expansion import exclude_disputed_slots, preserve_uncontradicted_slots, remove_unusable_programmes, reviewed_programmes, write_guide


NOW = datetime(2026, 11, 7, 18, tzinfo=timezone.utc)



class RedUnoSantaCruzScheduleTests(unittest.TestCase):
    dated = """<table>
    <tr><td colspan="4"><table><tr><td>Red Uno</td></tr></table></td></tr>
    <tr><td>HORA</td><td>SÁBADO 7 DE NOVIEMBRE</td><td>HORA</td><td>DOMINGO 8 DE NOVIEMBRE</td></tr>
    <tr><td rowspan="2">06:00</td><td rowspan="2">Otro</td><td>06:00</td><td>El Chapulín Colorado</td></tr>
    <tr><td>07:00</td><td>Santa Misa</td></tr>
    <tr><td>07:55</td><td>Otra emisión</td><td>08:00</td><td>Desacuerdo</td></tr>
    <tr><td>22:30</td><td>Película</td><td>21:00</td><td>Show nocturno</td></tr>
    <tr><td>00:00</td><td>Problemas y Soluciones</td><td>00:00</td><td>Problemas y Soluciones</td></tr>
    <tr><td>01:00</td><td>Otra emisión</td><td>01:00</td><td>Otro</td></tr>
    </table><!-- /programacion | version | 2026-11-07T14:44:23.072Z -->"""
    tariff = """<h2><strong>NOVIEMBRE</strong></h2><table><tr><td>Hora</td><td>Domingo</td><td>Categoría</td><td>RED</td>
    <td>SCZ</td><td>LPZ</td><td>CBBA</td></tr>
    <tr><td>06:00-07:00</td><td>El Chapulin Colorado..</td><td>A</td><td>0</td><td>1</td><td>1</td><td>1</td></tr>
    <tr><td>07:00-08:00</td><td>Santa Misa</td><td>NINGUNO</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
    <tr><td>08:00-21:00</td><td>Distinto</td><td>A</td><td>0</td><td>1</td><td>1</td><td>1</td></tr>
    <tr><td>21:00-23:55</td><td>Show nocturno</td><td>A</td><td>0</td><td>1</td><td>1</td><td>1</td></tr>
    <tr><td>00:00-01:00</td><td>Problemas y soluciones</td><td>A</td><td>0</td><td>1</td><td>1</td><td>1</td></tr></table>"""
    now = datetime(2026, 11, 8, 10, 10, tzinfo=timezone.utc)

    def schedules(self, dated=None, tariff=None):
        with patch("guia_expansion.read_response", side_effect=[
                (self.dated if dated is None else dated).encode(),
                (self.tariff if tariff is None else tariff).encode()]):
            return [(a.astimezone(timezone.utc), b.astimezone(timezone.utc), title)
                    for a, b, title, *_ in reduno_rows(self.now, None)]

    def test_only_uses_jointly_supported_intervals_and_preserves_local_midnight(self):
        rows = self.schedules()
        self.assertEqual([row[2] for row in rows], ["El Chapulín Colorado", "Santa Misa", "Problemas y Soluciones"])
        self.assertEqual([(a.day, a.hour, b.hour) for a, b, _ in rows], [(8, 10, 11), (8, 11, 12), (9, 4, 5)])

    def test_rejects_old_or_wrong_region_schedules(self):
        self.assertEqual(self.schedules(dated=self.dated.replace("2026-11-07T", "2026-10-25T")), [])
        self.assertEqual(self.schedules(tariff=self.tariff.replace(">SCZ<", ">TJA<")), [])
        self.assertEqual(self.schedules(tariff=self.tariff.replace(">NOVIEMBRE<", ">OCTUBRE<")), [])
        saturday = self.schedules(tariff=self.tariff.replace(">Domingo<", ">Sábado<"))
        self.assertEqual([(start.day, start.hour, title) for start, _, title in saturday],
                         [(8, 4, "Problemas y Soluciones")])

    def test_supports_common_weekday_time_column(self):
        dated = """<table>
        <tr><td>HORA</td><td>DOMINGO 8 DE NOVIEMBRE</td><td>LUNES 9 DE NOVIEMBRE</td></tr>
        <tr><td>06:00</td><td>El Chapulín Colorado</td><td>Otro</td></tr>
        <tr><td>07:00</td><td>Santa Misa</td><td>Distinto</td></tr>
        <tr><td>08:00</td><td>Otro</td><td>También distinto</td></tr>
        </table><!-- /programacion | version | 2026-11-07T14:44:23.072Z -->"""
        self.assertEqual([row[2] for row in self.schedules(dated=dated)], ["El Chapulín Colorado", "Santa Misa"])

    def test_binding_requires_santa_cruz_identity_and_reviewed_locator(self):
        source = {"url": "http://190.181.61.93:8000/play/a002/index.m3u8"}
        channel = {"tvgID": "RedUno.bo@SantaCruz", "id": "iptv:RedUno.bo", "sources": [source]}
        self.assertEqual(binding(channel), ("reduno", "santa-cruz", None))
        self.assertIsNone(binding({**channel, "tvgID": "RedUno.bo@LaPaz"}))
        self.assertIsNone(binding({**channel, "sources": [{"url": source["url"] + "?other"}]}))


class RaiDomesticScheduleTests(unittest.TestCase):
    def test_only_resolves_overlap_for_same_asset_and_continuous_playlist(self):
        asset = "/video/raiplay-item.json"

        def event(hour, duration, title, playlist, path_id=""):
            return {"channel": "Rai 5", "hour": hour, "duration_in_minutes": f"{duration} min",
                    "name": title, "playlist_id": playlist, "path_id": path_id}

        before = event("07:12", 53, "Previous", "100")
        linear = event("08:05", 160, "Ballet", "101", asset)
        duplicate = event("08:05", 172, "Ballet", "", asset)
        after = event("10:45", 3, "Next", "102")

        def read(*events):
            response = json.dumps({"channel": "Rai 5", "date": "07-11-2026", "events": events})
            with patch("guia_expansion.read_response", return_value=response):
                return list(rai_rows("rai-5", "Rai 5", NOW, None))

        self.assertEqual([row[2] for row in read(before, linear, duplicate, after)],
                         ["Previous", "Ballet", "Next"])
        for variant, neighbours in (
            (dict(duplicate, path_id="/video/other.json"), (before, after)),
            (dict(duplicate, playlist_id="103"), (before, after)),
            (duplicate, (after,)),
            (duplicate, (before,)),
        ):
            with self.subTest(variant=variant, neighbours=neighbours):
                rows = read(linear, variant, *neighbours)
                self.assertEqual(len(rows), 2 + len(neighbours))


class RaiSudtirolGuideTests(unittest.TestCase):
    def test_replaces_generic_row_only_with_exact_dated_duration(self):
        from guia_rai_sudtirol import FEED, PROVIDER
        from guide_continuity import renew

        schedule = """
        <table class="content_program">
          <tr><td itemprop="startDate">20:20</td><td><strong itemprop="name"><a href="/view_event.php?id=123">dF</a></strong></td></tr>
          <tr><td itemprop="startDate">20:50</td><td><strong itemprop="name">Unlinked programme</strong></td></tr>
        </table>
        """
        detail = """<script>Date.parseExact('2026-11-07', 'M/d/yyyy')</script>
        <h4 itemprop="name">dF</h4>
        <span itemprop="startDate">20:20</span><span itemprop="duration">29'  30"</span>"""
        generic = programme(FEED, "20261107080000 +0000", "20261109080000 +0000", "Rai Sudtirol - Deutsch")
        exact = ET.fromstring(ET.tostring(generic))
        generic.attrib.pop("zapper-provider-id")
        generic.attrib.pop("zapper-provenance")
        with TemporaryDirectory() as path:
            snapshot = Path(path)
            (snapshot / "raisudtirol-2026-11-07.html").write_text(schedule)
            event = snapshot / "raisudtirol-event-123.html"
            event.write_text(detail)
            [row] = renew(FEED, [generic], [], NOW, snapshot)
            self.assertEqual(row.findtext("title"), "dF")
            self.assertEqual(row.get("start"), "20261107192000 +0000")
            self.assertEqual(row.get("stop"), "20261107194930 +0000")
            self.assertEqual(row.get("zapper-provider-id"), PROVIDER)
            self.assertEqual(renew(FEED, [exact], [], NOW, snapshot), [exact])

            for mismatch in (detail.replace("2026-11-07", "2026-11-08"),
                             detail.replace("29'", "31'"),
                             detail.replace("20:20", "20:30")):
                with self.subTest(detail=mismatch):
                    event.write_text(mismatch)
                    self.assertEqual(renew(FEED, [generic], [], NOW, snapshot), [])
                    self.assertEqual(renew(FEED, [generic], [row], NOW, snapshot), [row])

def programme(feed, start, stop, title="Noticias"):
    element = ET.Element("programme", {
        "channel": feed, "start": start, "stop": stop,
        "zapper-provider-id": "official:test",
        "zapper-provenance": "https://example.com/guide",
    })
    ET.SubElement(element, "title").text = title
    return element


class ExpansionRefreshTests(unittest.TestCase):
    def test_targeted_continuity_keeps_existing_slot_and_ignores_stale_or_wrong_source(self):
        from guide_continuity import PROVIDERS, ROKU_GUIDE, ROKU_ID, ROKU_VOICE, renew
        feed = ROKU_VOICE
        now = datetime(2026, 11, 8, 10, 30, tzinfo=timezone.utc)
        existing = programme(feed, "20261108100000 +0000", "20261108110000 +0000", "Approved")
        future = programme(feed, "20261108120000 +0000", "20261108130000 +0000", "Cached")
        future.set("zapper-provider-id", PROVIDERS[feed])
        future.set("zapper-provenance", ROKU_GUIDE)
        wrong = programme(feed, "20261108130000 +0000", "20261108140000 +0000", "Other edition")
        html = (
            f'<meta property="og:image" content="https://images.sr.roku.com/id/{ROKU_ID}/asset">'
            '<div data-platform="Roku" data-show="Do not overwrite"'
            ' data-start="2026-11-08T10:00:00Z" data-end="2026-11-08T11:00:00Z"></div>'
            '<div data-platform="Roku" data-show="Next"'
            ' data-start="2026-11-08T11:00:00Z" data-end="2026-11-08T12:00:00Z"></div>'
            '<div data-platform="Pluto" data-show="Other platform"'
            ' data-start="2026-11-08T13:00:00Z" data-end="2026-11-08T14:00:00Z"></div>'
            '<div data-platform="Roku" data-show="Expired"'
            ' data-start="2026-11-08T08:00:00Z" data-end="2026-11-08T09:00:00Z"></div>'
        )
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            file = snapshot / "roku-rav.html"
            file.write_text(html)
            renewed = renew(feed, [existing], [future, wrong], now, snapshot)
            self.assertEqual([r.findtext("title") for r in renewed], ["Approved", "Next", "Cached"])
            self.assertIs(renewed[0], existing)
            file.write_text(html.replace(ROKU_ID, "another-channel"))
            self.assertEqual(renew(feed, [existing], [future, wrong], now, snapshot), [existing, future])

    def test_targeted_continuity_checks_trece_edition_and_pbs_timezone(self):
        from guide_continuity import PBS_KIDS, TRECE, renew
        now = datetime(2026, 11, 8, 10, 30, tzinfo=timezone.utc)
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            start = int(datetime(2026, 11, 8, 11, tzinfo=timezone.utc).timestamp() * 1000)
            end = start + 3600000
            movistar = {"Canal": {"CodCadenaTv": "13TV", "Nombre": "TRECE"},
                        "FechaHoraInicio": str(start), "FechaHoraFin": str(end), "Titulo": "Misa"}
            (snapshot / "movistar-trece-2026-11-08.json").write_text(json.dumps([
                dict(movistar, Canal={"CodCadenaTv": "14TV", "Nombre": "OTHER"}, Titulo="Other station"),
                movistar, {"Canal": None}, {}
            ]))
            result = renew(TRECE, [], [], now, snapshot)
            self.assertEqual([(r.get("start"), r.findtext("title")) for r in result], [("20261108110000 +0000", "Misa")])
            pbs = snapshot / "tvpassport-pbskids-2026-11-08.html"
            markup = ('<option value="America/New_York" selected>Eastern</option>'
                      '<div id="itemheader1" data-callsign="PBS-KID" data-showName="PBS exact"'
                      ' data-st="2026-11-08 06:00:00" data-duration="30"></div>'
                      '<div id="itemheader2" data-callsign="WGBH" data-showName="Local affiliate"'
                      ' data-st="2026-11-08 07:00:00" data-duration="30"></div>')
            pbs.write_text(markup)
            result = renew(PBS_KIDS, [], [], now, snapshot)
            self.assertEqual([(r.get("start"), r.findtext("title")) for r in result],
                             [("20261108110000 +0000", "PBS exact")])
            pbs.write_text(markup.replace("America/New_York", "America/Los_Angeles"))
            self.assertEqual(renew(PBS_KIDS, [], [], now, snapshot), [])

    def test_ticker_uses_publisher_cycle_only_for_the_exact_stream(self):
        feed = "tickerNews.au@SD"
        route = ("https://live-hls-7agy.livepush.io/live_abr_cdn/"
                 "nsitWAl1pTZtwca/emnmZbKZcO8RfwJR/index.m3u8")
        entry = {"id": "iptv:tickerNews.au", "tvgID": feed, "sources": [{"url": route}]}
        clock = datetime(2026, 11, 8, 7, 29, tzinfo=timezone.utc)
        data = {"tz": "Australia/Melbourne", "slots": [
            {"start": 0, "name": "Ticker Today"}, {"start": 28, "name": "Weather"},
            {"start": 30, "name": "Ticker Originals"}, {"start": 60, "name": "Ticker Today"},
        ]}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            listing = snapshot / "ticker-news.html"
            cycle = 'function pos(o) {return (o.hour%2)*60;} while(nx<=st)nx+=120;'
            listing.write_text(f'<div data-epg="{escape(json.dumps(data))}"></div>{cycle}{route}')
            rows = programmes_for_expansion([entry], None, clock, snapshot=snapshot)[feed]
            weather = next(row for row in rows if row.get("start") == "20261108072800 +0000")
            self.assertEqual((weather.findtext("title"), weather.get("stop")), ("Weather", "20261108073000 +0000"))
            wrong = dict(entry, sources=[{"url": "https://example.com/ticker-foreign.m3u8"}])
            self.assertEqual(programmes_for_expansion([wrong], None, clock, snapshot=snapshot), {})
            listing.write_text(f'<div data-epg="{escape(json.dumps(data))}"></div>{route}')
            self.assertEqual(programmes_for_expansion([entry], None, clock, snapshot=snapshot), {})
    def test_love_nature_binds_tubi_edition_by_inventory_route_and_channel_id(self):
        feed = "LoveNature.ca@SD"
        entry = {"id": "iptv:LoveNature.ca", "tvgID": feed, "sources": [{"url": "https://aegis-cloudfront-1.tubi.video/6d6d0f24-8445-4b4c-bdf6-44f9e38beaa4/playlist.m3u8"}]}
        now = datetime(2026, 11, 8, 6, 31, tzinfo=timezone.utc)
        xml = ET.Element("tv")
        channel = ET.SubElement(xml, "channel", id="700418")
        name = ET.SubElement(channel, "display-name")
        name.text = "Love Nature"
        xml.append(programme("700418", "20261108062906 +0000", "20261108072947 +0000", "Fur Seals: Battle for Survival"))
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            path = snapshot / "tubi-love-nature.xml"
            path.write_bytes(ET.tostring(xml))
            rows = programmes_for_expansion([entry], None, now, snapshot=snapshot)[feed]
            self.assertEqual([row.findtext("title") for row in rows], ["Fur Seals: Battle for Survival"])
            wrong = dict(entry, sources=[{"url": "https://example.com/canadian-edition.m3u8"}])
            self.assertEqual(programmes_for_expansion([wrong], None, now, snapshot=snapshot), {})
            channel.set("id", "700419")
            path.write_bytes(ET.tostring(xml))
            self.assertEqual(programmes_for_expansion([entry], None, now, snapshot=snapshot), {})

    def test_senado_chile_only_admits_explicit_nonoverlapping_tv_sessions(self):
        feed = "TVSenado.cl@SD"
        route = "https://janus-tv-ply.senado.cl/playlist/playlist.m3u8"
        entry = {"id": "iptv:TVSenado.cl", "tvgID": feed, "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 6, 24, tzinfo=timezone.utc)
        public_tv = "La sesi&oacute;n ser&aacute; transmitida por TV Senado y por el sitio http://tv.senado.cl."

        def table(name, hours, broadcast=public_tv, detail="Agenda publicada"):
            return (f'<table><tr><th>COMISI&Oacute;N:</th><td><strong>{name}</strong><label>{broadcast}</label></td></tr>'
                    f'<tr><th>HORARIO:</th><td>{hours}</td></tr><tr><th>MATERIA:</th><td>{detail}</td></tr></table>')

        page = '<h1 class="titular serif">Lunes, 9 de Noviembre de 2026</h1>'
        page += table("de Constitución", "10:30 a 12:00") + table("de Gobierno", "11:00 a 12:30")
        page += table("de Salud", "14:00 a 15:30")
        page += table("de Minería", "14:00 a 15:30", "Solo sitio web")
        page += table("de Economía", "16:00 a 17:30", detail="Sesión suspendida")
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "senado-cl-citaciones-oficial.html").write_text(page)
            rows = programmes_for_expansion([entry], None, now, snapshot=snapshot)[feed]
            self.assertEqual([(r.get("start"), r.get("stop"), r.findtext("title")) for r in rows],
                             [("20261109170000 +0000", "20261109183000 +0000", "Comisión de Salud")])
            self.assertEqual(programme_state(rows, now), "agenda-verificada")
            wrong = dict(entry, sources=[{"url": "https://example.com/otra-camara.m3u8"}])
            self.assertEqual(programmes_for_expansion([wrong], None, now, snapshot=snapshot), {})

    def test_direct_provider_can_refresh_without_batches_but_cache_cannot(self):
        base, direct, retained, expired = ("Base.ar@SD", "Direct.ar@SD",
                                           "Retained.ar@SD", "Expired.ar@SD")
        catalogue = [{"tvgId": base, "country": "AR", "name": "Base"}]
        expansion = [{"tvgID": feed, "name": feed, "category": "General", "sources": []}
                     for feed in (direct, retained, expired)]

        def playlist_for(feeds):
            return f'#EXTM3U x-tvg-url="{EPG_HEADER}"\n' + "".join(
                f'#EXTINF:-1 tvg-id="{feed}" group-title="General",{feed}\n'
                f'https://example.com/{feed}.m3u8\n' for feed in feeds)

        baseline = ET.Element("tv")
        ET.SubElement(baseline, "channel", id=base)
        baseline.append(programme(base, "20261107170000 +0000", "20261107190000 +0000"))
        base_xml = ET.tostring(baseline, encoding="unicode")
        old = ET.fromstring(base_xml)
        for feed in (direct, retained, expired):
            ET.SubElement(old, "channel", id=feed)
        old.append(programme(retained, "20261107170000 +0000", "20261107193000 +0000", "Known current"))
        old.append(programme(expired, "20261107150000 +0000", "20261107160000 +0000", "Expired"))
        live = programme(direct, "20261107175000 +0000", "20261107190000 +0000", "Direct current")

        class FixedClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW.astimezone(tz)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "publication/epg"
            settings.mkdir(parents=True)
            payload = {"catalog": catalogue, "expansion": expansion, "alternatives": [], "youtube": [],
                       "playlist": playlist_for((base, direct, retained, expired)),
                       "flexXML": ET.tostring(old, encoding="unicode"), "unavailableProgrammeIDs": []}
            raw = json.dumps(payload).encode()
            envelope = {"sha256": sha256(raw).hexdigest(), "payload": b64encode(raw).decode()}
            (settings / "expansion-seed.json").write_text(json.dumps(envelope))
            (settings / "expansion-iptvorg.channels.xml").write_text("<channels/>")
            (root / "catalog-flex.json").write_text(json.dumps({"channels": catalogue}))
            (root / "guide-sports-news.xml").write_text("<tv/>")
            m3u, xml = root / "base.m3u", root / "base.xml"
            m3u.write_text(playlist_for((base,)))
            xml.write_text(base_xml)
            with patch.object(refresh_expansion, "datetime", FixedClock), patch.object(
                refresh_expansion.subprocess, "check_output", return_value="test-revision"
            ), patch.object(refresh_expansion.guia_expansion, "programmes_for_expansion") as schedules:
                schedules.return_value = {direct: [live]}
                release = root / "direct"
                summary = refresh_expansion.build(
                    root, m3u, xml, root / "no-batches", release, fetch_official=False, official_snapshot=root
                )
                schedules.assert_called_once_with(expansion, None, NOW, fetch=False, snapshot=root)
                actual, _ = unpack(release / "coordinated-generation.json")
                rows = {p.get("channel"): p.findtext("title") for p in ET.fromstring(actual["flexXML"]).findall("programme")}
                self.assertEqual(rows, {base: "Noticias", direct: "Direct current", retained: "Known current"})
                self.assertEqual(actual["withheldExpansionIDs"], [expired])
                self.assertEqual(summary["freshProviderChannels"], 0)
                self.assertEqual(summary["officialOnlyChannels"], 1)
                missing = programme(direct, "20261107175000 +0000", "20261107190000 +0000", "No information")
                for direct_reply in ({}, {direct: [missing]}):
                    schedules.return_value = direct_reply
                    failed = root / "failed"
                    with self.assertRaisesRegex(ValueError, "No reviewed batch or direct provider"):
                        refresh_expansion.build(
                            root, m3u, xml, root / "no-batches", failed, fetch_official=False, official_snapshot=root
                        )
                    self.assertFalse(failed.exists())

    def test_clean_base_retains_declared_missing_epg_until_verified_rows_arrive(self):
        play, atacama = "PlayTV.br@SD", "AtacamaTV.cl@SD"
        guide = ET.Element("tv")
        for feed in (play, atacama, "Other.tv@SD"):
            ET.SubElement(guide, "channel", id=feed)
        guide.append(programme(play, "20261108050000 +0000", "20261108110000 +0000", "Igreja Mundial"))
        original = ET.tostring(guide, encoding="unicode")
        clean, unavailable = remove_unusable_programmes(original, [play, atacama])
        self.assertEqual(clean, original)
        self.assertEqual(unavailable, [atacama])
        guide.append(programme(atacama, "20261108120000 +0000", "20261108130000 +0000"))
        clean, unavailable = remove_unusable_programmes(ET.tostring(guide, encoding="unicode"), [play, atacama])
        self.assertEqual(unavailable, [])
        self.assertEqual(len(ET.fromstring(clean).findall("programme")), 2)

    def test_cine_western_generic_label_is_not_a_programme(self):
        feed = "CineWestern.es@SD"
        guide = ET.Element("tv")
        ET.SubElement(guide, "channel", id=feed)
        guide.append(programme(feed, "20261108070000 +0000", "20261108080000 +0000",
                               "Cine Western"))
        clean, unavailable = remove_unusable_programmes(ET.tostring(guide, encoding="unicode"))
        self.assertEqual(unavailable, [feed])
        self.assertEqual(ET.fromstring(clean).findall("programme"), [])
        guide.append(programme(feed, "20261108080000 +0000", "20261108093000 +0000",
                               "Viking"))
        clean, unavailable = remove_unusable_programmes(ET.tostring(guide, encoding="unicode"))
        self.assertEqual(unavailable, [])
        self.assertEqual([row.findtext("title") for row in ET.fromstring(clean).findall("programme")],
                         ["Viking"])

    def test_osn_al_araby_exact_channel_and_epoch_conversion(self):
        feed = "AlArabyTV.qa@SD"
        route = "https://live.kwikmotion.com/alaraby1live/alaraby_abr/playlist.m3u8"
        entry = {"id": "iptv:AlArabyTV.qa", "tvgID": feed, "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 6, 10, tzinfo=timezone.utc)
        start = datetime(2026, 11, 8, 6, tzinfo=timezone.utc)
        slot = {"channelId": "3800", "channelName": "Al Araby TV", "programTitle": "هذا النهار",
                "startTime": int(start.timestamp() * 1000),
                "endTime": int(start.replace(hour=7).timestamp() * 1000)}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "osn-3800-2026-11-08.json").write_text(json.dumps({"programs": [
                slot, dict(slot, channelId="3801", programTitle="Al Araby 2"),
                dict(slot, channelName="Al Araby TV 2"), dict(slot, endTime=None),
            ]}))
            guide = programmes_for_expansion([entry], None, now, snapshot=snapshot)[feed]
            self.assertEqual([(row.get("start"), row.get("stop"), row.findtext("title")) for row in guide],
                             [("20261108060000 +0000", "20261108070000 +0000", "هذا النهار")])
            wrong = dict(entry, sources=[{"url": "https://example.com/other-edition.m3u8"}])
            self.assertEqual(programmes_for_expansion([wrong], None, now, snapshot=snapshot), {})

    def test_spacetoon_schedule_requires_uae_edition_and_clock(self):
        service = {"@type": "BroadcastService", "name": "Space Toon",
                   "areaServed": "United Arab Emirates", "url": "https://spacetoon.com/"}
        show = ('<li><time datetime="Sun Nov 08 2026 19:45:00 GMT+0000 (UTC)">23:45 - 00:15</time>'
                '<span><bdi>Numberblocks &amp; friends</bdi></span></li>')

        def page(service, show):
            return f'<script type="application/ld+json">{json.dumps(service)}</script>{show}'

        [row] = list(space_toon_grid(page(service, show)))
        self.assertEqual(row[0].date().day, 8)
        self.assertEqual(row[1].date().day, 9)
        self.assertEqual(row[2], "Numberblocks & friends")
        self.assertEqual(list(space_toon_grid(page({**service, "areaServed": "Saudi Arabia"}, show))), [])
        self.assertEqual(list(space_toon_grid(page(service, show.replace("23:45", "19:45")))), [])

    def test_ct_shared_keeps_dated_decko_and_art_on_one_slot(self):
        day = datetime(2026, 11, 8).date()

        def slot(clock, title, duration):
            return (f'<li><span class="progTime">{clock}</span>'
                    f'<a href="/show/" class="progTitle" idec="1">{title}<span></span></a>'
                    f'<span class="stopaz">{duration} minut</span></li>')

        def block(heading, slots):
            return f'<h3 class="kotva">{heading}</h3><ul class="programmeList">{slots}</ul>'

        page = '<link rel="canonical" href="https://www.ceskatelevize.cz/tv-program/08.11.2026/" />'
        page += block("Ráno od 06:00 na ČT :D", slot("06:00", "Matylda", 7) + slot("06:05", "Peppa", 6))
        page += block("Večer od 20:00 na ČT art", slot("20:15", "Jiří Suchý", 80))
        page += block("Po půlnoci od 24:00 na ČT art", slot("00:15", "Karel Cudlín", 53))
        page += block("Večer od 20:00 na ČT1", slot("20:30", "Jiný kanál", 40))
        rows = ct_shared_day(page, day)
        self.assertEqual([row[2] for row in rows], ["Matylda", "Peppa", "Jiří Suchý", "Karel Cudlín"])
        self.assertEqual(rows[0][1], rows[1][0])
        self.assertEqual(rows[-1][0].date().day, 9)
        self.assertEqual(rows[-1][0].astimezone(timezone.utc).date().day, 8)
        self.assertEqual(ct_shared_day(page.replace("08.11.2026", "09.11.2026"), day), [])

    def test_cbs_national_current_uses_corroborated_public_player_when_rundown_label_differs(self):
        now = datetime(2026, 11, 8, 5, 6, tzinfo=timezone.utc)
        rundown = {"navigation": {
            "dateUpdated": {"date": "2026-11-08 05:04:00", "timezone": "UTC"},
            "data": [{"edition": "CBSN-US", "type": "live", "epgSeriesName": "CBS Weekend News",
                      "rundown_start_time": "2026-11-08 05:00:00"}],
            "epgData": [
                {"from": "12:30:00 AM", "to": "01:00:00 AM", "title": "CBS Weekend News"},
                {"from": "01:00:00 AM", "to": "02:00:00 AM", "title": "60 Minutes"},
                {"from": "02:00:00 AM", "to": "03:00:00 AM", "title": "CBS Weekend News"},
            ],
        }}
        xml = ET.Element("tv")
        station = ET.SubElement(xml, "channel", id="464941")
        ET.SubElement(station, "display-name").text = "CBS News National Stream"
        xml.extend(programme("464941", start, stop, title) for start, stop, title in [
            ("20261108050000 +0000", "20261108060000 +0000", "48 Hours"),
            ("20261108060000 +0000", "20261108070000 +0000", "60 Minutes"),
            ("20261108070000 +0000", "20261108080000 +0000", "48 Hours"),
        ])
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "cbs-CBSN-US.json").write_text(json.dumps(rundown))
            (snapshot / "cbs-national-20261108.xml").write_bytes(ET.tostring(xml))
            page = snapshot / "cbs-national-official.html"
            page.write_text('<h3 class="channel-guide-item__title">CBS News 24/7</h3>'
                            '<p class="channel-guide-item__description">48 Hours</p>')
            self.assertEqual([row[2] for row in cbs_rows("CBSN-US", now, snapshot)], ["48 Hours", "60 Minutes"])
            page.write_text(page.read_text().replace("CBS News 24/7", "CBS News Miami"))
            self.assertEqual(list(cbs_rows("CBSN-US", now, snapshot)), [])

    def test_partial_guide_keeps_same_source_omissions_without_restoring_overlaps(self):
        feed = "LaUne.be@SD"
        corrected = programme(feed, "20261107180000 +0000", "20261107190000 +0000", "Nueva emisión")
        corrected.set("zapper-provenance", "https://example.com/guide/2026-11-07")
        prior = [
            programme(feed, "20261107173000 +0000", "20261107180000 +0000", "Vencida"),
            programme(feed, "20261107180000 +0000", "20261107183000 +0000", "Reemplazada"),
            programme(feed, "20261107183000 +0000", "20261107191500 +0000", "Solapada"),
            programme(feed, "20261107190000 +0000", "20261107200000 +0000", "Futura"),
        ]
        other = programme(feed, "20261107200000 +0000", "20261107210000 +0000", "Otro operador")
        other.set("zapper-provider-id", "official:alternate")
        wrong_host = programme(feed, "20261107210000 +0000", "20261107220000 +0000", "Otro sitio")
        wrong_host.set("zapper-provenance", "https://example.net/guide")
        fresh = {feed: [corrected]}
        result = preserve_uncontradicted_slots(fresh, {feed: [*prior, other, wrong_host]}, NOW)
        self.assertEqual([row.findtext("title") for row in result[feed]], ["Nueva emisión", "Futura"])
        self.assertEqual(fresh, {feed: [corrected]})

    def test_partial_guide_keeps_verified_future_outside_sparse_response(self):
        feed = "AlSaudiya.sa@SD"
        same_provider_future = programme(feed, "20261108020000 +0000", "20261108023000 +0000", "Vista anterior")
        fresh = {feed: [
            programme(feed, "20261107190000 +0000", "20261107200000 +0000", "Emisión de hoy"),
            programme(feed, "20261109030000 +0000", "20261109034000 +0000", "Emisión del lunes"),
        ]}
        cached = {feed: [same_provider_future]}
        result = preserve_uncontradicted_slots(fresh, cached, NOW)
        self.assertEqual(result[feed], [*fresh[feed], same_provider_future])

    def test_rtve_partial_reply_keeps_previously_verified_long_schedule(self):
        feed = "TVEStar.es@HD"
        refreshed = programme(feed, "20261107180000 +0000", "20261107190000 +0000", "Short API")
        future = programme(feed, "20261107190000 +0000", "20261107200000 +0000", "Portal")
        unverified = programme(feed, "20261107200000 +0000", "20261107210000 +0000", "Wrong portal")
        for row, source in (
            (refreshed, "https://api.rtve.es/api/lives/broadcasts/11186.json?locale=es"),
            (future, "https://www.rtve.es/play/tve-internacional/argentina/"),
            (unverified, "https://www.rtve.es/play/tve-internacional/chile/"),
        ):
            row.set("zapper-provider-id", "rtve-star:11186")
            row.set("zapper-provenance", source)
        result = preserve_uncontradicted_slots({feed: [refreshed]}, {feed: [future, unverified]}, NOW)
        self.assertEqual(result[feed], [refreshed, future])

    def test_medi1_arabic_keeps_dated_upcoming_programmes_and_overnight_gap(self):
        route = "https://cdn.live.easybroadcast.io/abr_corp/83_medi1tv-arabic_g90v4ec/playlist.m3u8"
        entry = {"id": "iptv:Medi1TVArabic.ma", "tvgID": "Medi1TVArabic.ma@SD", "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 2, 30, tzinfo=timezone.utc)
        slot = {"date": "08/11/2026", "langue": "ar", "heure": "05:15", "titre": "عن قرب"}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "medi1-arabic-2026-11-08.json").write_text(json.dumps([
                slot, dict(slot, heure="05:30", titre="نافذة"),
                dict(slot, date="07/11/2026", heure="05:20", titre="Wrong day"),
            ]))
            guide = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual(programme_state(guide, now), "agenda-verificada")
            self.assertEqual([(row.get("start"), row.get("stop"), row.findtext("title")) for row in guide],
                             [("20261108051500 +0000", "20261108053000 +0000", "عن قرب")])
            wrong = dict(entry, sources=[{"url": route.replace("medi1tv-arabic", "medi1tv-maghreb")}])
            self.assertEqual(programmes_for_expansion([wrong], None, now, snapshot=snapshot), {})

    def test_canal13_guatemala_distinguishes_the_channel_and_overnight_closure(self):
        route = "http://190.61.101.11:7050/play/a07f/index.m3u8"
        entry = {"id": "iptv:Canal13.gt", "tvgID": "Canal13.gt@SD", "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 3, 48, tzinfo=timezone.utc)
        page = (
            'Ahora: sábado, 7 de noviembre de 2026, 21:47:37 GMT-6 (Guatemala)'
            '<h5>Hoy - 7/11/26 - Sábado</h5><table>'
            '<a href="/es/gt/c/canal-13/072100/flash" title="21:00 Flash">Flash</a>'
            '<a href="/es/gt/c/canal-13/072200/los-borgias" title="22:00 Los Borgias">Los Borgias</a>'
            '<a href="/es/gt/c/canal-13/072300/el-pantera" title="23:00 El pantera">El pantera</a></table>'
            '<h5>Mañana - 8/11/26 - Domingo</h5><table>'
            '<a href="/es/gt/c/canal-13/080000/fin" title="00:00 Fin de transmisión">Fin de transmisión</a>'
            '<a href="/es/gt/c/canal-13/080600/camara" title="06:00 Cámara loca">Cámara loca</a>'
            '<a href="/es/gt/c/canal-13/080700/misa" title="07:00 Santa misa">Santa misa</a></table>'
        )
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "canal13-guatemala.html").write_text(page)
            rows = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([(row.get("start"), row.get("stop"), row.findtext("title")) for row in rows],
                             [("20261108030000 +0000", "20261108040000 +0000", "Flash"),
                              ("20261108040000 +0000", "20261108050000 +0000", "Los Borgias"),
                              ("20261108050000 +0000", "20261108060000 +0000", "El pantera"),
                              ("20261108120000 +0000", "20261108130000 +0000", "Cámara loca")])
            wrong = dict(entry, sources=[{"url": "http://168.196.127.137:6001/play/a064/index.m3u8"}])
            self.assertEqual(programmes_for_expansion([wrong], None, now, snapshot=snapshot), {})

    def test_rtve_star_expands_only_with_two_exact_official_schedule_anchors(self):
        route = "https://rtvelivestream-rtveplayplus.rtve.es/rtvesec/int/star_main_1080.m3u8"
        entry = {"id": "iptv:TVEStar.es", "tvgID": "TVEStar.es@HD", "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 4, 42, tzinfo=timezone.utc)
        short = {"page": {"items": [{"id": "11186", "canal": "Star HD", "idGolumi": "rtvesec/int/star_main_",
            "now": {"inicio": "08-11-2026 05:20:00", "fin": "08-11-2026 06:30:00", "titulo": "LOS MISTERIOS DE LAURA"},
            "next": {"inicio": "08-11-2026 06:30:00", "fin": "08-11-2026 07:35:00", "titulo": "DESAPARECIDA"},
        }]}}
        def card(start, stop, name, episode=""):
            return (f'<div class="item trigger" data-title="{name}" data-begindate="2026-11-08 {start}" '
                    f'data-enddate="2026-11-08 {stop}"><span class="cat-detalle">{episode}</span></div>')
        guide = ('<section><div id="guia-canal-int-sta">'
                 + card("05:20", "06:30", "Los Misterios De Laura")
                 + card("06:30", "07:35", "Desaparecida")
                 + card("07:35", "09:15", "Cine", "Akelarre")
                 + '</div></section>')
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "rtve-star.json").write_text(json.dumps(short))
            filename = snapshot / "rtve-star-argentina.html"
            filename.write_text(guide)
            rows = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([(row.findtext("title"), row.findtext("sub-title")) for row in rows],
                             [("LOS MISTERIOS DE LAURA", None), ("DESAPARECIDA", None), ("Cine", "Akelarre")])
            filename.write_text(guide.replace("06:30", "06:35"))
            self.assertEqual(len(programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]), 2)

    def test_bandamax_uses_mexican_schedule_only_for_the_verified_route(self):
        base = "https://www.formulatv.com/mx/programacion/bandamax/"
        now = datetime(2026, 11, 8, 5, 18, tzinfo=timezone.utc)
        route = "https://streaming.onnethn.com/Bandamax/index.m3u8"
        entry = {"id": "iptv:Bandamax.mx", "tvgID": "Bandamax.mx@SD", "sources": [{"url": route}]}
        payload = {"@graph": [{"@type": "ItemList", "@id": base + "#programacion", "itemListElement": [
            {"item": {"name": "La cantina del tunco", "startDate": "2026-11-07T22:35:00-06:00",
                      "endDate": "2026-11-07T23:30:00-06:00", "publishedOn": {"@id": base + "#canal"}}},
            {"item": {"name": "El show del hombre", "startDate": "2026-11-07T23:30:00-06:00",
                      "endDate": "2026-11-08T00:25:00-06:00", "publishedOn": {"@id": base + "#canal"}}},
        ]}]}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "formula-bandamax-hoy.html").write_text(
                '<script type="application/ld+json">' + json.dumps(payload) + '</script>')
            rows = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([(row.findtext("title"), row.get("start")) for row in rows],
                             [("La cantina del tunco", "20261108043500 +0000"),
                              ("El show del hombre", "20261108053000 +0000")])
            wrong = dict(entry, sources=[{"url": route.replace("/Bandamax/", "/another-edition/")}])
            self.assertEqual(programmes_for_expansion([wrong], None, now, snapshot=snapshot), {})

    def test_cablenoticias_accepts_only_the_verified_colombian_station(self):
        station = "https://www.formulatv.com/co/programacion/cablenoticias/"
        route = "http://181.78.211.244:8005/play/a09u/index.m3u8"
        entry = {"id": "iptv:Cablenoticias.co", "tvgID": "Cablenoticias.co@SD", "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 5, 25, tzinfo=timezone.utc)
        items = [
            {"item": {"name": title, "startDate": start, "endDate": stop,
                      "publishedOn": {"@id": service + "#canal"}}}
            for title, start, stop, service in [
                ("La otra cara de la moneda", "00:00", "01:00", station),
                ("Testigo Directo", "01:00", "01:30", station),
                ("Otro canal", "01:30", "02:00", station.replace("/co/", "/mx/")),
            ]
        ]
        for item in items:
            for key in ("startDate", "endDate"):
                item["item"][key] = "2026-11-08T" + item["item"][key] + ":00-05:00"
        payload = {"@graph": [{"@type": "ItemList", "@id": station + "#programacion",
                              "itemListElement": items}]}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "formula-cablenoticias-hoy.html").write_text(
                '<script type="application/ld+json">' + json.dumps(payload) + '</script>')
            rows = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([(row.findtext("title"), row.get("start")) for row in rows],
                             [("La otra cara de la moneda", "20261108050000 +0000"),
                              ("Testigo Directo", "20261108060000 +0000")])
            wrong = dict(entry, sources=[{"url": route.replace("/a09u/", "/a08u/")}])
            self.assertEqual(programmes_for_expansion([wrong], None, now, snapshot=snapshot), {})

    def test_wonder_uses_utc_dated_schedule_only_for_observed_uk_stream(self):
        route = "https://lds-wonder-rakuten.amagi.tv/playlist.m3u8"
        entry = {"id": "iptv:Wonder.uk", "tvgID": "Wonder.uk@SD", "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 23, 45, tzinfo=timezone.utc)
        canonical = '<link rel="canonical" href="https://www.filmtheque.com/en/guide/channel/wonder-uk"/>'

        def item(date, clocks, title, episode):
            return (f'<li><time dateTime="{date}">{clocks}</time><span><bdi dir="auto">{title}</bdi>'
                    f'<bdi dir="auto" class="text-secondary">· <!-- -->{episode}</bdi>'
                    '<bdi dir="auto" class="text-xs text-secondary">Explore</bdi></span></li>')

        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            day = snapshot / "wonder-uk-2026-11-08.html"
            day.write_text(canonical + item("Sun Nov 08 2026 23:40:00 GMT+0000 (Coordinated Universal Time)",
                                             "23:40<!-- -->–<!-- -->00:25", "Ultimate Vehicles", "Fastest Ships"))
            tomorrow = snapshot / "wonder-uk-2026-11-09.html"
            tomorrow.write_text(canonical + item("Mon Nov 09 2026 00:25:00 GMT+0000 (Coordinated Universal Time)",
                                                  "00:25–01:20", "Ultimate Vehicles", "Biggest Aircraft"))
            rows = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([(row.get("stop"), row.findtext("sub-title")) for row in rows],
                             [("20261109002500 +0000", "Fastest Ships"), ("20261109012000 +0000", "Biggest Aircraft")])
            wrong = dict(entry, sources=[{"url": route.replace("rakuten", "another-edition")}])
            self.assertEqual(programmes_for_expansion([wrong], None, now, snapshot=snapshot), {})
            day.write_text(day.read_text().replace("/wonder-uk", "/wonder-au"))
            tomorrow.write_text(tomorrow.read_text().replace("Nov 09", "Nov 10"))
            self.assertEqual(programmes_for_expansion([entry], None, now, snapshot=snapshot), {})

    def test_wonder_respects_absolute_starts_when_the_guide_prints_british_summer_time(self):
        route = "https://lds-wonder-rakuten.amagi.tv/playlist.m3u8"
        entry = {"id": "iptv:Wonder.uk", "tvgID": "Wonder.uk@SD", "sources": [{"url": route}]}
        now = datetime(2026, 7, 1, 11, 15, tzinfo=timezone.utc)
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "wonder-uk-2026-07-01.html").write_text(
                '<link rel="canonical" href="https://www.filmtheque.com/en/guide/channel/wonder-uk"/>'
                '<li><time dateTime="Wed Jul 01 2026 11:00:00 GMT+0000 (Coordinated Universal Time)">'
                '12:00–13:00</time><span><bdi dir="auto">Inside Air Cargo</bdi></span></li>')
            rows = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([row.get("stop") for row in rows], ["20260701120000 +0000"])

    def test_intravel_uses_the_observed_uk_schedule_only_for_the_exact_route(self):
        route = "https://amg00861-amg00861c10-rakuten-uk-3152.playouts.now.amagi.tv/playlist.m3u8"
        entry = {"id": "iptv:INTRAVEL.nl", "tvgID": "INTRAVEL.nl@Philippines", "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 3, 42, tzinfo=timezone.utc)
        xml = (
            '<tv><channel id="GB400003BM"><display-name>INTRAVEL</display-name></channel>'
            '<programme channel="GB400003BM" start="20261108032750 +0000" stop="20261108043155 +0000">'
            "<title>Ainsley's Market Menu</title><sub-title>Logan</sub-title></programme></tv>"
        )
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "samsung-gb.xml.gz").write_bytes(gzip.compress(xml.encode()))
            rows = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([(row.findtext("sub-title"), row.get("zapper-provider-id")) for row in rows],
                             [("Logan", "samsung-gb:GB400003BM")])
            wrong = dict(entry, sources=[{"url": route.replace("rakuten-uk-3152", "different-edition")}])
            self.assertEqual(programmes_for_expansion([wrong], None, now, snapshot=snapshot), {})

    def test_eldeber_honors_the_announced_launch_day_and_bolivian_midnight(self):
        route = "https://master.tucableip.com/eldebertv/index.m3u8"
        entry = {"id": "iptv:ELDEBER.bo", "tvgID": "ELDEBER.bo@SD", "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 4, tzinfo=timezone.utc)
        days = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")
        schedule = {day: [] for day in days}
        schedule["Domingo"] = [["Todavía no estrenado", "06:00 a 08:00 hrs."]]
        schedule["Lunes"] = [["A Primera Hora", "06:00 a 08:00 hrs."],
                             ["Programación Variada", "08:00 a 09:00 hrs."],
                             ["Dinero", "20:00 a 21:00 hrs."],
                             ["Noticias Internacionales DW", "23:00 a 00:00 hrs."]]
        page = 'timeZone: "America/La_Paz"; var schedule = ' + json.dumps(schedule) + ";"
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "eldeber-home.html").write_text(page)
            rows = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual(programme_state(rows, now), "agenda-verificada")
            self.assertEqual([(row.get("start"), row.get("stop"), row.findtext("title")) for row in rows],
                             [("20261109100000 +0000", "20261109120000 +0000", "A Primera Hora"),
                              ("20261110000000 +0000", "20261110010000 +0000", "Dinero"),
                              ("20261110030000 +0000", "20261110040000 +0000", "Noticias Internacionales DW")])
            wrong = dict(entry, sources=[{"url": route.replace("eldebertv", "other")}])
            self.assertEqual(programmes_for_expansion([wrong], None, now, snapshot=snapshot), {})
            (snapshot / "eldeber-home.html").write_text(page.replace("20:00 a 21:00", "18:00 a 19:00"))
            self.assertEqual(programmes_for_expansion([entry], None, now, snapshot=snapshot), {})

    def test_otv_lebanon_reads_dated_events_for_the_exact_signal(self):
        route = "https://otv.hibridcdn.net/otv/tv_abr/playlist.m3u8"
        entry = {"id": "iptv:OTV.lb", "tvgID": "OTV.lb@SD", "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 4, 17, tzinfo=timezone.utc)
        station = {"@type": "TelevisionChannel", "identifier": "OTVLB", "name": "OTV"}
        event = {"@type": "BroadcastEvent", "name": "Jenan Neswan", "publishedOn": station,
                 "startDate": "2026-11-08T04:15:00.000Z", "endDate": "2026-11-08T05:00:00.000Z"}
        listing = {"mainEntity": {"@type": "ItemList", "itemListElement": [
            {"item": event},
            {"item": dict(event, name="Wrong market", publishedOn=dict(station, identifier="OTVGH"))},
            {"item": dict(event, name="No offset", startDate="2026-11-08T04:15:00")},
        ]}}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "otv-lebanon.html").write_text(
                f'<script type="application/ld+json">{json.dumps(listing)}</script>')
            rows = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([(row.get("start"), row.get("stop"), row.findtext("title")) for row in rows],
                             [("20261108041500 +0000", "20261108050000 +0000", "Jenan Neswan")])
            wrong = dict(entry, sources=[{"url": "https://example.com/otv"}])
            self.assertEqual(programmes_for_expansion([wrong], None, now, snapshot=snapshot), {})

    def test_citytv_bogota_requires_a_colombian_guide_and_the_exact_source(self):
        route = "http://138.121.15.230:9002/CITY-TV/index.m3u8"
        entry = {"id": "iptv:CitytvBogota.co", "tvgID": "CitytvBogota.co@SD", "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 4, 13, tzinfo=timezone.utc)
        page = (
            '<a href="https://citytv.eltiempo.com/">Citytv</a> GMT-5 (Colombia)'
            '<h5>Hoy - 7/11/26 - Sábado</h5><table>'
            '<a href="/es/co/c/city-tv/072230/reporte" title="22:30 Arriba, Bogotá - Reporte semanal"></a></table>'
            '<h5>Mañana - 8/11/26 - Domingo</h5><table>'
            '<a href="/es/co/c/city-tv/080000/ads" title="00:00 Programación comercial"></a>'
            '<a href="/es/co/c/city-tv/080030/error" title="01:30 Conflicting clock"></a>'
            '<a href="/es/co/c/city-tv/080100/arriba" title="01:00 Arriba Bogotá"></a>'
            '<a href="/es/co/c/city-tv/080330/fin" title="03:30 Fuera del aire"></a></table>'
        )
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            filename = snapshot / "citytv-bogota.html"
            filename.write_text(page)
            rows = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([(row.get("start"), row.get("stop"), row.findtext("title")) for row in rows],
                             [("20261108033000 +0000", "20261108050000 +0000", "Arriba, Bogotá - Reporte semanal"),
                              ("20261108060000 +0000", "20261108083000 +0000", "Arriba Bogotá")])
            wrong = dict(entry, sources=[{"url": "https://example.com/citytv"}])
            self.assertEqual(programmes_for_expansion([wrong], None, now, snapshot=snapshot), {})
            filename.write_text(page.replace("GMT-5 (Colombia)", "GMT-6 (Guatemala)"))
            self.assertEqual(programmes_for_expansion([entry], None, now, snapshot=snapshot), {})

    def test_traveltv_keeps_dated_day_and_night_slots_for_the_official_route(self):
        route = "https://streamer103.neterra.tv/travel/live.m3u8"
        entry = {"id": "iptv:TravelTV.bg", "tvgID": "TravelTV.bg@SD", "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 5, 45, tzinfo=timezone.utc)

        def grid(first_hour, hours, title, dates="8 ноември (неделя) &#8211; 15 ноември (неделя)"):
            clock = "".join(f"<th>{hour}:{minute:02}</th>"
                            for hour in range(first_hour, first_hour + hours) for minute in (0, 30))
            cell = f'<div class="ws-item-title ws-item-title-1">{title}</div>'
            rows = f'<tr class="datarow{hours * 2 - 1}"><td></td></tr><tr class="datarow1"><td>{cell}</td></tr>'
            return (f'<h4>{dates}</h4>'
                    f'<table class="verticalheader">{clock}</table>'
                    f'<table class="vertical1"><th class="rowheader">Неделя</th>{rows}</table>')

        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "traveltv-night-shows-this-week.html").write_text(grid(0, 8, "Импресии"))
            day = snapshot / "traveltv-programme.html"
            day.write_text(grid(8, 16, "Българската история"))
            guide = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([(row.get("start"), row.get("stop"), row.findtext("title")) for row in guide],
                             [("20261108053000 +0000", "20261108060000 +0000", "Импресии"),
                              ("20261108213000 +0000", "20261108220000 +0000", "Българската история")])
            wrong = dict(entry, sources=[{"url": route.replace("/travel/", "/other/")}])
            self.assertEqual(programmes_for_expansion([wrong], None, now, snapshot=snapshot), {})
            day.write_text(grid(8, 16, "Стара програма",
                                "1 ноември (неделя) &#8211; 7 ноември (събота)"))
            guide = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([row.findtext("title") for row in guide], ["Импресии"])

    def test_pbo_weekly_schedule_preserves_lima_broadcast_day_after_midnight(self):
        route = "https://live-evg11.tv360.bitel.com.pe/bitel/pbo_abr/playlist.m3u8"
        entry = {"id": "iptv:PBO.pe", "tvgID": "PBO.pe@SD", "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 5, 15, tzinfo=timezone.utc)
        page = (
            "Hora de Lima · TV, radio 91.9 FM y digital en simultáneo"
            '<div class="schedule__panel" data-day="6">'
            '<li class="schedule__item" data-start="1380" data-end="1470">'
            '<span class="schedule__repe">Repetición</span><strong>A la Cuarta</strong></li>'
            '<div class="schedule__panel" data-day="7">'
            '<li class="schedule__item" data-start="300" data-end="360"><strong>Documentales</strong></li>'
        )
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "pbo-programacion.html").write_text(page)
            guide = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([(row.get("start"), row.get("stop"), row.findtext("sub-title")) for row in guide],
                             [("20261108040000 +0000", "20261108053000 +0000", "Repetición"),
                              ("20261108100000 +0000", "20261108110000 +0000", None)])
            wrong = dict(entry, sources=[{"url": route.replace("pbo_abr", "other_abr")}])
            self.assertEqual(programmes_for_expansion([wrong], None, now, snapshot=snapshot), {})

    def test_chilean_13_go_signal_identity_and_usach_placeholder_gap(self):
        entries = [
            {"id": "iptv:13Festival.cl", "tvgID": "13Festival.cl@SD", "sources": [
                {"url": "https://origin.dpsgo.com/ssai/event/Nftd0fM2SXasfDlRphvUsg/master.m3u8"}]},
            {"id": "iptv:TVUsach.cl", "tvgID": "TVUsach.cl@SD", "sources": [
                {"url": "https://stv4.janus.cl/playlist/stream.m3u8"}]},
        ]
        now = datetime(2026, 11, 8, 3, 12, tzinfo=timezone.utc)
        programme = {"beginTime": "2026-11-08T02:00:54+00:00", "endTime": "2026-11-08T03:16:31+00:00",
                     "title": "Grandes noches de Viña", "episodeTitle": "Juan Gabriel"}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            guide_file = snapshot / "13go-epg-13festival.json"
            station = {"channel": "13Festival", "channelCode": "1844", "updated": "2026-11-08T03:10:20+00:00",
                       "events": [programme]}
            guide_file.write_text(json.dumps(station))
            (snapshot / "tvusach-schedule-2026-11-08.json").write_text(json.dumps(
                {"schedule": [{"title": "TV Usach", "programId": 1, "start": 1794110400, "end": 1794196740}]}))
            (snapshot / "tvusach-schedule-2026-11-09.json").write_text(json.dumps(
                {"schedule": [{"title": "Línea Uno", "programId": 6, "start": 1794220200, "end": 1794225600}]}))
            guides = programmes_for_expansion(entries, None, now, snapshot=snapshot)
            self.assertEqual([e.findtext("sub-title") for e in guides["13Festival.cl@SD"]], ["Juan Gabriel"])
            self.assertEqual([e.findtext("title") for e in guides["TVUsach.cl@SD"]], ["Línea Uno"])
            self.assertEqual(programme_state(guides["TVUsach.cl@SD"], now), "agenda-verificada")
            guide_file.write_text(json.dumps(dict(station, channelCode="1814")))
            self.assertNotIn("13Festival.cl@SD", programmes_for_expansion(entries, None, now, snapshot=snapshot))
            guide_file.write_text(json.dumps(dict(station, updated="2026-11-01T03:10:20+00:00")))
            self.assertNotIn("13Festival.cl@SD", programmes_for_expansion(entries, None, now, snapshot=snapshot))

    def test_trace_latina_exact_guide_and_al_arabiya_business_verified_gap(self):
        trace_route = "https://channels.trace.plus/Traceprod/LATINA_hd/index.m3u8"
        business_route = "https://live.alarabiya.net/alarabiapublish/aswaaq.smil/playlist.m3u8"
        trace = {"id": "iptv:TraceLatina.fr", "tvgID": "TraceLatina.fr@SD", "sources": [{"url": trace_route}]}
        business = {"id": "iptv:AlArabiyaBusiness.ae", "tvgID": "AlArabiyaBusiness.ae@SD",
                    "sources": [{"url": business_route}]}
        now = datetime(2026, 11, 8, 3, 5, tzinfo=timezone.utc)
        trace_slot = {"channelName": "Trace Latina", "title": "Viva Latina",
                      "startTime": "2026-11-08T03:00:00Z", "endTime": "2026-11-08T06:00:00Z"}
        market = {"actualFrom": "2026-11-08T06:00:00+00:00", "actualTo": "2026-11-08T06:59:00+00:00",
                  "title": "Market Open"}
        empty = dict(market, actualFrom="2026-11-08T00:00:00+00:00",
                     actualTo="2026-11-08T06:00:00+00:00", title="TV guide is not available")
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "trace-latina-today.html").write_text("".join(
                f'<a data-program="{escape(json.dumps(item), quote=True)}"></a>'
                for item in (trace_slot, dict(trace_slot, channelName="Trace Urban"))))
            (snapshot / "shahid-1003218-2026-11-08.json").write_text(json.dumps(
                {"items": [{"channelId": "1003218", "items": [empty, market]}]}))
            guides = programmes_for_expansion([trace, business], None, now, snapshot=snapshot)
            self.assertEqual([p.findtext("title") for p in guides[trace["tvgID"]]], ["Viva Latina"])
            self.assertEqual([p.findtext("title") for p in guides[business["tvgID"]]], ["Market Open"])
            self.assertEqual(programme_state(guides[business["tvgID"]], now), "agenda-verificada")
            self.assertEqual(programmes_for_expansion(
                [dict(trace, sources=[{"url": trace_route.replace("LATINA", "URBAN")}])], None, now,
                snapshot=snapshot), {})

    def test_tv5monde_info_guide_keeps_news_edition_and_exact_public_source(self):
        feed = "TV5MondeInfo.fr@SD"
        route = "https://tvf-tvfm.otteravision.com/tvf/tvfm/tvfm_720.m3u8"
        entry = {"id": "iptv:tv5mondeinfo.fr", "tvgID": feed, "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 4, 38, tzinfo=timezone.utc)
        slot = {"channelName": "TV5 Monde Info", "title": "64' Débat",
                "startTime": "2026-11-08T04:30:00Z", "endTime": "2026-11-08T04:50:00Z"}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "tv5-monde-info-today.html").write_text("".join(
                f'<a data-program="{escape(json.dumps(item), quote=True)}"></a>'
                for item in (slot, dict(slot, channelName="TV5 Monde Europe", title="Fiction"))))
            guide = programmes_for_expansion([entry], None, now, snapshot=snapshot)[feed]
            self.assertEqual([(row.get("start"), row.get("stop"), row.findtext("title")) for row in guide],
                             [("20261108043000 +0000", "20261108045000 +0000", "64' Débat")])
            wrong = dict(entry, sources=[{"url": "https://example.com/another-edition.m3u8"}])
            self.assertEqual(programmes_for_expansion([wrong], None, now, snapshot=snapshot), {})

    def test_rakuten_guide_uses_exact_feed_language_and_dated_episodes(self):
        route = "https://amg01082-cna-amg01082c1-rlaxx-us-11304.playouts.now.amagi.tv/playlist.m3u8"
        entry = {"id": "iptv:CNAOriginals.sg", "tvgID": "CNAOriginals.sg@SD", "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 3, 12, tzinfo=timezone.utc)
        slot = {"starts_at": "2026-11-08T03:00:03+00:00", "ends_at": "2026-11-08T04:00:01+00:00",
                "title": "Singapore Hour_2025_0_18_Episode 18 - Sweet, Salty and Slightly Strange"}
        station = {"id": "cna", "labels": {"languages": [{"id": "ENG"}]},
                   "live_programs": [slot, dict(slot, starts_at="2026-11-08T03:01:00")]}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "rakuten-uk-page-1.json").write_text(json.dumps({"data": [dict(station, id="cna-news")]}))
            p = snapshot / "rakuten-uk-page-2.json"
            p.write_text(json.dumps({"data": [station]}))
            guide = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([(row.get("start"), row.get("stop"), row.findtext("title"), row.findtext("sub-title"))
                              for row in guide],
                             [("20261108030003 +0000", "20261108040001 +0000", "Singapore Hour",
                               "Sweet, Salty and Slightly Strange")])
            wrong = dict(entry, sources=[{"url": route.replace("rlaxx-us", "other")}])
            self.assertEqual(programmes_for_expansion([wrong], None, now, snapshot=snapshot), {})
            station["labels"]["languages"][0]["id"] = "FRA"
            p.write_text(json.dumps({"data": [station]}))
            self.assertEqual(programmes_for_expansion([entry], None, now, snapshot=snapshot), {})

    def test_terra_mater_and_estrella_guides_bind_exact_editions(self):
        terra = ("https://53ff3982.wurl.com/master/f36d25e7e52f1ba8d7e56eb859c636563214f541/"
                 "UmxheHhUVi1ldV9UZXJyYU1hdGVyV2lsZF9ITFM/playlist.m3u8")
        sources = (
            ("TerraMaterWILD.de@German", terra, "samsung-de.xml.gz", "DEAJ32000012H",
             "Terra Mater WILD", "Naturtalente"),
            ("EstrellaNews.us@SD", "https://estrella-news-oando.amagi.tv/playlist.m3u8",
             "tubi-estrella.xml", "560215", "Estrella News", "24 Horas"),
        )
        now = datetime(2026, 11, 8, 2, 45, tzinfo=timezone.utc)
        for feed, route, filename, key, station, title in sources:
            with self.subTest(feed=feed), TemporaryDirectory() as directory:
                snapshot = Path(directory)
                payload = (f'<tv><channel id="{key}"><display-name>{station}</display-name></channel>'
                           f'<programme channel="{key}" start="20261108023000 +0000" '
                           f'stop="20261108030000 +0000"><title>{title}</title></programme></tv>').encode()
                path = snapshot / filename
                path.write_bytes(gzip.compress(payload) if filename.endswith(".gz") else payload)
                entry = {"id": "iptv:" + feed.split("@")[0], "tvgID": feed, "sources": [{"url": route}]}
                guide = programmes_for_expansion([entry], None, now, snapshot=snapshot)
                self.assertEqual([row.findtext("title") for row in guide[feed]], [title])
                wrong = dict(entry, sources=[{"url": route.replace("/playlist.m3u8", "/other.m3u8")}])
                self.assertEqual(programmes_for_expansion([wrong], None, now, snapshot=snapshot), {})

    def test_times_brasil_uses_dated_same_channel_blocks_across_midnight(self):
        route = ("https://ssai2-ads.api.leiniao.com/global-adinsertion-api/hls/live/v2/"
                 "21fdb6dd7491406bba86497a24390a25/playlist.m3u8")
        entry = {"id": "iptv:TimesBrasil.br", "tvgID": "TimesBrasil.br@SD", "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 2, 49, tzinfo=timezone.utc)
        start = datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc)
        stop = datetime(2026, 11, 8, 5, 0, tzinfo=timezone.utc)
        block = (
            '<div class="program"><a href="/sinopse/1/CBC/21h00/2026-11-07/semana-prime">Semana Prime</a>'
            '<div class="details">21h00 - 02h00 | Variedades</div>'
            f'<div data-start="{int(start.timestamp())}" data-end="{int(stop.timestamp())}"></div>'
        )
        with TemporaryDirectory() as tmp:
            snapshot = Path(tmp)
            path = snapshot / "tvplus-cnbc.html"
            path.write_text(block + block.replace("/CBC/", "/CNN/"))
            guide = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([(row.get("start"), row.get("stop"), row.findtext("title")) for row in guide],
                             [("20261108000000 +0000", "20261108050000 +0000", "Semana Prime")])
            wrong = dict(entry, sources=[{"url": route.replace("21fdb", "other")}])
            self.assertEqual(programmes_for_expansion([wrong], None, now, snapshot=snapshot), {})
            path.write_text(block.replace(str(int(stop.timestamp())), "1"))
            self.assertEqual(programmes_for_expansion([entry], None, now, snapshot=snapshot), {})

    def test_sportitalia_uses_recent_eit_with_explicit_boundaries(self):
        route = "https://edge-001.streamup.eu/sportitalia/sihd_abr/playlist.m3u8"
        entry = {"id": "iptv:Sportitalia.it", "tvgID": "Sportitalia.it@SD", "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 2, 35, tzinfo=timezone.utc)
        row = ("<table><tr><td>08/11</td><td>02h00&gt;04h00</td><td>?</td><td>ita</td>"
               "<td>Campionato Argentino Boca Jr.-Defensa Y Justicia</td><td>Dalla Bombonera</td></tr></table>")
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            page = snapshot / "sportitalia-eit-80.html"
            page.write_text("Scan date : 08/11/2026 01:28<br>" + row)
            guide = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([(row.get("start"), row.get("stop"), row.findtext("title")) for row in guide],
                             [("20261108010000 +0000", "20261108030000 +0000",
                               "Campionato Argentino Boca Jr.-Defensa Y Justicia")])
            wrong = dict(entry, sources=[{"url": route.replace("/sihd_abr/", "/si24/")}])
            self.assertEqual(programmes_for_expansion([wrong], None, now, snapshot=snapshot), {})
            page.write_text("Scan date : 08/11/2025 01:28<br>" + row)
            self.assertEqual(programmes_for_expansion([entry], None, now, snapshot=snapshot), {})

    def test_2m_maroc_uses_exact_route_and_structured_bounds(self):
        route = "http://185.9.2.18/chid_218/index.m3u8"
        entry = {"id": "iptv:2M.ma", "tvgID": "2M.ma@SD", "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 2, 12, tzinfo=timezone.utc)
        slot = {"channelName": "2M Maroc", "title": "2M Mag",
                "startTime": "2026-11-08T02:00:00.000Z", "endTime": "2026-11-08T03:05:00.000Z"}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            page = snapshot / "2m-maroc-today.html"
            other = dict(slot, channelName="Al Aoula", title="Other edition")
            no_offset = dict(slot, title="Unverifiable", startTime="2026-11-08T02:00:00.000")
            page.write_text("".join(f'<a data-program="{escape(json.dumps(item), quote=True)}"></a>'
                                    for item in (slot, other, no_offset)))
            guide = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([(row.get("start"), row.get("stop"), row.findtext("title")) for row in guide],
                             [("20261108020000 +0000", "20261108030500 +0000", "2M Mag")])
            wrong = dict(entry, sources=[{"url": route.replace("chid_218", "chid_219")}])
            self.assertEqual(programmes_for_expansion([wrong], None, now, snapshot=snapshot), {})

    def test_thai_3hd_uses_dated_official_schedule_without_restricted_slots_or_inserts(self):
        route = "https://live-us1.thaimomo.com/live-as/ch3hd-3/playlist.m3u8"
        entry = {"id": "iptv:3HD.th", "tvgID": "3HD.th@SD", "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 2, 11, tzinfo=timezone.utc)
        slot = {"day": "2026-11-08", "start": "08:55", "end": "10:15", "title": "เพลิงพรางเทียน",
                "block": "0", "allow_country": "ALL"}
        rows = [slot, dict(slot, start="09:20", end="09:30", title="Shopping"),
                dict(slot, start="10:15", end="11:15", block="1", allow_country="TH", title="Restricted"),
                dict(slot, start="23:55", end="00:35", title="168 ชั่วโมง")]
        result = {"date": "20261108", "schedules": rows}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            page = snapshot / "ch3plus-33hd.json"
            page.write_text(json.dumps({"code": 200, "result": result}))
            guide = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([(row.get("start"), row.get("stop"), row.findtext("title")) for row in guide], [
                ("20261108015500 +0000", "20261108031500 +0000", "เพลิงพรางเทียน"),
                ("20261108165500 +0000", "20261108173500 +0000", "168 ชั่วโมง"),
            ])
            wrong = dict(entry, sources=[{"url": route.replace("ch3hd-3", "another-feed")}])
            self.assertEqual(programmes_for_expansion([wrong], None, now, snapshot=snapshot), {})
            self.assertEqual(programmes_for_expansion([entry], None, now.replace(day=9), snapshot=snapshot), {})

    def test_cnn_international_uses_europe_middle_east_and_africa_station(self):
        route = ("https://d3bp6dwmpbdajl.cloudfront.net/v1/master/3722c60a815c199d9c0ef36c5b73da68a62b09d1/cc-ury0meh5m4nzm/index.m3u8")
        entry = {"id": "iptv:CNNInternational.us", "tvgID": "CNNInternational.us@Europe",
                 "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 2, 21, tzinfo=timezone.utc)
        eastern = ('<select id="timezone_selector"><option value="America/New_York" selected>'
                   'Eastern</option></select>')
        row = ('<div data-callsign="CNNI" data-st="2026-11-07 21:00:00" data-duration="30" '
               'data-showName="World Sport"></div>')
        wrong_station = row.replace('data-callsign="CNNI"', 'data-callsign="CNN"')
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "tvpassport-3012-2026-11-07.html").write_text(eastern + row + wrong_station)
            guide = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([(row.get("start"), row.get("stop"), row.findtext("title")) for row in guide],
                             [("20261108020000 +0000", "20261108023000 +0000", "World Sport")])
            wrong = dict(entry, sources=[{"url": route.replace("/cc-ury0meh5m4nzm/", "/other/")}])
            self.assertEqual(programmes_for_expansion([wrong], None, now, snapshot=snapshot), {})

    def test_telemundo_east_accepts_only_eastern_feed_and_its_own_callsign(self):
        route = "https://streamer1.nexgen.bz/TELEMUNDO/index.m3u8"
        entry = {"id": "iptv:Telemundo.us", "tvgID": "Telemundo.us@EastHD", "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 2, 21, tzinfo=timezone.utc)
        eastern = ('<select id="timezone_selector"><option value="America/New_York" selected>'
                   'Eastern</option></select>')
        event = ('<div data-callsign="MUN" data-st="2026-11-07 19:55:00" data-duration="125" '
                 'data-showName="Fútbol Mexicano Primera División" '
                 'data-episodeTitle="Tigres UANL vs. Cruz Azul"></div>')
        wrong_station = event.replace('data-callsign="MUN"', 'data-callsign="L&amp;C"')
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "tvpassport-1521-2026-11-07.html").write_text(eastern + event + wrong_station)
            guide = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([(row.get("start"), row.get("stop"), row.findtext("sub-title")) for row in guide],
                             [("20261108005500 +0000", "20261108030000 +0000", "Tigres UANL vs. Cruz Azul")])
            wrong = dict(entry, sources=[{"url": route.replace("/TELEMUNDO/", "/other/")}])
            self.assertEqual(programmes_for_expansion([wrong], None, now, snapshot=snapshot), {})

    def test_law_crime_uses_exact_station_and_eastern_listing_day_across_midnight(self):
        route = "https://a-cdn.klowdtv.com/live3/law_720p/playlist.m3u8"
        entry = {"id": "iptv:LawCrime.us", "tvgID": "LawCrime.us@SD", "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 5, 15, tzinfo=timezone.utc)
        eastern = ('<select id="timezone_selector"><option value="America/New_York" selected>'
                   'Eastern</option></select>')
        valid = ('<div data-callsign="L&amp;C" data-st="2026-11-08 00:00:00" data-duration="60" '
                 'data-showName="Police Pursuits by Law&amp;Crime" data-episodeTitle="The Hot Ride"></div>')
        wrong_station = valid.replace("L&amp;C", "OTHER").replace("The Hot Ride", "Not our channel")
        stale = valid.replace("2026-11-08", "2026-11-07").replace("The Hot Ride", "Expired")
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            page = snapshot / "tvpassport-32823-2026-11-07.html"
            page.write_text(eastern + valid + wrong_station + stale)
            guide = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual(len(guide), 1)
            self.assertEqual(guide[0].get("start"), "20261108050000 +0000")
            self.assertEqual(guide[0].get("stop"), "20261108060000 +0000")
            self.assertEqual(guide[0].findtext("title"), "Police Pursuits by Law&Crime")
            self.assertEqual(guide[0].findtext("sub-title"), "The Hot Ride")
            wrong_route = dict(entry, sources=[{"url": route.replace("law_720p", "other_720p")}])
            self.assertEqual(programmes_for_expansion([wrong_route], None, now, snapshot=snapshot), {})
            page.write_text(eastern.replace("New_York", "Chicago") + valid)
            self.assertEqual(programmes_for_expansion([entry], None, now, snapshot=snapshot), {})

    def test_wildearth_accepts_zaf_guide_only_with_dated_broadcaster_agreement(self):
        route = "https://dqga3jatxofgx.cloudfront.net/WildEarth.m3u8"
        entry = {"id": "iptv:WildEarth.za", "tvgID": "WildEarth.za@SD", "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 1, 47, tzinfo=timezone.utc)
        matching = [("02:30", "03:00", "Escape to Nature"), ("03:00", "06:00", "SafariLIVE Sunrise")]
        conflicting = ("06:00", "07:00", "KenyaLIVE Morning")
        guide = ET.Element("tv")
        ET.SubElement(ET.SubElement(guide, "channel", id="452935"), "display-name").text = "WildEarth"
        for start, stop, title in [*matching, ("06:00", "07:30", "Different schedule")]:
            guide.append(programme("452935", f"20261108{start.replace(':', '')}00 +0000",
                                   f"20261108{stop.replace(':', '')}00 +0000", title=title))
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            ET.ElementTree(guide).write(snapshot / "wildearth-zaf-epg.xml", encoding="utf-8")
            rows = [{"start_time": f"2026-11-08T{start}:00Z", "end_time": f"2026-11-08T{stop}:00Z",
                     "program_title": title} for start, stop, title in [*matching, conflicting]]
            page = snapshot / "wildearth-official.html"
            state = lambda shows: escape(json.dumps({"props": {"scheduledShows": shows}}), quote=True)
            page.write_text(f'<div id="app" data-page="{state(rows)}"></div>')
            result = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([row.findtext("title") for row in result], [row[2] for row in matching])
            page.write_text(f'<div id="app" data-page="{state(rows[1:])}"></div>')
            self.assertEqual(programmes_for_expansion([entry], None, now, snapshot=snapshot), {})

    def test_trt_world_uses_utc_portal_times_for_its_exact_edition(self):
        route = "https://mumbai-edge.smartplaytv.in/TRTWorld/index.m3u8"
        entry = {"id": "iptv:TRTWorld.tr", "tvgID": "TRTWorld.tr@SD", "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 1, 39, tzinfo=timezone.utc)
        current = {"title": "One on One", "starttime": "2026-11-08T01:30:00.000Z",
                   "endtime": "2026-11-08T01:45:00.000Z", "dateOfEpg": "2026-11-08T00:00:00.000Z"}
        edition = {"id": 129458, "slug": "trtworld", "current": current}
        wrong = dict(edition, id=1, slug="trtarabi", current=dict(current, title="Otro canal"))
        wrong_date = dict(edition, current=dict(current, title="Día equivocado",
                                                dateOfEpg="2026-11-07T00:00:00.000Z"))
        schedule = {"epg": [{"date": "2026-11-08", "tvChannels": [wrong, wrong_date, edition]}]}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "trt-api-epg-gmt0.json").write_text(json.dumps(schedule))
            rows = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([(row.get("start"), row.findtext("title")) for row in rows],
                             [("20261108013000 +0000", "One on One")])

    def test_newsmax_balkans_uses_dated_epochs_for_the_exact_registered_signal(self):
        route = "https://newsmaxadria.mts-si.tv/newsmaxadria/index.m3u8"
        entry = {"id": "iptv:NewsmaxBalkans.ba", "tvgID": "NewsmaxBalkans.ba@SD", "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 1, 28, tzinfo=timezone.utc)
        heading = "<h1>NewsMax Balkans TV program</h1>"
        grid = ('<section class="box sch full" id="sched">'
                '<li class="now" data-s="1794090600" data-e="1794103200">'
                '<time>23:30</time><span><strong>Naša priča</strong></span></li>'
                '<li data-s="1794103200" data-e="1794108600">'
                '<time>03:00</time><span><strong>Tražim reč</strong></span></li>'
                '<li data-s="1794108600" data-e="1794111600">'
                '<time>05:30</time><span><strong>Hora contradictoria</strong></span></li></section>')
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "newsmax-gledajtv-2026-11-08.html").write_text(heading + grid)
            programmes = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([row.findtext("title") for row in programmes], ["Naša priča", "Tražim reč"])
            self.assertEqual(programmes[0].get("stop"), "20261108020000 +0000")
            wrong = dict(entry, sources=[{"url": route.replace("newsmaxadria/", "anotherchannel/")}])
            self.assertEqual(programmes_for_expansion([wrong], None, now, snapshot=snapshot), {})
            (snapshot / "newsmax-gledajtv-2026-11-09.html").write_text(heading + grid)
            self.assertEqual(programmes_for_expansion([entry], None, now.replace(day=9), snapshot=snapshot), {})

    def test_tabie_uses_qatar_tv_1_and_the_actual_day_of_its_listings(self):
        now = datetime(2026, 11, 8, 1, 15, tzinfo=timezone.utc)
        feed = "QatarTelevision.qa@SD"
        station = {"id": "iptv:QatarTelevision.qa", "tvgID": feed, "sources": [
            {"url": "https://live.kwikmotion.com/qtv1live/qtv1.smil/playlist.m3u8"},
        ]}
        current = {"startTime": "2026-11-08T04:00:00", "endTime": "2026-11-08T04:30:00",
                   "title": "الناس بيزات"}
        wrong_day = {"startTime": "2026-11-07T04:00:00", "endTime": "2026-11-07T04:30:00",
                     "title": "Old guide"}
        with TemporaryDirectory() as folder:
            snapshot = Path(folder)
            page = snapshot / "qtv-tabie-2026-11-08.json"
            page.write_text(json.dumps({"channelId": 22, "programs": [current]}))
            self.assertEqual(programmes_for_expansion([station], None, now, snapshot=snapshot), {})
            page.write_text(json.dumps({"channelId": 23, "programs": [wrong_day, current]}))
            rows = programmes_for_expansion([station], None, now, snapshot=snapshot)[feed]
        self.assertEqual([(r.get("start"), r.get("stop"), r.findtext("title")) for r in rows],
                         [("20261108010000 +0000", "20261108013000 +0000", "الناس بيزات")])
        self.assertEqual(programme_state(rows, now), "vigente")

    def test_rudaw_keeps_the_next_tv_programme_without_importing_radio(self):
        now = datetime(2026, 11, 8, 1, 15, tzinfo=timezone.utc)
        feed = "RudawTV.iq@SD"
        station = {"id": "iptv:RudawTV.iq", "tvgID": feed, "sources": [
            {"url": "https://live.rudaw.net/hls/rudaw-tv/master.m3u8"},
        ]}
        next_tv = {"day": 0, "type": "tv", "channel_id": 1, "start_time": "10:00",
                   "end_time": "10:50", "title": "کاژێر 10:00"}
        radio = {**next_tv, "type": "radio", "channel_id": 2, "title": "Radio"}
        wrong_day = {**next_tv, "day": 1, "title": "Wrong day"}
        with TemporaryDirectory() as folder:
            snapshot = Path(folder)
            (snapshot / "rudaw-schedule-sorani-0.json").write_text(
                json.dumps({"success": True, "data": [next_tv, radio, wrong_day]}))
            rows = programmes_for_expansion([station], None, now, snapshot=snapshot)[feed]
        self.assertEqual([(r.get("start"), r.get("stop"), r.findtext("title")) for r in rows],
                         [("20261108070000 +0000", "20261108075000 +0000", "کاژێر 10:00")])
        self.assertEqual(programme_state(rows, now), "agenda-verificada")

    def test_meo_does_not_advance_the_previous_cnn_portugal_programme(self):
        now = datetime(2026, 11, 8, 1, 6, tzinfo=timezone.utc)
        feed = "CNNPortugal.pt@SD"
        station = {"id": "iptv:CNNPortugal.pt", "tvgID": feed, "sources": [
            {"url": "https://github.com/thomraider12/canaistvpt/raw/main/m3u8s/cnnpt.m3u8"},
        ]}
        def row(channel, start, stop, title):
            return {"CallLetter": channel, "StartDate": start, "EndDate": stop, "Title": title}

        previous = row("CNNPT", "2026-11-07T23:55:00", "2026-11-08T01:05:00", "CNN Meia Noite")
        current = row("CNNPT", "2026-11-08T01:05:00", "2026-11-08T02:03:00", "Finlândia")
        unrelated = row("CNN", "2026-11-08T01:05:00", "2026-11-08T02:03:00", "CNN US")
        with TemporaryDirectory() as folder:
            snapshot = Path(folder)
            (snapshot / "meo-cnnpt-2026-11-07.json").write_text(
                json.dumps({"StatusCode": 200, "Result": [previous]}))
            (snapshot / "meo-cnnpt-2026-11-08.json").write_text(
                json.dumps({"StatusCode": 200, "Result": [previous, current, unrelated]}))
            rows = programmes_for_expansion([station], None, now, snapshot=snapshot)[feed]
        self.assertEqual([r.findtext("title") for r in rows], ["CNN Meia Noite", "Finlândia"])
        self.assertEqual(rows[1].get("start"), "20261108010500 +0000")
        self.assertEqual(programme_state(rows, now), "vigente")

    def test_watania2_reads_only_the_officially_selected_broadcast_day(self):
        now = datetime(2026, 11, 8, 1, tzinfo=timezone.utc)
        feed = "ElWatania2.tn@SD"
        key = "69c593d18d6cac7a55cd575c"
        station = {"id": "iptv:ElWatania2.tn", "tvgID": feed, "sources": [
            {"url": "https://sw1.tanitweb.net/TunisiaTV/_definst_/watania2/playlist.m3u8?DVR"},
        ]}
        programme_html = ('<div class="desc"><time>17:30 - 19:09</time><h3>'
                          '<a href="/ar/emission/1">كرة القدم</a></h3><p>المتلوي</p></div>')
        with TemporaryDirectory() as folder:
            snapshot = Path(folder)
            day_page = snapshot / "watania2-2026-11-08.html"
            day_page.write_text(f'<a class="channel selected" href="/ar/programme/6/{key}/">'
                                + programme_html)
            self.assertEqual(programmes_for_expansion([station], None, now, snapshot=snapshot), {})
            day_page.write_text(f'<a class="channel selected" href="/ar/programme/7/{key}/">'
                                + programme_html)
            rows = programmes_for_expansion([station], None, now, snapshot=snapshot)[feed]
        self.assertEqual([(r.get("start"), r.get("stop"), r.findtext("title")) for r in rows],
                         [("20261108163000 +0000", "20261108181000 +0000", "كرة القدم")])
        self.assertEqual(programme_state(rows, now), "agenda-verificada")

    def test_asharq_discovery_prefers_the_matching_live_programme(self):
        now = datetime(2026, 11, 8, 0, 52, tzinfo=timezone.utc)
        feed = "AsharqDiscovery.sa@SD"
        station = {"id": "iptv:AsharqDiscovery.sa", "tvgID": feed, "sources": [
            {"url": "https://svs.itworkscdn.net/asharqdiscoverylive/asharqd.smil/playlist_dvr.m3u8"},
        ]}
        current = {"start": "2026-11-08T00:45:00Z", "stop": "2026-11-08T01:05:00Z",
                   "name": {"ar": "", "en": "Port Control: Spain - Episode 13"}}
        overlap = {"start": "2026-11-08T01:00:00Z", "stop": "2026-11-08T01:50:00Z",
                   "name": {"ar": "استكشاف حركة الكون", "en": "How The Universe Works"}}
        later = {"start": "2026-11-08T02:00:00Z", "stop": "2026-11-08T02:45:00Z",
                 "name": {"ar": "كيف تدرب قاتلاً"}}
        wrong = programme(feed, "20261108004746 +0000", "20261108014345 +0000", "Goblin Works Garage")
        wrong.set("zapper-provider-id", "iptv-org:shahid.mbc.net:1001845")
        with TemporaryDirectory() as folder:
            snapshot = Path(folder)
            (snapshot / "asharq-now-discovery-current-2026-11-08.json").write_text(
                json.dumps({"status": True, "data": [current]}))
            (snapshot / "asharq-now-discovery-end-2026-11-08.json").write_text(
                json.dumps({"status": True, "data": [later, overlap, current]}))
            rows = programmes_for_expansion([station], None, now, snapshot=snapshot)[feed]
            guide = snapshot / "guide.xml"
            write_guide(guide, {feed: [wrong, *rows]})
            output = ET.parse(guide).findall("programme")
        self.assertEqual([r.findtext("title") for r in output], ["Port Control: Spain - Episode 13", "كيف تدرب قاتلاً"])
        self.assertTrue(all(r.get("zapper-provider-id") == "asharq-now:discovery" for r in output))

    def test_ruv2_keeps_upcoming_signed_news_and_does_not_import_ruv(self):
        now = datetime(2026, 11, 8, 0, 40, tzinfo=timezone.utc)
        station = {"id": "iptv:RUV2.is", "tvgID": "RUV2.is@SD", "sources": [
            {"url": "https://ruvlive.akamaized.net/out/v1/2ff7673de40f419fa5164498fae89089/index.m3u8"},
        ]}
        def schedule(channel, day):
            return {
                "channel": channel, "selected_iso": day + "T00:00:00",
                "events": [{"start_time": day + "T19:00:00", "end_time": day + "T19:20:00",
                            "title": "Fréttir (með táknmálstúlkun)", "description": ["Fréttir"]}],
            }
        with TemporaryDirectory() as folder:
            snapshot = Path(folder)
            (snapshot / "ruv2-2026-11-08.json").write_text(json.dumps(schedule("ruv2", "2026-11-08")))
            (snapshot / "ruv2-2026-11-09.json").write_text(json.dumps(schedule("ruv", "2026-11-09")))
            rows = programmes_for_expansion([station], None, now, snapshot=snapshot)["RUV2.is@SD"]
        self.assertEqual([(r.get("start"), r.get("stop"), r.get("zapper-provider-id")) for r in rows],
                         [("20261108190000 +0000", "20261108192000 +0000", "ruv:ruv2")])
        self.assertEqual(programme_state(rows, now), "agenda-verificada")

    def test_mdr_uses_the_previous_broadcast_day_and_exact_regional_title(self):
        now = datetime(2026, 11, 8, 0, 35, tzinfo=timezone.utc)
        station = {"id": "iptv:MDRFernsehen.de", "tvgID": "MDRFernsehen.de@Thuringen", "sources": [
            {"url": "https://dash4.antik.sk/live/test_mdr_tizen/playlist.m3u8"},
        ]}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            markup = '<h1 class="viewdate epsilon">Samstag, 07.11<span>.2026</span></h1>'
            for start, stop, titles in (
                ("23:20", "00:50", ("Van der Valk",)),
                ("00:50", "01:25", ("Brisant",)),
                ("01:25", "03:25", ("Riverboat (Wdh.)",)),
                ("19:00", "19:30", ("MDR SACHSENSPIEGEL", "MDR THÜRINGEN JOURNAL")),
            ):
                markup += f'<li class="program theta"><span class="time">{start}</span>'
                markup += f'<span class="until">bis {stop}</span>'
                markup += "".join(f'<h3 class="theta">{title}</h3>' for title in titles) + "</li>"
            (snapshot / "mdr-2026-11-07.html").write_text(markup)
            rows = programmes_for_expansion([station], None, now, snapshot=snapshot)["MDRFernsehen.de@Thuringen"]
            riverboat = next(row for row in rows if row.findtext("title") == "Riverboat (Wdh.)")
            self.assertEqual(riverboat.get("start"), "20261108002500 +0000")
            self.assertEqual(riverboat.get("stop"), "20261108022500 +0000")
            self.assertIn("MDR THÜRINGEN JOURNAL", [row.findtext("title") for row in rows])
            self.assertNotIn("MDR SACHSENSPIEGEL", [row.findtext("title") for row in rows])

    def test_iranintl_reads_only_the_farsi_iitv_schedule(self):
        now = datetime(2026, 11, 8, 0, 35, tzinfo=timezone.utc)
        station = {"id": "iptv:IranInternational.uk", "tvgID": "IranInternational.uk@SD", "sources": [
            {"url": "https://hlspackager.akamaized.net/live/DB/IRAN_INTERNATIONAL/HLS/IRAN_INTERNATIONAL.m3u8"},
        ]}
        programme_data = {"id": "programme.iitv.2972", "title": "خبر", "language": "farsi"}
        item = {"id": "btsschedule.iitv.398583", "broadcastTime": "2026-11-08T00:30:00Z",
                "duration": "00:45:00:00", "programme": programme_data}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            data = {"scheduleData": [{"items": [item, {**item, "id": "btsschedule.afintl.398583"}]}]}
            (snapshot / "iranintl-schedule.html").write_text(
                f"<script>self.__next_f.push({json.dumps([1, json.dumps(data)])})</script>"
            )
            rows = programmes_for_expansion([station], None, now, snapshot=snapshot)["IranInternational.uk@SD"]
            (row,) = rows
            self.assertEqual(row.findtext("title"), "خبر")
            self.assertEqual(row.get("stop"), "20261108011500 +0000")

    def test_citynews_vancouver_uses_the_emitted_utc_offset(self):
        now = datetime(2026, 11, 8, 0, 24, tzinfo=timezone.utc)
        station = {"id": "iptv:CityNewsVancouver.ca", "tvgID": "CityNewsVancouver.ca@SD", "sources": [
            {"url": "https://citynewsregional.akamaized.net/hls/live/1024054/Regional_Live_9/master.m3u8"},
        ]}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            page = snapshot / "citynews-vancouver.html"
            markup = (
                '<video data-video-id="6272887411001"></video>'
                '<div data-channel="citynews_bc" data-timezone="America/Vancouver">'
                '<div class="cn-schedule-tile " data-start="2026-11-07T12:00:00-07:00" '
                'data-end="2026-11-07T18:00:00-07:00"><div class="cn-schedule-popup-title">'
                'CityNews 24/7 Vancouver</div></div></div>'
            )
            page.write_text(markup)
            rows = programmes_for_expansion([station], None, now, snapshot=snapshot)
            (row,) = rows["CityNewsVancouver.ca@SD"]
            self.assertEqual(row.get("start"), "20261107190000 +0000")
            self.assertEqual(row.get("stop"), "20261108010000 +0000")
            page.write_text(markup.replace("citynews_bc", "citynews_to"))
            self.assertFalse(programmes_for_expansion([station], None, now, snapshot=snapshot))

    def test_bbs_preserves_the_official_overnight_gap(self):
        now = datetime(2026, 11, 8, 0, 24, tzinfo=timezone.utc)
        station = {"id": "iptv:BBSTV.ug", "tvgID": "BBSTV.ug@SD", "sources": [
            {"url": "https://bbstv.ug/hls/ch01/index.m3u8"},
        ]}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "bbs-live.html").write_text(
                '<source src="https://bbstv.ug/hls/ch01/index.m3u8">'
                '<div class="epg-day-panel " data-day="Sun"><table>'
                '<tr><td class="epg-time">01:00 – 01:30</td><td class="epg-title">Music</td></tr>'
                '<tr><td class="epg-time">05:00 – 05:30</td>'
                '<td class="epg-title">SUNDAY MORNING DEVOTION</td></tr></table></div>'
            )
            rows = programmes_for_expansion([station], None, now, snapshot=snapshot)["BBSTV.ug@SD"]
            self.assertEqual(programme_state(rows, now), "agenda-verificada")
            self.assertEqual(rows[-1].get("start"), "20261108020000 +0000")
            self.assertEqual(rows[-1].findtext("title"), "SUNDAY MORNING DEVOTION")

    def test_utalca_and_rtvd_use_each_signals_local_weekday(self):
        now = datetime(2026, 11, 8, 0, 12, tzinfo=timezone.utc)
        utalca = {"id": "iptv:UTalcaTV.cl", "tvgID": "UTalcaTV.cl@SD", "sources": [
            {"url": "https://unlimited1-us.dps.live/campustv/campustv.smil/campustv/livestream1/chunks.m3u8"},
        ]}
        rtvd = {"id": "iptv:Canal4RD.do", "tvgID": "Canal4RD.do@SD", "sources": [
            {"url": "https://cdn.protvradiostream.com/canal4rd-1/ngrp:canal4rd-1_all/playlist.m3u8"},
        ]}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            headings = ("PROGRAMACIÓN UTALCATV 2025", "SÁBADO", "21:00", "UTalca Presenta",
                        "22:00", "Arquitecturas del territorio", "DOMINGO", "21:00", "Otro", "22:00", "Fin")
            (snapshot / "utalca-schedule.html").write_text("".join(f"<h2>{title}</h2>" for title in headings))
            (snapshot / "rtvd-schedule.html").write_text(
                '<script>"https://protvradiostream.com:1936/canal4rd-1/ngrp:canal4rd-1_all/playlist.m3u8?DVR"'
                '</script><li class="item" data-start-time="20:00:00" data-end-time="20:49:59" '
                'data-visible-days="6,"><div><h3>La Cultura Vive</h3></div></li>'
            )
            rows = programmes_for_expansion([utalca, rtvd], None, now, snapshot=snapshot)
            for station, title, stop in (
                (utalca, "UTalca Presenta", "20261108010000 +0000"),
                (rtvd, "La Cultura Vive", "20261108005000 +0000"),
            ):
                current = next(item for item in rows[station["tvgID"]] if item.get("start") == "20261108000000 +0000")
                self.assertEqual(current.findtext("title"), title)
                self.assertEqual(current.get("stop"), stop)

    def test_rtve_star_api_requires_the_star_broadcast_id_and_route(self):
        channel = {"id": "iptv:TVEStar.es", "tvgID": "TVEStar.es@HD", "sources": [
            {"url": "https://rtvelivestream-rtveplayplus.rtve.es/rtvesec/int/star_main_1080.m3u8"},
        ]}
        row = {"titulo": "LA PROMESA", "inicio": "08-11-2026 01:05:00", "fin": "08-11-2026 01:55:00"}
        station = {"id": "11186", "canal": "Star HD", "idGolumi": "rtvesec/int/star_main_", "now": row}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            listing = snapshot / "rtve-star.json"
            listing.write_text(json.dumps({"page": {"items": [station]}}))
            now = datetime(2026, 11, 8, 0, 12, tzinfo=timezone.utc)
            rows = programmes_for_expansion([channel], None, now, snapshot=snapshot)
            (item,) = rows["TVEStar.es@HD"]
            self.assertEqual(item.findtext("title"), "LA PROMESA")
            self.assertEqual(item.get("start"), "20261108000500 +0000")
            self.assertEqual(item.get("stop"), "20261108005500 +0000")
            station["idGolumi"] = "rtvesec/int/tvei_ame_main_"
            listing.write_text(json.dumps({"page": {"items": [station]}}))
            self.assertFalse(programmes_for_expansion([channel], None, now, snapshot=snapshot))

    def test_oromar_uses_the_terrestrial_schedule_and_keeps_the_gap(self):
        channel = {"id": "iptv:OromarTV.ec", "tvgID": "OromarTV.ec@SD", "sources": [
            {"url": "https://stream.oromar.tv/hls/oromartv_hi/index.m3u8"},
        ]}
        api = {
            "Oromartv": {"title": "Oromartv", "streamUrl": "https://stream.oromar.tv/hls/oromartv.m3u8",
                         "programas": [{"name": "Butaca Premiere", "start": "18:00:00",
                                        "end": "20:00:00", "dias": ["Sábado"]},
                                       {"name": "Siguiente", "start": "21:00:00",
                                        "end": "22:00:00", "dias": ["Sábado"]}]},
            "OTV +": {"title": "OTV+", "streamUrl": "https://stream.oromar.tv/hls/camriva/index.m3u8",
                      "programas": [{"name": "Programación streaming 24/7", "start": "00:00:00",
                                     "end": "23:59:00", "dias": ["Sábado"]}]},
        }
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "oromar-schedule.json").write_text(json.dumps(api))
            evening = datetime(2026, 11, 8, 0, tzinfo=timezone.utc)
            rows = programmes_for_expansion([channel], None, evening, snapshot=snapshot)
            row, upcoming = rows["OromarTV.ec@SD"]
            self.assertEqual(row.findtext("title"), "Butaca Premiere")
            self.assertEqual(row.get("start"), "20261107230000 +0000")
            self.assertEqual(row.get("stop"), "20261108010000 +0000")
            gap = datetime(2026, 11, 8, 1, tzinfo=timezone.utc)
            gap_rows = programmes_for_expansion([channel], None, gap, snapshot=snapshot)
            self.assertEqual(gap_rows["OromarTV.ec@SD"][1].attrib, upcoming.attrib)
            self.assertEqual(programme_state(gap_rows["OromarTV.ec@SD"], gap), "agenda-verificada")
            channel["sources"] = [{"url": api["OTV +"]["streamUrl"]}]
            self.assertFalse(programmes_for_expansion([channel], None, evening, snapshot=snapshot))

    def test_moviesphere_uses_the_broadcasters_uncompressed_xmltv(self):
        channel = {"id": "iptv:MovieSphere.us", "tvgID": "MovieSphere.us@US", "sources": [
            {"url": "https://amg00353-lionsgatestudio-moviesphere-xumo-zh5u0.amagi.tv/playlist.m3u8"},
        ]}
        page = ('<tv><channel id="147"><display-name>MovieSphere</display-name></channel>'
                '<programme channel="147" start="20261107170000 +0000" stop="20261107190000 +0000">'
                '<title>Gods of Egypt</title></programme></tv>')
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "moviesphere.xml").write_text(page)
            output = programmes_for_expansion([channel], None, NOW, snapshot=snapshot)
            (row,) = output["MovieSphere.us@US"]
            self.assertEqual(row.findtext("title"), "Gods of Egypt")
            self.assertEqual(row.get("zapper-provider-id"), "lionsgate:147")

    def test_rcntotal_accepts_only_the_exact_rcn_mas_utc_listing(self):
        item = {"channel_id": "125383", "channel_name": "RCN Mas", "name": "La Hija Del Mariachi Ep 30",
                "start_time_in_utc": "2026-11-07T23:00:00Z", "end_time_in_utc": "2026-11-08T00:00:00Z"}
        station = {"id": 1446680, "name": "RCN Más", "shows": [
            item, {**item, "channel_id": "61988"}, {**item, "channel_name": "NTN 24"},
            {**item, "is_duplicated": True}, {**item, "start_time_in_utc": "2026-11-07T23:00:00"},
        ]}
        page = '<script>self.__next_f.push(' + json.dumps([1, json.dumps(station, separators=(",", ":"))]) + ')</script>'
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "rcnmas.html").write_text(page)
            (start, stop, title, *_), = rcntotal_rows("125383", snapshot)
            self.assertEqual(start.isoformat(), "2026-11-07T23:00:00+00:00")
            self.assertEqual(stop.isoformat(), "2026-11-08T00:00:00+00:00")
            self.assertEqual(title, item["name"])

    def test_rtve_uses_dated_embedded_rows_after_the_visible_day_ends(self):
        grid = {"diahoy": "20261107235458", "uidCanal": "tv-24-horas", "idAssetCanal": "1694255",
                "items": [{"begintime": "20261108000000", "duration": "001800", "name": "Noticias 24 H"}]}
        page = '<script>self.__next_f.push(' + json.dumps([1, "17:" + json.dumps(grid, separators=(",", ":"))]) + ')</script>'
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "epg-emisoras-altas").mkdir()
            (snapshot / "epg-emisoras-altas/rtve.html").write_text(page)
            (start, stop, title, *_), = rtve_rows(NOW.replace(hour=23), snapshot)
            self.assertEqual(start.astimezone(timezone.utc).isoformat(), "2026-11-07T23:00:00+00:00")
            self.assertEqual(stop.astimezone(timezone.utc).isoformat(), "2026-11-07T23:30:00+00:00")
            self.assertEqual(title, "Noticias 24 H")

    def test_elcinema_compares_the_same_slot_across_two_date_boundaries(self):
        grid = (
            '<input class="only-date no-margin" value="2026-11-08 00:00">'
            '<input name="only_date" value="2026-11-07"><input name="only_time" value="19:00">'
            '<div class="tv-line"><a title="Al Araby TV 2" href="/tvguide/1382/">'
            '<div class="tv-slot genre"><ul class="unstyled no-margin">'
            '<li>Documental</li><li>07:00 مساءً - 09:00 مساءً</li></ul></div>'
        )
        detail = (
            '<div class=" dates">الأحد 8 نوفمبر</div>'
            '<ul class="unstyled no-margin"><li>Documental</li>'
            '<li>07:00 مساءً <span class="subheader">[120 دقيقة]</span></li></ul>'
        )
        bindings = {"AlArabyTV2.qa@SD": "1382"}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "grid.html").write_text(grid)
            (snapshot / "1382.html").write_text(detail)
            now = NOW.replace(hour=22, minute=40)
            (row,) = elcinema_programmes(bindings, now, snapshot)["AlArabyTV2.qa@SD"]
            self.assertEqual(row.attrib["start"], "20261107220000 +0000")
            self.assertEqual(row.attrib["stop"], "20261108000000 +0000")
            self.assertEqual(elcinema_programmes(bindings, now.replace(hour=23), snapshot), {})
            (snapshot / "1382.html").write_text(detail.replace("Documental", "Otro título"))
            self.assertEqual(elcinema_programmes(bindings, now, snapshot), {})

    def test_elcinema_work_card_requires_verified_edition_and_grid(self):
        now = datetime(2026, 11, 8, 9, 23, tzinfo=timezone.utc)
        feed = "AlIraqia.iq@SD"
        grid = (
            '<input class="only-date no-margin" value="2026-11-08 11:00">'
            '<input name="only_date" value="2026-11-08"><input name="only_time" value="06:00">'
            '<div class="tv-line"><a title="قناة العراقية" href="/tvguide/1349/">'
            '<div class="tv-slot genre"><ul class="unstyled no-margin">'
            '<li>اسمي حسن</li><li>06:00 صباحًا - 07:00 صباحًا</li></ul></div>'
        )
        # The current channel page has separate lists for the clock, title and details.
        work = (
            '<div class="boxed-category-3 padded-half">'
            '<ul class="unstyled text-center"><li>06:00  صباحًا</li>'
            '<li><span class="subheader">[60 دقيقة]</span></li></ul>'
            '<ul class="unstyled no-margin"><li><a href="/work/2098480/">اسمي حسن</a></li>'
            '<li>مسلسل (2026)</li><li>Favourite</li></ul></div>'
        )
        detail = '<div class=" dates">الأحد 8 نوفمبر</div>' + work
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "grid.html").write_text(grid)
            (snapshot / "1349.html").write_text(detail)
            bindings = {feed: "1349"}
            self.assertEqual(elcinema_programmes(bindings, now, snapshot), {})
            (row,) = elcinema_programmes(bindings, now, snapshot, validated_work_cards={feed})[feed]
            self.assertEqual(row.findtext("title"), "اسمي حسن")
            self.assertEqual((row.get("start"), row.get("stop")),
                             ("20261108090000 +0000", "20261108100000 +0000"))
            (snapshot / "grid.html").write_text(grid.replace("07:00", "06:30"))
            self.assertEqual(elcinema_programmes(bindings, now, snapshot, validated_work_cards={feed}), {})
            malformed = work.replace('<a href="/work/2098480/">اسمي حسن</a>', "Invalid title")
            title_without_clock = work.replace('06:00  صباحًا', 'Unknown clock')
            (snapshot / "grid.html").write_text(grid)
            (snapshot / "1349.html").write_text(
                '<div class=" dates">الأحد 8 نوفمبر</div>' + malformed + title_without_clock
            )
            self.assertEqual(elcinema_programmes(bindings, now, snapshot, validated_work_cards={feed}), {})

    def test_elcinema_gap_admits_only_the_corroborated_upcoming_listing(self):
        now = datetime(2026, 11, 8, 1, 3, tzinfo=timezone.utc)
        feed = "JordanTV.jo@SD"
        grid = (
            '<input class="only-date no-margin" value="2026-11-08 03:00">'
            '<input name="only_date" value="2026-11-07"><input name="only_time" value="22:00">'
            '<div class="tv-line"><a title="Jordan TV" href="/tvguide/1314/">'
            '<div class="tv-slot genre"><ul class="unstyled no-margin">'
            '<li>شفاء القلوب</li><li>11:00 مساءً - 11:30 مساءً</li></ul></div>'
        )
        detail = (
            '<div class=" dates">الأحد 8 نوفمبر</div>'
            '<ul class="unstyled no-margin"><li>Invalid listing</li><li>Invalid clock</li></ul>'
            '<ul class="unstyled no-margin"><li>شفاء القلوب</li>'
            '<li>11:00 مساءً <span class="subheader">[30 دقيقة]</span></li></ul>'
            '<ul class="unstyled no-margin"><li>Uncorroborated</li>'
            '<li>11:30 مساءً <span class="subheader">[30 دقيقة]</span></li></ul>'
        )
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "grid.html").write_text(grid)
            (snapshot / "1314.html").write_text(detail)
            rows = elcinema_programmes({feed: "1314"}, now, snapshot)[feed]
        self.assertEqual([(r.get("start"), r.get("stop"), r.findtext("title")) for r in rows],
                         [("20261108020000 +0000", "20261108023000 +0000", "شفاء القلوب")])
        self.assertEqual(programme_state(rows, now), "agenda-verificada")

    def test_ayn_checks_today_and_stops_at_an_explicit_gap(self):
        def slot(clock, title):
            return f'<div class="epg_tl_item  epg_popup_detail "><h2>{title}</h2><p>{clock}</p></div>'

        schedule = (
            '<title>AYN TV - قناة عمان الثقافية - جدول البرامج</title>'
            '<section class="epg_sec"><div class="epg_timeline_aside"><h3>الأحد</h3></div>'
            + slot("01:20", "Nombre verificado") + slot("02:00 - 02:10", "لا يوجد جدول")
            + slot("02:10", "Última fila") + '</section>'
            '<div class="tab-box" id="tab-1"><img data-src="/2026-11-08/sample.jpg"></div>'
            '<div class="tab-box" id="tab-2"></div>'
        )
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "ayn-schedule-160.html").write_text(schedule)
            (start, stop, title, *_), = ayn_rows("160", NOW.replace(hour=22), snapshot)
            self.assertEqual((start.isoformat(), stop.isoformat(), title),
                             ("2026-11-08T01:20:00+04:00", "2026-11-08T02:00:00+04:00", "Nombre verificado"))
            self.assertEqual(list(ayn_rows("160", NOW, snapshot)), [])

    def test_dmax_uses_italian_wall_clock_and_merges_an_overnight_slot(self):
        row = {"show_name": "Real TV", "title": "St.1 Ep.12",
               "start_time": "23:35", "end_time": "01:30", "duration": 115}
        before = dict(row, start_dt="2026-11-07T23:35:00+00:00", end_dt="2026-11-08T00:00:00+00:00")
        after = dict(row, start_dt="2026-11-08T00:00:00+00:00", end_dt="2026-11-08T01:30:00+00:00")
        channel = {"uid": "31", "channel": {"alternateId": "dmax", "countryCode": "it"},
                   "days": [{"programs": [before]}, {"programs": [after]}]}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "dmax-page_guida-tv.json").write_text(
                json.dumps({"blocks": [{"type": "epgBlock", "channels": [channel]}]}))
            (start, stop, title, subtitle, _, _), = dmax_rows(snapshot)
            self.assertEqual(start.astimezone(timezone.utc).isoformat(), "2026-11-07T22:35:00+00:00")
            self.assertEqual(stop.astimezone(timezone.utc).isoformat(), "2026-11-08T00:30:00+00:00")
            self.assertEqual((title, subtitle), ("Real TV", "St.1 Ep.12"))

    def test_cbs_miami_anchors_listed_current_to_its_exact_live_rundown(self):
        now = datetime(2026, 11, 8, 2, 32, tzinfo=timezone.utc)
        edition = "CBSN-MIA"
        title = "CBS News Miami (replay)"
        response = {"navigation": {
            "dateUpdated": {"date": "2026-11-08 02:32:00", "timezone": "UTC"},
            "data": [{"edition": edition, "type": "live", "rundown_start_time": "2026-11-08 02:00:00",
                      "epgSeriesName": title}],
            "epgData": [
                {"title": title, "from": "10:00:00 PM", "to": "10:30:00 PM"},
                {"title": title, "from": "10:30:00 PM", "to": "11:00:00 PM"},
                {"title": "CBS News Miami 11pm (Live)", "from": "11:00:00 PM", "to": "11:30:00 PM"},
            ],
        }}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            page = snapshot / "cbs-CBSN-MIA.json"
            page.write_text(json.dumps(response))
            rows = list(cbs_rows(edition, now, snapshot))
            self.assertEqual([(row[0].isoformat(), row[2]) for row in rows], [
                ("2026-11-07T22:30:00-04:00", title),
                ("2026-11-07T23:00:00-04:00", "CBS News Miami 11pm (Live)"),
            ])
            prior_slot = {"title": title, "from": "09:30:00 PM", "to": "10:00:00 PM"}
            response["navigation"]["epgData"].insert(0, prior_slot)
            page.write_text(json.dumps(response))
            self.assertEqual(list(cbs_rows(edition, now, snapshot)), rows)
            # Both UTC offsets would now anchor the same replay title at different rows.
            response["navigation"]["epgData"].insert(
                0, {"title": title, "from": "09:00:00 PM", "to": "09:30:00 PM"}
            )
            page.write_text(json.dumps(response))
            self.assertEqual(list(cbs_rows(edition, now, snapshot)), [])
            del response["navigation"]["epgData"][:2]
            response["navigation"]["epgData"][1]["title"] = "Wrong programme"
            page.write_text(json.dumps(response))
            self.assertEqual(list(cbs_rows(edition, now, snapshot)), [])

    def test_rai_asia_australia_retains_only_noncontradicted_official_cache(self):
        from guia_expansion import (RAI_ASIA_AUSTRALIA_GUIDE, RAI_ASIA_AUSTRALIA_ID,
                                    RAI_ASIA_AUSTRALIA_PROVIDER, rai_asia_australia_schedule)
        from refresh_expansion import schedule_allowed

        now = datetime(2026, 11, 8, 8, 2, tzinfo=timezone.utc)
        provider = RAI_ASIA_AUSTRALIA_PROVIDER
        identity = RAI_ASIA_AUSTRALIA_ID

        def row(start, stop, title, provider_id):
            node = ET.Element("programme", {
                "channel": identity, "start": f"20261108{start} +0000", "stop": f"20261108{stop} +0000",
                "zapper-provider-id": provider_id,
                "zapper-provenance": f"{RAI_ASIA_AUSTRALIA_GUIDE}/08-11-2026.json",
            })
            ET.SubElement(node, "title").text = title
            return node

        good = row("110000", "113000", "Omitted slot", provider)
        conflicting = row("093500", "094500", "Disputed cached slot", provider)
        expired = row("070000", "080000", "Expired slot", provider)
        wrong = row("120000", "123000", "Cable alias", "foxtel")
        event = {"channel": "Rai Italia Asia", "duration": "00:30:00"}
        response = {"channel": "Rai Italia Asia", "day": "08-11-2026", "absolute_path": "rai-italia-asia",
                    "events": [dict(event, date=f"2026-11-08T{clock}+0000", name=title)
                               for clock, title in (("08:05:00", "Current"),
                                                    ("09:15:00", "Conflict A"), ("09:30:00", "Conflict B"))]}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            cached = [good, conflicting, expired, wrong]
            self.assertEqual(rai_asia_australia_schedule(now, cached, snapshot), [conflicting, good])
            (snapshot / "rai-asia-08-11-2026.json").write_text(json.dumps(response))
            schedule = rai_asia_australia_schedule(now, cached, snapshot)
            self.assertEqual([n.findtext("title") for n in schedule], ["Current", "Omitted slot"])
            guide = ET.Element("tv")
            ET.SubElement(guide, "channel", id=identity)
            guide.extend([wrong])
            cleaned, unavailable = remove_unusable_programmes(ET.tostring(guide, encoding="unicode"))
            self.assertEqual(unavailable, [identity])
            self.assertEqual(ET.fromstring(cleaned).findall("programme"), [])
            self.assertFalse(schedule_allowed(wrong))
            self.assertTrue(schedule_allowed(good))

    def test_rai_asia_australia_requires_exact_edition_date_and_offset(self):
        from guia_expansion import rai_asia_australia_rows
        now = datetime(2026, 11, 8, 8, 2, tzinfo=timezone.utc)
        correct = {"channel": "Rai Italia Asia", "date": "2026-11-08T19:00:00+1100",
                   "duration": "00:04:00", "name": "TG1"}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            path = snapshot / "rai-asia-08-11-2026.json"
            payload = {"channel": "Rai Italia Asia", "day": "08-11-2026",
                       "absolute_path": "rai-italia-asia", "events": [
                           correct, dict(correct, channel="Rai Italia Africa"),
                           dict(correct, date="2026-11-08T08:00:00"),
                           dict(correct, duration="00:00:00", date="2026-11-08T09:00:00+0000"),
                           dict(correct, duration=3600), dict(correct, name=123), "invalid row",
                       ]}
            path.write_text(json.dumps(payload))
            rows = list(rai_asia_australia_rows(now, snapshot))
            self.assertEqual([(r[0].astimezone(timezone.utc).isoformat(), r[2]) for r in rows],
                             [("2026-11-08T08:00:00+00:00", "TG1")])
            path.write_text(json.dumps(dict(payload, day="07-11-2026")))
            self.assertEqual(list(rai_asia_australia_rows(now, snapshot)), [])
            spans = [
                dict(correct, date="2026-11-08T09:00:00+0000", duration="02:00:00", name="Long"),
                dict(correct, date="2026-11-08T09:10:00+0000", duration="00:10:00", name="First"),
                dict(correct, date="2026-11-08T09:40:00+0000", duration="00:10:00", name="Second"),
                dict(correct, date="2026-11-08T11:00:00+0000", duration="00:10:00", name="After"),
            ]
            path.write_text(json.dumps(dict(payload, events=spans)))
            self.assertEqual([r[2] for r in rai_asia_australia_rows(now, snapshot)], ["After"])

    def test_cbs_retries_stale_rundown_once_without_accepting_stale_snapshot(self):
        now = datetime(2026, 11, 8, 7, 58, tzinfo=timezone.utc)
        edition = "CBSN-MIA"
        title = "CBS News Miami 11pm (replay)"

        def reply(stamp):
            return json.dumps({"navigation": {
                "dateUpdated": {"timezone": "UTC", "date": stamp},
                "data": [{"edition": edition, "type": "live", "epgSeriesName": title,
                          "rundown_start_time": "2026-11-08 07:30:00"}],
                "epgData": [{"from": "02:30:00 AM", "to": "03:00:00 AM", "title": title},
                            {"from": "03:00:00 AM", "to": "03:30:00 AM", "title": "Next"}],
            }}).encode()

        stale = reply("2026-11-08 06:55:00")
        fresh = reply("2026-11-08 07:58:00")
        with patch("guia_expansion.read_response", side_effect=[stale, fresh]) as fetch:
            self.assertEqual([row[2] for row in cbs_rows(edition, now, None)], [title, "Next"])
            self.assertEqual(fetch.call_count, 2)
            original = fetch.call_args_list[0].args[0]
            self.assertEqual(fetch.call_args_list[1].args[0], f"{original}&_={int(now.timestamp())}")
        with patch("guia_expansion.read_response", return_value=stale) as fetch:
            self.assertEqual(list(cbs_rows(edition, now, None)), [])
            self.assertEqual(fetch.call_count, 2)
            fetch.reset_mock()
            self.assertEqual(list(cbs_rows(edition, now, Path("."))), [])
            self.assertEqual(fetch.call_count, 1)

    def test_cbs_accepts_only_an_independently_confirmed_current_programme(self):
        entry = {"id": "iptv:CBSNews247.us", "tvgID": "CBSNews247.us@SD", "sources": [
            {"url": "https://cbsn-us-vtt.cbsnstream.cbsnews.com/out/v1/ef868690d34144509eda696884bf1619/master.m3u8"}
        ]}
        response = {"navigation": {
            "dateUpdated": {"timezone": "UTC", "date": "2026-11-07 18:00:00"},
            "data": [{"edition": "CBSN-US", "type": "live", "rundown_start_time": "2026-11-07 18:00:00",
                      "epgSeriesName": "En vivo"}],
            "epgData": [{"from": "02:00:00 PM", "to": "03:00:00 PM", "title": "Siguiente según CBS"}],
        }}
        guide = ET.Element("tv")
        ET.SubElement(ET.SubElement(guide, "channel", id="464941"), "display-name").text = "CBS News National Stream"
        row = programme("464941", "20261107180000 +0000", "20261107190000 +0000", title="En vivo")
        guide.append(row)
        guide.append(programme("464941", "20261107190000 +0000", "20261107200000 +0000", title="Otra plataforma"))
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "cbs-CBSN-US.json").write_text(json.dumps(response))
            epg = snapshot / "cbs-national-20261107.xml"
            epg.write_bytes(ET.tostring(guide))
            result = programmes_for_expansion([entry], None, NOW, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([node.findtext("title") for node in result], ["En vivo", "Siguiente según CBS"])
            row.find("title").text = "Coincidencia falsa"
            epg.write_bytes(ET.tostring(guide))
            self.assertEqual(programmes_for_expansion([entry], None, NOW, snapshot=snapshot), {})

    def test_star_greece_uses_the_broadcast_date_past_midnight(self):
        entry = {"id": "iptv:StarChannel.gr", "tvgID": "StarChannel.gr@SD", "sources": [
            {"url": "http://livestar.siliconweb.com/starvod/star4/star4.m3u8"}
        ]}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            markup = "".join(
                f'<div class="schedule-rows__top"><div class="time">{clock}</div>'
                f'<div class="content"><h2><a>{title}</a></h2></div></div>'
                for clock, title in [("23:15", "Antes de medianoche"), ("01:30", "Después"),
                                     ("04:15", "Siguiente")]
            )
            (snapshot / "star-2026-11-07.html").write_text(markup)
            result = programmes_for_expansion([entry], None, NOW.replace(hour=23), snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual(result[0].get("start"), "20261107211500 +0000")
            self.assertEqual(result[0].get("stop"), "20261107233000 +0000")

    def test_eska_guide_uses_its_exact_row_and_provider_timestamps(self):
        entry = {"id": "iptv:EskaTV.pl", "tvgID": "EskaTV.pl@SD", "sources": [
            {"url": "http://92.114.85.87:8018/play/a00h/index.m3u8"}
        ]}
        start = int(NOW.replace(hour=17).timestamp() * 1000)
        stop = int(NOW.replace(hour=19).timestamp() * 1000)

        def row(name, title):
            return (f'<div class="tv__row" data-channel="{name}">'
                    f'<div data-start="{start}" data-end="{stop}">'
                    f'<span class="tvcast__title" title="{title}"></span></div></div>')

        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "eska-tv-html_module_page1_").write_text(
                row("Eska TV Extra", "Otra señal") + row("Eska TV", "Eska TV info")
            )
            result = programmes_for_expansion([entry], None, NOW, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([node.findtext("title") for node in result], ["Eska TV info"])
            self.assertEqual(result[0].get("start"), "20261107170000 +0000")

    def test_nusantara_schedule_uses_the_previous_broadcast_day_after_midnight(self):
        entry = {"id": "iptv:NusantaraTV.id", "tvgID": "NusantaraTV.id@SD", "sources": [
            {"url": "https://nusantaratv.siar.us/nusantaratv/live/playlist.m3u8"}
        ]}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "ntv-2026-11-07.html").write_text(
                '<h1>Jadwal Program Nusantara TV Sabtu 7 November 2026</h1>'
                '<p>04.00-06.00 &nbsp; Prime<br />00.00-01.00 &nbsp; Música nocturna<br />'
                '01.00-04.00 &nbsp; Siguiente</p>'
            )
            result = programmes_for_expansion([entry], None, NOW, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual(result[0].findtext("title"), "Música nocturna")
            self.assertEqual(result[0].get("start"), "20261107170000 +0000")
            self.assertEqual(result[0].get("stop"), "20261107180000 +0000")

    def test_shahid_accepts_only_the_requested_station_and_ignores_empty_slots(self):
        entry = {"id": "iptv:MBC4.ae", "tvgID": "MBC4.ae@SD", "sources": [
            {"url": "https://shd-gcp-live.edgenextcdn.net/live/bitmovin-mbc-4/test/index.m3u8"}
        ]}
        first = {"actualFrom": "2026-11-07T17:30:00.010+00:00", "actualTo": "2026-11-07T18:30:00.030+00:00",
                 "title": "MBC4 programa vigente"}
        second = {"actualFrom": "2026-11-07T18:30:00.015+00:00", "actualTo": "2026-11-07T20:30:00.030+00:00",
                  "title": "MBC4 programa siguiente"}
        response = {"items": [
            {"channelId": 387238, "items": [dict(first, title="MBC1 programa distinto")]},
            {"channelId": 400919, "items": [first, second, dict(first, title="TV guide is not available")]},
        ]}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "shahid-400919-2026-11-07.json").write_text(json.dumps(response))
            result = programmes_for_expansion([entry], None, NOW, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([row.findtext("title") for row in result], [first["title"], second["title"]])

    def test_telemetro_keeps_today_when_tomorrow_has_two_conflicting_schedules(self):
        entry = {"id": "iptv:Telemetro.pa", "tvgID": "Telemetro.pa@SD", "sources": [
            {"url": "http://181.78.121.15:8000/play/a001/index.m3u8"}
        ]}

        def section(index, label, rows):
            return f'<label for="id-{index}" class="programming-grid__grid-selector-btn">{label}</label>' + "".join(
                '<article class="programming-grid__show-info-wrapper">'
                f'<span>{clock}<small class="clock">PM</small></span>'
                f'<h2 class="programming-grid__show-name">{title}</h2></article>'
                for clock, title in rows
            )

        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "telemetro.html").write_text(
                '<!-- Request Time: 2026-11-07 11:30:00 -->'
                + section(0, "Hoy", [("12:00", "Hoy"), ("02:00", "Siguiente")])
                + section(1, "Mañana", [("03:00", "Primera"), ("01:00", "Conflicto")])
            )
            result = programmes_for_expansion([entry], None, NOW, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([node.findtext("title") for node in result], ["Hoy"])
            self.assertEqual(result[0].get("stop"), "20261107190000 +0000")

    def test_mediaset_italia_rejects_the_americas_edition(self):
        entry = {"id": "iptv:MediasetItalia.it", "tvgID": "MediasetItalia.it@SD", "sources": [
            {"url": "https://d3k8wzt41aflvx.cloudfront.net/MediasetEU/Live.m3u8"}
        ]}
        listings = [{
            "startTime": int(NOW.replace(hour=17).timestamp() * 1000),
            "endTime": int(NOW.replace(hour=19).timestamp() * 1000),
            "mediasetlisting$epgTitle": "Programa europeo",
        }]
        station = {"callSign": "MY", "title": "Mediaset Italia | Europa e Africa"}
        guide = {"guid": "MY", "stations": {"Station": station}, "listings": listings}
        response = {"service": "allListingFeedEpg", "isOk": True, "response": {"entries": [guide]}}
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            path = snapshot / "mediaset-gateway-MY.json"
            path.write_text(json.dumps(response))
            result = programmes_for_expansion([entry], None, NOW, snapshot=snapshot)
            self.assertEqual([node.findtext("title") for node in result[entry["tvgID"]]], ["Programa europeo"])
            station.update(callSign="MW", title="Mediaset Italia | America")
            path.write_text(json.dumps(response))
            self.assertEqual(programmes_for_expansion([entry], None, NOW, snapshot=snapshot), {})

    def test_sydney_guide_uses_the_exact_regional_stream_id(self):
        entry = {"id": "iptv:ABCTV.au", "tvgID": "ABCTV.au@NSW",
                 "sources": [{"url": "https://c.mjh.nz/abc-nsw.m3u8"}]}
        guide = ET.Element("tv")
        for key in ("mjh-abc-nsw", "mjh-abc-vic"):
            ET.SubElement(ET.SubElement(guide, "channel", id=key), "display-name").text = "ABC TV"
            guide.append(programme(key, "20261107170000 +0000", "20261107190000 +0000", title=key))
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "sydney-epg.xml.gz").write_bytes(gzip.compress(ET.tostring(guide)))
            result = programmes_for_expansion([entry], None, NOW, snapshot=snapshot)
            self.assertEqual([node.findtext("title") for node in result[entry["tvgID"]]], ["mjh-abc-nsw"])
            wrong = dict(entry, sources=[{"url": "https://c.mjh.nz/abc-vic.m3u8"}])
            self.assertEqual(programmes_for_expansion([wrong], None, NOW, snapshot=snapshot), {})

    def test_ertnews_uses_the_broadcasters_day_and_ignores_a_different_channel(self):
        entry = {"id": "iptv:ERTNews.gr", "tvgID": "ERTNews.gr@HD",
                 "sources": [{"url": "https://ert-ucdn.broadpeak-aas.com/ertnews/master.m3u8"}]}
        markup = """
          <h1 class="hidden">ΠΡΟΓΡΑΜΜΑΕΡΤ NEWS</h1>
          <span class="section-title fs-lg">Σάββατο 07/11/2026</span>
          <article id="broadcast_3" data-start-time="2026-11-08 00:30:00+0200"
                   data-end-time="2026-11-08 01:30:00+0200">
            <strong class="section-title fs-lg">Noticias ERT</strong>
          </article>
        """
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            page = snapshot / "ertnews-2026-11-07.html"
            page.write_text(markup)
            result = programmes_for_expansion([entry], None, NOW.replace(hour=23), snapshot=snapshot)
            self.assertEqual(result[entry["tvgID"]][0].get("start"), "20261107223000 +0000")
            page.write_text(markup.replace("ΕΡΤ NEWS", "ΕΡΤ1"))
            self.assertEqual(programmes_for_expansion([entry], None, NOW.replace(hour=23), snapshot=snapshot), {})

    def test_apunt_binds_only_the_web_feed_and_preserves_the_provider_offset(self):
        route = (
            "https://fastly.live.brightcove.com/1846756479408303045/"
            f"eu-central-1/{APUNT_ACCOUNT}/token/master.m3u8"
        )
        entry = {"id": "iptv:APunt.es", "tvgID": "APunt.es@SD", "sources": [{"url": route}]}
        cards = [{
            "startTime": {"date": "2026-11-07 18:30:00", "timezone": "+01:00", "timezone_type": 1},
            "endTime": {"date": "2026-11-07 19:30:00", "timezone": "+01:00", "timezone_type": 1},
            "title": "Programa web",
        }]
        markup = '<ap-schedule-carousel :cards="{}"></ap-schedule-carousel>'.format(
            escape(json.dumps(cards), quote=True)
        )
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            page = snapshot / "apunt-live.html"
            page.write_text(f"https://players.brightcove.net/{APUNT_ACCOUNT}/player {markup}")
            result = programmes_for_expansion([entry], None, NOW, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual(result[0].get("start"), "20261107173000 +0000")
            self.assertEqual(result[0].get("stop"), "20261107183000 +0000")
            self.assertEqual(result[0].findtext("title"), "Programa web")
            wrong = dict(entry, sources=[{"url": route.replace("1846756479408303045", "99999")}])
            self.assertEqual(programmes_for_expansion([wrong], None, NOW, snapshot=snapshot), {})
            page.write_text(markup)
            self.assertEqual(programmes_for_expansion([entry], None, NOW, snapshot=snapshot), {})

    def test_apunt_extends_the_web_edition_past_the_broadcast_day(self):
        route = ("https://fastly.live.brightcove.com/1846756479408303045/"
                 f"eu-central-1/{APUNT_ACCOUNT}/token/master.m3u8")
        entry = {"id": "iptv:APunt.es", "tvgID": "APunt.es@SD", "sources": [{"url": route}]}
        now = datetime(2026, 11, 8, 6, 15, tzinfo=timezone.utc)
        card = {
            "startTime": {"date": "2026-11-08 06:00:00", "timezone": "+01:00", "timezone_type": 1},
            "endTime": {"date": "2026-11-08 07:00:00", "timezone": "+01:00", "timezone_type": 1},
            "title": "Último de la noche",
        }
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            (snapshot / "apunt-live.html").write_text(
                f'https://players.brightcove.net/{APUNT_ACCOUNT}/player <ap-schedule-carousel :cards="'
                + escape(json.dumps([card]), quote=True) + '"></ap-schedule-carousel>')
            tomorrow = snapshot / "apunt-internet-dema.html"
            markup = ('{"url":"https://www.apuntmedia.es/programacio/internet/dema"}'
                      '<article data-start-time="2026-11-08T07:00:00" data-end-time="2026-11-08T07:35:00">'
                      '<div class="schedule-item__title">MÚSICA DE BANDA</div>'
                      '<h2 class="schedule-item__episode-title">Unió Musical d&#039;Algímia</h2></article>')
            tomorrow.write_text(markup)
            result = programmes_for_expansion([entry], None, now, snapshot=snapshot)[entry["tvgID"]]
            self.assertEqual([(row.get("start"), row.findtext("title")) for row in result],
                             [("20261108050000 +0000", "Último de la noche"),
                              ("20261108060000 +0000", "MÚSICA DE BANDA")])
            self.assertEqual(result[1].findtext("sub-title"), "Unió Musical d'Algímia")
            tomorrow.write_text(markup.replace("/programacio/internet/", "/programacio/tv/"))
            self.assertEqual(programmes_for_expansion([entry], None, now, snapshot=snapshot), {})

    def test_btv_fallback_uses_only_the_exact_current_signal(self):
        entry = {"id": "iptv:bTV.bg", "tvgID": "bTV.bg@SD", "sources": []}
        guide = ET.Element("tv")
        ET.SubElement(ET.SubElement(guide, "channel", id="bTV.bg"), "display-name").text = "bTV"
        guide.append(programme("bTV.bg", "20261107170000 +0000", "20261107190000 +0000"))
        guide.append(programme("bTVCinema.bg", "20261107170000 +0000", "20261107190000 +0000",
                               title="Different edition"))
        with TemporaryDirectory() as directory:
            snapshot = Path(directory)
            archive = snapshot / "epg_ripper_BG1.xml.gz"
            archive.write_bytes(gzip.compress(ET.tostring(guide)))
            result = programmes_for_expansion([entry], None, NOW, snapshot=snapshot)
            self.assertEqual(set(result), {entry["tvgID"]})
            self.assertEqual([node.findtext("title") for node in result[entry["tvgID"]]], ["Noticias"])
            self.assertEqual(programmes_for_expansion([entry], None, NOW.replace(hour=20), snapshot=snapshot), {})
            archive.write_bytes(b"damaged response")
            self.assertEqual(programmes_for_expansion([entry], None, NOW, snapshot=snapshot), {})

    def test_shared_release_keeps_held_identity_and_orders_every_country(self):
        rows = [
            ("Example.es@SD", "ES"), ("Example.ar@SD", "AR"),
            ("AmericaTV.ar@SD", "AR"), ("Regional.ar@SD", "AR"),
            ("Held.ar@SD", "AR"),
        ]
        guide = ET.Element("tv")
        playlist = f'#EXTM3U x-tvg-url="{EPG_HEADER}"\n'
        for number, (feed, _) in enumerate(rows, 1):
            ET.SubElement(guide, "channel", id=feed)
            playlist += (
                f'#EXTINF:-1 tvg-id="{feed}" tvg-chno="{number}" group-title="00 - General",{feed}\n'
                f'https://example.com/{number}.m3u8\n'
            )
            if feed != "Held.ar@SD":
                guide.append(programme(feed, "20261107170000 +0000", "20261107190000 +0000"))
        catalog = [{"tvgId": feed, "country": country} for feed, country in rows[:3]]
        expansion = [{"tvgID": feed, "name": feed, "category": "00 - General"}
                     for feed, _ in rows[3:]]
        payload = {"catalog": catalog, "expansion": expansion, "playlist": playlist,
                   "flexXML": ET.tostring(guide, encoding="unicode"), "unavailableProgrammeIDs": []}
        with TemporaryDirectory() as directory:
            output = Path(directory) / "release"
            write_release(payload, output, NOW, [], keep_inventory=True)
            actual, _ = unpack(output / "coordinated-generation.json")
            self.assertEqual(actual["expansion"], expansion)
            self.assertEqual(actual["withheldExpansionIDs"], ["Held.ar@SD"])
            self.assertEqual(actual["flexXML"], (output / "guide-flex-20261003.xml").read_text())
            self.assertEqual([node.get("id") for node in ET.fromstring(actual["flexXML"]).findall("channel")],
                             ["AmericaTV.ar@SD", "Example.ar@SD", "Regional.ar@SD", "Example.es@SD"])
            self.assertIn('tvg-id="AmericaTV.ar@SD" tvg-chno="3"', actual["playlist"])
            self.assertEqual(actual["playlist"].count('group-title="General"'), 4)

    def test_reviewed_provider_keeps_valid_slots_around_multiday_conflicts(self):
        feed = "Exact.es@SD"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bindings = root / "reviewed.xml"
            bindings.write_text(f'<channels><channel xmltv_id="{feed}" site="example.com" site_id="10"/></channels>')
            rows = [
                ("20261107173000 +0000", "20261107190000 +0000", "Current"),
                ("20261107173000 +0000", "20261107190000 +0000", "Current"),
                ("20261107190000 +0000", "20261107200000 +0000", "Tomorrow last guess"),
                ("20261107190000 +0000", "20261107194500 +0000", "Tomorrow last guess"),
                ("20261107194500 +0000", "20261107210000 +0000", "Overlapping neighbour"),
                ("20261107203000 +0000", "20261107211500 +0000", "Chain overlap"),
                ("20261107211500 +0000", "20261107220000 +0000", "Verified later"),
            ]
            tv = ET.Element("tv")
            tv.extend(programme(feed, start, stop, title) for start, stop, title in rows)
            ET.ElementTree(tv).write(root / "example.com.xml")
            fresh, disputed = reviewed_programmes(root, bindings, NOW)
            self.assertEqual([node.findtext("title") for node in fresh[feed]], ["Current", "Verified later"])
            disputed_row = programme(feed, "20261107203000 +0000", "20261107210000 +0000", "Previously seen")
            disputed_row.set("zapper-provider-id", "iptv-org:example.com:10")
            independent = programme(feed, "20261107203000 +0000", "20261107210000 +0000", "Another source")
            cached = {feed: [disputed_row, independent]}
            exclude_disputed_slots(cached, disputed)
            self.assertEqual(cached[feed], [independent])

    def test_wrong_shahid_documentary_edition_is_rejected_in_fresh_and_cached_guide(self):
        feed = "AsharqDocumentary.sa@SD"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bindings = root / "reviewed.xml"
            bindings.write_text(f'<channels><channel xmltv_id="{feed}" site="shahid.mbc.net" site_id="997605"/></channels>')
            wrong = programme(feed, "20261107173000 +0000", "20261107190000 +0000", "Volcanes y humanos")
            tv = ET.Element("tv")
            tv.append(wrong)
            ET.ElementTree(tv).write(root / "shahid.mbc.net.xml")
            self.assertEqual(reviewed_programmes(root, bindings, NOW), ({}, {}))
            wrong.set("zapper-provider-id", "iptv-org:shahid.mbc.net:997605")
            exact = programme(feed, "20261107173000 +0000", "20261107190000 +0000", "Confirmed edition")
            output = root / "candidate.xml"
            write_guide(output, {feed: [wrong, exact]})
            self.assertEqual([row.findtext("title") for row in ET.parse(output).getroot().findall("programme")],
                             ["Confirmed edition"])

    def test_reviewed_provider_cannot_emit_a_different_identity(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bindings = root / "reviewed.xml"
            bindings.write_text('<channels><channel xmltv_id="Exact.es@SD" site="example.com" site_id="10"/></channels>')
            wrong = ET.Element("tv")
            wrong.append(programme("Wrong.es@SD", "20261107170000 +0000", "20261107190000 +0000"))
            ET.ElementTree(wrong).write(root / "example.com.xml")
            with self.assertRaisesRegex(ValueError, "Unexpected provider binding"):
                reviewed_programmes(root, bindings, NOW)

    def test_expired_guide_is_not_admitted(self):
        expired = programme("Exact.es@SD", "20261107150000 +0000", "20261107170000 +0000")
        self.assertEqual(programme_state([expired], NOW), "fuera-de-ventana")

    def test_future_guide_with_a_real_gap_keeps_the_signal_in_both_outputs(self):
        future, expired, outside, absent = ("Future.ar@SD", "Expired.ar@SD", "Outside.ar@SD", "Absent.ar@SD")
        ids = (future, expired, outside, absent)
        guide = ET.Element("tv")
        playlist = f'#EXTM3U x-tvg-url="{EPG_HEADER}"\n'
        for feed in ids:
            ET.SubElement(guide, "channel", id=feed)
            playlist += f'#EXTINF:-1 tvg-id="{feed}" group-title="00 - General",{feed}\nhttps://example.com/{feed}\n'
        rows = ET.Element("tv")
        rows.append(programme(future, "20261107170000 +0000", "20261107180000 +0000", "Terminó"))
        rows.append(programme(future, "20261107183000 +0000", "20261107190000 +0000", "Siguiente"))
        rows.append(programme(expired, "20261107170000 +0000", "20261107180000 +0000"))
        rows.append(programme(outside, "20261109060100 +0000", "20261109070100 +0000"))
        expansion = [{"tvgID": feed, "name": feed, "category": "00 - General"} for feed in ids]
        payload = {"catalog": [], "expansion": expansion, "playlist": playlist,
                   "flexXML": ET.tostring(guide, encoding="unicode"), "unavailableProgrammeIDs": []}
        with TemporaryDirectory() as directory:
            source = Path(directory) / "verified.xml"
            output = Path(directory) / "release"
            ET.ElementTree(rows).write(source)
            write_release(payload, output, NOW, [source], keep_inventory=True)
            actual, _ = unpack(output / "coordinated-generation.json")
            self.assertEqual(actual["expansion"], expansion)
            self.assertEqual(set(actual["withheldExpansionIDs"]), {expired, outside, absent})
            parsed = ET.fromstring(actual["flexXML"])
            self.assertEqual([row.get("id") for row in parsed.findall("channel")], [future])
            self.assertEqual(programme_state(parsed.findall("programme"), NOW), "agenda-verificada")
            self.assertEqual([p.findtext("title") for p in parsed.findall("programme")], ["Terminó", "Siguiente"])
            self.assertIn(f'tvg-id="{future}"', actual["playlist"])
            self.assertNotIn(f'tvg-id="{expired}"', actual["playlist"])
            self.assertNotIn(f'tvg-id="{absent}"', actual["playlist"])
