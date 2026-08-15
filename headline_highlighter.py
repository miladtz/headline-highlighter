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
from pathlib import Path
from typing import Iterable

import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageTk
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
        x0 = min(data["left"][i] for i in indices); y0 = min(data["top"][i] for i in indices)
        x1 = max(data["left"][i] + data["width"][i] for i in indices)
        y1 = max(data["top"][i] + data["height"][i] for i in indices)
        text = " ".join(words)
        if len(normalise(text)) >= 3:
            result.append({"text": text, "box": (x0, y0, x1, y1), "height": y1 - y0})
    return sorted(result, key=lambda item: (item["box"][1], item["box"][0]))


def detect_headline(lines: list[dict], image_height: int) -> list[dict]:
    """Choose the most title-like line and adjoining similarly sized lines."""
    candidates = [line for line in lines if line["box"][1] < image_height * .65]
    if not candidates:
        return []
    scored = [(line["height"] * 3 + min(len(line["text"]), 90) * .25 - line["box"][1] / image_height * 12, i)
              for i, line in enumerate(candidates)]
    _, best = max(scored)
    selected = [candidates[best]]
    # Headlines commonly wrap on neighbouring lines of a comparable glyph size.
    for direction in (-1, 1):
        i = best + direction
        while 0 <= i < len(candidates):
            candidate = candidates[i]
            previous = selected[0] if direction < 0 else selected[-1]
            gap = abs(candidate["box"][1] - previous["box"][3])
            similar = .55 <= candidate["height"] / max(previous["height"], 1) <= 1.75
            if gap < max(previous["height"], candidate["height"]) * 1.8 and similar:
                if direction < 0: selected.insert(0, candidate)
                else: selected.append(candidate)
                i += direction
            else:
                break
    return selected


def find_manual_headline(lines: list[dict], headline: str) -> list[dict]:
    wanted = normalise(headline)
    if not wanted:
        return []
    for start in range(len(lines)):
        joined = ""
        for end in range(start, min(start + 5, len(lines))):
            joined += normalise(lines[end]["text"])
            if wanted in joined or joined in wanted and len(joined) > 6:
                return lines[start:end + 1]
    return []


def marker_layer(size: tuple[int, int], box: tuple[int, int, int, int], color: str, fraction: float, seed: int) -> Image.Image:
    """Create an intentionally irregular translucent marker stroke, clipped to progress."""
    x0, y0, x1, y1 = box
    pad_x = max(8, (y1-y0) // 3); pad_y = max(5, (y1-y0) // 5)
    x0 -= pad_x; x1 += pad_x; y0 -= pad_y; y1 += pad_y
    endpoint = x0 + max(0, (x1-x0) * min(1, fraction))
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    rgb = tuple(int(color.lstrip("#")[j:j+2], 16) for j in (0, 2, 4))
    rnd = random.Random(seed)
    radius = max(3, (y1-y0) // 2)
    step = max(3, radius // 2)
    x = x0 - radius
    while x < endpoint + radius:
        jitter = rnd.randint(-max(1, radius // 5), max(1, radius // 5))
        draw.ellipse((x-radius, (y0+y1)//2-radius+jitter, x+radius, (y0+y1)//2+radius+jitter), fill=(*rgb, 105))
        x += step
    # A faint second pass produces the layered texture of a real highlighter.
    draw.rectangle((x0, y0 + radius//3, endpoint, y1 - radius//4), fill=(*rgb, 40))
    return layer


def generate_video(image_path: str, lines: list[dict], line_time: float, gap: float, duration: float,
                   color: str, destination: str, progress) -> None:
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
               "-r", str(fps), "-i", "-", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", destination]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for frame in range(frames):
            t = frame / fps
            composed = image.copy()
            cursor = 0.0
            for n, line in enumerate(lines):
                f = min(1.0, max(0.0, (t-cursor) / line_time))
                if f:
                    composed.alpha_composite(marker_layer(image.size, line["box"], color, f, n))
                cursor += line_time + (gap if n < len(lines)-1 else 0)
            process.stdin.write(composed.tobytes())
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
        self.title(APP_NAME); self.geometry("760x690"); self.minsize(650, 600)
        self.image_path = ""; self.all_lines: list[dict] = []; self.headline_lines: list[dict] = []
        self.values = self.load_settings()
        self.line_time = tk.StringVar(value=str(self.values.get("line_time", 0.8)))
        self.gap = tk.StringVar(value=str(self.values.get("gap", 0.18)))
        self.duration = tk.StringVar(value=str(self.values.get("duration", 5.0)))
        self.color = tk.StringVar(value=self.values.get("color", "#FFF176"))
        self.headline = tk.StringVar(); self.filename = tk.StringVar(value="headline_highlight.mp4")
        self.folder = tk.StringVar(value=str(Path.home() / "Videos"))
        self.build_ui()

    def load_settings(self):
        try: return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): return {}

    def save_settings(self):
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps({"line_time": self.line_time.get(), "gap": self.gap.get(), "duration": self.duration.get(), "color": self.color.get()}), encoding="utf-8")

    def build_ui(self):
        outer = ttk.Frame(self, padding=16); outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=APP_NAME, font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Drop a screenshot below or choose one. The headline is found automatically.").pack(anchor="w", pady=(0, 10))
        self.drop = tk.Label(outer, text="Drop screenshot here\n(or click Browse)", height=5, relief="groove", bg="#f3f6fb", font=("Segoe UI", 11))
        self.drop.pack(fill="x"); self.drop.drop_target_register(DND_FILES); self.drop.dnd_bind("<<Drop>>", self.on_drop); self.drop.bind("<Button-1>", lambda _e: self.browse())
        ttk.Button(outer, text="Browse…", command=self.browse).pack(anchor="e", pady=6)
        self.status = ttk.Label(outer, text="No screenshot selected.", wraplength=700); self.status.pack(anchor="w")
        ttk.Label(outer, text="Headline override (optional)").pack(anchor="w", pady=(12, 0))
        entry = ttk.Entry(outer, textvariable=self.headline); entry.pack(fill="x"); entry.bind("<FocusOut>", lambda _e: self.apply_override())
        grid = ttk.Frame(outer); grid.pack(fill="x", pady=12); grid.columnconfigure(1, weight=1); grid.columnconfigure(3, weight=1)
        fields = [("Line highlight time (seconds)", self.line_time), ("Gap between lines (seconds)", self.gap), ("Total video length (seconds)", self.duration)]
        for row, (label, variable) in enumerate(fields):
            ttk.Label(grid, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=3)
            ttk.Entry(grid, textvariable=variable, width=12).grid(row=row, column=1, sticky="ew", pady=3)
        ttk.Label(grid, text="Highlight color").grid(row=0, column=2, sticky="w", padx=(25, 10))
        ttk.Entry(grid, textvariable=self.color, width=10).grid(row=0, column=3, sticky="ew")
        ttk.Button(grid, text="Choose…", command=self.choose_color).grid(row=1, column=3, sticky="w", pady=3)
        ttk.Label(grid, text="Output filename").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Entry(grid, textvariable=self.filename).grid(row=3, column=1, columnspan=3, sticky="ew", pady=3)
        ttk.Label(grid, text="Output folder").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Entry(grid, textvariable=self.folder).grid(row=4, column=1, columnspan=2, sticky="ew", pady=3)
        ttk.Button(grid, text="Browse…", command=self.choose_folder).grid(row=4, column=3, sticky="e")
        self.progress = ttk.Progressbar(outer, maximum=100); self.progress.pack(fill="x", pady=(4, 8))
        buttons = ttk.Frame(outer); buttons.pack(fill="x"); self.generate_button = ttk.Button(buttons, text="Generate Video", command=self.start_generate); self.generate_button.pack(side="left")
        ttk.Button(buttons, text="Exit", command=self.destroy).pack(side="right")

    def on_drop(self, event): self.open_image(clean_drop_path(event.data))
    def browse(self):
        path = filedialog.askopenfilename(filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.webp"), ("All files", "*.*")])
        if path: self.open_image(path)
    def open_image(self, path):
        try:
            image = Image.open(path); image.verify(); image = Image.open(path).convert("RGB")
            self.image_path = path; self.status.configure(text="Reading headline with OCR…") ; self.update_idletasks()
            self.all_lines = ocr_lines(image); self.headline_lines = detect_headline(self.all_lines, image.height)
            detected = " ".join(x["text"] for x in self.headline_lines)
            self.headline.set(detected); self.status.configure(text=f"Selected: {Path(path).name}\nDetected: {detected or 'No headline found — enter it above.'}")
        except Exception as exc: messagebox.showerror(APP_NAME, f"Could not read that image or run OCR.\n\n{exc}")
    def apply_override(self):
        if self.headline.get().strip() and self.all_lines:
            result = find_manual_headline(self.all_lines, self.headline.get())
            if result: self.headline_lines = result; self.status.configure(text="Manual headline matched in the screenshot.")
            else:
                # Retry with the more document-oriented segmentation mode. This gives
                # a manual override a second OCR route without asking for coordinates.
                retry = ocr_lines(Image.open(self.image_path).convert("RGB"), psm=6)
                result = find_manual_headline(retry, self.headline.get())
                if result:
                    self.all_lines = retry; self.headline_lines = result
                    self.status.configure(text="Manual headline matched in the screenshot.")
                else: self.status.configure(text="Manual text was not found by OCR. Check spelling or use a clearer image.")
    def choose_color(self):
        answer = colorchooser.askcolor(self.color.get(), parent=self)
        if answer[1]: self.color.set(answer[1])
    def choose_folder(self):
        answer = filedialog.askdirectory(initialdir=self.folder.get())
        if answer: self.folder.set(answer)
    def start_generate(self):
        self.apply_override()
        if not self.image_path or not self.headline_lines: return messagebox.showwarning(APP_NAME, "Choose a screenshot and a detectable headline first.")
        try:
            line_time, gap, duration = map(float, (self.line_time.get(), self.gap.get(), self.duration.get()))
            if min(line_time, duration) <= 0 or gap < 0 or not re.fullmatch(r"#[0-9a-fA-F]{6}", self.color.get()): raise ValueError
        except ValueError: return messagebox.showwarning(APP_NAME, "Use positive times, a non-negative gap, and a #RRGGBB color.")
        name = self.filename.get().strip() or "headline_highlight.mp4"
        if not name.lower().endswith(".mp4"): name += ".mp4"
        destination = str(Path(self.folder.get()).expanduser() / name)
        try: Path(destination).parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc: return messagebox.showerror(APP_NAME, f"Cannot create output folder.\n{exc}")
        self.save_settings(); self.generate_button.configure(state="disabled"); self.progress["value"] = 0
        def task():
            try:
                generate_video(self.image_path, self.headline_lines, line_time, gap, duration, self.color.get(), destination, lambda p: self.after(0, lambda: self.progress.configure(value=p)))
                self.after(0, lambda: messagebox.showinfo(APP_NAME, f"Video created:\n{destination}"))
            except Exception as exc: self.after(0, lambda: messagebox.showerror(APP_NAME, str(exc)))
            finally: self.after(0, lambda: self.generate_button.configure(state="normal"))
        threading.Thread(target=task, daemon=True).start()


if __name__ == "__main__":
    App().mainloop()
