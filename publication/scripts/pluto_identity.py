"""Conservative Pluto UUID map for the canonical Chevosky/IPTV Flex playlist.

The catalogue identifies a channel by tvgId and an explicit Pluto source UUID.
A URL can corroborate the identity; a URL alone must never decide which
channel/programme identity is published.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

PLUTO_ID = re.compile(r"^[0-9a-f]{24}$", re.IGNORECASE)
# Support current stable worker route and the previously approved legacy routes.
PLUTO_PATH = re.compile(
    r"(?:/pluto/|/channels/|(?:^|/)plu-)([0-9a-f]{24})(?=\.m3u8(?:$|/)|/|$)",
    re.IGNORECASE,
)


def playlist_uuid(url: str) -> str | None:
    """Read an explicitly encoded Pluto UUID, never infer one from a name."""
    try:
        u = urlsplit(url)
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not u.netloc:
        return None
    m = PLUTO_PATH.search(u.path)
    return m.group(1).lower() if m else None


def canonical_pluto_ids(catalog_path: Path, playlist: dict) -> dict[str, str]:
    """Map playlist tvg-id -> official Pluto UUID, with mismatch protection.

    Only exact channel IDs, names, and a corroborating Pluto URL are eligible.
    Unknown/ambiguous items remain outside the map rather than being guessed.
    A contradiction raises to preserve the last-known-good published guide.
    """
    catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    if not isinstance(catalog, dict) or not isinstance(catalog.get("channels"), list):
        raise ValueError("Invalid canonical Flex catalog")
    seen = set()
    mapping = {}
    for channel in catalog["channels"]:
        cid = channel.get("tvgId")
        if not isinstance(cid, str) or not cid:
            raise ValueError("Canonical channel lacks tvgId")
        if cid in seen:
            raise ValueError("Duplicate canonical tvgId")
        seen.add(cid)
        entry = playlist.get(cid)
        if entry is None:
            continue
        if channel.get("name") != entry.get("name"):
            raise ValueError(f"Playlist/catalog identity name differs for {cid}")
        sources = channel.get("sources") or []
        pluto = [s for s in sources if s.get("type") == "pluto"]
        if not pluto:
            continue
        ids = {str(s.get("channelId", "")).lower() for s in pluto}
        if len(ids) != 1 or not PLUTO_ID.fullmatch(next(iter(ids))):
            raise ValueError(f"Ambiguous/invalid Pluto UUID for {cid}")
        official = ids.pop()
        from_url = playlist_uuid(str(entry.get("url", "")))
        if from_url is None:
            raise ValueError(f"Pluto playlist route does not encode a UUID for {cid}")
        if from_url != official:
            raise ValueError(f"Pluto playlist/catalog UUID mismatch for {cid}")
        mapping[cid] = official
    return mapping


def retained_epg_check(coverage: dict, reference: dict) -> tuple[int, list[str]]:
    """Protect previously confirmed EPG-bearing IDs from silent synthetic loss.

    Only real schedule provenance counts; a rolling synthetic label is NOT EPG.
    """
    known = reference.get("tvgIds")
    sources = coverage.get("coverage_sources", {})
    if not isinstance(known, list) or len(known) != len(set(known)) or not isinstance(sources, dict):
        raise ValueError("Invalid EPG regression reference or coverage report")
    missing = [cid for cid in known if sources.get(cid, {}).get("type") in (None, "synthetic")]
    return len(known), sorted(missing)
