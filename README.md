<p align="center">
  <img src="./assets/app-icon.png" width="92" alt="Boku No Translator icon">
</p>

<h1 align="center">Boku No Translator</h1>

<p align="center">
  Real-time translator for games powered by AI model.
</p>

<p align="center">
  <img src="./assets/readme-hero.png" width="100%" alt="Boku No Translator banner">
</p>

<p align="center">
  <img alt="Windows" src="https://img.shields.io/badge/Windows-10%2F11-2563eb">
  <img alt="OCR" src="https://img.shields.io/badge/OCR-PaddleOCR-0f766e">
  <img alt="Translation" src="https://img.shields.io/badge/Translation-GGUF-7c3aed">
  <img alt="Hotkey" src="https://img.shields.io/badge/Hotkey-Ctrl%2BAlt%2BA-f59e0b">
  <img alt="License" src="https://img.shields.io/badge/License-Non--Commercial-red">
</p>

## Demo

<p align="center">
  <img src="./assets/ocr-overlay-demo.gif" width="100%" alt="Boku No Translator OCR overlay demo">
</p>

## What It Does

Boku No Translator watches a selected window, detects text with OCR, translates sentence groups, and draws the translated text back as an overlay. It is built for repeated use while playing or testing, so it starts in the system tray and stays out of the way until you need the control panel.

## Highlights

- Tray-first Windows app with `Ctrl+Alt+A` quick launch
- Window-following OCR overlay for games and desktop apps
- Source and target language controls separate from app UI language
- PaddleOCR detection/recognition settings exposed in the UI
- GGUF translation model support with automatic Hugging Face download
- Screenshot and MP4 demo capture with the overlay composited in
- Vertical Japanese reading order and bottom subtitle support for English translation

## Powered By

Boku No Translator builds on local OCR and local GGUF inference:

- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) for text detection and recognition.
- [llama-cpp-python](https://github.com/abetlen/llama-cpp-python) for local GGUF model inference.
- [HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive](https://huggingface.co/HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive) as the default translation model repository.
- Default GGUF file: [`Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf`](https://huggingface.co/HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive/blob/main/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf).

## Requirements

For the packaged installer:

- Windows 10 or Windows 11
- Internet connection for first-time OCR/model downloads
- Several GB of free disk space for the app, OCR cache, and GGUF model cache
- NVIDIA GPU is recommended for faster OCR/translation, but CPU mode can be selected in Settings

For running or building from source:

- Python 3.11 recommended
- Dependencies listed in [requirements.txt](./requirements.txt)
- Paddle runtime installed in the build environment; the build script keeps an existing GPU Paddle runtime or installs CPU `paddlepaddle` when none is detected

## Install

Use the generated installer:

```text
release\boku-no-translator-setup.exe
```

The installer places the app here:

```text
%LOCALAPPDATA%\Programs\boku-no-translator
```

Writable app data, config, screenshots, videos, translation cache, and model caches are stored here:

```text
%LOCALAPPDATA%\boku-no-translator
```

## Hotkeys

| Hotkey | Action |
| --- | --- |
| `Ctrl+Alt+A` | Show or hide the control panel |
| `Ctrl+Alt+S` | Save a screenshot of the target window with overlay |
| `Ctrl+Alt+R` | Start or stop MP4 recording with overlay |

Hotkeys can be changed from the Settings tab.

## Models

When `translation_model_path` is empty, the default GGUF model is downloaded automatically from [HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive](https://huggingface.co/HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive).

Default file:

```text
Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf
```

PaddleOCR/PaddleX OCR models are also downloaded automatically when OCR initializes. To pre-download configured models after install, run:

```powershell
boku-no-translator.exe --preload-models
```

The installed app also creates a Start Menu shortcut named `Boku No Translator - Download Models`.

## Support

If Boku No Translator saves you time or helps your setup, support is welcome:

<p>
  <a href="https://buymeacoffee.com/dubudubap">
    <img alt="Buy Me a Coffee" src="https://img.shields.io/badge/Support-Buy%20Me%20a%20Coffee-ffdd00?style=for-the-badge&labelColor=111827&color=ffdd00">
  </a>
</p>

Supporters who want to be credited can leave a display name or message. When the project reaches an official release, supporter names may be added to a `Supporters` section in this README.

## Supporters

Official release supporters will be listed here. Listing is opt-in, so only names or handles provided for credit will be added.

## Build

Build the packaged app:

```powershell
.\build_app.ps1
```

If this makes an error with ErrorID which includes UnauthorizedAccess, try:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Scope Process
```

If this makes an error with ErrorID which includes Python, try to install python 3.11

Create the installer:

```powershell
.\build_installer.ps1
```

The app executable is created at:

```text
dist\boku-no-translator\boku-no-translator.exe
```

The installer is created at:

```text
release\boku-no-translator-setup.exe
```

## License

Boku No Translator is source-available for non-commercial use only. Commercial use, resale, paid service use, or inclusion in commercial products is not permitted without separate written permission.

See [LICENSE.md](./LICENSE.md) for the full project license. Third-party libraries, runtimes, and downloaded models remain under their own licenses.
