"""XMLTV serialization with Git-safe whitespace handling."""
from pathlib import Path
import re
import xml.etree.ElementTree as ET


def xmltv_bytes(root: ET.Element) -> bytes:
    """Serialize guide text without whitespace errors in the physical lines."""
    raw = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    # References survive XML line-end normalization and leave parsed text unchanged.
    raw = raw.replace(b"\r", b"&#13;")
    return re.sub(rb"(?<=[ \t])\n|\n(?=[ \t]* \t)", b"&#10;", raw)


def write_xmltv_clean(root: ET.Element, path: Path) -> None:
    path.write_bytes(xmltv_bytes(root))
