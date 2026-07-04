param(
    [switch]$SkipAppBuild
)

$ErrorActionPreference = "Stop"

$AppName = "boku-no-translator"
$DisplayName = "Boku No Translator"
$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $AppDir
$Python = Join-Path $RootDir ".venv\Scripts\python.exe"
$DistDir = Join-Path $AppDir "dist\$AppName"
$ReleaseDir = Join-Path $AppDir "release"
$PayloadDir = Join-Path $AppDir "build\installer_payload"
$PayloadZip = Join-Path $PayloadDir "$AppName-package.zip"
$SetupExe = Join-Path $ReleaseDir "$AppName-setup.exe"

if (-not $SkipAppBuild) {
    & (Join-Path $AppDir "build_app.ps1")
}

if (-not (Test-Path (Join-Path $DistDir "$AppName.exe"))) {
    throw "Packaged app was not found. Run build_app.ps1 first: $DistDir\$AppName.exe"
}
if (-not (Test-Path $Python)) {
    throw "Build Python was not found: $Python"
}

New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
Get-ChildItem -LiteralPath $ReleaseDir -Filter "~$AppName-setup.*" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
if (Test-Path $PayloadDir) {
    Remove-Item -LiteralPath $PayloadDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $PayloadDir | Out-Null

Compress-Archive -Path (Join-Path $DistDir "*") -DestinationPath $PayloadZip -Force

$InstallPs1 = Join-Path $PayloadDir "install_package.ps1"
$InstallCmd = Join-Path $PayloadDir "install.cmd"

@"
`$ErrorActionPreference = "Stop"

`$AppName = "$AppName"
`$DisplayName = "$DisplayName"
`$InstallDir = Join-Path `$env:LOCALAPPDATA "Programs\`$AppName"
`$DataDir = Join-Path `$env:LOCALAPPDATA "`$AppName"
`$PackageZip = Join-Path `$PSScriptRoot "`$AppName-package.zip"
`$ProgramsRoot = [System.IO.Path]::GetFullPath((Join-Path `$env:LOCALAPPDATA "Programs"))
`$ResolvedInstallDir = [System.IO.Path]::GetFullPath(`$InstallDir)

if (-not `$ResolvedInstallDir.StartsWith(`$ProgramsRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to install outside LOCALAPPDATA\Programs: `$ResolvedInstallDir"
}

Write-Host "Installing `$DisplayName..."
Write-Host "  App:  `$InstallDir"
Write-Host "  Data: `$DataDir"

if (Test-Path `$InstallDir) {
    Remove-Item -LiteralPath `$InstallDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path `$InstallDir | Out-Null
New-Item -ItemType Directory -Force -Path `$DataDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path `$DataDir "models\huggingface") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path `$DataDir "models\paddlex") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path `$DataDir "models\paddle") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path `$DataDir "models\paddleocr") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path `$DataDir "demo_capture\screenshots") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path `$DataDir "demo_capture\videos") | Out-Null

Expand-Archive -LiteralPath `$PackageZip -DestinationPath `$InstallDir -Force

`$ShortcutDir = Join-Path `$env:APPDATA "Microsoft\Windows\Start Menu\Programs"
New-Item -ItemType Directory -Force -Path `$ShortcutDir | Out-Null

`$Shell = New-Object -ComObject WScript.Shell
`$ShortcutPath = Join-Path `$ShortcutDir "`$DisplayName.lnk"
`$Shortcut = `$Shell.CreateShortcut(`$ShortcutPath)
`$Shortcut.TargetPath = Join-Path `$InstallDir "`$AppName.exe"
`$Shortcut.WorkingDirectory = `$InstallDir
`$Shortcut.WindowStyle = 7
`$Shortcut.Description = `$DisplayName
`$Shortcut.Hotkey = "CTRL+ALT+A"
`$Shortcut.Save()

`$PreloadBat = Join-Path `$InstallDir "preload_models.bat"
if (Test-Path `$PreloadBat) {
    `$PreloadShortcut = `$Shell.CreateShortcut((Join-Path `$ShortcutDir "`$DisplayName - Download Models.lnk"))
    `$PreloadShortcut.TargetPath = `$PreloadBat
    `$PreloadShortcut.WorkingDirectory = `$InstallDir
    `$PreloadShortcut.WindowStyle = 1
    `$PreloadShortcut.Description = "`$DisplayName model downloader"
    `$PreloadShortcut.Save()
}

`$UninstallScriptPath = Join-Path `$InstallDir "uninstall_boku_no_translator.ps1"
@'
`$ErrorActionPreference = "Stop"
`$AppName = "boku-no-translator"
`$DisplayName = "Boku No Translator"
`$InstallDir = Join-Path `$env:LOCALAPPDATA "Programs\`$AppName"
`$ShortcutDir = Join-Path `$env:APPDATA "Microsoft\Windows\Start Menu\Programs"
Remove-Item -LiteralPath (Join-Path `$ShortcutDir "`$DisplayName.lnk") -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path `$ShortcutDir "`$DisplayName - Download Models.lnk") -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path `$ShortcutDir "`$DisplayName - Uninstall.lnk") -Force -ErrorAction SilentlyContinue
if (Test-Path `$InstallDir) {
    Remove-Item -LiteralPath `$InstallDir -Recurse -Force
}
Write-Host "`$DisplayName was removed."
Write-Host "User data and downloaded models were kept at: `$env:LOCALAPPDATA\`$AppName"
'@ | Set-Content -LiteralPath `$UninstallScriptPath -Encoding UTF8

`$UninstallShortcut = `$Shell.CreateShortcut((Join-Path `$ShortcutDir "`$DisplayName - Uninstall.lnk"))
`$UninstallShortcut.TargetPath = "powershell.exe"
`$UninstallShortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File ```"`$UninstallScriptPath```""
`$UninstallShortcut.WorkingDirectory = `$InstallDir
`$UninstallShortcut.Description = "Uninstall `$DisplayName"
`$UninstallShortcut.Save()

Write-Host ""
Write-Host "Installed successfully."
Write-Host "Start Menu shortcut: `$ShortcutPath"
Write-Host "Launch hotkey: Ctrl+Alt+A"
Write-Host ""
Write-Host "Models are downloaded automatically on first OCR/translation use."
Write-Host "To download configured models now, run the Start Menu shortcut: `$DisplayName - Download Models"
"@ | Set-Content -LiteralPath $InstallPs1 -Encoding UTF8

@"
@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_package.ps1"
if errorlevel 1 (
  echo.
  echo Installation failed.
  pause
  exit /b 1
)
echo.
echo Installation complete.
pause
"@ | Set-Content -LiteralPath $InstallCmd -Encoding ASCII

Copy-Item -LiteralPath $PayloadZip -Destination (Join-Path $ReleaseDir "$AppName-package.zip") -Force
Copy-Item -LiteralPath $InstallPs1 -Destination (Join-Path $ReleaseDir "install_$($AppName.Replace('-', '_')).ps1") -Force

& $Python -m pip install --upgrade pyinstaller

$StubDistDir = Join-Path $PayloadDir "stub_dist"
$StubWorkDir = Join-Path $PayloadDir "stub_build"
$StubSpecDir = Join-Path $PayloadDir "stub_spec"
$StubName = "$AppName-setup-stub"
$StubExe = Join-Path $StubDistDir "$StubName.exe"

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --console `
    --name $StubName `
    --icon (Join-Path $AppDir "assets\app.ico") `
    --distpath $StubDistDir `
    --workpath $StubWorkDir `
    --specpath $StubSpecDir `
    (Join-Path $AppDir "installer_stub.py")

if (-not (Test-Path $StubExe)) {
    throw "Installer stub was not created: $StubExe"
}

function Copy-FileToStream {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][System.IO.Stream]$OutputStream
    )
    $InputStream = [System.IO.File]::OpenRead($Path)
    try {
        $Buffer = New-Object byte[] (1024 * 1024 * 8)
        while (($Read = $InputStream.Read($Buffer, 0, $Buffer.Length)) -gt 0) {
            $OutputStream.Write($Buffer, 0, $Read)
        }
    } finally {
        $InputStream.Dispose()
    }
}

if (Test-Path $SetupExe) {
    Remove-Item -LiteralPath $SetupExe -Force
}

$Marker = [System.Text.Encoding]::ASCII.GetBytes("`n__BOKU_NO_TRANSLATOR_PAYLOAD_ZIP_V1__`n")
$Output = [System.IO.File]::Open($SetupExe, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
try {
    Copy-FileToStream -Path $StubExe -OutputStream $Output
    $Output.Write($Marker, 0, $Marker.Length)
    Copy-FileToStream -Path $PayloadZip -OutputStream $Output
} finally {
    $Output.Dispose()
}

if (-not (Test-Path $SetupExe)) {
    throw "Setup executable was not created: $SetupExe"
}

Write-Host ""
Write-Host "Installer complete:"
Write-Host "  $SetupExe"
Write-Host ""
Write-Host "Fallback package files are also available in:"
Write-Host "  $ReleaseDir"
