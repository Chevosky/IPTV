"""Regression: upstream multiline XMLTV text must not break git diff --check."""
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from xmltv_format import write_xmltv_clean


class XMLTVFormatTests(unittest.TestCase):
    def test_multiline_description_trailing_whitespace(self):
        root = ET.Element("tv")
        program = ET.SubElement(root, "programme", {
            "channel": "fixture.channel", "start": "20261019120000 +0000", "stop": "20261019130000 +0000"
        })
        ET.SubElement(program, "title").text = "Æske test"
        ET.SubElement(program, "desc").text = "Pandoras Æske er åbnet.  \nSecond line\t\nInside  words"
        ET.indent(root, space="  ")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guide-flex-candidate.xml"
            write_xmltv_clean(root, path)
            content = path.read_bytes()
            self.assertTrue(all(line == line.rstrip(b" \t") for line in content.split(b"\n")))
            self.assertIn("Pandoras Æske er åbnet.\nSecond line\nInside  words".encode("utf-8"), content)
            parsed = ET.parse(path)
            self.assertEqual(parsed.findtext("./programme/title"), "Æske test")
            self.assertEqual(parsed.find("./programme").get("channel"), "fixture.channel")
            self.assertEqual(parsed.findtext("./programme/desc"), "Pandoras Æske er åbnet.\nSecond line\nInside  words")

    def test_empty_and_inline_xml(self):
        root = ET.Element("tv")
        ET.SubElement(root, "channel", {"id": "test"})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guide.xml"
            write_xmltv_clean(root, path)
            self.assertEqual(ET.parse(path).find("./channel").get("id"), "test")
            self.assertFalse(any(line != line.rstrip(b" \t") for line in path.read_bytes().split(b"\n")))


if __name__ == "__main__":
    unittest.main()
