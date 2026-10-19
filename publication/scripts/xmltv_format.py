"""XMLTV serialization with Git-safe whitespace handling."""
from pathlib import Path
import xml.etree.ElementTree as ET


def write_xmltv_clean(root: ET.Element, path: Path) -> None:
    """Keep XMLTV content intact except trailing horizontal whitespace per line."""
    raw = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    clean = b"\n".join(line.rstrip(b" \t") for line in raw.split(b"\n"))
    path.write_bytes(clean)
