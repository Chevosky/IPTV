"""Resolve reviewed ElCinema listings against its dated public grid."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo

from guia_expansion import normalized_programmes, plain_html, read_response


CAIRO = ZoneInfo("Africa/Cairo")
MONTHS_AR = ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
             "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر")
ELCINEMA_CLOCK = r"(\d{2}:\d{2})\s*(صباحًا|مساءً)"
ELCINEMA_EMPTY = {"", "برنامج", "فيلم", "مسلسل"}
ELCINEMA_DURATION = r'<span class="subheader">\[(\d+) دقيقة\]</span>'
ELCINEMA_WORK_CARD = re.compile(
    r'<ul class="unstyled text-center">\s*<li>\s*' + ELCINEMA_CLOCK
    + r'\s*</li>\s*<li>\s*' + ELCINEMA_DURATION + r'\s*</li>\s*</ul>.*?'
    r'<ul class="unstyled no-margin">\s*<li>\s*<a href="/work/\d+/">(.*?)</a>\s*</li>',
    re.S,
)


def elcinema_clock(value, meridian):
    return datetime.strptime(value + (" AM" if meridian == "صباحًا" else " PM"), "%I:%M %p").time()


def elcinema_calibration(page, now):
    # The date picker uses Cairo time, while the grid shows another local clock.
    fields = re.findall(r'(?:class="only-date no-margin"|name="only_(?:date|time)") value="([^"]+)"', page)
    if len(fields) != 3:
        raise ValueError("ElCinema no publica los dos relojes de referencia")
    cairo_clock, local_date, local_clock = fields
    cairo = datetime.fromisoformat(cairo_clock).replace(tzinfo=CAIRO)
    if not cairo <= now < cairo + timedelta(hours=1):
        raise ValueError("ElCinema's clock does not match the current Cairo hour")
    local = datetime.fromisoformat(f"{local_date}T{local_clock}")
    offset = local - cairo.astimezone(timezone.utc).replace(tzinfo=None)
    return cairo, timezone(offset)


def elcinema_grid_rows(page):
    for channel in page.split('<div class="tv-line">')[1:]:
        match = re.search(r'<a title="[^"]*" href="/tvguide/(\d+)/">', channel)
        if match is None:
            continue
        for part in channel.split('<div class="row collapse tv-head">', 1)[0].split('<div class="tv-slot ')[1:]:
            details = re.search(r'<ul class="unstyled no-margin">(.*?)</ul>', part, re.S)
            if details is None:
                continue
            fields = re.findall(r'<li>(.*?)</li>', details[1], re.S)
            stamp = re.fullmatch(ELCINEMA_CLOCK + r"\s*-\s*" + ELCINEMA_CLOCK, fields[-1].strip()) if fields else None
            if stamp:
                start = elcinema_clock(*stamp.groups()[:2])
                stop = elcinema_clock(*stamp.groups()[2:])
                duration = (datetime.combine(datetime.min, stop) - datetime.combine(datetime.min, start)).seconds // 60
                yield match[1], plain_html(fields[0]), start, duration


def elcinema_detail_slots(section, include_work_cards):
    for listing in re.findall(r'<ul class="unstyled no-margin">(.*?)</ul>', section, re.S):
        fields = re.findall(r"<li>(.*?)</li>", listing, re.S)
        if len(fields) != 2:
            continue
        clock = re.fullmatch(ELCINEMA_CLOCK + r"\s*" + ELCINEMA_DURATION, fields[1].strip())
        if clock:
            yield fields[0], *clock.groups()

    if include_work_cards:
        # Keep each title bound to its own clock even if another card is malformed.
        for card in re.split(r'<div class="boxed-category-\d+ padded-half">', section)[1:]:
            match = ELCINEMA_WORK_CARD.search(card)
            if match:
                tm, meridian, duration, title = match.groups()
                yield title, tm, meridian, duration


def elcinema_channel_rows(page, cairo, source_zone, *, include_work_cards=False):
    calendar = {f"{day.day} {MONTHS_AR[day.month - 1]}": day for day in
                (cairo.date() + timedelta(days=offset) for offset in range(-1, 4))}
    sections = re.split(r'<div class=" dates">\s*\S+\s+(\d+\s+\S+)\s*</div>', page)
    for label, section in zip(sections[1::2], sections[2::2]):
        day = calendar.get(" ".join(label.split()))
        if day is None:
            continue
        for title, tm, meridian, duration in elcinema_detail_slots(section, include_work_cards):
            wall_time = elcinema_clock(tm, meridian)
            local = datetime.combine(day, wall_time, source_zone)
            programme_day = local.astimezone(CAIRO).date()
            start = local + (day - programme_day)
            if start.astimezone(CAIRO).date() == day:
                yield start, start + timedelta(minutes=int(duration)), plain_html(title), wall_time, int(duration)


def elcinema_programmes(bindings, now, snapshot=None, *, validated_work_cards=()):
    if not bindings:
        return {}
    url = "https://elcinema.com/tvguide/"
    try:
        grid = read_response(url, snapshot, "grid.html").decode()
        cairo, source_zone = elcinema_calibration(grid, now)
        expected = set(elcinema_grid_rows(grid))
    except (OSError, ValueError):
        return {}

    def collect(binding):
        feed, station = binding
        detail_url = f"{url}{station}/"
        try:
            page = read_response(detail_url, snapshot, f"{station}.html").decode()
            # A separate guide edition and time check is required before enabling work cards.
            rows = list(elcinema_channel_rows(
                page, cairo, source_zone, include_work_cards=feed in validated_work_cards,
            ))
            checked = [row for row in rows if row[2] not in ELCINEMA_EMPTY
                       and (station, *row[2:]) in expected]
            current = [row for row in checked if row[0] <= now < row[1]]
            if len(current) == 1:
                # Corroborate the channel detail against the dated grid before using future slots.
                selected = rows
            else:
                # During a gap, only expose upcoming programmes visible on both dated pages.
                selected = [row for row in checked if now < row[0]]
            return feed, normalized_programmes(
                feed, f"elcinema-detail:{station}",
                ((start, stop, title, None, None, detail_url) for start, stop, title, _, _ in selected
                 if title not in ELCINEMA_EMPTY), now,
            )
        except (OSError, ValueError):
            pass
        return feed, []

    with ThreadPoolExecutor(max_workers=2) as workers:
        return {feed: rows for feed, rows in workers.map(collect, bindings.items()) if rows}
