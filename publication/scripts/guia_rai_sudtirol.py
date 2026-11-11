"""Read only explicitly timed programmes from the public German-language Rai Südtirol guide."""
from datetime import datetime, timedelta
from http.cookiejar import CookieJar
import re
from urllib.request import HTTPCookieProcessor, Request, build_opener
from zoneinfo import ZoneInfo

from guia_expansion import plain_html, read_response

FEED = "RaiSudtirol.it@SD"
PROVIDER = "rai-sudtirol:de:tv"
ORIGIN = "https://www.raisudtirol.rai.it"
HOME = f"{ORIGIN}/de/"
ROME = ZoneInfo("Europe/Rome")
SOURCES = frozenset(f"https://wzstreaming.rai.it/TVlive/smil:liveStream.smil/{name}.m3u8"
                    for name in ("index", "playlist"))


def rows(now, snapshot):
    opener = build_opener(HTTPCookieProcessor(CookieJar())) if snapshot is None else None

    def read(url, name):
        if opener is None:
            return read_response(url, snapshot, name, maximum=250_000).decode("utf-8")
        headers = {"User-Agent": "Mozilla/5.0", "Referer": HOME}
        if url != HOME:
            headers["X-Requested-With"] = "XMLHttpRequest"
        with opener.open(Request(url, headers=headers), timeout=12) as response:
            payload = response.read(250_001)
        if len(payload) > 250_000:
            raise ValueError("Respuesta de Rai Südtirol demasiado grande")
        return payload.decode("utf-8", errors="replace")

    if opener is not None:
        try:
            read(HOME, "raisudtirol-home.html")
        except (OSError, ValueError):
            return
    today = now.astimezone(ROME).date()
    for offset in (-1, 0, 1):
        day = today + timedelta(days=offset)
        url = f"{ORIGIN}/lib/data_palinsesto.php?tipo=tv&day={day.year}-{day.month}-{day.day}&struct=sb"
        try:
            schedule = read(url, f"raisudtirol-{day}.html")
        except (OSError, ValueError):
            continue
        if 'class="content_program"' not in schedule:
            continue
        listings = []
        for item in re.findall(r"<tr\b[^>]*>(.*?)</tr>", schedule, re.S):
            time = re.search(r'itemprop="startDate">([0-2]\d:[0-5]\d)<', item)
            title = re.search(r'<strong itemprop="name">(.*?)</strong>', item, re.S)
            if not time or not title:
                continue
            event = re.search(r'href="/view_event\.php\?id=(\d+)"', title[1])
            try:
                clock = datetime.strptime(time[1], "%H:%M").time()
            except ValueError:
                continue
            listings.append((datetime.combine(day, clock, ROME), plain_html(title[1]), event[1] if event else None))
        if any(left[0] >= right[0] for left, right in zip(listings, listings[1:])):
            continue
        for index, (start, title, event_id) in enumerate(listings):
            if not event_id or "\ufffd" in title or not now - timedelta(hours=12) <= start <= now + timedelta(hours=36):
                continue
            url = f"{ORIGIN}/view_event.php?id={event_id}"
            try:
                detail = read(url, f"raisudtirol-event-{event_id}.html")
            except (OSError, ValueError):
                continue
            date = re.search(r"Date\.parseExact\('(\d{4}-\d{2}-\d{2})'", detail)
            time = re.search(r'itemprop="startDate">([0-2]\d:[0-5]\d)<', detail)
            name = re.search(r'<h4 itemprop="name">(.*?)</h4>', detail, re.S)
            duration = re.search(r"""itemprop="duration">\s*(\d{1,3})'\s*(?:([0-5]?\d)")?\s*<""", detail)
            if not (date and date[1] == day.isoformat() and time and time[1] == start.strftime("%H:%M")
                    and name and plain_html(name[1]) == title and duration):
                continue
            stop = start + timedelta(minutes=int(duration[1]), seconds=int(duration[2] or 0))
            if not now < stop or (index + 1 < len(listings) and stop > listings[index + 1][0]):
                continue
            description = re.search(r'<p class="description" itemprop="description">(.*?)</p>', detail, re.S)
            yield start, stop, title, None, plain_html(description[1]) if description else None, url
