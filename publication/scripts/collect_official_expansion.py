#!/usr/bin/env python3
"""Collect a bounded set of reviewed direct-guide providers locally."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

from filter_expansion import unpack
from guia_expansion import binding, programmes_for_expansion
from xmltv_format import write_xmltv_clean


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("providers", nargs="+")
    args = parser.parse_args()
    wanted = set(args.providers)
    payload, _ = unpack(args.seed)
    entries = [entry for entry in payload["expansion"]
               if (resolved := binding(entry)) and resolved[0] in wanted]
    programmes = programmes_for_expansion(entries, None, datetime.now(timezone.utc), fetch=True)
    root = ET.Element("tv")
    for entry in entries:
        feed = entry["tvgID"]
        if feed not in programmes:
            continue
        channel = ET.SubElement(root, "channel", id=feed)
        ET.SubElement(channel, "display-name").text = entry["name"]
        root.extend(programmes[feed])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_xmltv_clean(root, args.output)
    print(json.dumps({"providers": sorted(wanted), "entries": len(entries),
                      "freshChannels": len(programmes), "programmes": sum(map(len, programmes.values()))}))


if __name__ == "__main__":
    main()
