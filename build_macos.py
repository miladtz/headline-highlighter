"""Build an Apple-Silicon macOS .app with its OCR and video runtimes."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).parent
deps = ROOT / "vendor" / "macos"
required = [
    deps / "ffmpeg",
    deps / "tesseract" / "tesseract",
    deps / "tesseract" / "tessdata" / "eng.traineddata",
]
missing = [str(item) for item in required if not item.exists()]
if missing:
    raise SystemExit(
        "Missing macOS bundled runtime files:\n" + "\n".join(missing)
        + "\nRun the macOS GitHub Actions workflow to prepare them."
    )

subprocess.run([
    sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir",
    "--name", "Headline Highlighter", "--osx-bundle-identifier", "com.headlinehighlighter.app",
    "--collect-all", "tkinterdnd2",
    "--add-binary", f"{deps / 'ffmpeg'}:.",
    "--add-binary", f"{deps / 'tesseract' / 'tesseract'}:tesseract",
    "--add-data", f"{deps / 'tesseract' / 'tessdata'}:tesseract/tessdata",
    "headline_highlighter.py",
], check=True, cwd=ROOT)
print(ROOT / "dist" / "Headline Highlighter.app")
