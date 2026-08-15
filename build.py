"""Build a standalone Headline Highlighter executable (Windows only)."""
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).parent
deps = ROOT / "vendor"
required = [deps / "ffmpeg.exe", deps / "tesseract" / "tesseract.exe", deps / "tesseract" / "tessdata" / "eng.traineddata"]
missing = [str(x) for x in required if not x.exists()]
if missing:
    raise SystemExit("Missing bundled runtime files:\n" + "\n".join(missing) + "\nRun scripts/download_tools.ps1 first.")
subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onefile", "--name", "HeadlineHighlighter",
                "--collect-all", "tkinterdnd2", "--add-binary", f"{deps / 'ffmpeg.exe'};.", "--add-data", f"{deps / 'tesseract'};tesseract", "headline_highlighter.py"], check=True, cwd=ROOT)
print(ROOT / "dist" / "HeadlineHighlighter.exe")
