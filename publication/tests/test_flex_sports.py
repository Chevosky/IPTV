from base64 import b64encode
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET

PUB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PUB / "scripts"))
from filter_expansion import EPG_HEADER, playlist_rows, unpack, write_release
from flex_sports import project, load_review
from validate_zapper_guide import previous_inventory, stats

SPORTS = PUB / "sports"


def data(entries):
    guide = ET.Element("tv")
    for row in entries:
        feed = row["id"]
        ET.SubElement(ET.SubElement(guide, "channel", id=feed), "display-name").text = row["name"]
        programme = ET.SubElement(guide, "programme", {
            "channel": feed, "start": "20261108073000 +0000", "stop": "20261108093000 +0000"})
        ET.SubElement(programme, "title").text = "Current programme"
    return {
        "playlist": f'#EXTM3U x-tvg-url="{EPG_HEADER}"\n'
                    '#EXTINF:-1 tvg-id="Base.ar@SD" group-title="Noticias",Base\nhttps://example.org/base.m3u8\n',
        "flexXML": '<tv><channel id="Base.ar@SD"/><programme channel="Base.ar@SD" zapper-retained-history="1" '
                   'start="20261106000000 +0000" stop="20261106010000 +0000"><title>Past</title></programme></tv>',
        "sportsXML": ET.tostring(guide, encoding="unicode"),
        "publication": {"generatedAt": 1794124800}, # 2026-11-08 08:00:00Z
        "flexSports": entries,
        "youtube": [{"publisherID": "UCx", "videoID": "ABCDEFGHIJK"}],
    }


class FlexSportsTests(unittest.TestCase):
    def setUp(self):
        self.entries = json.loads((SPORTS / "flex-direct-bindings.json").read_bytes())
        self.payload = data(self.entries)

    def test_project_is_deterministic_and_preserves_base_and_history(self):
        original = deepcopy(self.payload)
        m3u, xml = project(self.payload, playlist_rows)
        self.assertEqual((m3u, xml), project(self.payload, playlist_rows))
        self.assertEqual(self.payload, original)
        _, rows = playlist_rows(m3u)
        self.assertEqual(set(rows), {"Base.ar@SD", *(r["id"] for r in self.entries)})
        self.assertIn('zapper-retained-history="1"', xml.decode())
        self.assertNotIn("ABCDEFGHIJK", m3u)
        self.assertNotIn("UCx", xml.decode())
        for entry in self.entries:
            self.assertIn(entry["url"], rows[entry["id"]])
            self.assertEqual(len(ET.fromstring(xml).findall(f'programme[@channel="{entry["id"]}"]')), 1)
        prior = dict(self.payload)
        prior.pop("flexSports")
        self.assertEqual(project(prior, playlist_rows), (prior["playlist"], prior["flexXML"].encode()))

    def test_sports_are_merged_in_lineup_order_with_canonical_icons_and_xmltv_order(self):
        payload = deepcopy(self.payload)
        base_guide = ET.fromstring(payload["flexXML"])
        neighbours = [
            ("Antes.es@SD", "AA Spanish", "ES", "es"),
            ("Despues.es@SD", "ZZ Spanish", "ES", "es"),
            ("Ante.us@SD", "AA English", "US", "en"),
            ("Post.us@SD", "ZZ English", "US", "en"),
        ]
        payload["catalog"] = [{"id": "base", "tvgId": "Base.ar@SD", "name": "Base", "country": "AR", "language": "es"}]
        for feed, name, country, language in neighbours:
            payload["playlist"] += (f'#EXTINF:-1 tvg-id="{feed}" group-title="Deportes",{name}\n'
                                    'https://example.org/base.m3u8\n')
            ET.SubElement(base_guide, "channel", id=feed)
            payload["catalog"].append(dict(id=feed, tvgId=feed, name=name, country=country, language=language))
        payload["flexXML"] = ET.tostring(base_guide, encoding="unicode")
        entries = {row["name"]: row for row in self.entries}
        m3u, xml = project(payload, playlist_rows)
        _, rows = playlist_rows(m3u)
        root = ET.fromstring(xml)
        channel_ids = [n.get("id") for n in root.findall("channel")]
        self.assertEqual(list(rows), channel_ids)
        self.assertEqual(channel_ids, [
            "Base.ar@SD", entries["NBC NEWS NOW"]["id"],
            "Antes.es@SD", entries["RM TV + 🇪🇸"]["id"], "Despues.es@SD",
            "Ante.us@SD", entries["beIN SPORTS XTRA"]["id"], "Post.us@SD",
            entries["FIFA+"]["id"], entries["RM TV"]["id"],
        ])
        tags = [row.tag for row in root]
        self.assertEqual(tags, ["channel"] * len(rows) + ["programme"] * len(root.findall("programme")))
        by_channel_time = [(channel_ids.index(row.get("channel")), row.get("start"))
                           for row in root.findall("programme")]
        self.assertEqual(by_channel_time, sorted(by_channel_time))
        for row in entries.values():
            self.assertIn(f'tvg-logo="{row["logoURL"]}"', rows[row["id"]])
            self.assertEqual(root.find(f'channel[@id="{row["id"]}"]/icon').get("src"), row["logoURL"])

    def test_second_cycle_counts_only_base_after_validating_exact_previous_projection(self):
        payload = deepcopy(self.payload)
        previous_guide = ET.fromstring(payload["flexXML"])
        ET.SubElement(previous_guide, "channel", id="Withheld.ar@SD")
        for channel in ("Base.ar@SD", "Withheld.ar@SD"):
            programme = ET.SubElement(previous_guide, "programme", {
                "channel": channel, "start": "20261108073000 +0000", "stop": "20261108093000 +0000"})
            ET.SubElement(programme, "title").text = "Long programme text " * 3_000
        payload["playlist"] += '#EXTINF:-1 tvg-id="Withheld.ar@SD" group-title="Noticias",Withheld\nhttps://example.org/withheld.m3u8\n'
        payload["flexXML"] = ET.tostring(previous_guide, encoding="unicode")
        _, projected = project(payload, playlist_rows)
        now = datetime.fromtimestamp(payload["publication"]["generatedAt"], tz=timezone.utc)
        base = {"Base.ar@SD"}
        inactive = {"Withheld.ar@SD"}

        def seal(path, data):
            raw = json.dumps(data).encode()
            path.write_text(json.dumps({"sha256": sha256(raw).hexdigest(), "payload": b64encode(raw).decode()}))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed, prior, guide = root / "expansion-seed.json", root / "coordinated-generation.json", root / "guide-flex.xml"
            seal(seed, {"expansion": [{"tvgID": "Extra.ar@SD"}]})
            seal(prior, payload)
            guide.write_bytes(projected)
            wanted, excluded = previous_inventory(guide, seed, base, inactive)
            self.assertEqual(wanted, base)
            self.assertEqual(excluded, inactive | {"Extra.ar@SD"} | {row["id"] for row in self.entries})
            previous = stats(guide, wanted, now, require_fresh=False, excluded_ids=excluded)
            self.assertEqual((previous["channels"], previous["programmes"], previous["currentCoverage"]), (1, 1, 1))
            with self.assertRaisesRegex(ValueError, "exactly"):
                stats(guide, base, now, require_fresh=False, excluded_ids=inactive)

            guide.write_bytes(projected.replace(b"Current programme", b"Substituted guide", 1))
            with self.assertRaisesRegex(ValueError, "differs"):
                previous_inventory(guide, seed, base, inactive)

            guide.write_bytes(projected)
            with self.assertRaisesRegex(ValueError, "overlap"):
                previous_inventory(guide, seed, base | {self.entries[0]["id"]}, inactive)
            seal(seed, {"expansion": [{"tvgID": self.entries[0]["id"]}]})
            with self.assertRaisesRegex(ValueError, "overlap"):
                previous_inventory(guide, seed, base, inactive)

    def test_writer_matches_sealed_projection_and_failure_creates_no_output(self):
        now=datetime.fromtimestamp(self.payload["publication"]["generatedAt"],tz=timezone.utc)
        payload={**self.payload,"catalog":[{"id":"base","tvgId":"Base.ar@SD","name":"Base","country":"AR","language":"es"}],
                 "expansion":[],"unavailableProgrammeIDs":[]}
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            good=base/"good"
            write_release(deepcopy(payload),good,now,[])
            sealed,_=unpack(good/"coordinated-generation.json")
            self.assertEqual(len(playlist_rows(sealed["playlist"])[1]),1)
            exported,guide=project(sealed,playlist_rows)
            self.assertEqual((good/"zapper-flex.m3u").read_text(),exported)
            self.assertEqual((good/"guide-flex.xml").read_bytes(),guide)
            self.assertEqual((good/"guide-flex-20261003.xml").read_bytes(),guide)
            invalid=base/"invalid"
            with self.assertRaises(ValueError):
                write_release({**payload,"flexSports":self.entries[:-1]},invalid,now,[])
            self.assertFalse(invalid.exists())

    def test_lost_duplicate_unknown_and_nonportable_sources_fail_closed(self):
        for change in (self.entries[:-1], self.entries[:-1]+self.entries[:1],
                       [dict(self.entries[0],id="youtube:unknown")]+self.entries[1:],
                       [dict(self.entries[0],url="https://example.org/player.html")]+self.entries[1:],
                       [dict(self.entries[0],url="https://example.org/live.mpd")]+self.entries[1:],
                       [dict(self.entries[0],url="https://example.org/live.m3u8?token=x")]+self.entries[1:]):
            with self.subTest(change=len(change)), self.assertRaises(ValueError):
                project(dict(self.payload,flexSports=change), playlist_rows)

    def test_guide_missing_stale_duplicate_overlap_and_editorial_veto_fail_closed(self):
        feed=self.entries[0]["id"]
        for situation in ("missing","stale","duplicate","overlap","veto"):
            payload=deepcopy(self.payload)
            guide=ET.fromstring(payload["sportsXML"])
            programme=guide.find(f'programme[@channel="{feed}"]')
            assert programme is not None
            if situation=="missing":
                guide.remove(programme)
            if situation=="stale":
                programme.set("start","20261106000000 +0000")
                programme.set("stop","20261106010000 +0000")
            if situation=="duplicate":
                guide.append(deepcopy(guide.find(f'channel[@id="{feed}"]')))
            if situation=="overlap":
                guide.append(deepcopy(programme))
            if situation=="veto":
                payload["editorialWithheldTvgIDs"]=[feed]
            payload["sportsXML"]=ET.tostring(guide,encoding="unicode")
            with self.subTest(situation=situation),self.assertRaises(ValueError):
                project(payload,playlist_rows,require_fresh=True)

    def test_existing_projection_can_carry_stale_sports_without_inventing_schedule(self):
        payload=deepcopy(self.payload)
        payload["publication"]["generatedAt"]+=60*60*48
        m3u,xml=project(payload,playlist_rows)
        self.assertEqual(len(playlist_rows(m3u)[1]),6)
        self.assertIn("Current programme",xml.decode())
        self.assertNotIn("20261110",xml.decode())
        with self.assertRaises(ValueError):
            project(payload,playlist_rows,require_fresh=True)

    def test_provider_binding_must_be_exact(self):
        path=SPORTS/"guide-bindings.json"
        selected=SPORTS/"flex-direct-bindings.json"
        self.assertEqual(load_review(selected,path), self.entries)
        changed=json.loads(path.read_bytes())
        match=next(r for r in changed["channels"] if r["id"]==self.entries[0]["id"])
        match["schedule"]["providerChannelID"]="different-edition"
        with tempfile.TemporaryDirectory() as tmp:
            bad=Path(tmp)/"bindings.json"
            bad.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                load_review(selected,bad)


if __name__=="__main__":
    unittest.main()
