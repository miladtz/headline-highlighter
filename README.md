# Headline Highlighter

A no-admin Windows desktop application that turns a screenshot into an MP4 with an animated, organic marker highlight over its headline.

Drop an image (or browse), accept the OCR-detected headline or type a text override, choose timing/color/output, then select **Generate Video**. The app stores the most recently used timing and color in the current user's AppData directory.

## Build

On Windows, install Python 3.12+, then run:

```powershell
python -m pip install -r requirements.txt
./scripts/download_tools.ps1
python build.py
```

The resulting `dist/HeadlineHighlighter.exe` is self-contained: FFmpeg, Tesseract and English OCR data are embedded by PyInstaller. The GitHub Actions workflow produces the same EXE as a downloadable artifact.
