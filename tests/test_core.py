import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from headline_highlighter import box_is_dark, clean_drop_path, detect_headline, find_manual_headline, find_phrase_boxes, headline_quality, normalise, source_copy_destination, timestamped_destination, zoom_frame


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

    def test_manual_headline_tolerates_an_ocr_missed_word(self):
        lines = [
            {"text": "Woman arrested and facing felony charges in", "box": (1, 20, 500, 50), "height": 30},
            {"text": "World War II Memorial vandalism case DOJ", "box": (1, 55, 500, 85), "height": 30},
            {"text": "says", "box": (1, 90, 80, 120), "height": 30},
        ]
        manual = "Woman arrested and facing felony charges in World War II Memorial vandalism case, DOJ says"
        self.assertEqual(find_manual_headline(lines, manual), lines)

    def test_timestamped_destination_preserves_requested_name_and_avoids_collisions(self):
        now = datetime(2026, 8, 15, 14, 30, 5)
        first = Path(timestamped_destination("C:/videos", "my clip.mp4", now))
        self.assertEqual(first.name, "my clip_20260815_143005.mp4")
        with patch("headline_highlighter.Path.exists", side_effect=[True, False]):
            second = Path(timestamped_destination("C:/videos", "my clip.mp4", now))
        self.assertEqual(second.name, "my clip_20260815_143005_2.mp4")

    def test_source_copy_uses_the_video_stem_and_image_extension(self):
        copied = Path(source_copy_destination("C:/input/news.png", "C:/videos/headline_20260815_143005.mp4"))
        self.assertEqual(copied.name, "headline_20260815_143005.png")

    def test_detector_prefers_large_top_line(self):
        title = {"text": "The Main Headline", "box": (10, 30, 320, 80), "height": 50}
        body = {"text": "This is paragraph text with lots of words", "box": (10, 220, 400, 236), "height": 16}
        self.assertIn(title, detect_headline([title, body], 600))

    def test_detector_excludes_top_navigation_from_article_title(self):
        navigation = {"text": "POLITICS U.S. NEWS WORLD LOCAL SPORTS BUSINESS HEALTH", "box": (0, 30, 800, 83), "height": 53}
        first = {"text": "Woman arrested and facing felony charges in", "box": (200, 240, 1100, 300), "height": 60}
        second = {"text": "World War II Memorial vandalism case DOJ", "box": (200, 310, 1100, 370), "height": 60}
        third = {"text": "says", "box": (200, 380, 350, 440), "height": 60}
        result = detect_headline([navigation, first, second, third], 700)
        self.assertEqual(result, [first, second, third])

    def test_headline_quality_prefers_complete_multiline_title(self):
        complete = [
            {"text": "Woman arrested and facing felony charges in", "box": (0, 10, 600, 55), "height": 45},
            {"text": "World War II Memorial vandalism case DOJ", "box": (0, 60, 600, 105), "height": 45},
            {"text": "says", "box": (0, 110, 100, 155), "height": 45},
        ]
        partial = complete[:1]
        self.assertGreater(headline_quality(complete), headline_quality(partial))

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
