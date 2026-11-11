#!/usr/bin/env python3
"""Project an audited expansion into a paired playlist and XMLTV release candidate."""
import argparse
import base64
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from guide_history import instant as history_instant, preserve as preserve_history, stable_bindings
from order_lineup import order_lineup
from xmltv_format import xmltv_bytes

XMLTV_DATE = "%Y%m%d%H%M%S %z"
EPG_HEADER = "https://raw.githubusercontent.com/Chevosky/IPTV/main/guide-flex.xml"
VERIFIED_WINDOW = timedelta(hours=36)
VERIFIED_STATES = {"vigente", "agenda-verificada"}


def unpack(path):
    envelope = json.loads(path.read_bytes())
    payload = base64.b64decode(envelope["payload"], validate=True)
    if hashlib.sha256(payload).hexdigest() != envelope["sha256"]:
        raise ValueError("El sobre original no coincide con su hash")
    return json.loads(payload), envelope["sha256"]


def programme_state(programmes, now):
    spans = []
    for node in programmes:
        attrs = node.attrib
        provenance = urlsplit(attrs.get("zapper-provenance", ""))
        provider = attrs.get("zapper-provider-id", "")
        title = node.findtext("title", "").strip()
        if provenance.scheme != "https" or not provenance.hostname or not provider or not title:
            continue
        start, stop = (datetime.strptime(attrs[key], XMLTV_DATE) for key in ("start", "stop"))
        if start < stop <= start + timedelta(hours=12):
            spans.append((start, stop, provider))
    spans.sort()
    if any(first[1] > second[0] for first, second in zip(spans, spans[1:])):
        return "solapamiento"
    if any(start <= now < stop for start, stop, _ in spans):
        return "vigente"
    if any(now < start <= now + VERIFIED_WINDOW for start, _, _ in spans):
        return "agenda-verificada"
    if spans:
        return "fuera-de-ventana"
    return "sin-programacion-verificada"


def playlist_rows(playlist):
    head, *rows = re.split(r"(?m)(?=^#EXTINF:)", playlist)
    feeds = [re.search(r'^#EXTINF:[^\n]*\btvg-id="([^"]+)"', row).group(1) for row in rows]
    if len(feeds) != len(set(feeds)) or EPG_HEADER not in head:
        raise ValueError("M3U con IDs duplicados o URL de guía inesperada")
    return head, dict(zip(feeds, rows))


def overlay_verified_guides(root, guides, expansion_ids, now):
    for path in guides:
        guide = ET.parse(path).getroot()
        if guide.tag != "tv":
            raise ValueError(f"{path}: XMLTV inválido")
        by_feed = defaultdict(list)
        for node in guide.findall("programme"):
            feed = node.attrib["channel"]
            if feed not in expansion_ids:
                raise ValueError(f"{path}: {feed} está fuera de la expansión auditada")
            by_feed[feed].append(node)
        for feed, rows in by_feed.items():
            if programme_state(rows, now) not in VERIFIED_STATES:
                continue
            for node in root.findall("programme"):
                if node.attrib["channel"] == feed:
                    root.remove(node)
            root.extend(rows)


def build(payload, now, verified_guides=(), editorial_ids=frozenset()):
    root = ET.fromstring(payload["flexXML"])
    header, m3u_rows = playlist_rows(payload["playlist"])
    expansion_ids = {entry["tvgID"] for entry in payload["expansion"]}
    overlay_verified_guides(root, verified_guides, expansion_ids, now)
    programmes = defaultdict(list)
    for node in root.findall("programme"):
        programmes[node.attrib["channel"]].append(node)
    report = []
    excluded = set()
    for entry in payload["expansion"]:
        feed = entry["tvgID"]
        if feed not in m3u_rows:
            raise ValueError(f"El M3U no contiene la señal {feed}")
        state = programme_state(programmes[feed], now)
        retained = state in VERIFIED_STATES
        sources = sorted({node.attrib["zapper-provenance"] for node in programmes[feed]
                          if "zapper-provenance" in node.attrib})
        report.append({"id": feed, "name": entry["name"], "category": entry["category"],
                       "state": state, "included": retained, "sources": sources})
        if not retained:
            excluded.add(feed)
    excluded.update(editorial_ids)
    for node in list(root):
        if node.get("channel", node.get("id")) in excluded:
            root.remove(node)
    m3u = header + "".join(row for feed, row in m3u_rows.items() if feed not in excluded)
    declared = [node.attrib["id"] for node in root.findall("channel")]
    included = set(m3u_rows) - excluded
    if (set(declared) != included or len(declared) != len(included)
            or any(node.attrib["channel"] not in included for node in root.findall("programme"))):
        raise ValueError("La salida M3U y XMLTV no coinciden")
    return m3u, xmltv_bytes(root), report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--as-of", type=datetime.fromisoformat, default=datetime.now(timezone.utc))
    parser.add_argument("--verified-guide", type=Path, action="append", default=[],
                        help="XMLTV revisado, de mayor prioridad al final")
    args = parser.parse_args()
    if args.as_of.tzinfo is None:
        parser.error("--as-of requiere zona horaria")
    payload, sha256 = unpack(args.input)
    write_release(payload, args.output, args.as_of, args.verified_guide, sha256)


def history_xmltv_bytes(root):
    rank = {channel.get("id"): index for index, channel in enumerate(root.findall("channel"))}
    ordered = iter(sorted(root.findall("programme"), key=lambda row: (
        rank[row.attrib["channel"]], history_instant(row.attrib["start"]),
    )))
    for index, row in enumerate(root):
        if row.tag == "programme":
            root[index] = next(ordered)
    return xmltv_bytes(root)


def write_release(payload, output, now, verified_guides, input_sha256=None, *,
                  keep_inventory=False, editorial=None, previous_payload=None, history_programme_allowed=None):
    editorial = editorial or {}
    editorial_ids = set(editorial.get("withheldTvgIDs", []))
    youtube_publishers = set(editorial.get("withheldYouTubePublishers", []))
    if editorial_ids or youtube_publishers:
        if not keep_inventory:
            raise ValueError("Editorial review requires preserving the inventory")
        known = {row["tvgId"] for row in payload["catalog"]} | {row["tvgID"] for row in payload["expansion"]}
        if not editorial_ids <= known or not youtube_publishers <= {row["publisherID"] for row in payload["youtube"]}:
            raise ValueError("Editorial review contains an unknown signal")
    m3u, xml, report = build(payload, now, verified_guides, editorial_ids)
    accepted = {row["id"] for row in report if row["included"]}
    excluded = {row["id"] for row in report if not row["included"]}
    if keep_inventory:
        payload["withheldExpansionIDs"] = sorted(excluded)
        payload["editorialWithheldTvgIDs"] = sorted(editorial_ids)
        payload["editorialWithheldYouTubePublishers"] = sorted(youtube_publishers)
    else:
        payload["expansion"] = [entry for entry in payload["expansion"] if entry["tvgID"] in accepted]
        payload.pop("withheldExpansionIDs", None)
    m3u, xml = order_lineup(m3u, xml, payload["catalog"], payload["expansion"])
    payload["unavailableProgrammeIDs"] = [feed for feed in payload["unavailableProgrammeIDs"]
                                           if feed not in excluded and feed not in accepted and feed not in editorial_ids]
    history_count = 0
    if previous_payload is not None:
        try:
            allowed = stable_bindings(previous_payload, payload)
        except (TypeError, ValueError):
            allowed = set()
        if allowed:
            xml, history_count = preserve_history(
                xml, previous_payload["flexXML"].encode(), now, history_xmltv_bytes,
                allowed_ids=allowed, unavailable_ids=set(payload["unavailableProgrammeIDs"]),
                programme_allowed=history_programme_allowed,
            )
    payload["playlist"] = m3u
    payload["flexXML"] = xml.decode()
    payload["publication"] = {"schemaVersion": 1, "generatedAt": int(now.timestamp())}
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    envelope = {"sha256": hashlib.sha256(serialized).hexdigest(),
                "payload": base64.b64encode(serialized).decode("ascii")}
    from flex_sports import project
    exported_playlist, exported_guide = project(
        payload, playlist_rows, require_fresh=not previous_payload or "flexSports" not in previous_payload)
    output.mkdir(mode=0o700)
    (output / "zapper-flex.m3u").write_text(exported_playlist)
    (output / "guide-flex.xml").write_bytes(exported_guide)
    (output / "guide-flex-20261003.xml").write_bytes(exported_guide)
    (output / "coordinated-generation.json").write_text(json.dumps(envelope) + "\n")
    for row in report:
        if row["id"] in editorial_ids:
            row["included"] = False
            row["editorialReview"] = True
    summary = {"inputSHA256": input_sha256, "checkedAt": now.isoformat(),
               "expansionCount": len(report), "includedCount": sum(row["included"] for row in report),
               "playlistCount": len(playlist_rows(exported_playlist)[1]), "expansion": report,
               "retainedHistoricalProgrammes": history_count}
    (output / "revision-epg.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print({key: value for key, value in summary.items() if key != "expansion"})


if __name__ == "__main__":
    main()
