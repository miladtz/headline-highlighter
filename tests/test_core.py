import unittest

from headline_highlighter import clean_drop_path, detect_headline, find_manual_headline, normalise


class HeadlineSelectionTests(unittest.TestCase):
    def test_normalise_ignores_case_and_punctuation(self):
        self.assertEqual(normalise("Hello, World!"), "helloworld")

    def test_drop_path_removes_tkdnd_braces(self):
        self.assertEqual(clean_drop_path("{C:/Users/Me/My Image.png}"), "C:/Users/Me/My Image.png")

    def test_manual_headline_returns_wrapped_ocr_lines(self):
        lines = [
            {"text": "A major story", "box": (1, 20, 200, 50), "height": 30},
            {"text": "unfolds today", "box": (1, 55, 200, 85), "height": 30},
            {"text": "Small body copy", "box": (1, 150, 150, 164), "height": 14},
        ]
        self.assertEqual(find_manual_headline(lines, "A major story unfolds today"), lines[:2])

    def test_detector_prefers_large_top_line(self):
        title = {"text": "The Main Headline", "box": (10, 30, 320, 80), "height": 50}
        body = {"text": "This is paragraph text with lots of words", "box": (10, 220, 400, 236), "height": 16}
        self.assertIn(title, detect_headline([title, body], 600))


if __name__ == "__main__":
    unittest.main()
