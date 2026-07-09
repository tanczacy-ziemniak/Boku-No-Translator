<p align="center">
  <img src="./assets/app-icon.png" width="92" alt="Boku No Translator icon">
</p>

<h1 align="center">Boku No Translator</h1>

<p align="center">
  A tray-first OCR translation overlay for games, visual novels, and live app windows.
</p>

<p align="center">
  Don't forget to Star ⭐️ this repository to keep track of updates!
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

## Description
Real-time translator for games powered by local OCR and a local AI translation model.

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
- Separate OCR and translation device selection with automatic GPU detection
- Screenshot and MP4 demo capture with the overlay composited in
- Vertical Japanese reading order and bottom subtitle support for English translation

## Powered By

Boku No Translator builds on local OCR and local GGUF inference:

- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) for text detection and recognition.
- [llama-cpp-python](https://github.com/abetlen/llama-cpp-python) for local GGUF model inference.
- [HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive](https://huggingface.co/HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive) as the default translation model repository.
- Default GGUF file: [`Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf`](https://huggingface.co/HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive/blob/main/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf).

## Requirements

For the packaged ZIP or installer:

- Windows 10 or Windows 11
- Internet connection for first-time OCR/model downloads
- Several GB of free disk space for the app, OCR cache, and GGUF model cache
- NVIDIA GPU is recommended for faster OCR/translation, but CPU mode can be selected in Settings
- Python, venv, and llama.cpp do not need to be installed separately for the packaged ZIP. They are bundled.

For running or building from source:

- Python 3.11 recommended
- Dependencies listed in [requirements.txt](./requirements.txt)
- Paddle runtime is selected by `build_app.ps1` from the detected NVIDIA GPU compute capability

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

## How to use
Build the packaged app from the source ZIP:

1. Extract the ZIP first. Do not run it from inside the compressed folder.
2. Open PowerShell and change to the extracted folder (run as Administrator if you want the script to install Python via winget). For example:
```cd "C:\path\to\extracted\Boku-No-Translator"```
3. Temporarily allow script execution for this session and start the build:
```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
.\build_app.ps1 -LlamaCudaInstallMode source -RequireLlamaCuda
```
4. After the build completes, preload models and verify they load correctly:
```powershell
cd .\dist\boku-no-translator
.\preload_models.bat
```
5. If preloading succeeds, run the packaged app:
```powershell
.\boku-no-translator.exe
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

The ZIP also includes `preload_models.bat`, which runs the same command and keeps the console open so you can see progress and errors.


## Devices

The app auto-detects NVIDIA GPUs at startup:

- One GPU: defaults to `gpu:0`
- Multiple GPUs: defaults to `gpu:0`
- No GPU: defaults to `cpu`
- Invalid stale values such as `gpu:1` on a one-GPU machine are corrected to `gpu:0`

OCR and translation can be assigned separately in Settings:

- `OCR device`
- `Translation device`

The overlay status tells you what is actually connected:

- Green: ready on the shown device
- Yellow: CPU fallback
- Gray: loading or downloading
- Red: failed, check `%LOCALAPPDATA%\boku-no-translator\logs\app.log`



## License

Boku No Translator is source-available for non-commercial use only. Commercial use, resale, paid service use, or inclusion in commercial products is not permitted without separate written permission.

See [LICENSE.md](./LICENSE.md) for the full project license. Third-party libraries, runtimes, and downloaded models remain under their own licenses.
