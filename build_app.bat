@echo off
setlocal
cd /d "%~dp0"
set "BOKU_APP_DIR=%~dp0"

title Boku No Translator Builder
echo Boku No Translator one-click builder
echo.
echo This will build the packaged app into:
echo   %~dp0dist\boku-no-translator
echo.
echo GPU runtime is selected automatically:
echo   RTX 50 / Blackwell  - Paddle CUDA 12.9, llama.cpp CUDA source build
echo   RTX 20/30/40 series - Paddle CUDA 12.6, llama.cpp CUDA auto mode
echo   GTX 10 / Tesla P/V  - Paddle CUDA 11.8, llama.cpp CUDA auto mode
echo.
echo Keep this window open. The first build can take a long time.
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop'; try { Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force } catch { Write-Warning ('Could not set process execution policy, continuing with powershell.exe -ExecutionPolicy Bypass: ' + $_.Exception.Message) }; Set-Location -LiteralPath $env:BOKU_APP_DIR; $buildScript = Join-Path (Get-Location) 'build_app.ps1'; if (-not (Test-Path -LiteralPath $buildScript)) { throw 'build_app.ps1 was not found. Put build_app.bat in the same folder as the source files.' }; & $buildScript -PaddleGpuRuntime auto -LlamaCudaInstallMode auto -RequireLlamaCuda; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; $dist = Join-Path (Get-Location) 'dist\boku-no-translator'; $exe = Join-Path $dist 'boku-no-translator.exe'; $preload = Join-Path $dist 'preload_models.bat'; Write-Host ''; Write-Host 'Build finished.'; Write-Host ''; Write-Host ('Output folder: ' + $dist); if (Test-Path -LiteralPath $preload) { Write-Host ('First, run:    ' + $preload) } else { Write-Warning ('Expected preload helper was not found: ' + $preload) }; if (Test-Path -LiteralPath $exe) { Write-Host ('Then, run:     ' + $exe) } else { Write-Warning ('Expected app executable was not found: ' + $exe) }; Write-Host ''; Write-Host 'You can zip or share the whole dist\boku-no-translator folder after the build completes.'"

set "BOKU_BUILD_EXIT=%ERRORLEVEL%"
echo.
if not "%BOKU_BUILD_EXIT%"=="0" (
  echo Build failed. Read the error messages above, then run build_app.bat again after fixing them.
) else (
  echo Done. The app is in dist\boku-no-translator.
)
echo.
pause
exit /b %BOKU_BUILD_EXIT%
