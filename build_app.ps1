param(
    [switch]$SkipDependencyInstall,
    [switch]$InstallCpuPaddle,
    [switch]$CpuOnly,
    [string]$Python311InstallerUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe",
    [string]$LlamaCudaWheelIndex = "https://abetlen.github.io/llama-cpp-python/whl/cu124"
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
$LlamaLibDir = Join-Path $SitePackages "llama_cpp\lib"

function Find-Python311 {
    $Candidates = @(
        @{ Command = "py"; Args = @("-3.11") },
        @{ Command = "python"; Args = @() },
        @{ Command = "python3"; Args = @() },
        @{ Command = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"; Args = @() },
        @{ Command = Join-Path $env:ProgramFiles "Python311\python.exe"; Args = @() },
        @{ Command = Join-Path ${env:ProgramFiles(x86)} "Python311\python.exe"; Args = @() }
    )

    foreach ($Candidate in $Candidates) {
        try {
            $Command = $Candidate.Command
            if ($Command -like "*\*" -and -not (Test-Path $Command)) {
                continue
            }
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

function Install-Python311FromOfficialInstaller {
    $DownloadDir = Join-Path $RootDir ".build"
    New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null
    $InstallerPath = Join-Path $DownloadDir "python-3.11-amd64.exe"

    Write-Host "Downloading Python 3.11 installer from python.org..."
    Write-Host "  $Python311InstallerUrl"
    Invoke-WebRequest -Uri $Python311InstallerUrl -OutFile $InstallerPath

    try {
        $Signature = Get-AuthenticodeSignature -LiteralPath $InstallerPath
        if ($Signature.Status -ne "Valid") {
            Write-Warning "Python installer signature status is '$($Signature.Status)'. Continuing, but verify the installer if this is unexpected."
        } elseif ($Signature.SignerCertificate.Subject -notlike "*Python Software Foundation*") {
            Write-Warning "Python installer is signed, but signer was not recognized as Python Software Foundation: $($Signature.SignerCertificate.Subject)"
        }
    } catch {
        Write-Warning "Could not verify Python installer signature: $($_.Exception.Message)"
    }

    Write-Host "Installing Python 3.11 silently for the current user..."
    $Process = Start-Process -FilePath $InstallerPath -ArgumentList @(
        "/quiet",
        "InstallAllUsers=0",
        "PrependPath=1",
        "Include_launcher=1",
        "Include_pip=1",
        "Include_test=0",
        "Shortcuts=0"
    ) -Wait -PassThru
    if ($Process.ExitCode -ne 0) {
        throw "Python 3.11 installer failed with exit code $($Process.ExitCode)."
    }
}

function Ensure-Python311 {
    $Found = Find-Python311
    if ($Found) {
        return $Found
    }

    $Winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($Winget) {
        Write-Host "Python 3.11 was not found. Installing Python 3.11 with winget..."
        & winget install --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements

        $Found = Find-Python311
        if ($Found) {
            return $Found
        }

        Write-Warning "winget completed, but Python 3.11 was not found. Falling back to the official python.org installer."
    } else {
        Write-Host "Python 3.11 was not found and winget is unavailable. Falling back to the official python.org installer."
    }

    Install-Python311FromOfficialInstaller

    $Found = Find-Python311
    if (-not $Found) {
        throw "Python 3.11 install completed, but Python 3.11 is still not available. Open a new terminal and rerun build_app.ps1."
    }
    return $Found
}

function Test-NvidiaGpu {
    $Candidates = @("nvidia-smi")
    if ($env:SystemRoot) {
        $Candidates += (Join-Path $env:SystemRoot "System32\nvidia-smi.exe")
    }
    foreach ($Candidate in $Candidates) {
        try {
            & $Candidate --query-gpu=index --format=csv,noheader,nounits 1>$null 2>$null
            if ($LASTEXITCODE -eq 0) {
                return $true
            }
        } catch {
        }
    }
    return $false
}

function Test-PythonPackage {
    param([Parameter(Mandatory=$true)][string]$PackageName)
    & $Python -c "import importlib.metadata as md; raise SystemExit(0 if '$PackageName' in [d.metadata.get('Name','').lower() for d in md.distributions()] else 1)" 1>$null 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Install-CudaPythonPackages {
    if ($CpuOnly) {
        Write-Host "CPU-only build requested. Skipping CUDA Python package installation."
        return
    }

    Write-Host "Installing CUDA runtime Python packages used by the bundled app..."
    & $Python -m pip install --upgrade `
        nvidia-cublas-cu12 `
        nvidia-cuda-runtime-cu12 `
        nvidia-cudnn-cu12 `
        nvidia-cufft-cu12 `
        nvidia-curand-cu12 `
        nvidia-cusolver-cu12 `
        nvidia-cusparse-cu12 `
        nvidia-nvjitlink-cu12

    Write-Host "Installing CUDA-enabled llama-cpp-python wheel..."
    $LlamaCudaDll = Join-Path $LlamaLibDir "ggml-cuda.dll"
    if (Test-Path $LlamaCudaDll) {
        & $Python -m pip install --upgrade --prefer-binary --extra-index-url $LlamaCudaWheelIndex llama-cpp-python
    } else {
        & $Python -m pip install --upgrade --force-reinstall --prefer-binary --extra-index-url $LlamaCudaWheelIndex llama-cpp-python
    }
}

function Ensure-PaddleRuntime {
    $HasPaddle = & $Python -c "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('paddle') else 1)" 1>$null 2>$null
    $PaddleExists = ($LASTEXITCODE -eq 0)

    if ($CpuOnly -or $InstallCpuPaddle) {
        Write-Host "Installing CPU Paddle runtime."
        & $Python -m pip uninstall -y paddlepaddle-gpu 2>$null
        & $Python -m pip install --upgrade paddlepaddle
        return
    }

    if (Test-NvidiaGpu) {
        Write-Host "NVIDIA GPU detected. Installing GPU Paddle runtime."
        & $Python -m pip uninstall -y paddlepaddle 2>$null
        & $Python -m pip install --upgrade paddlepaddle-gpu
        return
    }

    if (-not $PaddleExists) {
        Write-Host "No NVIDIA GPU detected. Installing CPU Paddle runtime."
        & $Python -m pip install --upgrade paddlepaddle
    } else {
        Write-Host "Using existing Paddle runtime from the build environment."
    }
}

function Assert-GpuBuildInputs {
    if ($CpuOnly) {
        return
    }
    if (-not (Test-Path $NvidiaCudnnDir)) {
        throw "nvidia-cudnn-cu12 was not found after automatic installation: $NvidiaCudnnDir"
    }
    $LlamaCudaDll = Join-Path $LlamaLibDir "ggml-cuda.dll"
    if (-not (Test-Path $LlamaCudaDll)) {
        Write-Warning "CUDA llama.cpp DLL was not found: $LlamaCudaDll"
        Write-Warning "Translation may fall back to CPU unless the CUDA llama-cpp-python wheel is available for this Python/CUDA combination."
    }
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
        Install-CudaPythonPackages
    }

    Ensure-PaddleRuntime

    & $Python -m pip install --upgrade pyinstaller
    Assert-GpuBuildInputs

    $PyInstallerArgs = @(
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name", $AppName,
        "--icon", "assets\app.ico",
        "--add-data", "config.yaml;.",
        "--add-data", "LICENSE.md;.",
        "--add-data", "assets;assets",
        "--collect-all", "imagesize",
        "--copy-metadata", "imagesize",
        "--copy-metadata", "opencv-contrib-python",
        "--copy-metadata", "pyclipper",
        "--copy-metadata", "pypdfium2",
        "--copy-metadata", "python-bidi",
        "--copy-metadata", "shapely",
        "--collect-all", "PySide6",
        "--collect-all", "paddle",
        "--collect-all", "paddleocr",
        "--collect-all", "paddlex",
        "--collect-all", "llama_cpp",
        "--collect-all", "huggingface_hub",
        "--collect-all", "meikiocr",
        "--hidden-import", "paddle",
        "--hidden-import", "paddleocr",
        "--hidden-import", "paddlex",
        "--hidden-import", "llama_cpp",
        "--hidden-import", "huggingface_hub"
    )
    if (Test-Path $NvidiaCudnnDir) {
        $PyInstallerArgs += @("--add-data", "$NvidiaCudnnDir;nvidia\cudnn")
        if (Test-PythonPackage "nvidia-cudnn-cu12") {
            $PyInstallerArgs += @("--copy-metadata", "nvidia-cudnn-cu12")
        }
    }
    $PyInstallerArgs += @("app.py")

    & $Python -m PyInstaller @PyInstallerArgs

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
