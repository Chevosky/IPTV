"""Extend short schedules using verified editions without changing the refresh cadence."""
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from zoneinfo import ZoneInfo
import json

from guia_expansion import XMLTV, normalized_programmes, read_response
import guia_rai_sudtirol
import guia_bbc_xumo

TRECE = "Trece.es@SD"
PBS_KIDS = "PBSKids.us@SD"
ROKU_VOICE = "AmericasVoice.us@SD"
BASE_FEEDS = (TRECE, ROKU_VOICE, guia_rai_sudtirol.FEED, guia_bbc_xumo.FEED)
BASE_SOURCES = {guia_rai_sudtirol.FEED: guia_rai_sudtirol.SOURCES, guia_bbc_xumo.FEED: guia_bbc_xumo.SOURCES}
PBS_PATH = "pbs-kids-247/31867"
ROKU_ID = "d748045d77105901b8e25fcc34aa9e2f"
ROKU_GUIDE = "https://whatsonfreetv.com/channel/america-s-voice"
PBS_URL = "https://www.tvpassport.com/tv-listings/stations"
MOVISTAR_GUIDE = "https://www.movistarplus.es/programacion-tv/13tv/"
MOVISTAR_API = "https://ottcache.dof6.com/movistarplus/webplayer/OTT/epg"
PROVIDERS = {
    guia_rai_sudtirol.FEED: guia_rai_sudtirol.PROVIDER,
    guia_bbc_xumo.FEED: guia_bbc_xumo.PROVIDER,
    TRECE: "iptv-org:movistarplus.es:13TV",
    PBS_KIDS: f"iptv-org:tvpassport.com:{PBS_PATH}",
    ROKU_VOICE: f"whatsonfreetv:roku:{ROKU_ID}",
}
PLACEHOLDERS = {
    guia_rai_sudtirol.FEED: {"rai sudtirol - deutsch", "rai südtirol - deutsch"},
    guia_bbc_xumo.FEED: {"bbc earth", "bbc earth - inglés", "bbc earth - english", "bbc earth (us) - english"},
    TRECE: {"trece", "trece - español", "trece - spanish"},
    ROKU_VOICE: {"america's voice", "america's voice - inglés", "america's voice - english"},
}
NEW_YORK = ZoneInfo("America/New_York")
MADRID = ZoneInfo("Europe/Madrid")


class ScheduleHTML(HTMLParser):
    def __init__(self, *, roku=False):
        super().__init__()
        self.roku = roku
        self.rows = []
        self.verified_timezone = False
        self.verified_roku_id = False

    def handle_starttag(self, tag, attrs):
        row = dict(attrs)
        if (tag == "option" and row.get("value") == NEW_YORK.key and "selected" in row):
            self.verified_timezone = True
        if (tag == "meta" and row.get("property") == "og:image"
                and f"id/{ROKU_ID}/" in row.get("content", "")):
            self.verified_roku_id = True
        if tag != "div":
            return
        if self.roku:
            if row.get("data-platform") == "Roku" and row.get("data-show"):
                self.rows.append(row)
        elif (row.get("id", "").startswith("itemheader") and row.get("data-callsign") == "PBS-KID"
              and row.get("data-st") and row.get("data-showname")):
            self.rows.append(row)


def trece_rows(now, snapshot):
    for offset in (0, 1):
        day = now.astimezone(MADRID).date() + timedelta(days=offset)
        url = f"{MOVISTAR_API}?from={day}T00:00:00&span=1&channel=13TV&version=8&mdrm=true&tlsstream=true&demarcation=18"
        try:
            data = json.loads(read_response(
                url, snapshot, f"movistar-trece-{day}.json",
                user_agent="Mozilla/5.0", headers={"Referer": "https://www.movistarplus.es/programacion-tv"},
            ))
        except (OSError, ValueError):
            continue
        if not isinstance(data, list):
            continue
        for row in data:
            try:
                channel = row["Canal"]
                if channel.get("CodCadenaTv") != "13TV" or channel.get("Nombre") != "TRECE":
                    continue
                start, stop = (datetime.fromtimestamp(int(row[key]) / 1000, timezone.utc)
                               for key in ("FechaHoraInicio", "FechaHoraFin"))
                yield start, stop, row["Titulo"], row.get("TituloEpisodio"), row.get("Resena"), f"{MOVISTAR_GUIDE}{day}"
            except (AttributeError, KeyError, OverflowError, TypeError, ValueError):
                continue


def pbs_rows(now, snapshot):
    # The previous TV day lasts until 06:00 Eastern, including the early hours today.
    for offset in (-1, 0, 1):
        day = now.astimezone(NEW_YORK).date() + timedelta(days=offset)
        url = f"{PBS_URL}/{PBS_PATH}/{day}"
        parser = ScheduleHTML()
        try:
            parser.feed(read_response(url, snapshot, f"tvpassport-pbskids-{day}.html").decode())
        except (OSError, ValueError):
            continue
        if not parser.verified_timezone:
            continue
        for row in parser.rows:
            try:
                start = datetime.strptime(row["data-st"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=NEW_YORK)
                stop = start + timedelta(minutes=int(row["data-duration"]))
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            yield start, stop, row["data-showname"], row.get("data-episodetitle"), row.get("data-description"), url


def roku_rows(_now, snapshot):
    try:
        html = read_response(ROKU_GUIDE, snapshot, "roku-rav.html").decode()
    except (OSError, ValueError):
        return
    parser = ScheduleHTML(roku=True)
    parser.feed(html)
    if not parser.verified_roku_id:
        return
    for row in parser.rows:
        try:
            start, stop = (datetime.strptime(row[key], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                           for key in ("data-start", "data-end"))
        except (KeyError, ValueError):
            continue
        yield start, stop, row["data-show"], None, None, ROKU_GUIDE


def window(row):
    return tuple(datetime.strptime(row.attrib[key], XMLTV) for key in ("start", "stop"))


def is_placeholder(feed, row):
    if feed in {guia_rai_sudtirol.FEED, guia_bbc_xumo.FEED} and (row.get("zapper-provider-id") or row.get("zapper-provenance")):
        return False
    title = row.findtext("title", "").strip().casefold().replace("’", "'")
    if title not in PLACEHOLDERS.get(feed, ()):
        return False
    start, stop = window(row)
    return stop - start == timedelta(days=2)


def append_uncovered(current, candidates, now):
    accepted = []
    windows = [window(row) for row in current]
    for row in candidates:
        start, stop = window(row)
        if not (now < stop and start <= now + timedelta(hours=36)):
            continue
        if not any(stop > left and start < right for left, right in windows):
            accepted.append(row)
            windows.append((start, stop))
    return accepted


def renew(feed, current, previous, now, snapshot):
    actual = [row for row in current if not is_placeholder(feed, row)]
    remaining = now
    for start, stop in sorted(map(window, actual)):
        if start > remaining:
            break
        remaining = max(remaining, stop)
    if remaining >= now + timedelta(hours=8):
        return actual
    readers = {TRECE: trece_rows, PBS_KIDS: pbs_rows, ROKU_VOICE: roku_rows,
               guia_rai_sudtirol.FEED: guia_rai_sudtirol.rows, guia_bbc_xumo.FEED: guia_bbc_xumo.rows}
    fresh = normalized_programmes(feed, PROVIDERS[feed], readers[feed](now, snapshot), now)
    new = append_uncovered(actual, fresh, now)
    prior = [row for row in previous if row.get("zapper-provider-id") == PROVIDERS[feed]]
    return actual + new + append_uncovered(actual + new, prior, now)
