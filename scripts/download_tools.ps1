$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$root = Split-Path -Parent $PSScriptRoot
$vendor = Join-Path $root 'vendor'
New-Item -ItemType Directory -Force -Path $vendor | Out-Null

# Pinned, redistributable Windows builds. Downloads occur only while building.
$ffmpegZip = Join-Path $env:TEMP 'ffmpeg.zip'
Invoke-WebRequest 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile $ffmpegZip
Expand-Archive -Force $ffmpegZip (Join-Path $env:TEMP 'ffmpeg-unpack')
$ffmpeg = Get-ChildItem (Join-Path $env:TEMP 'ffmpeg-unpack') -Recurse -Filter ffmpeg.exe | Select-Object -First 1
Copy-Item $ffmpeg.FullName (Join-Path $vendor 'ffmpeg.exe') -Force

$tesseractInstaller = Join-Path $env:TEMP 'tesseract-installer.exe'
Invoke-WebRequest 'https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe' -OutFile $tesseractInstaller

$tesseractDir = Join-Path $vendor 'tesseract'
Start-Process -FilePath $tesseractInstaller -ArgumentList @('/S', "/D=$tesseractDir") -Wait

# The NSIS installer can finish its parent process before copied files appear.
$exePath = Join-Path $tesseractDir 'tesseract.exe'
for ($attempt = 1; $attempt -le 30 -and -not (Test-Path $exePath); $attempt++) {
    Start-Sleep -Seconds 2
}

if (-not (Test-Path $exePath)) {
    Get-ChildItem -Recurse -Force $vendor | Select-Object FullName
    throw 'Tesseract installation finished, but tesseract.exe was not found.'
}
