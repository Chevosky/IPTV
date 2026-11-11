"""Read the national weekly schedule exposed by ISTV's own website."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
import html
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

HOME = "https://istv.com.br/"
AJAX = HOME + "wp-admin/admin-ajax.php"
LIVE = HOME + "aovivo/"
CHANNEL = "ISTV.br@SD"
DAYS = ("Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo")
ZONE = ZoneInfo("America/Sao_Paulo")
XMLTV = "%Y%m%d%H%M%S %z"
CARDS = re.compile(r'class="my-post-title">([^<]+)</h3>\s*<p class="my-post-excerpt">([^<]+)</p>')


def read_public(url, data=None):
    headers = {"User-Agent": "Zapper guide validation", "Referer": HOME}
    if data:
        headers["X-Requested-With"] = "XMLHttpRequest"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read(1_000_001).decode("utf-8")


def check_advertised_stream(verified_urls):
    try:
        page = read_public(LIVE)
    except (OSError, ValueError):
        return "official-page-unavailable"
    sources = {html.unescape(url) for url in re.findall(
        r'<source\s+src="(https://[^"\s<>]+\.m3u8(?:\?[^"\s<>]*)?)"', page)}
    if sources & verified_urls:
        return "matches-verified-url"
    return "changed-pending-validation" if sources else "official-url-unavailable"


def fetch_days():
    page = read_public(HOME)
    field = re.search(r'class="my-prog-custom"[^>]*data-nonce="([^"]+)"', page)
    if not field:
        raise ValueError("ISTV cambió su formulario de programación")

    def fetch(day):
        data = urllib.parse.urlencode({
            "action": "load_prog_posts", "nonce": field[1], "dia": day,
            "regiao": "nacional", "posts_per_page": 60,
        }).encode()
        return day, read_public(AJAX, data)

    with ThreadPoolExecutor(max_workers=3) as pool:
        return dict(pool.map(fetch, DAYS))


def weekly_programmes(pages, now):
    week = {}
    for day in DAYS:
        cards = CARDS.findall(pages[day])
        if len(cards) < 10:
            raise ValueError(f"Grilla nacional de ISTV incompleta para {day}")
        entries = {}
        for title, detail in cards:
            prefix, delimiter, times = html.unescape(detail).partition(" - ")
            if not delimiter or prefix.strip() != day:
                raise ValueError("El sitio devolvió programación de otro día")
            title = html.unescape(title).strip()
            if not title:
                raise ValueError("Programa de ISTV sin título")
            for value in times.split("/"):
                clock = time.fromisoformat(value.strip())
                if clock in entries:
                    raise ValueError(f"Horario de ISTV duplicado para {day}")
                entries[clock] = title
        if min(entries) != time(0):
            raise ValueError(f"La grilla de ISTV empieza después de medianoche para {day}")
        week[day] = sorted(entries.items())

    today = now.astimezone(ZONE).date()
    programmes = []
    for index in range(7):
        date = today + timedelta(days=index)
        entries = week[DAYS[date.weekday()]]
        starts = [datetime.combine(date, clock, ZONE) for clock, _ in entries]
        midnight = datetime.combine(date + timedelta(days=1), time(0), ZONE)
        for (_, title), start, stop in zip(entries, starts, starts[1:] + [midnight]):
            programme = ET.Element("programme", {
                "channel": CHANNEL, "start": start.strftime(XMLTV), "stop": stop.strftime(XMLTV),
                "zapper-provenance": HOME, "zapper-schedule-edition": "nacional",
            })
            ET.SubElement(programme, "title", lang="pt").text = title
            programmes.append(programme)
    return programmes


def overlay(data, programmes):
    if not programmes:
        return data
    root = ET.fromstring(data)
    if not any(node.get("id") == CHANNEL for node in root.findall("channel")):
        raise ValueError("ISTV ausente de la guía base")
    for node in root.findall("programme"):
        if node.get("channel") == CHANNEL:
            root.remove(node)
    root.extend(programmes)
    return ET.tostring(root, encoding="unicode")


def retained_programmes(data, now):
    if not data:
        return []
    root = ET.fromstring(data)
    programmes = [node for node in root.findall("programme")
                  if node.get("channel") == CHANNEL and node.get("zapper-provenance") == HOME]
    if any(datetime.strptime(node.attrib["stop"], XMLTV) > now for node in programmes):
        return programmes
    return []
