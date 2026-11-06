"""User-selected channel families, independent of upstream category labels."""
import json
import re
from pathlib import Path


def normalize(value):
    return "".join(c for c in value.casefold() if c.isalnum())


def load_policy(root):
    policy = json.loads((Path(root) / "publication/channel-policy.json").read_text())
    if policy.get("schemaVersion") != 1 or not all(isinstance(x, str) and normalize(x) for x in policy["excludedFamilies"]):
        raise ValueError("Invalid channel exclusion policy")
    if not all(isinstance(x, str) and x for x in policy["excludedTvgIDs"]):
        raise ValueError("Invalid excluded channel IDs")
    return policy


def excluded(name, tvg_id, policy):
    families = [normalize(x) for x in policy["excludedFamilies"]]
    return tvg_id in policy["excludedTvgIDs"] or any(family in normalize(name or "") or family in normalize(tvg_id or "") for family in families)


def active_channels(catalog, policy):
    return [c for c in catalog["channels"] if not excluded(c["name"], c["tvgId"], policy)]


def filter_playlist(text, policy):
    output, block = [], []

    def flush():
        if not block:
            return
        entry = block[0]
        match = re.search(r'tvg-id="([^"]+)"', entry)
        cid = match.group(1) if match else ""
        name = re.split(r',(?=(?:[^"]*"[^"]*")*[^"]*$)', entry, maxsplit=1)[-1].strip()
        if not excluded(name, cid, policy):
            output.extend(block)

    for line in text.splitlines(keepends=True):
        if line.startswith("#EXTINF:"):
            flush()
            block = [line]
        elif block:
            block.append(line)
        else:
            output.append(line)
    flush()
    return "".join(output)
