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
$SitePackages = Join-Path $VenvDir "Lib\site-packages"
$NvidiaCudnnDir = Join-Path $SitePackages "nvidia\cudnn"

function Find-Python311 {
    $Candidates = @(
        @{ Command = "py"; Args = @("-3.11") },
        @{ Command = "python"; Args = @() },
        @{ Command = "python3"; Args = @() }
    )

    foreach ($Candidate in $Candidates) {
        try {
            $Command = $Candidate.Command
            $Args = $Candidate.Args
            $Version = & $Command @Args -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($LASTEXITCODE -eq 0 -and ($Version | Select-Object -First 1) -eq "3.11") {
                return $Candidate
            }
        } catch {
        }
    }
    return $null
}

function Ensure-Python311 {
    $Found = Find-Python311
    if ($Found) {
        return $Found
    }

    $Winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $Winget) {
        throw "Python 3.11 was not found and winget is unavailable. Install Python 3.11 or install winget, then rerun build_app.ps1."
    }

    Write-Host "Python 3.11 was not found. Installing Python 3.11 with winget..."
    & winget install --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements

    $Found = Find-Python311
    if (-not $Found) {
        throw "Python 3.11 install completed, but Python 3.11 is still not available in PATH. Open a new terminal and rerun build_app.ps1."
    }
    return $Found
}

if (-not (Test-Path $Python)) {
    Write-Host "Creating virtual environment: $VenvDir"
    $BootstrapPython = Ensure-Python311
    $BootstrapCommand = $BootstrapPython.Command
    $BootstrapArgs = $BootstrapPython.Args
    & $BootstrapCommand @BootstrapArgs -m venv $VenvDir
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
    if (-not (Test-Path $NvidiaCudnnDir)) {
        throw "nvidia-cudnn-cu12 was not found in the build environment: $NvidiaCudnnDir"
    }

    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --name $AppName `
        --icon "assets\app.ico" `
        --add-data "config.yaml;." `
        --add-data "LICENSE.md;." `
        --add-data "assets;assets" `
        --add-data "$NvidiaCudnnDir;nvidia\cudnn" `
        --collect-all imagesize `
        --copy-metadata imagesize `
        --copy-metadata nvidia-cudnn-cu12 `
        --copy-metadata opencv-contrib-python `
        --copy-metadata pyclipper `
        --copy-metadata pypdfium2 `
        --copy-metadata python-bidi `
        --copy-metadata shapely `
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
echo Boku No Translator model preload
echo.
echo This downloads and verifies the configured OCR and translation models.
echo First run can take a long time because the GGUF translation model is several GB.
echo Keep this window open until you see: "All configured models are ready."
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "`$exe = Join-Path (Get-Location) '$AppName.exe'; `$log = Join-Path `$env:LOCALAPPDATA 'boku-no-translator\logs\preload_models.log'; Remove-Item -LiteralPath `$log -Force -ErrorAction SilentlyContinue; `$p = Start-Process -FilePath `$exe -ArgumentList '--preload-models' -PassThru; `$last = 0; while (-not `$p.HasExited) { if (Test-Path `$log) { `$lines = Get-Content -LiteralPath `$log -ErrorAction SilentlyContinue; if (`$lines.Count -gt `$last) { `$lines[`$last..(`$lines.Count - 1)]; `$last = `$lines.Count } }; Start-Sleep -Seconds 1 }; if (Test-Path `$log) { `$lines = Get-Content -LiteralPath `$log -ErrorAction SilentlyContinue; if (`$lines.Count -gt `$last) { `$lines[`$last..(`$lines.Count - 1)] } }; exit `$p.ExitCode"
pause
"@ | Set-Content -LiteralPath $PreloadBat -Encoding ASCII

    $ReadmeFirst = Join-Path $DistDir "README_FIRST.txt"
    @"
Boku No Translator
==================

Quick start from ZIP
1. Run boku-no-translator.exe.
2. The app starts in the system tray.
3. Click the tray icon to open Settings / Usage / Language.
4. On first use, OCR and translation models may download automatically.

Recommended first run
- Run preload_models.bat once before using the overlay.
- It downloads and verifies PaddleOCR and the GGUF translation model.
- The translation model is several GB, so the first run can take a long time.

Device behavior
- OCR device and Translation device are separate settings.
- The app auto-detects NVIDIA GPUs.
- If one GPU exists, the default is gpu:0.
- If no GPU exists, the default is cpu.
- If an invalid device such as gpu:1 is found on a one-GPU machine, it is corrected to gpu:0.

Status overlay
- Green means ready on the shown device.
- Yellow means CPU fallback.
- Gray means loading/downloading.
- Red means failed. Check logs at:
  %LOCALAPPDATA%\boku-no-translator\logs\app.log

No Python is required for the ZIP package. Python and llama.cpp are bundled inside _internal.
"@ | Set-Content -LiteralPath $ReadmeFirst -Encoding UTF8

    Write-Host ""
    Write-Host "Build complete:"
    Write-Host "  $DistDir\$AppName.exe"
    Write-Host "  $PreloadBat"
    Write-Host ""
    Write-Host "App name: $DisplayName ($AppName)"
} finally {
    Pop-Location
}
