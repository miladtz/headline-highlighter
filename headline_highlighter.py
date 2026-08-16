"""Headline Highlighter - make animated marker videos from screenshots."""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import tempfile
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from statistics import median
from typing import Iterable

import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageFilter, ImageGrab, ImageTk
import pytesseract
from tkinterdnd2 import DND_FILES, TkinterDnD

APP_NAME = "Headline Highlighter"
SETTINGS_PATH = Path(os.getenv("APPDATA", Path.home())) / "HeadlineHighlighter" / "settings.json"


def bundled_path(name: str) -> str:
    """Return a resource path for source execution and PyInstaller execution."""
    return str(Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / name)


def configure_tools() -> None:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    exe = root / "tesseract" / "tesseract.exe"
    if exe.exists():
        pytesseract.pytesseract.tesseract_cmd = str(exe)
        os.environ["TESSDATA_PREFIX"] = str(exe.parent / "tessdata")


def clean_drop_path(value: str) -> str:
    # TkDND adds braces around paths containing spaces.
    return value.strip().strip("{}").split("} {")[0].strip("{}").strip()


def normalise(text: str) -> str:
    return re.sub(r"\W+", "", text).casefold()


def match_token(text: str) -> str:
    """Normalise OCR token variants, including Roman-numeral glyph confusion."""
    token = normalise(text)
    # Tesseract commonly reads II as Il, lI, or 11. Restrict this equivalence
    # to all-ambiguous tokens so ordinary words are never altered.
    if token and re.fullmatch(r"[il1]+", token):
        return "i" * len(token)
    return token


def ocr_lines(image: Image.Image, psm: int = 11) -> list[dict]:
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, config=f"--psm {psm}")
    grouped: dict[tuple[int, int, int, int], list[int]] = {}
    for i, word in enumerate(data["text"]):
        if word.strip() and float(data["conf"][i]) >= 20:
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i], data["page_num"][i])
            grouped.setdefault(key, []).append(i)
    result = []
    for indices in grouped.values():
        words = [data["text"][i].strip() for i in indices]
        word_boxes = [{"text": data["text"][i].strip(),
                       "box": (data["left"][i], data["top"][i], data["left"][i] + data["width"][i], data["top"][i] + data["height"][i])}
                      for i in indices]
        x0 = min(data["left"][i] for i in indices); y0 = min(data["top"][i] for i in indices)
        x1 = max(data["left"][i] + data["width"][i] for i in indices)
        y1 = max(data["top"][i] + data["height"][i] for i in indices)
        text = " ".join(words)
        if len(normalise(text)) >= 3:
            result.append({"text": text, "box": (x0, y0, x1, y1), "height": y1 - y0, "words": word_boxes})
    return sorted(result, key=lambda item: (item["box"][1], item["box"][0]))


def remove_header_navigation(lines: list[dict], image_height: int) -> list[dict]:
    """Drop a header separated from the article by a large hero-area gap."""
    if len(lines) < 2:
        return lines
    for index, (previous, following) in enumerate(zip(lines, lines[1:])):
        gap = following["box"][1] - previous["box"][3]
        header_ends_early = previous["box"][3] < image_height * .28
        separated = gap > max(previous["height"], following["height"]) * 2.0
        if header_ends_early and separated:
            return lines[index + 1:]
    return lines


def detect_headline(lines: list[dict], image_height: int) -> list[dict]:
    """Choose the most title-like line and adjoining similarly sized lines."""
    candidates = [line for line in lines if line["box"][1] < image_height * .65]
    if not candidates:
        return []
    # Split the page into vertical text clusters. A site navigation bar and
    # article headline have a large blank gap between them, while wrapped
    # headline lines are close together. This avoids treating both as one
    # headline even when one OCR mode gives the menu large bounding boxes.
    clusters: list[list[dict]] = [[candidates[0]]]
    for line in candidates[1:]:
        previous = clusters[-1][-1]
        gap = line["box"][1] - previous["box"][3]
        if gap > max(previous["height"], line["height"]) * 2.2:
            clusters.append([line])
        else:
            clusters[-1].append(line)
    cluster = max(clusters, key=lambda group: sum(line["height"] ** 2 + len(normalise(line["text"])) * line["height"] * .2 for line in group))
    scored = [(line["height"] * 3 + min(len(line["text"]), 90) * .25 - line["box"][1] / image_height * 12, i)
              for i, line in enumerate(cluster)]
    _, best = max(scored)
    selected = [cluster[best]]
    # Headlines commonly wrap on neighbouring lines of a comparable glyph size.
    for direction in (-1, 1):
        i = best + direction
        while 0 <= i < len(cluster):
            candidate = cluster[i]
            previous = selected[0] if direction < 0 else selected[-1]
            gap = abs(candidate["box"][1] - previous["box"][3])
            similar = .55 <= candidate["height"] / max(previous["height"], 1) <= 1.75
            if gap < max(previous["height"], candidate["height"]) * 1.8 and similar:
                if direction < 0: selected.insert(0, candidate)
                else: selected.append(candidate)
                i += direction
            else:
                break
    # Page navigation can be OCR'd as a very wide text line.  It is often
    # separated from the real title by a large blank hero area, so never let
    # a distant header row join the headline merely because its glyph height
    # happens to be similar in an OCR pass.
    title_top = cluster[best]["box"][1]
    title_height = cluster[best]["height"]
    selected = [line for line in selected if line["box"][1] >= title_top - title_height * 1.6]
    return selected


def headline_quality(lines: list[dict]) -> float:
    """Rank alternate OCR passes; large, multi-line title text beats body copy."""
    if not lines:
        return -1.0
    return sum(max(1, len(normalise(line["text"]))) * line["height"] for line in lines) + len(lines) * 140


def trim_mixed_title_lines(lines: list[dict]) -> list[dict]:
    """Remove small labels OCR merged beside an otherwise large headline."""
    word_heights = [word["box"][3] - word["box"][1]
                    for line in lines for word in line.get("words", [])]
    if not word_heights:
        return lines
    # A page section label often shares the OCR *line* with the headline but
    # its glyphs are much smaller.  The median title-word height provides a
    # stable cutoff without discarding short title words such as “II” or “to”.
    minimum_height = max(8, median(word_heights) * .58)
    trimmed = []
    for line in lines:
        title_words = [word for word in line.get("words", [])
                       if word["box"][3] - word["box"][1] >= minimum_height]
        if not title_words:
            continue
        x0 = min(word["box"][0] for word in title_words); y0 = min(word["box"][1] for word in title_words)
        x1 = max(word["box"][2] for word in title_words); y1 = max(word["box"][3] for word in title_words)
        trimmed.append({**line, "text": " ".join(word["text"] for word in title_words),
                        "box": (x0, y0, x1, y1), "height": y1 - y0, "words": title_words})
    return trimmed or lines


def find_manual_headline(lines: list[dict], headline: str) -> list[dict]:
    """Resolve a typed headline even when OCR drops or misreads a few words."""
    wanted = normalise(headline)
    if not wanted:
        return []
    for start in range(len(lines)):
        joined = ""
        for end in range(start, min(start + 5, len(lines))):
            joined += normalise(lines[end]["text"])
            if wanted in joined:
                return lines[start:end + 1]

    # OCR of large white type on a dark webpage can miss a word or split a
    # word strangely.  Choose the strongest nearby run by word overlap plus
    # sequence similarity rather than requiring a character-perfect match.
    wanted_words = [normalise(word) for word in headline.split() if normalise(word)]
    if len(wanted_words) < 2:
        return []
    best_score, best_lines = 0.0, []
    wanted_set = set(wanted_words)
    for start in range(len(lines)):
        for end in range(start, min(start + 7, len(lines))):
            candidate_text = " ".join(line["text"] for line in lines[start:end + 1])
            candidate_words = [normalise(word) for word in candidate_text.split() if normalise(word)]
            if not candidate_words:
                continue
            overlap = len(wanted_set & set(candidate_words)) / len(wanted_set)
            similarity = SequenceMatcher(None, wanted, normalise(candidate_text)).ratio()
            score = overlap * .7 + similarity * .3
            if score > best_score:
                best_score, best_lines = score, lines[start:end + 1]
    # A long manually entered title should still resolve with several OCR
    # mistakes, while avoiding accidental selection of unrelated body copy.
    return best_lines if best_score >= .52 else []


def timestamped_destination(folder: str, filename: str, now: datetime | None = None) -> str:
    """Create a non-destructive, timestamped MP4 destination path."""
    requested = Path(filename.strip() or "headline_highlight.mp4")
    stem = requested.stem or "headline_highlight"
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    base = Path(folder).expanduser() / f"{stem}_{stamp}.mp4"
    candidate, number = base, 2
    while candidate.exists():
        candidate = base.with_name(f"{base.stem}_{number}{base.suffix}")
        number += 1
    return str(candidate)


def source_copy_destination(source_path: str, video_destination: str) -> str:
    """Store the input image beside its MP4 using the same timestamped stem."""
    suffix = Path(source_path).suffix.lower() or ".png"
    return str(Path(video_destination).with_suffix(suffix))


def line_highlight_durations(lines: list[dict], title_time: float) -> list[float]:
    """Allocate title time by marker distance for one consistent draw speed."""
    widths = [max(1, line["box"][2] - line["box"][0]) for line in lines]
    total_width = sum(widths)
    return [title_time * width / total_width for width in widths]


def find_phrase_boxes(lines: list[dict], phrase_input: str, split_phrases: bool = True) -> tuple[list[dict], list[str]]:
    """Find phrase boxes; separate UI fields treat commas as literal text."""
    requested = [piece.strip() for piece in phrase_input.split(",") if piece.strip()] if split_phrases else [phrase_input.strip()]
    words = [(line_index, word) for line_index, line in enumerate(lines) for word in line.get("words", [])]
    found, missing = [], []
    for phrase in requested:
        wanted = "".join(match_token(word) for word in phrase.split())
        match = None
        for start in range(len(words)):
            joined = ""
            for end in range(start, min(len(words), start + 40)):
                joined += match_token(words[end][1]["text"])
                # A one-word input such as "fire" should select the OCR word
                # "fires" as a whole, while multi-word phrases stay exact.
                one_word_prefix = end == start and joined.startswith(wanted)
                if joined == wanted or one_word_prefix:
                    match = words[start:end + 1]
                    break
                if len(joined) > len(wanted):
                    break
            if match:
                break
        if not match:
            missing.append(phrase)
            continue
        by_line: dict[int, list[dict]] = {}
        for line_index, word in match:
            by_line.setdefault(line_index, []).append(word)
        for line_index, matched_words in by_line.items():
            x0 = min(word["box"][0] for word in matched_words); y0 = min(word["box"][1] for word in matched_words)
            x1 = max(word["box"][2] for word in matched_words); y1 = max(word["box"][3] for word in matched_words)
            line_box = lines[line_index]["box"]
            found.append({"text": phrase, "box": (x0, y0, x1, y1),
                          "line_box": (x0, line_box[1], x1, line_box[3]), "height": y1 - y0})
    return sorted(found, key=lambda item: (item["box"][1], item["box"][0])), missing


def marker_layer(size: tuple[int, int], box: tuple[int, int, int, int], color: str, fraction: float, seed: int,
                 opacity: int, outline: bool = False, brush: bool = False, grunge: bool = False) -> Image.Image:
    """Create a filled marker band, outline, dry brush, or distressed banner."""
    x0, y0, x1, y1 = box
    pad_x = max(2, (y1-y0) // 12); pad_y = max(1, (y1-y0) // 16)
    x0 -= pad_x; x1 += pad_x; y0 -= pad_y; y1 += pad_y
    endpoint = x0 + max(0, (x1-x0) * min(1, fraction))
    full_stroke = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(full_stroke, "RGBA")
    rgb = tuple(int(color.lstrip("#")[j:j+2], 16) for j in (0, 2, 4))
    rnd = random.Random(seed)
    height = y1 - y0
    segments = max(12, int((x1 - x0) / 26))
    edge_variation = max(1, min(3, height // 18))
    # Straight vertical end caps are especially obvious on a one- or two-word
    # phrase and make an otherwise textured stroke look like a rectangle.
    # Offset the top and bottom of each cap independently, creating the
    # naturally tapered, slightly diagonal start and finish of a marker pass.
    cap_variation = max(3, min(max(4, height // 3), max(4, (x1 - x0) // 8)))
    top, bottom = [], []
    # Generate from the *full* line, so all frames share precisely the same
    # texture; only a left-to-right mask changes during the animation.
    for i in range(segments + 1):
        x = x0 + (x1 - x0) * i / segments
        top_x = x
        bottom_x = x
        if i in (0, segments):
            top_x += rnd.randint(-cap_variation, cap_variation)
            bottom_x += rnd.randint(-cap_variation, cap_variation)
        top.append((top_x, y0 + rnd.randint(-edge_variation, edge_variation)))
        bottom.append((bottom_x, y1 + rnd.randint(-edge_variation, edge_variation)))
    if outline:
        # The outline option deliberately leaves the interior untouched.  It
        # is useful for the compact, yellow-style treatment of short phrases.
        edge_width = max(1, height // 18)
        draw.line(top, fill=(*rgb, min(255, opacity + 90)), width=edge_width, joint="curve")
        draw.line(bottom, fill=(*rgb, min(255, opacity + 90)), width=edge_width, joint="curve")
        draw.line([top[0], bottom[0]], fill=(*rgb, min(255, opacity + 90)), width=edge_width)
        draw.line([top[-1], bottom[-1]], fill=(*rgb, min(255, opacity + 90)), width=edge_width)
    elif brush:
        # Build irregular groups of bristles rather than uniform parallel
        # lines.  Varying starts, gaps, pigment density, and strand lengths
        # makes each generated stroke resemble a real brush print.
        bristles = max(14, height // 2)
        for i in range(bristles):
            y = y0 + height * i / max(1, bristles - 1) + rnd.randint(-2, 2)
            if rnd.random() < .12:
                continue
            thickness = rnd.choice((1, 1, 1, 2, 2, 3))
            left = x0 + rnd.randint(-pad_x * 4, pad_x * 5)
            right = x1 + rnd.randint(-pad_x * 5, pad_x * 4)
            cursor = left
            while cursor < right:
                segment = rnd.randint(max(5, (x1 - x0) // 12), max(8, (x1 - x0) // 3))
                end = min(right, cursor + segment)
                pigment = max(45, min(255, opacity + rnd.randint(-50, 35)))
                draw.line((cursor, y, end, y + rnd.randint(-1, 1)), fill=(*rgb, pigment), width=thickness)
                cursor = end + rnd.randint(0, max(2, (x1 - x0) // 18))
            if rnd.random() < .55:
                strand = rnd.randint(max(4, (x1 - x0) // 14), max(8, (x1 - x0) // 5))
                draw.line((left - strand, y + rnd.randint(-3, 3), left + strand, y), fill=(*rgb, opacity), width=1)
                draw.line((right - strand, y, right + strand, y + rnd.randint(-3, 3)), fill=(*rgb, opacity), width=1)
    elif grunge:
        # A dense banner with random paint loss and frayed, uneven edges.
        # The defects are seeded, so they remain stable as the animation draws
        # left-to-right instead of flickering from frame to frame.
        draw.polygon(top + list(reversed(bottom)), fill=(*rgb, opacity))
        specks = min(100, max(24, int((x1 - x0) / 9)))
        for _ in range(specks):
            if rnd.random() < .72:
                edge = rnd.choice((x0, x1))
                direction = 1 if edge == x0 else -1
                x = edge + direction * rnd.randint(0, max(2, (x1 - x0) // 4))
            else:
                x = rnd.randint(x0, x1)
            y = rnd.randint(y0, y1)
            width = rnd.randint(1, max(3, (x1 - x0) // 16))
            hole_height = rnd.randint(1, max(2, height // 6))
            draw.rectangle((x, y, x + width, y + hole_height), fill=(0, 0, 0, 0))
        for _ in range(max(4, height // 9)):
            y = rnd.randint(y0 - edge_variation, y1 + edge_variation)
            start = x0 + rnd.randint(-pad_x * 3, pad_x * 5)
            end = x1 + rnd.randint(-pad_x * 5, pad_x * 3)
            draw.line((start, y, end, y + rnd.randint(-1, 1)), fill=(*rgb, max(35, opacity // 2)), width=1)
    else:
        # Establish a continuous pigment body first.  A narrow word has too
        # little area for a polygon-only stroke to read as filled once the
        # feathering is applied, which made words like “says” resemble an
        # outline.  This base is deliberately under the irregular edge so
        # every filled marker, regardless of length, has the same treatment.
        corner = max(2, min(height // 3, (x1 - x0) // 5))
        draw.rounded_rectangle((x0, y0, x1, y1), radius=corner, fill=(*rgb, opacity))
        draw.polygon(top + list(reversed(bottom)), fill=(*rgb, opacity))
    # A soft underpass makes the pigment feel absorbed into the page instead
    # of sitting on it as a sharp digital rectangle.
    feather = full_stroke.filter(ImageFilter.GaussianBlur(.65))
    full_stroke = Image.alpha_composite(feather, full_stroke)
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    if endpoint > x0:
        right = min(size[0], max(0, round(endpoint)))
        left = min(size[0], max(0, round(x0)))
        if right > left:
            layer.paste(full_stroke.crop((left, 0, right, size[1])), (left, 0))
    return layer


def box_is_dark(image: Image.Image, box: tuple[int, int, int, int]) -> bool:
    """Classify the surrounding page, rather than bright headline glyphs."""
    x0, y0, x1, y1 = box
    margin = max(3, min(20, max(x1 - x0, y1 - y0) // 3))
    left, top = max(0, x0 - margin), max(0, y0 - margin)
    right, bottom = min(image.width, x1 + margin), min(image.height, y1 + margin)
    # Sampling the border around the phrase avoids a small white word such as
    # “says” overwhelming a dark-page classification.  It also works on a
    # light page because the surrounding paper, rather than the dark letters,
    # determines the result.
    pixels = []
    for x in range(left, right, max(1, (right - left) // 16)):
        for y in range(top, min(bottom, y0)):
            pixels.append(image.getpixel((x, y))[:3])
        for y in range(max(top, y1), bottom):
            pixels.append(image.getpixel((x, y))[:3])
    for y in range(y0, y1, max(1, (y1 - y0) // 8)):
        for x in range(left, min(right, x0)):
            pixels.append(image.getpixel((x, y))[:3])
        for x in range(max(left, x1), right):
            pixels.append(image.getpixel((x, y))[:3])
    if not pixels:
        pixels = [image.getpixel((min(max(0, x0), image.width - 1), min(max(0, y0), image.height - 1)))[:3]]
    brightness = sum(sum(pixel) / 3 for pixel in pixels) / len(pixels)
    return brightness < 135


def preserve_text_appearance(composed: Image.Image, original: Image.Image, box: tuple[int, int, int, int], color: str, dark_background: bool) -> None:
    """Keep black type crisp on paper, and tint white type on dark pages."""
    rgb = tuple(int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    src, dst = original.load(), composed.load()
    x0, y0, x1, y1 = (max(0, box[0]), max(0, box[1]), min(original.width, box[2]), min(original.height, box[3]))
    for y in range(y0, y1):
        for x in range(x0, x1):
            pixel = src[x, y]
            luminance = (pixel[0] * 299 + pixel[1] * 587 + pixel[2] * 114) // 1000
            if dark_background and luminance > 170:
                # Bright headline letters accept most of the marker pigment.
                dst[x, y] = tuple(round(pixel[i] * .18 + rgb[i] * .82) for i in range(3)) + (pixel[3],)
            elif not dark_background and luminance < 105:
                # On a light page the marker is behind the dark ink.
                dst[x, y] = pixel


def zoom_frame(image: Image.Image, center: tuple[float, float], scale: float) -> Image.Image:
    """Return a sub-pixel smooth zoom, keeping the headline as the focus."""
    if scale <= 1:
        return image
    crop_w, crop_h = image.width / scale, image.height / scale
    left = min(max(0, center[0] - crop_w / 2), image.width - crop_w)
    top = min(max(0, center[1] - crop_h / 2), image.height - crop_h)
    # crop()+resize() rounds the crop rectangle each frame, producing visible
    # one-pixel shakes. An affine transform retains fractional coordinates.
    inverse_scale = 1 / scale
    return image.transform(
        image.size,
        Image.Transform.AFFINE,
        (inverse_scale, 0, left, 0, inverse_scale, top),
        resample=Image.Resampling.BICUBIC,
    )


def generate_video(image_path: str, lines: list[dict], title_time: float, gap: float, duration: float,
                   color: str, destination: str, progress, marker_style: str = "filled") -> None:
    image = Image.open(image_path).convert("RGBA")
    fps = 30
    frames = max(1, round(duration * fps))
    ffmpeg = bundled_path("ffmpeg.exe")
    if not Path(ffmpeg).exists():
        found = shutil.which("ffmpeg")
        if not found:
            raise RuntimeError("Bundled FFmpeg is missing. Reinstall Headline Highlighter.")
        ffmpeg = found
    command = [ffmpeg, "-y", "-f", "rawvideo", "-vcodec", "rawvideo", "-pix_fmt", "rgba", "-s", f"{image.width}x{image.height}",
               "-r", str(fps), "-i", "-", "-an", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", destination]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    highlight_durations = line_highlight_durations(lines, title_time)
    focus = ((min(line["box"][0] for line in lines) + max(line["box"][2] for line in lines)) / 2,
             (min(line["box"][1] for line in lines) + max(line["box"][3] for line in lines)) / 2)
    try:
        for frame in range(frames):
            t = frame / fps
            composed = image.copy()
            cursor = 0.0
            for n, line in enumerate(lines):
                line_duration = highlight_durations[n]
                f = min(1.0, max(0.0, (t-cursor) / line_duration))
                if f:
                    # The outline closely follows the exact words, while the
                    # filled, brush, and grunge styles use the natural headline band.
                    render_box = line["box"] if marker_style == "outline" else line.get("line_box", line["box"])
                    dark_background = box_is_dark(image, render_box)
                    opacity = 65 if dark_background and marker_style == "outline" else (110 if dark_background else 118)
                    line_color = line.get("color", color)
                    composed.alpha_composite(marker_layer(image.size, render_box, line_color, f, n, opacity,
                                                          outline=marker_style == "outline", brush=marker_style == "brush",
                                                          grunge=marker_style == "grunge"))
                    visible_box = (render_box[0], render_box[1], round(render_box[0] + (render_box[2] - render_box[0]) * f), render_box[3])
                    preserve_text_appearance(composed, image, visible_box, line_color, dark_background)
                cursor += line_duration + (gap if n < len(lines)-1 else 0)
            composed = zoom_frame(composed, focus, 1 + .08 * frame / max(1, frames - 1))
            try:
                process.stdin.write(composed.tobytes())
            except BrokenPipeError:
                errors = process.stderr.read().decode(errors="replace")
                process.wait()
                detail = errors[-1600:] or "FFmpeg ended before it could accept a video frame."
                raise RuntimeError(f"FFmpeg could not create the MP4:\n{detail}") from None
            progress((frame + 1) / frames * 100)
        process.stdin.close()
        errors = process.stderr.read().decode(errors="replace")
        if process.wait() != 0:
            raise RuntimeError(f"FFmpeg could not create the MP4:\n{errors[-800:]}")
    finally:
        if process.poll() is None:
            process.kill()


class App(TkinterDnD.Tk):
    def __init__(self):
        super().__init__()
        configure_tools()
        self.title(APP_NAME); self.geometry("820x760"); self.minsize(720, 680)
        self.image_path = ""; self.image_height = 0; self.all_lines: list[dict] = []; self.ocr_variants: list[list[dict]] = []; self.headline_variants: list[list[dict]] = []; self.headline_lines: list[dict] = []; self.detected_headline = ""; self.highlight_items: list[dict] = []
        self.values = self.load_settings()
        self.title_time = tk.StringVar(value=str(self.values.get("title_time", 3.0)))
        self.gap = tk.StringVar(value=str(self.values.get("gap", 0.18)))
        self.duration = tk.StringVar(value=str(self.values.get("duration", 5.0)))
        self.color = tk.StringVar(value=self.values.get("color", "#FFF200"))
        saved_style = self.values.get("marker_style")
        if saved_style not in ("filled", "outline", "brush", "grunge"):
            saved_style = "outline" if self.values.get("outline_style", self.values.get("tight_shape", False)) else "filled"
        self.marker_style = tk.StringVar(value=saved_style)
        self.manual_headline = tk.StringVar(); self.filename = tk.StringVar(value="headline_highlight.mp4")
        self.folder = tk.StringVar(value=str(Path.home() / "Videos"))
        self.phrase_rows = [(tk.StringVar(), tk.StringVar(value="#FFF200")) for _ in range(10)]
        self.phrase_color_buttons: list[tk.Button] = []
        self.build_ui()

    def load_settings(self):
        try: return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): return {}

    def save_settings(self):
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps({"title_time": self.title_time.get(), "gap": self.gap.get(), "duration": self.duration.get(), "color": self.color.get(), "marker_style": self.marker_style.get()}), encoding="utf-8")

    def build_ui(self):
        outer = ttk.Frame(self, padding=16); outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=APP_NAME, font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Drop a screenshot below or choose one. The headline is found automatically.").pack(anchor="w", pady=(0, 10))
        self.drop = tk.Label(outer, text="Drop screenshot here, click Browse, or click here and press Ctrl+V", height=5, relief="groove", bg="#f3f6fb", font=("Segoe UI", 11))
        self.drop.pack(fill="x"); self.drop.drop_target_register(DND_FILES); self.drop.dnd_bind("<<Drop>>", self.on_drop); self.drop.bind("<Button-1>", self.focus_paste_area)
        self.drop.bind("<Control-v>", self.paste_image); self.drop.bind("<Control-V>", self.paste_image)
        ttk.Button(outer, text="Browse…", command=self.browse).pack(anchor="e", pady=6)
        self.status = ttk.Label(outer, text="No screenshot selected.", wraplength=700); self.status.pack(anchor="w")
        ttk.Label(outer, text="Manual headline (optional; replaces OCR detection)").pack(anchor="w", pady=(12, 0))
        manual_entry = ttk.Entry(outer, textvariable=self.manual_headline); manual_entry.pack(fill="x")
        phrase_frame = ttk.LabelFrame(outer, text="Phrase highlights (optional; each phrase can have its own color)", padding=7)
        phrase_frame.pack(fill="x", pady=(8, 0))
        for index, (phrase, phrase_color) in enumerate(self.phrase_rows):
            column = (index // 5) * 3; row = index % 5
            ttk.Label(phrase_frame, text=f"{index + 1}.").grid(row=row, column=column, sticky="w", padx=(0, 3), pady=2)
            ttk.Entry(phrase_frame, textvariable=phrase, width=25).grid(row=row, column=column + 1, sticky="ew", pady=2)
            button = tk.Button(phrase_frame, text="Color", bg=phrase_color.get(), activebackground=phrase_color.get(), command=lambda i=index: self.choose_phrase_color(i))
            button.grid(row=row, column=column + 2, padx=(4, 10), pady=2)
            self.phrase_color_buttons.append(button)
        phrase_frame.columnconfigure(1, weight=1); phrase_frame.columnconfigure(4, weight=1)
        grid = ttk.Frame(outer); grid.pack(fill="x", pady=12); grid.columnconfigure(1, weight=1); grid.columnconfigure(3, weight=1)
        fields = [("Title highlighting time (seconds)", self.title_time), ("Gap between lines (seconds)", self.gap), ("Total video length (seconds)", self.duration)]
        for row, (label, variable) in enumerate(fields):
            ttk.Label(grid, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=3)
            ttk.Entry(grid, textvariable=variable, width=12).grid(row=row, column=1, sticky="ew", pady=3)
        ttk.Label(grid, text="Highlight color").grid(row=0, column=2, sticky="w", padx=(25, 10))
        ttk.Entry(grid, textvariable=self.color, width=10).grid(row=0, column=3, sticky="ew")
        ttk.Button(grid, text="Choose…", command=self.choose_color).grid(row=1, column=3, sticky="w", pady=3)
        style_frame = ttk.Frame(grid); style_frame.grid(row=2, column=2, columnspan=2, sticky="w", padx=(25, 0), pady=3)
        ttk.Label(style_frame, text="Highlight style").pack(side="left", padx=(0, 8))
        ttk.Radiobutton(style_frame, text="Filled", variable=self.marker_style, value="filled").pack(side="left")
        ttk.Radiobutton(style_frame, text="Outline", variable=self.marker_style, value="outline").pack(side="left", padx=(8, 0))
        ttk.Radiobutton(style_frame, text="Brush", variable=self.marker_style, value="brush").pack(side="left", padx=(8, 0))
        ttk.Radiobutton(style_frame, text="Grunge", variable=self.marker_style, value="grunge").pack(side="left", padx=(8, 0))
        ttk.Label(grid, text="Output filename").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Entry(grid, textvariable=self.filename).grid(row=3, column=1, columnspan=3, sticky="ew", pady=3)
        ttk.Label(grid, text="Output folder").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Entry(grid, textvariable=self.folder).grid(row=4, column=1, columnspan=2, sticky="ew", pady=3)
        ttk.Button(grid, text="Browse…", command=self.choose_folder).grid(row=4, column=3, sticky="e")
        self.progress = ttk.Progressbar(outer, maximum=100); self.progress.pack(fill="x", pady=(4, 8))
        buttons = ttk.Frame(outer); buttons.pack(fill="x"); self.generate_button = ttk.Button(buttons, text="Generate Video", command=self.start_generate); self.generate_button.pack(side="left")
        ttk.Button(buttons, text="Exit", command=self.destroy).pack(side="right")

    def on_drop(self, event): self.open_image(clean_drop_path(event.data))
    def focus_paste_area(self, _event=None):
        self.drop.focus_set()
        self.status.configure(text="Paste area selected. Press Ctrl+V to paste a screenshot, or use Browse to select a file.")
        return "break"
    def paste_image(self, _event=None):
        try:
            pasted = ImageGrab.grabclipboard()
            if isinstance(pasted, list):
                if pasted:
                    self.open_image(pasted[0])
                    return "break"
                raise ValueError("The clipboard does not contain an image.")
            if not isinstance(pasted, Image.Image):
                raise ValueError("Copy a screenshot to the clipboard first.")
            temporary = Path(tempfile.gettempdir()) / "HeadlineHighlighter"
            temporary.mkdir(parents=True, exist_ok=True)
            image_path = temporary / f"pasted_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
            pasted.save(image_path, "PNG")
            self.open_image(str(image_path))
            return "break"
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not paste the screenshot.\n\n{exc}")
            return "break"
    def browse(self):
        path = filedialog.askopenfilename(filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.webp"), ("All files", "*.*")])
        if path: self.open_image(path)
    def open_image(self, path):
        try:
            image = Image.open(path); image.verify(); image = Image.open(path).convert("RGB")
            self.image_path = path; self.status.configure(text="Reading headline with OCR…") ; self.update_idletasks()
            # Sparse-text and document OCR modes each perform better on
            # different screenshots. Choose the strongest headline result.
            self.image_height = image.height
            self.ocr_variants = [remove_header_navigation(ocr_lines(image, psm), image.height) for psm in (11, 6, 3)]
            self.headline_variants = [detect_headline(lines, image.height) for lines in self.ocr_variants]
            choices = list(zip(self.headline_variants, self.ocr_variants))
            self.headline_lines, self.all_lines = max(choices, key=lambda choice: headline_quality(choice[0]))
            self.headline_lines = trim_mixed_title_lines(self.headline_lines)
            self.highlight_items = list(self.headline_lines)
            detected = " ".join(x["text"] for x in self.headline_lines)
            self.detected_headline = detected
            self.manual_headline.set(detected)
            self.status.configure(text=f"Selected: {Path(path).name}\nDetected headline: {detected or 'No headline found — enter it above.'}")
        except Exception as exc: messagebox.showerror(APP_NAME, f"Could not read that image or run OCR.\n\n{exc}")
    def apply_selection(self) -> bool:
        if not self.all_lines:
            return False
        selected = self.headline_lines
        manual_override = bool(self.manual_headline.get().strip()) and normalise(self.manual_headline.get()) != normalise(self.detected_headline)
        if manual_override:
            # A typed title should highlight the matched words, not their
            # entire OCR line: an OCR line can contain both a page header
            # (such as the BBC logo) and the first title word.
            exact_options = []
            for lines in self.ocr_variants:
                word_matches, missing = find_phrase_boxes(lines, self.manual_headline.get(), split_phrases=False)
                if not missing:
                    exact_options.append((word_matches, lines))
            if exact_options:
                selected, matched_lines = min(exact_options, key=lambda choice: sum((item["box"][2] - item["box"][0]) * (item["box"][3] - item["box"][1]) for item in choice[0]))
                self.all_lines = matched_lines
            else:
                matches = [(find_manual_headline(lines, self.manual_headline.get()), lines) for lines in self.ocr_variants]
                selected, matched_lines = max(matches, key=lambda choice: headline_quality(choice[0]))
                if selected:
                    self.all_lines = matched_lines
                else:
                    self.status.configure(text="Manual headline was not found by OCR. Check spelling or use a clearer image.")
                    return False
        phrase_entries = [(phrase.get().strip(), color.get()) for phrase, color in self.phrase_rows if phrase.get().strip()]
        if phrase_entries:
            matched, missing = [], []
            title_center = sum((line["box"][1] + line["box"][3]) / 2 for line in selected) / max(1, len(selected))
            for phrase, phrase_color in phrase_entries:
                # Search every filtered OCR line, not just the initially
                # selected lines. This finds wrapped phrases (for example,
                # "axes 2,800 jobs") even if detection missed their line.
                phrase_options = [find_phrase_boxes(lines, phrase, split_phrases=False) for lines in self.ocr_variants]
                found = [option[0] for option in phrase_options if not option[1]]
                if not found:
                    missing.append(phrase)
                    continue
                def match_rank(result):
                    center = sum((item["box"][1] + item["box"][3]) / 2 for item in result) / len(result)
                    area = sum((item["box"][2] - item["box"][0]) * (item["box"][3] - item["box"][1]) for item in result)
                    return abs(center - title_center), area
                matches = min(found, key=match_rank)
                for match in matches:
                    match["color"] = phrase_color
                    matched.append(match)
            if missing:
                self.status.configure(text="Could not match phrase(s): " + ", ".join(missing))
                return False
            self.highlight_items = matched
            self.status.configure(text=f"Highlighting {len(matched)} phrase segment(s) in the order entered.")
        else:
            self.highlight_items = list(selected)
            self.status.configure(text="Highlighting the full headline line by line.")
        return bool(self.highlight_items)
    def choose_color(self):
        answer = colorchooser.askcolor(self.color.get(), parent=self)
        if answer[1]: self.color.set(answer[1])
    def choose_phrase_color(self, index: int):
        answer = colorchooser.askcolor(self.phrase_rows[index][1].get(), parent=self)
        if answer[1]:
            self.phrase_rows[index][1].set(answer[1])
            self.phrase_color_buttons[index].configure(bg=answer[1], activebackground=answer[1])
    def choose_folder(self):
        answer = filedialog.askdirectory(initialdir=self.folder.get())
        if answer: self.folder.set(answer)
    def start_generate(self):
        if not self.image_path or not self.apply_selection(): return messagebox.showwarning(APP_NAME, "Choose a screenshot and a detectable headline or phrase first.")
        try:
            title_time, gap, duration = map(float, (self.title_time.get(), self.gap.get(), self.duration.get()))
            phrase_colors = [color.get() for phrase, color in self.phrase_rows if phrase.get().strip()]
            if (min(title_time, duration) <= 0 or gap < 0 or
                    not re.fullmatch(r"#[0-9a-fA-F]{6}", self.color.get()) or
                    any(not re.fullmatch(r"#[0-9a-fA-F]{6}", value) for value in phrase_colors)): raise ValueError
        except ValueError: return messagebox.showwarning(APP_NAME, "Use positive times, a non-negative gap, and a #RRGGBB color.")
        total_animation = title_time + gap * max(0, len(self.highlight_items) - 1)
        if duration < total_animation:
            return messagebox.showwarning(APP_NAME, "Total video length must be at least the title highlighting time plus all line gaps.")
        destination = timestamped_destination(self.folder.get(), self.filename.get())
        try: Path(destination).parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc: return messagebox.showerror(APP_NAME, f"Cannot create output folder.\n{exc}")
        self.save_settings(); self.generate_button.configure(state="disabled"); self.progress["value"] = 0
        def task():
            try:
                generate_video(self.image_path, self.highlight_items, title_time, gap, duration, self.color.get(), destination, lambda p: self.after(0, lambda: self.progress.configure(value=p)), marker_style=self.marker_style.get())
                source_destination = source_copy_destination(self.image_path, destination)
                shutil.copy2(self.image_path, source_destination)
                self.after(0, lambda: messagebox.showinfo(APP_NAME, f"Video created:\n{destination}\n\nSource image saved:\n{source_destination}"))
            except Exception as exc:
                # Exception variables are cleared at the end of an except block;
                # bind the message now so Tk receives the actual failure detail.
                error_text = str(exc) or repr(exc)
                self.after(0, lambda message=error_text: messagebox.showerror(APP_NAME, message))
            finally: self.after(0, lambda: self.generate_button.configure(state="normal"))
        threading.Thread(target=task, daemon=True).start()


if __name__ == "__main__":
    App().mainloop()
