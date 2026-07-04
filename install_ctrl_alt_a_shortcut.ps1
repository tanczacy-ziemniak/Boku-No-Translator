$ErrorActionPreference = "Stop"

$AppName = "boku-no-translator"
$DisplayName = "Boku No Translator"
$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ExePath = Join-Path $AppDir "dist\$AppName\$AppName.exe"
$RunBat = Join-Path $AppDir "run_app.bat"

if (Test-Path $ExePath) {
    $Target = $ExePath
    $WorkingDirectory = Split-Path -Parent $ExePath
} else {
    $Target = $RunBat
    $WorkingDirectory = $AppDir
}

$ShortcutDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$ShortcutPath = Join-Path $ShortcutDir "$DisplayName.lnk"

$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $Target
$Shortcut.WorkingDirectory = $WorkingDirectory
$Shortcut.WindowStyle = 7
$Shortcut.Description = $DisplayName
$Shortcut.Hotkey = "CTRL+ALT+A"
$Shortcut.Save()

Write-Host "Shortcut installed:"
Write-Host "  $ShortcutPath"
Write-Host "Hotkey:"
Write-Host "  Ctrl+Alt+A"
