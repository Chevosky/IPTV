"""XMLTV serialization with Git-safe whitespace handling."""
from pathlib import Path
import xml.etree.ElementTree as ET


def xmltv_bytes(root: ET.Element) -> bytes:
    """Serialize guide text without whitespace errors in the physical lines."""
    # Providers occasionally return descriptions with a space or tab immediately
    # before a newline.  Escaping that newline changes the physical XML but leaves
    # the unwanted whitespace in the parsed programme text.  Normalise meaningful
    # text nodes instead; indentation nodes are whitespace-only and must remain
    # untouched for readable XML.
    for element in root.iter():
        if element.text and not element.text.isspace():
            element.text = "\n".join(line.strip() for line in element.text.splitlines())
    raw = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    raw = raw.replace(b"\r", b"&#13;")
    return raw


def write_xmltv_clean(root: ET.Element, path: Path) -> None:
    path.write_bytes(xmltv_bytes(root))
