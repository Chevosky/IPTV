"""Refresh approved YouTube feeds using the YouTube Data API."""

from collections import Counter
from datetime import datetime, timedelta
import json
import re
import unicodedata
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

API = "https://www.googleapis.com/youtube/v3/"
PUBLISHER = re.compile(r"UC[A-Za-z0-9_-]{22}")
VIDEO = re.compile(r"[A-Za-z0-9_-]{11}")
MAX_SEARCHES = 8
SEARCH_INTERVAL = timedelta(hours=6)
# This publisher uses a date suffix for its rolling newscast.
ABC_LIVE = re.compile(
    r"^live abc news live(?: (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday) .+"
    r"| 24 7 news context and analysis)?$"
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def client(key):
    opener = build_opener(NoRedirect())

    def fetch(endpoint, params):
        url = API + endpoint + "?" + urlencode(params)
        request = Request(url, headers={"X-Goog-Api-Key": key, "Accept": "application/json"})
        with opener.open(request, timeout=8) as response:
            data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise ValueError("YouTube response exceeds limit")
        result = json.loads(data)
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise ValueError("YouTube returned an invalid response")
        return result

    return fetch


def normalized_title(title):
    if not isinstance(title, str):
        return ""
    # The public live extractor may append its local wall clock to the title.
    title = re.sub(r"\s+\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", "", title)
    normalized = unicodedata.normalize("NFKC", title).casefold()
    return " ".join("".join(char if char.isalnum() else " " for char in normalized).split())


def matches_feed(row, title):
    previous = normalized_title(row.get("liveTitle") or "")
    candidate = normalized_title(title)
    return bool(previous and (candidate == previous or (
        row["publisherID"] == "UCBi2mrWuNuyYy4gbM6fU18Q"
        and ABC_LIVE.fullmatch(previous) and ABC_LIVE.fullmatch(candidate)
    )))


def current_video(item, publisher):
    snippet = item.get("snippet", {})
    status = item.get("status", {})
    timing = item.get("liveStreamingDetails", {})
    if not all(isinstance(value, dict) for value in (snippet, status, timing)):
        return False
    return (snippet.get("channelId") == publisher and snippet.get("liveBroadcastContent") == "live"
            and status.get("privacyStatus") == "public" and status.get("embeddable") is True
            and bool(timing.get("actualStartTime")) and not timing.get("actualEndTime"))


def videos(fetch, ids):
    found = {}
    ids = list(dict.fromkeys(ids))
    for offset in range(0, len(ids), 50):
        batch = set(ids[offset:offset + 50])
        items = fetch("videos", {"part": "snippet,status,liveStreamingDetails", "id": ",".join(sorted(batch))})["items"]
        for item in items:
            if not isinstance(item, dict) or item.get("id") not in batch or item["id"] in found:
                raise ValueError("YouTube returned an unexpected video")
            found[item["id"]] = item
    return found


def replacements(fetch, row):
    publisher = row["publisherID"]
    result = fetch("search", {
        "part": "snippet", "type": "video", "channelId": publisher, "eventType": "live",
        "videoEmbeddable": "true", "maxResults": 50,
    })
    if result.get("nextPageToken"):
        return []
    eligible = []
    for item in result["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), dict) or not isinstance(item.get("snippet"), dict):
            raise ValueError("YouTube returned an invalid search item")
        video = item["id"].get("videoId") or ""
        snippet = item["snippet"]
        if isinstance(video, str) and VIDEO.fullmatch(video) and snippet.get("channelId") == publisher and matches_feed(row, snippet.get("title", "")):
            eligible.append(video)
    return [item for item in videos(fetch, eligible).values()
            if current_video(item, publisher) and matches_feed(row, item["snippet"].get("title", ""))]


def refresh(entries, fetch, now):
    if any(not PUBLISHER.fullmatch(row["publisherID"]) or not VIDEO.fullmatch(row["videoID"]) for row in entries):
        raise ValueError("Invalid identifiers in the adopted YouTube catalog")
    if len({row["publisherID"] for row in entries}) != len(entries) or len({row["videoID"] for row in entries}) != len(entries):
        raise ValueError("Duplicate identifiers in the adopted YouTube catalog")
    try:
        current = videos(fetch, (row["videoID"] for row in entries))
    except (OSError, ValueError):
        return entries, {"status": "provider-unavailable"}

    used = {row["videoID"] for row in entries}
    counts = Counter()
    updated = {row["publisherID"]: row for row in entries}
    stamp = now.isoformat(timespec="milliseconds")
    search_available = True
    for row in sorted(entries, key=lambda entry: (entry.get("lastSearchAt", ""), entry["number"])):
        publisher = row["publisherID"]
        video = row["videoID"]
        if current_video(current.get(video, {}), publisher):
            updated[publisher] = {key: value for key, value in row.items() if key != "available"}
            counts["stillLive"] += 1
            continue

        stamp_value = row.get("lastSearchAt")
        checked = datetime.fromisoformat(stamp_value.replace("Z", "+00:00")) if stamp_value else None
        if search_available and row.get("liveTitle") and counts["searches"] < MAX_SEARCHES and (
            checked is None or now - checked >= SEARCH_INTERVAL
        ):
            counts["searches"] += 1
            try:
                matches = replacements(fetch, row)
            except (OSError, ValueError):
                search_available = False
            else:
                updated[publisher] = {**row, "lastSearchAt": stamp}
                if len(matches) == 1 and matches[0]["id"] not in used:
                    replacement = matches[0]["id"]
                    used.add(replacement)
                    updated[publisher] = {**{key: value for key, value in row.items() if key != "available"},
                                          "videoID": replacement, "liveTitle": matches[0]["snippet"]["title"],
                                          "verifiedAt": stamp, "lastSearchAt": stamp}
                    counts["rotated"] += 1
                    continue
        counts["priorLocatorRetained"] += 1
    status = "ok" if search_available else "search-unavailable"
    return [updated[row["publisherID"]] for row in entries], {"status": status, **counts}
