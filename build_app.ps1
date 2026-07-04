param(
    [switch]$SkipDependencyInstall,
    [switch]$InstallCpuPaddle
)

$ErrorActionPreference = "Stop"

$AppName = "boku-no-translator"
$DisplayName = "Boku No Translator"
$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $AppDir
$VenvDir = Join-Path $RootDir ".venv"
$Python = Join-Path $VenvDir "Scripts\python.exe"

if (-not (Test-Path $Python)) {
    Write-Host "Creating virtual environment: $VenvDir"
    python -m venv $VenvDir
}

if (-not (Test-Path $Python)) {
    throw "Python venv was not created: $Python"
}

Push-Location $AppDir
try {
    & $Python -m pip install --upgrade pip

    if (-not $SkipDependencyInstall) {
        & $Python -m pip install -r requirements.txt
    }

    $paddleCheck = & $Python -c "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('paddle') else 1)"
    if ($LASTEXITCODE -ne 0 -or $InstallCpuPaddle) {
        Write-Host "Installing CPU Paddle runtime because no Paddle runtime was detected or -InstallCpuPaddle was requested."
        & $Python -m pip install --upgrade paddlepaddle
    } else {
        Write-Host "Using existing Paddle runtime from the build environment."
    }

    & $Python -m pip install --upgrade pyinstaller

    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --name $AppName `
        --icon "assets\app.ico" `
        --add-data "config.yaml;." `
        --add-data "LICENSE.md;." `
        --add-data "assets;assets" `
        --collect-all PySide6 `
        --collect-all paddle `
        --collect-all paddleocr `
        --collect-all paddlex `
        --collect-all llama_cpp `
        --collect-all huggingface_hub `
        --collect-all meikiocr `
        --hidden-import paddle `
        --hidden-import paddleocr `
        --hidden-import paddlex `
        --hidden-import llama_cpp `
        --hidden-import huggingface_hub `
        app.py

    $DistDir = Join-Path $AppDir "dist\$AppName"
    $PreloadBat = Join-Path $DistDir "preload_models.bat"
    @"
@echo off
cd /d "%~dp0"
"%~dp0$AppName.exe" --preload-models
pause
"@ | Set-Content -LiteralPath $PreloadBat -Encoding ASCII

    Write-Host ""
    Write-Host "Build complete:"
    Write-Host "  $DistDir\$AppName.exe"
    Write-Host "  $PreloadBat"
    Write-Host ""
    Write-Host "App name: $DisplayName ($AppName)"
} finally {
    Pop-Location
}
