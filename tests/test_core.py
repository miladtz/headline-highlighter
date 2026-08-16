import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from headline_highlighter import box_is_dark, clean_drop_path, crop_to_16_9, detect_headline, find_manual_headline, find_phrase_boxes, headline_quality, line_highlight_durations, marker_layer, normalise, remove_header_navigation, source_copy_destination, timestamped_destination, trim_mixed_title_lines, zoom_frame, zoom_scale


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

    def test_title_time_is_distributed_by_marker_width(self):
        lines = [{"box": (0, 0, 10, 10)}, {"box": (0, 0, 8, 10)}, {"box": (0, 0, 3, 10)}]
        durations = line_highlight_durations(lines, 6)
        self.assertAlmostEqual(sum(durations), 6)
        self.assertAlmostEqual(durations[0] / 10, durations[1] / 8)
        self.assertAlmostEqual(durations[1] / 8, durations[2] / 3)

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

    def test_detector_keeps_an_ordinary_title_near_the_top(self):
        title = {"text": "Britain and Houthi forces face a new crisis", "box": (20, 35, 720, 95), "height": 60}
        body = {"text": "Additional article details appear below the headline", "box": (20, 180, 650, 205), "height": 25}
        self.assertEqual(detect_headline([title, body], 700), [title])

    def test_header_separated_by_a_hero_gap_is_removed_before_detection(self):
        navigation = {"text": "POLITICS U.S. NEWS WORLD LOCAL SPORTS", "box": (0, 40, 800, 80), "height": 40}
        title = {"text": "Woman arrested and facing felony charges", "box": (200, 250, 1100, 310), "height": 60}
        self.assertEqual(remove_header_navigation([navigation, title], 700), [title])

    def test_headline_quality_prefers_complete_multiline_title(self):
        complete = [
            {"text": "Woman arrested and facing felony charges in", "box": (0, 10, 600, 55), "height": 45},
            {"text": "World War II Memorial vandalism case DOJ", "box": (0, 60, 600, 105), "height": 45},
            {"text": "says", "box": (0, 110, 100, 155), "height": 45},
        ]
        partial = complete[:1]
        self.assertGreater(headline_quality(complete), headline_quality(partial))

    def test_mixed_ocr_line_drops_small_section_label_beside_headline(self):
        mixed = {"text": "JUSTICE DEPARTMENT Woman arrested", "box": (10, 20, 520, 80), "height": 60,
                 "words": [{"text": "JUSTICE", "box": (10, 42, 58, 54)},
                           {"text": "DEPARTMENT", "box": (62, 42, 145, 54)},
                           {"text": "Woman", "box": (220, 20, 370, 80)},
                           {"text": "arrested", "box": (380, 20, 520, 80)}]}
        trimmed = trim_mixed_title_lines([mixed])
        self.assertEqual(trimmed[0]["text"], "Woman arrested")
        self.assertEqual(trimmed[0]["box"], (220, 20, 520, 80))

    def test_zoom_frame_keeps_video_dimensions(self):
        image = Image.new("RGBA", (101, 99), "white")
        self.assertEqual(zoom_frame(image, (50, 40), 1.08).size, image.size)

    def test_zoom_out_reverses_the_existing_zoom_motion(self):
        self.assertEqual(zoom_scale("in", 0), 1)
        self.assertEqual(zoom_scale("out", 1), 1)
        self.assertAlmostEqual(zoom_scale("in", 1), 1.08)
        self.assertAlmostEqual(zoom_scale("out", 0), 1.08)

    def test_center_crop_to_16_by_9_removes_only_the_long_dimension(self):
        wide = crop_to_16_9(Image.new("RGBA", (2000, 1000), "white"))
        tall = crop_to_16_9(Image.new("RGBA", (1000, 1000), "white"))
        self.assertEqual(wide.size, (1778, 1000))
        self.assertEqual(tall.size, (1000, 562))

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

    def test_phrase_with_commas_matches_normalised_ocr_words(self):
        line = {"text": "firm axes 2,800 jobs", "box": (0, 0, 250, 30), "height": 30,
                "words": [{"text": "firm", "box": (0, 0, 40, 30)}, {"text": "axes", "box": (45, 0, 90, 30)},
                          {"text": "2,800", "box": (95, 0, 155, 30)}, {"text": "jobs", "box": (160, 0, 210, 30)}]}
        matches, missing = find_phrase_boxes([line], "axes 2,800 jobs", split_phrases=False)
        self.assertEqual(missing, [])
        self.assertEqual(matches[0]["box"], (45, 0, 210, 30))

    def test_phrase_matches_roman_numeral_ocr_variants(self):
        line = {"text": "World War Il Memorial", "box": (0, 0, 300, 30), "height": 30,
                "words": [{"text": "World", "box": (0, 0, 70, 30)}, {"text": "War", "box": (75, 0, 120, 30)},
                          {"text": "Il", "box": (125, 0, 145, 30)}, {"text": "Memorial", "box": (150, 0, 260, 30)}]}
        matches, missing = find_phrase_boxes([line], "**World War II", split_phrases=False)
        self.assertEqual(missing, [])
        self.assertEqual(matches[0]["box"], (0, 0, 145, 30))

    def test_phrase_has_a_full_line_band_box_and_tight_word_box(self):
        line = {"text": "facing felony", "box": (20, 10, 250, 60), "height": 50,
                "words": [{"text": "facing", "box": (40, 22, 110, 45)}, {"text": "felony", "box": (120, 22, 190, 45)}]}
        matches, missing = find_phrase_boxes([line], "facing felony", split_phrases=False)
        self.assertEqual(missing, [])
        self.assertEqual(matches[0]["box"], (40, 22, 190, 45))
        self.assertEqual(matches[0]["line_box"], (40, 10, 190, 60))

    def test_full_manual_phrase_uses_title_words_not_a_merged_header_line(self):
        line = {"text": "BBC Peloton boss John Foley to step down", "box": (0, 0, 500, 40), "height": 40,
                "words": [{"text": "BBC", "box": (0, 0, 55, 40)}, {"text": "Peloton", "box": (120, 0, 210, 40)},
                          {"text": "boss", "box": (215, 0, 260, 40)}, {"text": "John", "box": (265, 0, 315, 40)},
                          {"text": "Foley", "box": (320, 0, 380, 40)}, {"text": "to", "box": (385, 0, 410, 40)},
                          {"text": "step", "box": (415, 0, 465, 40)}, {"text": "down", "box": (470, 0, 520, 40)}]}
        matches, missing = find_phrase_boxes([line], "Peloton boss John Foley to step down", split_phrases=False)
        self.assertEqual(missing, [])
        self.assertEqual(matches[0]["box"], (120, 0, 520, 40))

    def test_background_classifier_handles_light_and_dark_pages(self):
        self.assertTrue(box_is_dark(Image.new("RGBA", (20, 20), "#10233d"), (0, 0, 20, 20)))
        self.assertFalse(box_is_dark(Image.new("RGBA", (20, 20), "white"), (0, 0, 20, 20)))

    def test_background_classifier_uses_dark_surrounding_area_for_small_white_word(self):
        image = Image.new("RGBA", (50, 40), "#10233d")
        image.paste("white", (17, 10, 33, 30))
        self.assertTrue(box_is_dark(image, (17, 10, 33, 30)))

    def test_outline_marker_style_leaves_its_centre_unfilled(self):
        filled = marker_layer((100, 40), (10, 10, 90, 30), "#FFF200", 1, 1, 150)
        outline = marker_layer((100, 40), (10, 10, 90, 30), "#FFF200", 1, 1, 150, outline=True)
        self.assertGreater(filled.getpixel((50, 20))[3], 0)
        self.assertEqual(outline.getpixel((50, 20))[3], 0)

    def test_brush_marker_style_paints_bristles_inside_the_headline_band(self):
        brush = marker_layer((100, 40), (10, 10, 90, 30), "#FFF200", 1, 1, 150, brush=True)
        self.assertGreater(brush.getpixel((50, 20))[3], 0)
        self.assertEqual(brush.getpixel((50, 3))[3], 0)

    def test_grunge_marker_style_paints_a_distressed_banner(self):
        grunge = marker_layer((100, 40), (10, 10, 90, 30), "#FFF200", 1, 1, 150, grunge=True)
        self.assertGreater(grunge.getpixel((50, 20))[3], 0)
        self.assertEqual(grunge.getpixel((50, 3))[3], 0)


if __name__ == "__main__":
    unittest.main()
