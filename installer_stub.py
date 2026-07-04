import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


APP_NAME = "boku-no-translator"
DISPLAY_NAME = "Boku No Translator"
PAYLOAD_MARKER = b"\n__BOKU_NO_TRANSLATOR_PAYLOAD_ZIP_V1__\n"


def local_appdata() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base)
    return Path.home() / "AppData" / "Local"


def find_payload_offset(exe_path: Path) -> int:
    chunk_size = 1024 * 1024
    marker_tail = b""
    with exe_path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        while pos > 0:
            read_size = min(chunk_size, pos)
            pos -= read_size
            f.seek(pos)
            data = f.read(read_size) + marker_tail
            index = data.rfind(PAYLOAD_MARKER)
            if index >= 0:
                return pos + index + len(PAYLOAD_MARKER)
            marker_tail = data[: len(PAYLOAD_MARKER) - 1]
    raise RuntimeError("Installer payload marker was not found.")


def copy_payload_zip(exe_path: Path, payload_offset: int, temp_dir: Path) -> Path:
    payload_zip = temp_dir / f"{APP_NAME}-payload.zip"
    with exe_path.open("rb") as src, payload_zip.open("wb") as dst:
        src.seek(payload_offset)
        shutil.copyfileobj(src, dst, length=1024 * 1024 * 8)
    return payload_zip


def assert_safe_install_dir(install_dir: Path):
    programs_dir = (local_appdata() / "Programs").resolve(strict=False)
    resolved = install_dir.resolve(strict=False)
    resolved_text = str(resolved).lower()
    programs_text = str(programs_dir).lower()
    if not (resolved_text == programs_text or resolved_text.startswith(programs_text + os.sep)):
        raise RuntimeError(f"Refusing to install outside LOCALAPPDATA\\Programs: {resolved}")


def ps_quote(value: Path | str) -> str:
    text = str(value)
    return "'" + text.replace("'", "''") + "'"


def write_uninstaller(install_dir: Path) -> Path:
    uninstall_path = install_dir / "uninstall_boku_no_translator.ps1"
    uninstall_path.write_text(
        f"""$ErrorActionPreference = "Stop"
$AppName = "{APP_NAME}"
$DisplayName = "{DISPLAY_NAME}"
$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\\$AppName"
$ShortcutDir = Join-Path $env:APPDATA "Microsoft\\Windows\\Start Menu\\Programs"
Remove-Item -LiteralPath (Join-Path $ShortcutDir "$DisplayName.lnk") -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $ShortcutDir "$DisplayName - Download Models.lnk") -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $ShortcutDir "$DisplayName - Uninstall.lnk") -Force -ErrorAction SilentlyContinue
if (Test-Path $InstallDir) {{
    Remove-Item -LiteralPath $InstallDir -Recurse -Force
}}
Write-Host "$DisplayName was removed."
Write-Host "User data and downloaded models were kept at: $env:LOCALAPPDATA\\$AppName"
""",
        encoding="utf-8",
    )
    return uninstall_path


def create_shortcuts(install_dir: Path, uninstall_path: Path):
    exe_path = install_dir / f"{APP_NAME}.exe"
    preload_path = install_dir / "preload_models.bat"
    shortcut_dir = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    shortcut_dir.mkdir(parents=True, exist_ok=True)

    script = f"""
$ErrorActionPreference = "Stop"
$Shell = New-Object -ComObject WScript.Shell
$ShortcutDir = {ps_quote(shortcut_dir)}
$Main = $Shell.CreateShortcut((Join-Path $ShortcutDir "{DISPLAY_NAME}.lnk"))
$Main.TargetPath = {ps_quote(exe_path)}
$Main.WorkingDirectory = {ps_quote(install_dir)}
$Main.WindowStyle = 7
$Main.Description = "{DISPLAY_NAME}"
$Main.Hotkey = "CTRL+ALT+A"
$Main.Save()

if (Test-Path {ps_quote(preload_path)}) {{
    $Preload = $Shell.CreateShortcut((Join-Path $ShortcutDir "{DISPLAY_NAME} - Download Models.lnk"))
    $Preload.TargetPath = {ps_quote(preload_path)}
    $Preload.WorkingDirectory = {ps_quote(install_dir)}
    $Preload.WindowStyle = 1
    $Preload.Description = "{DISPLAY_NAME} model downloader"
    $Preload.Save()
}}

$Uninstall = $Shell.CreateShortcut((Join-Path $ShortcutDir "{DISPLAY_NAME} - Uninstall.lnk"))
$UninstallScriptPath = {ps_quote(uninstall_path)}
$Uninstall.TargetPath = "powershell.exe"
$Uninstall.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$UninstallScriptPath`""
$Uninstall.WorkingDirectory = {ps_quote(install_dir)}
$Uninstall.Description = "Uninstall {DISPLAY_NAME}"
$Uninstall.Save()
"""
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=True,
    )


def install():
    install_dir = local_appdata() / "Programs" / APP_NAME
    data_dir = local_appdata() / APP_NAME
    assert_safe_install_dir(install_dir)

    print(f"Installing {DISPLAY_NAME}")
    print(f"  App:  {install_dir}")
    print(f"  Data: {data_dir}")

    with tempfile.TemporaryDirectory(prefix=f"{APP_NAME}-installer-") as tmp:
        temp_dir = Path(tmp)
        exe_path = Path(sys.executable).resolve()
        payload_offset = find_payload_offset(exe_path)
        payload_zip = copy_payload_zip(exe_path, payload_offset, temp_dir)

        if install_dir.exists():
            shutil.rmtree(install_dir)
        install_dir.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)
        for rel in (
            "models/huggingface",
            "models/paddlex",
            "models/paddle",
            "models/paddleocr",
            "demo_capture/screenshots",
            "demo_capture/videos",
        ):
            (data_dir / rel).mkdir(parents=True, exist_ok=True)

        print("Extracting app files...")
        with zipfile.ZipFile(payload_zip, "r") as archive:
            archive.extractall(install_dir)

    uninstall_path = write_uninstaller(install_dir)
    create_shortcuts(install_dir, uninstall_path)

    print("")
    print("Installed successfully.")
    print("Start Menu shortcut: Boku No Translator")
    print("Launch hotkey: Ctrl+Alt+A")
    print("")
    print("Runtime dependencies are bundled in the packaged app.")
    print("OCR and GGUF models are downloaded automatically on first use.")
    print("To download configured models now, run: Boku No Translator - Download Models")


def main():
    quiet = "--quiet" in sys.argv
    try:
        install()
    except Exception as exc:
        print("")
        print(f"Installation failed: {exc}")
        if not quiet:
            input("Press Enter to close...")
        return 1

    if not quiet:
        input("Press Enter to close...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
