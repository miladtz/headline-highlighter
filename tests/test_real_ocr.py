"""Integration checks using the supplied screenshots and the bundled Tesseract binary."""
import shutil
import unittest
from pathlib import Path

from PIL import Image
import pytesseract

from headline_highlighter import detect_headline, find_phrase_boxes, ocr_lines


ROOT = Path(__file__).parents[1]
TESSERACT = ROOT / "vendor" / "tesseract" / "tesseract.exe"


@unittest.skipUnless(TESSERACT.exists(), "Bundled Tesseract is only present in the Windows build workflow.")
class RealOcrFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pytesseract.pytesseract.tesseract_cmd = str(TESSERACT)
        # Tesseract needs its language data beside the executable.
        import os
        os.environ["TESSDATA_PREFIX"] = str(TESSERACT.parent / "tessdata")

    def assert_phrases_resolve(self, fixture: str, phrase_input: str):
        image = Image.open(ROOT / "tests" / "fixtures" / fixture).convert("RGB")
        headline = detect_headline(ocr_lines(image), image.height)
        matches, missing = find_phrase_boxes(headline, phrase_input)
        self.assertFalse(missing, f"OCR could not locate: {missing}")
        self.assertEqual(len(matches), len([p for p in phrase_input.split(",") if p.strip()]))
        for match in matches:
            x0, y0, x1, y1 = match["box"]
            self.assertGreater(x1, x0)
            self.assertGreater(y1, y0)

    def test_dark_headline_phrases(self):
        self.assert_phrases_resolve("dark_headline.png", "Britain, Houthi")

    def test_light_headline_phrases(self):
        self.assert_phrases_resolve("light_headline.png", "fire, Hormuz")

    def test_nbc_headline_phrases(self):
        self.assert_phrases_resolve("nbc_headline.png", "record, crime")
