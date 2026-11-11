"""Extract the dated Canal 8 Mar del Plata schedule from broadcaster Mi8."""

from datetime import datetime, time, timedelta, timezone
from html import unescape
import json
from pathlib import Path
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

HOME = "https://mi8.com.ar/programacion/"
AJAX = "https://mi8.com.ar/wp-admin/admin-ajax.php"
CHANNEL = "Canal8MardelPlata.ar@SD"
PROVIDER = "official:mi8.com.ar:canal8mdp"
ZONE = ZoneInfo("America/Argentina/Buenos_Aires")
XMLTV = "%Y%m%d%H%M%S %z"
MAX_BYTES = 2_000_000
SLOT = re.compile(
    r'<div class="extvs-st2-time"><span>(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})</span></div>'
    r'\s*<div class="extvs-st2-info">\s*<h3>(.*?)</h3>',
    re.S,
)


def read_page(url, data=None):
    request = Request(url, data=data, headers={
        "User-Agent": "Zapper guide",
        "Referer": HOME,
        "X-Requested-With": "XMLHttpRequest",
    })
    with urlopen(request, timeout=12) as response:
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("Mi8 schedule response too large")
    return body.decode("utf-8")


def programmes_for_day(page, day):
    programmes = []
    previous_start = previous_stop = None
    for start_clock, stop_clock, raw_title in SLOT.findall(page):
        start = datetime.combine(day, time.fromisoformat(start_clock), ZONE)
        if previous_start and start < previous_start:
            if start.hour > 4:
                raise ValueError("Unexpected Mi8 schedule day rollover")
            start += timedelta(days=1)
        stop = datetime.combine(start.date(), time.fromisoformat(stop_clock), ZONE)
        if stop < start:
            stop += timedelta(days=1)
        if not timedelta() < stop - start <= timedelta(hours=12):
            raise ValueError("Invalid Mi8 schedule duration")
        if previous_stop and start < previous_stop:
            raise ValueError("Overlapping Mi8 schedule")
        title = " ".join(unescape(re.sub(r"<[^>]+>", "", raw_title)).split())
        if not title:
            raise ValueError("Empty Mi8 schedule title")
        programme = ET.Element("programme", {
            "channel": CHANNEL,
            "start": start.astimezone(timezone.utc).strftime(XMLTV),
            "stop": stop.astimezone(timezone.utc).strftime(XMLTV),
            "zapper-provenance": HOME,
            "zapper-provider-id": PROVIDER,
        })
        ET.SubElement(programme, "title", {"lang": "es"}).text = title
        programmes.append(programme)
        previous_start, previous_stop = start, stop
    if not programmes:
        raise ValueError("Mi8 schedule is empty")
    return programmes


def dated_page(page, day):
    selected = re.findall(r'class="extvs-date-day extvs-date-day-at" data-date="(\d+)"', page)
    if len(selected) != 1 or "Canal 8 Mar del Plata" not in page:
        raise ValueError("Mi8 page is not the Canal 8 dated schedule")
    if datetime.fromtimestamp(int(selected[0]), timezone.utc).date() != day:
        raise ValueError("Mi8 schedule page date mismatch")
    return programmes_for_day(page, day)


def fetch_programmes(now):
    day = now.astimezone(ZONE).date()
    page = read_page(HOME)
    current = dated_page(page, day)
    tomorrow = day + timedelta(days=1)
    date_code = str(int(datetime.combine(tomorrow, time(), timezone.utc).timestamp()))
    if f'data-date="{date_code}"' not in page:
        return current
    shortcode = re.search(r'name="param_shortcode"\s+value="([^"]+)"', page)
    if not shortcode:
        return current
    params = unescape(shortcode[1])
    if json.loads(params).get("channel") != "canal8mdp":
        raise ValueError("Mi8 schedule selector does not target Canal 8")
    try:
        response = json.loads(read_page(AJAX, urlencode({
            "action": "extvs_get_schedule_simple",
            "param_shortcode": params,
            "date": date_code,
            "chanel": "canal8mdp",
        }).encode()))
        if response.get("current_day") != "no":
            return current
        upcoming = programmes_for_day(response["html"], tomorrow)
    except (OSError, ValueError, KeyError, AttributeError):
        return current
    current_end = datetime.strptime(current[-1].attrib["stop"], XMLTV)
    return current + [row for row in upcoming
                      if datetime.strptime(row.attrib["start"], XMLTV) >= current_end]


def write_xml(path, programmes):
    root = ET.Element("tv", {"generator-info-name": "Canal 8 Mar del Plata — Mi8"})
    channel = ET.SubElement(root, "channel", {"id": CHANNEL})
    ET.SubElement(channel, "display-name").text = "Canal 8 Mar del Plata"
    root.extend(programmes)
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    rows = (dated_page(args.snapshot.read_text(), now.astimezone(ZONE).date())
            if args.snapshot else fetch_programmes(now))
    write_xml(args.output, rows)
    print(f"{len(rows)} programmes")
