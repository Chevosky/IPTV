"""Dated national schedule from the Telefe broadcaster."""
import re
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET


CHANNEL = "TelefeBuenosAires.ar@SD"
PROVIDER = "mitelefe:grilla-nacional"
URL = "https://www.mitelefe.com/grilla-de-horarios-y-programacion"
ART = ZoneInfo("America/Argentina/Buenos_Aires")
DATE = "%Y%m%d%H%M%S %z"
SECTION = re.compile(r'<section\b[^>]*\bdata-controller="schedule"[^>]*>(.*?)</section>', re.S)
PANE = re.compile(r'\bid="tab-pane-(\d{8})"')
ITEM = re.compile(r'<div\b[^>]*\bdata-schedule-target="item"[^>]*>', re.S)
TIME = re.compile(r'<span class="schedule-time-text">\s*(\d{2}:\d{2})\s*</span>')
TITLE = re.compile(r'<h3 class="schedule-program-title\b[^"]*">(.*?)</h3>', re.S)


def slots(document, now):
    section, = [section for section in SECTION.findall(document)
                if "Grilla de horarios nacional" in section]
    panes = iter(PANE.split(section)[1:])
    local_today = now.astimezone(ART).date()
    programmes = []
    for day, pane in zip(panes, panes):
        local_day = datetime.strptime(day, "%Y%m%d").date()
        if not local_today - timedelta(days=1) <= local_day <= local_today + timedelta(days=2):
            continue
        dated_items = []
        last_clock = None
        rolled = False
        for item in ITEM.split(pane)[1:]:
            match = TIME.search(item)
            if not match:
                dated_items.append((None, ""))
                continue
            clock = datetime.strptime(match[1], "%H:%M").time()
            if last_clock is not None and clock <= last_clock:
                if rolled or last_clock.hour < 20 or clock.hour >= 6:
                    raise ValueError("Unordered Telefe schedule")
                local_day += timedelta(days=1)
                rolled = True
            last_clock = clock
            start = datetime.combine(local_day, clock, ART).astimezone(timezone.utc)
            title = TITLE.search(item)
            title = " ".join(unescape(re.sub(r"<[^>]+>", "", title[1])).split()) if title else ""
            dated_items.append((start, title))
        # A start closes only the previous card in the same dated pane.
        for (start, title), (stop, _) in zip(dated_items, dated_items[1:]):
            if not (start and stop and title and start < stop <= start + timedelta(hours=12)
                    and now < stop and start <= now + timedelta(hours=36)):
                continue
            row = ET.Element("programme", {
                "channel": CHANNEL, "start": start.strftime(DATE), "stop": stop.strftime(DATE),
                "zapper-provider-id": PROVIDER, "zapper-provenance": URL,
            })
            ET.SubElement(row, "title", lang="es").text = title
            programmes.append(row)
    programmes.sort(key=lambda row: row.get("start"))
    if any(left.get("stop") > right.get("start") for left, right in zip(programmes, programmes[1:])):
        raise ValueError("Overlapping Telefe schedule")
    return programmes


def fetch_programmes(now):
    request = Request(URL, headers={"User-Agent": "Zapper EPG/1.0"})
    with urlopen(request, timeout=12) as response:
        document = response.read(2_000_001)
    if len(document) > 2_000_000:
        raise ValueError("Oversized Telefe schedule")
    return slots(document.decode("utf-8"), now)


def overlay(fresh, fallback, parse_time):
    """Keep slots omitted by an incomplete fetch, including verified fallback windows."""
    windows = [(parse_time(row.get("start")), parse_time(row.get("stop"))) for row in fresh]
    retained = []
    for row in fallback:
        if row.get("channel") != CHANNEL:
            retained.append(row)
            continue
        start, stop = parse_time(row.get("start")), parse_time(row.get("stop"))
        if start and stop and all(stop <= a or start >= b for a, b in windows):
            retained.append(row)
    return retained + fresh
