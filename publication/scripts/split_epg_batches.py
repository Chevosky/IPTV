#!/usr/bin/env python3
"""Split a reviewed IPTV-Org channel binding into bounded local grab jobs."""
import argparse
import copy
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("channels", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    source = ET.parse(args.channels).getroot()
    grouped = defaultdict(list)
    for channel in source.findall("channel"):
        site = channel.get("site")
        if not site:
            raise SystemExit("A reviewed channel binding has no site")
        grouped[site].append(channel)
    args.output.mkdir(parents=True, exist_ok=True)
    for site, channels in sorted(grouped.items()):
        root = ET.Element(source.tag, source.attrib)
        root.extend(copy.deepcopy(channel) for channel in channels)
        ET.ElementTree(root).write(args.output / f"{site}.channels.xml", encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    main()
