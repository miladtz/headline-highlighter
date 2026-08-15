import unittest

from PIL import Image

from headline_highlighter import box_is_dark, clean_drop_path, detect_headline, find_manual_headline, find_phrase_boxes, normalise, zoom_frame


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

    def test_zoom_frame_keeps_video_dimensions(self):
        image = Image.new("RGBA", (101, 99), "white")
        self.assertEqual(zoom_frame(image, (50, 40), 1.08).size, image.size)

    def test_phrase_matches_are_returned_in_reading_order(self):
        line = {
            "text": "First phrase then second phrase", "box": (0, 0, 260, 30), "height": 30,
            "words": [
                {"text": "First", "box": (0, 0, 45, 30)}, {"text": "phrase", "box": (50, 0, 100, 30)},
                {"text": "then", "box": (105, 0, 140, 30)}, {"text": "second", "box": (145, 0, 200, 30)},
                {"text": "phrase", "box": (205, 0, 260, 30)},
            ],
        }
        matches, missing = find_phrase_boxes([line], "second phrase, first phrase")
        self.assertEqual(missing, [])
        self.assertEqual([match["text"] for match in matches], ["first phrase", "second phrase"])

    def test_single_word_prefix_matches_the_complete_ocr_word(self):
        line = {"text": "Iran fires back", "box": (0, 0, 130, 30), "height": 30,
                "words": [{"text": "Iran", "box": (0, 0, 35, 30)}, {"text": "fires", "box": (40, 0, 85, 30)}, {"text": "back", "box": (90, 0, 130, 30)}]}
        matches, missing = find_phrase_boxes([line], "fire")
        self.assertEqual(missing, [])
        self.assertEqual(matches[0]["box"], (40, 0, 85, 30))

    def test_background_classifier_handles_light_and_dark_pages(self):
        self.assertTrue(box_is_dark(Image.new("RGBA", (20, 20), "#10233d"), (0, 0, 20, 20)))
        self.assertFalse(box_is_dark(Image.new("RGBA", (20, 20), "white"), (0, 0, 20, 20)))


if __name__ == "__main__":
    unittest.main()
