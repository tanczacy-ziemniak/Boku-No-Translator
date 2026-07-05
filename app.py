
import ctypes
from ctypes import wintypes
import hashlib
import json
import multiprocessing as mp
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
import unicodedata
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import cv2
import mss
import numpy as np
import yaml
from PIL import Image
from PySide6.QtCore import Qt, QTimer, QPoint, QPointF, QRectF, QAbstractNativeEventFilter
from PySide6.QtGui import QAction, QColor, QIcon, QImage, QPainter, QPen, QPixmap, QPolygonF, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSpinBox,
    QStyle,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


APP_SLUG = "boku-no-translator"
APP_DISPLAY_NAME = "Boku No Translator"


def resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


def executable_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def local_app_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / APP_SLUG
    return Path.home() / ".local" / "share" / APP_SLUG


APP_DIR = executable_dir()
RESOURCE_DIR = resource_dir()
USER_DATA_DIR = local_app_data_dir()
DEFAULT_CONFIG_PATH = RESOURCE_DIR / "config.yaml"
CONFIG_PATH = USER_DATA_DIR / "config.yaml"
BBOX_CONFIG_PATH = DEFAULT_CONFIG_PATH
MODEL_CACHE_DIR = USER_DATA_DIR / "models"
LOG_DIR = USER_DATA_DIR / "logs"
APP_LOG_PATH = LOG_DIR / "app.log"
PRELOAD_LOG_PATH = LOG_DIR / "preload_models.log"
STDOUT_LOG_PATH = LOG_DIR / "stdout.log"
STDERR_LOG_PATH = LOG_DIR / "stderr.log"
APP_ICON_PATH = RESOURCE_DIR / "assets" / "app.ico"
DEFAULT_JAPANESE_AUX_SYMBOL_CHARS = (
    "「 」 『 』 、。【 】 〈 〉 《 》 〔 〕 ［ ］ ｛ ｝"
)
DLL_DIRECTORY_HANDLES = []
PRELOADED_DLL_HANDLES = []


class SafeLogStream:
    encoding = "utf-8"
    errors = "replace"

    def __init__(self, path: Path):
        self.path = Path(path)

    def write(self, text):
        if text is None:
            return 0
        text = str(text)
        if not text:
            return 0
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return False


def ensure_stdio_streams():
    if sys.stdout is None:
        sys.stdout = SafeLogStream(STDOUT_LOG_PATH)
    if sys.stderr is None:
        sys.stderr = SafeLogStream(STDERR_LOG_PATH)


def log_message(message: str):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with APP_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass


def preload_message(message: str):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with PRELOAD_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"{message}\n")
    except Exception:
        pass
    try:
        print(message, flush=True)
    except Exception:
        pass


def configure_runtime_environment():
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ensure_stdio_streams()
    os.environ.setdefault("HF_HOME", str(MODEL_CACHE_DIR / "huggingface"))
    os.environ.setdefault("HF_HUB_CACHE", str(MODEL_CACHE_DIR / "huggingface" / "hub"))
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(MODEL_CACHE_DIR / "paddlex"))
    os.environ.setdefault("PADDLE_HOME", str(MODEL_CACHE_DIR / "paddle"))
    os.environ.setdefault("PADDLEOCR_HOME", str(MODEL_CACHE_DIR / "paddleocr"))
    for cache_dir in (
        Path(os.environ["HF_HOME"]),
        Path(os.environ["HF_HUB_CACHE"]),
        Path(os.environ["PADDLE_PDX_CACHE_HOME"]),
        Path(os.environ["PADDLE_HOME"]),
        Path(os.environ["PADDLEOCR_HOME"]),
    ):
        cache_dir.mkdir(parents=True, exist_ok=True)


def huggingface_hub_cache_dirs() -> list[Path]:
    candidates = []
    hf_hub_cache = os.environ.get("HF_HUB_CACHE")
    if hf_hub_cache:
        candidates.append(Path(hf_hub_cache))

    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        candidates.append(Path(hf_home) / "hub")

    candidates.append(MODEL_CACHE_DIR / "huggingface" / "hub")
    candidates.append(Path.home() / ".cache" / "huggingface" / "hub")

    unique = []
    seen = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except Exception:
            resolved = candidate.expanduser()
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(resolved)
    return unique


def find_cached_hf_file(repo_id: str, filename: str) -> Path | None:
    repo_id = str(repo_id or "").strip()
    filename = str(filename or "").strip().replace("\\", "/")
    if not repo_id or not filename:
        return None

    repo_cache_name = "models--" + repo_id.replace("/", "--")
    filename_parts = [part for part in filename.split("/") if part]
    if not filename_parts:
        return None

    for hub_dir in huggingface_hub_cache_dirs():
        snapshots_dir = hub_dir / repo_cache_name / "snapshots"
        if not snapshots_dir.is_dir():
            continue
        for snapshot_dir in snapshots_dir.iterdir():
            candidate = snapshot_dir.joinpath(*filename_parts)
            try:
                if candidate.is_file() and candidate.stat().st_size > 0:
                    return candidate
            except Exception:
                continue
    return None


def add_dll_search_dir(path: Path):
    if sys.platform != "win32":
        return
    path = Path(path)
    if not path.exists() or not path.is_dir():
        return
    normalized = str(path.resolve())
    if any(existing == normalized for existing, _ in DLL_DIRECTORY_HANDLES):
        return
    try:
        handle = os.add_dll_directory(normalized)
        DLL_DIRECTORY_HANDLES.append((normalized, handle))
    except Exception:
        return
    current_path = os.environ.get("PATH", "")
    if normalized.lower() not in [part.lower() for part in current_path.split(os.pathsep) if part]:
        os.environ["PATH"] = normalized + os.pathsep + current_path


def llama_cpp_runtime_roots() -> list[Path]:
    roots = [
        Path(sys.prefix) / "Lib" / "site-packages",
        RESOURCE_DIR,
        APP_DIR,
        APP_DIR / "_internal",
        executable_dir(),
        executable_dir() / "_internal",
        Path(__file__).resolve().parent,
        Path(__file__).resolve().parent / "_internal",
    ]
    unique = []
    seen = set()
    for root in roots:
        try:
            resolved = root.resolve()
        except Exception:
            resolved = root
        key = str(resolved).lower()
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


NVIDIA_ACCELERATOR_DLL_SUBDIRS = (
    "bin",
    "PySide6",
    "shiboken6",
    "nvidia/cublas/bin",
    "nvidia/cuda_runtime/bin",
    "nvidia/cudnn/bin",
    "nvidia/cufft/bin",
    "nvidia/curand/bin",
    "nvidia/cusolver/bin",
    "nvidia/cusparse/bin",
    "nvidia/nvjitlink/bin",
)


def configure_accelerator_dlls():
    if sys.platform != "win32":
        return

    roots = llama_cpp_runtime_roots()
    for root in roots:
        for rel in NVIDIA_ACCELERATOR_DLL_SUBDIRS:
            add_dll_search_dir(root / rel)


def configure_llama_cpp_dlls():
    if sys.platform != "win32":
        return

    configure_accelerator_dlls()

    roots = llama_cpp_runtime_roots()
    for root in roots:
        add_dll_search_dir(root / "llama_cpp" / "lib")

    for root in roots:
        lib_dir = root / "llama_cpp" / "lib"
        if not lib_dir.exists():
            continue
        for dll_name in ("ggml-base.dll", "ggml-cpu.dll", "ggml-cuda.dll", "ggml.dll", "llama.dll", "mtmd.dll"):
            dll_path = lib_dir / dll_name
            if not dll_path.exists():
                continue
            normalized = str(dll_path.resolve())
            if any(existing == normalized for existing, _ in PRELOADED_DLL_HANDLES):
                continue
            try:
                handle = ctypes.CDLL(normalized, winmode=ctypes.RTLD_GLOBAL)
                PRELOADED_DLL_HANDLES.append((normalized, handle))
            except Exception as exc:
                print(f"[translate] DLL preload skipped: {dll_path.name}: {repr(exc)}", flush=True)


def ensure_user_config_file():
    configure_runtime_environment()
    if CONFIG_PATH.exists():
        return
    if DEFAULT_CONFIG_PATH.exists():
        CONFIG_PATH.write_text(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")


def read_yaml_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def run_hidden_command(args: list[str], timeout_sec: float = 5.0) -> subprocess.CompletedProcess | None:
    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    except AttributeError:
        flags = 0
    try:
        return subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            creationflags=flags,
        )
    except Exception:
        return None


def nvidia_smi_candidates() -> list[str]:
    candidates = ["nvidia-smi"]
    if sys.platform == "win32":
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        candidates.append(str(system_root / "System32" / "nvidia-smi.exe"))
    return candidates


def detect_gpu_infos() -> list[dict[str, Any]]:
    query_args = ["--query-gpu=index,name", "--format=csv,noheader,nounits"]
    for exe in nvidia_smi_candidates():
        result = run_hidden_command([exe, *query_args], timeout_sec=5.0)
        if not result or result.returncode != 0:
            continue

        infos: list[dict[str, Any]] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = [part.strip() for part in line.split(",", 1)]
            try:
                index = int(parts[0])
            except Exception:
                continue
            name = parts[1] if len(parts) > 1 else f"GPU {index}"
            infos.append({"index": index, "name": name})
        if infos:
            infos.sort(key=lambda item: int(item.get("index", 0)))
            return infos
    return []


def gpu_device_ids(gpu_infos: list[dict[str, Any]] | None = None) -> list[str]:
    infos = detect_gpu_infos() if gpu_infos is None else gpu_infos
    return [f"gpu:{int(info.get('index', 0))}" for info in infos]


def normalize_compute_device(value: Any, available_gpu_devices: list[str] | None = None) -> str:
    gpu_devices = [str(device) for device in (available_gpu_devices or []) if str(device).startswith("gpu:")]
    normalized = str(value or "auto").strip().lower().replace("cuda:", "gpu:")
    if normalized in {"", "auto", "gpu", "cuda"}:
        return gpu_devices[0] if gpu_devices else "cpu"
    if normalized in {"cpu", "none", "off"}:
        return "cpu"
    if normalized.startswith("gpu:"):
        try:
            requested_index = int(normalized.split(":", 1)[1])
        except Exception:
            requested_index = -1
        requested = f"gpu:{requested_index}"
        if requested in gpu_devices:
            return requested
        return gpu_devices[0] if gpu_devices else "cpu"
    return gpu_devices[0] if gpu_devices else "cpu"


def device_index(device: str) -> int:
    try:
        return int(str(device).split(":", 1)[1]) if str(device).startswith("gpu:") else 0
    except Exception:
        return 0


def device_display_name(device: str, gpu_infos: list[dict[str, Any]] | None = None) -> str:
    normalized = str(device or "cpu")
    if normalized == "cpu":
        return "CPU"
    infos = detect_gpu_infos() if gpu_infos is None else gpu_infos
    idx = device_index(normalized)
    for info in infos:
        if int(info.get("index", -1)) == idx:
            name = str(info.get("name", "") or "").strip()
            return f"{normalized} ({name})" if name else normalized
    return normalized


def configured_gpu_devices_from_dict(config_dict: dict) -> list[str]:
    configured = [
        str(device)
        for device in config_dict.get("available_devices", [])
        if str(device).startswith("gpu:")
    ]
    if configured:
        return configured
    return gpu_device_ids()


def resolve_translation_device(config_dict: dict) -> str:
    raw_device = str(config_dict.get("translation_device", "") or "").strip()
    if not raw_device:
        try:
            raw_device = f"gpu:{int(config_dict.get('translation_device_index', 0))}"
        except Exception:
            raw_device = "auto"
    return normalize_compute_device(raw_device, configured_gpu_devices_from_dict(config_dict))


configure_runtime_environment()


def load_app_icon(app: QApplication | None = None) -> QIcon:
    if APP_ICON_PATH.exists():
        icon = QIcon(str(APP_ICON_PATH))
        if not icon.isNull():
            return icon
    if app is not None:
        icon = app.style().standardIcon(QStyle.SP_ComputerIcon)
        if not icon.isNull():
            return icon
    return QIcon()


@dataclass
class AppConfig:
    paddle_lang: str
    ocr_version: str
    worker_mode: str
    selected_device: str
    available_devices: list
    capture_region: dict
    capture_mode: str
    capture_crop_enabled: bool
    capture_crop_left_ratio: float
    capture_crop_top_ratio: float
    capture_crop_right_ratio: float
    capture_crop_bottom_ratio: float
    vertical_box_filter_enabled: bool
    vertical_box_merge_enabled: bool
    vertical_box_min_aspect_ratio: float
    vertical_box_min_height_px: int
    vertical_box_column_gap_px: float
    vertical_sentence_same_column_gap_px: float
    vertical_reading_order_enabled: bool
    vertical_reading_right_to_left: bool
    mixed_text_keep_horizontal: bool
    horizontal_box_min_width_px: int
    horizontal_box_min_aspect_ratio: float
    target_window_title: str
    window_match_mode: str
    use_client_area: bool
    window_follow_fallback_to_region: bool
    window_follow_log: bool
    click_select_delay_sec: float
    capture_interval_ms: int
    ocr_scale: float
    min_ocr_confidence: float
    min_text_length: int
    ocr_text_detection_model_dir: str
    ocr_text_recognition_model_dir: str
    filter_numeric_symbol_only: bool
    filter_single_character_text: bool
    japanese_aux_ocr_enabled: bool
    japanese_aux_ocr_backend: str
    japanese_aux_ocr_mode: str
    japanese_aux_ocr_symbol_chars: str
    japanese_aux_ocr_provider: str
    japanese_aux_ocr_det_threshold: float
    japanese_aux_ocr_rec_threshold: float
    japanese_aux_ocr_line_confidence: float
    japanese_aux_ocr_match_iou: float
    japanese_aux_ocr_match_overlap_ratio: float
    japanese_aux_ocr_min_size_ratio: float
    japanese_aux_ocr_max_center_distance_px: float
    japanese_aux_ocr_log_changes: bool
    text_det_thresh: float
    text_det_box_thresh: float
    text_det_unclip_ratio: float
    text_rec_score_thresh: float
    paddle_aux_symbol_refine_enabled: bool
    paddle_aux_symbol_refine_mode: str
    paddle_aux_symbol_refine_left_ratio: float
    paddle_aux_symbol_refine_top_ratio: float
    paddle_aux_symbol_refine_right_ratio: float
    paddle_aux_symbol_refine_bottom_ratio: float
    paddle_aux_symbol_refine_item_padding_px: int
    paddle_aux_symbol_refine_item_top_ratio: float
    paddle_aux_symbol_refine_item_bottom_ratio: float
    paddle_box_refine_enabled: bool
    paddle_box_refine_padding_px: int
    paddle_box_refine_min_aux_coverage: float
    paddle_box_refine_min_text_ratio: float
    paddle_box_refine_long_horizontal_min_chars: int
    paddle_box_refine_long_horizontal_min_conf_gain: float
    paddle_box_refine_log_changes: bool
    enable_hpi: bool
    use_tensorrt: bool
    precision: str
    drop_old_frames: bool
    exclude_overlay_from_capture: bool
    hide_overlay_during_capture: bool
    bbox_line_width: int
    bbox_draw_mode: str
    model_status_overlay_enabled: bool
    show_ocr_text_box: bool
    text_box_font_size: int
    text_box_adaptive_font_size: bool
    text_box_font_scale: float
    text_box_min_font_size: int
    text_box_max_font_size: int
    text_box_padding: int
    text_box_margin: int
    text_box_max_width: int
    text_box_min_width: int
    text_box_background_alpha: int
    text_box_border_alpha: int
    text_box_text_alpha: int
    text_box_show_confidence: bool
    text_box_position: str
    vertical_text_enabled: bool
    vertical_text_mode: str
    vertical_text_min_aspect_ratio: float
    vertical_text_require_cjk: bool
    vertical_text_char_spacing_px: int
    skip_small_text_bbox: bool
    min_horizontal_bbox_height: int
    min_vertical_bbox_width: int
    sentence_grouping_enabled: bool
    sentence_group_max_gap_px: float
    sentence_group_min_overlap_ratio: float
    sentence_group_max_cross_axis_gap_px: float
    sentence_group_horizontal_joiner: str
    sentence_group_vertical_joiner: str
    sentence_group_debug_print: bool
    sentence_group_debug_every_n_frames: int
    horizontal_sentence_multiline_enabled: bool
    horizontal_sentence_line_gap_px: float
    horizontal_sentence_min_x_overlap_ratio: float
    horizontal_sentence_require_continuation: bool
    language_select_on_start: bool
    enable_translation: bool
    translation_backend: str
    translation_model_path: str
    translation_model_id: str
    translation_model_file: str
    translation_source_language: str
    translation_target_language: str
    translation_device: str
    translation_device_index: int
    translation_n_ctx: int
    translation_n_gpu_layers: int
    translation_temperature: float
    translation_top_p: float
    translation_top_k: int
    translation_max_new_tokens: int
    translation_prompt_template: str
    translation_cache_path: str
    translation_debug_print: bool
    translation_split_across_boxes: bool
    translation_hide_source_fallback: bool
    translation_context_enabled: bool
    translation_context_mode: str
    translation_context_max_entries: int
    translation_context_max_chars: int
    translation_context_max_frame_age: int
    translation_context_use_translated_history: bool
    translation_context_debug_print: bool
    vertical_en_bottom_caption_enabled: bool
    vertical_en_bottom_caption_group_metric: str
    vertical_en_bottom_caption_width_ratio: float
    vertical_en_bottom_caption_height_px: int
    vertical_en_bottom_caption_margin_bottom_px: int
    vertical_en_bottom_caption_padding_px: int
    vertical_en_bottom_caption_background_alpha: int
    vertical_en_bottom_caption_border_alpha: int
    vertical_en_bottom_caption_text_alpha: int
    hide_when_no_bbox: bool
    save_extracted_text: bool
    output_dir: str
    write_live_snapshot: bool
    write_current_text_txt: bool
    write_events_jsonl: bool
    log_only_when_changed: bool
    save_paddle_visualization: bool
    paddle_visualization_dir: str
    save_vis_every_n_frames: int
    save_vis_latest_copy: bool
    app_start_to_tray: bool
    app_ui_language: str
    app_help_language: str
    app_hotkey_toggle_panel: str
    demo_capture_enabled: bool
    demo_output_dir: str
    demo_screenshot_dir: str
    demo_video_dir: str
    demo_screenshot_hotkey: str
    demo_record_hotkey: str
    demo_record_fps: int
    demo_record_mp4: bool


@dataclass
class OCRItem:
    text: str
    confidence: float
    rel_x: float
    rel_y: float
    w: float
    h: float
    abs_x: float
    abs_y: float
    poly_abs: list
    poly_rel: list
    frame_id: int = 0
    device: str = ""
    box_type: str = ""
    sentence_id: int = 0
    sentence_text: str = ""
    sentence_translated_text: str = ""
    translated_text: str = ""
    bottom_caption_text: str = ""
    bottom_caption_group_id: int = 0


def speaker_aware_translation_prompt_template() -> str:
    return (
        "Translate the following Japanese visual novel or game UI text from {source} into natural {target}.\n\n"
        "Handle all input types:\n"
        "- dialogue and narration\n"
        "- standalone labels, menu text, status text, item names, dates, sizes, and species names\n\n"
        "Speaker labels:\n"
        "- Only treat the beginning as a speaker label when the source clearly contains both a speaker name and dialogue after it.\n"
        "- If a speaker label is present, keep the speaker name exactly as source text and output: OriginalSpeaker: translated dialogue.\n"
        "- Do not invent a speaker label.\n"
        "- Do not add a colon to standalone labels, menu text, status text, item names, dates, sizes, or species names.\n\n"
        "Standalone text:\n"
        "- Translate standalone nouns, labels, item names, and species names as ordinary text.\n"
        "- Keep short UI labels concise.\n\n"
        "Fragments and context:\n"
        "- If the text is a continuation fragment, translate it as a natural continuation in {target}.\n"
        "- Preserve incomplete-sentence feeling when the source is incomplete.\n"
        "- Use any provided previous OCR context only to resolve omitted subjects and speaker attribution.\n\n"
        "Style:\n"
        "- Return only the final {target} translation.\n"
        "- No quotes, notes, explanations, or source text.\n"
        "- Keep the translation concise enough for an overlay subtitle.\n\n"
        "Text: {text}"
    )


def english_translation_prompt_template() -> str:
    return speaker_aware_translation_prompt_template()


def generic_translation_prompt_template() -> str:
    raw = read_yaml_file(BBOX_CONFIG_PATH)
    raw.update(read_yaml_file(CONFIG_PATH))
    template = str(raw.get("translation_prompt_template", "") or "").strip()
    if template:
        return template
    return speaker_aware_translation_prompt_template()


def is_english_language_code(code: str) -> bool:
    return str(code or "").strip().lower() in {"en", "english", "eng_latn"}


def translation_prompt_template_for_target(target_code: str) -> str:
    return speaker_aware_translation_prompt_template()


def load_config() -> AppConfig:
    ensure_user_config_file()
    if not CONFIG_PATH.exists() and not BBOX_CONFIG_PATH.exists():
        raise FileNotFoundError(f"{APP_SLUG}/config.yaml이 필요합니다.")

    base_raw = read_yaml_file(BBOX_CONFIG_PATH)
    raw = read_yaml_file(CONFIG_PATH)
    if not raw:
        raw = dict(base_raw)

    def cfg_get(key: str, default=None):
        return raw.get(key, base_raw.get(key, default))

    def cfg_path(key: str, default: str) -> str:
        value = cfg_get(key, default)
        path_value = Path(str(value))
        if path_value.is_absolute():
            return str(path_value)
        return str(USER_DATA_DIR / path_value)

    translation_target_language = str(cfg_get("translation_target_language", "en"))
    if "translation_prompt_template" in raw:
        translation_prompt_template = str(raw.get("translation_prompt_template", ""))
    else:
        translation_prompt_template = translation_prompt_template_for_target(translation_target_language)

    detected_gpus = detect_gpu_infos()
    available_gpu_devices = gpu_device_ids(detected_gpus)
    selected_device = normalize_compute_device(cfg_get("selected_device", "auto"), available_gpu_devices)

    raw_translation_device = str(cfg_get("translation_device", "") or "").strip()
    if not raw_translation_device:
        try:
            raw_translation_device = f"gpu:{int(cfg_get('translation_device_index', 0))}"
        except Exception:
            raw_translation_device = "auto"
    translation_device = normalize_compute_device(raw_translation_device, available_gpu_devices)
    translation_device_index = device_index(translation_device)
    translation_n_gpu_layers = int(cfg_get("translation_n_gpu_layers", -1))
    if translation_device == "cpu":
        translation_n_gpu_layers = 0
    elif translation_n_gpu_layers == 0:
        translation_n_gpu_layers = -1

    return AppConfig(
        paddle_lang=raw.get("paddle_lang", "en"),
        ocr_version=raw.get("ocr_version", "PP-OCRv6"),
        worker_mode=raw.get("worker_mode", "selected"),
        selected_device=selected_device,
        available_devices=available_gpu_devices,
        capture_region=raw.get("capture_region", {"left": 0, "top": 0, "width": 2560, "height": 1440}),
        capture_mode=raw.get("capture_mode", "region"),
        capture_crop_enabled=bool(raw.get("capture_crop_enabled", False)),
        capture_crop_left_ratio=float(raw.get("capture_crop_left_ratio", 0.0)),
        capture_crop_top_ratio=float(raw.get("capture_crop_top_ratio", 0.0)),
        capture_crop_right_ratio=float(raw.get("capture_crop_right_ratio", 1.0)),
        capture_crop_bottom_ratio=float(raw.get("capture_crop_bottom_ratio", 1.0)),
        vertical_box_filter_enabled=bool(raw.get("vertical_box_filter_enabled", False)),
        vertical_box_merge_enabled=bool(raw.get("vertical_box_merge_enabled", True)),
        vertical_box_min_aspect_ratio=float(raw.get("vertical_box_min_aspect_ratio", raw.get("vertical_text_min_aspect_ratio", 1.55))),
        vertical_box_min_height_px=int(raw.get("vertical_box_min_height_px", 50)),
        vertical_box_column_gap_px=float(raw.get("vertical_box_column_gap_px", 24.0)),
        vertical_sentence_same_column_gap_px=float(raw.get("vertical_sentence_same_column_gap_px", raw.get("vertical_box_column_gap_px", 24.0))),
        vertical_reading_order_enabled=bool(raw.get("vertical_reading_order_enabled", True)),
        vertical_reading_right_to_left=bool(raw.get("vertical_reading_right_to_left", True)),
        mixed_text_keep_horizontal=bool(raw.get("mixed_text_keep_horizontal", True)),
        horizontal_box_min_width_px=int(raw.get("horizontal_box_min_width_px", 20)),
        horizontal_box_min_aspect_ratio=float(raw.get("horizontal_box_min_aspect_ratio", 0.75)),
        target_window_title=raw.get("target_window_title", ""),
        window_match_mode=raw.get("window_match_mode", "contains"),
        use_client_area=bool(raw.get("use_client_area", True)),
        window_follow_fallback_to_region=bool(raw.get("window_follow_fallback_to_region", True)),
        window_follow_log=bool(raw.get("window_follow_log", True)),
        click_select_delay_sec=float(raw.get("click_select_delay_sec", 3.0)),
        capture_interval_ms=int(raw.get("capture_interval_ms", 200)),
        ocr_scale=float(raw.get("ocr_scale", 2.0)),
        min_ocr_confidence=float(raw.get("min_ocr_confidence", 0.85)),
        min_text_length=int(raw.get("min_text_length", 2)),
        ocr_text_detection_model_dir=str(cfg_get("ocr_text_detection_model_dir", "")),
        ocr_text_recognition_model_dir=str(cfg_get("ocr_text_recognition_model_dir", "")),
        filter_numeric_symbol_only=bool(raw.get("filter_numeric_symbol_only", True)),
        filter_single_character_text=bool(raw.get("filter_single_character_text", True)),
        japanese_aux_ocr_enabled=bool(raw.get("japanese_aux_ocr_enabled", True)),
        japanese_aux_ocr_backend=raw.get("japanese_aux_ocr_backend", "meikiocr"),
        japanese_aux_ocr_mode=raw.get("japanese_aux_ocr_mode", "symbol_only"),
        japanese_aux_ocr_symbol_chars=raw.get("japanese_aux_ocr_symbol_chars", DEFAULT_JAPANESE_AUX_SYMBOL_CHARS),
        japanese_aux_ocr_provider=raw.get("japanese_aux_ocr_provider", "auto"),
        japanese_aux_ocr_det_threshold=float(raw.get("japanese_aux_ocr_det_threshold", 0.45)),
        japanese_aux_ocr_rec_threshold=float(raw.get("japanese_aux_ocr_rec_threshold", 0.08)),
        japanese_aux_ocr_line_confidence=float(raw.get("japanese_aux_ocr_line_confidence", 0.60)),
        japanese_aux_ocr_match_iou=float(raw.get("japanese_aux_ocr_match_iou", 0.05)),
        japanese_aux_ocr_match_overlap_ratio=float(raw.get("japanese_aux_ocr_match_overlap_ratio", 0.45)),
        japanese_aux_ocr_min_size_ratio=float(raw.get("japanese_aux_ocr_min_size_ratio", 0.25)),
        japanese_aux_ocr_max_center_distance_px=float(raw.get("japanese_aux_ocr_max_center_distance_px", 48.0)),
        japanese_aux_ocr_log_changes=bool(raw.get("japanese_aux_ocr_log_changes", True)),
        text_det_thresh=float(raw.get("text_det_thresh", 0.85)),
        text_det_box_thresh=float(raw.get("text_det_box_thresh", 0.85)),
        text_det_unclip_ratio=float(raw.get("text_det_unclip_ratio", 2)),
        text_rec_score_thresh=float(raw.get("text_rec_score_thresh", 0.0)),
        paddle_aux_symbol_refine_enabled=bool(raw.get("paddle_aux_symbol_refine_enabled", False)),
        paddle_aux_symbol_refine_mode=raw.get("paddle_aux_symbol_refine_mode", "ratio"),
        paddle_aux_symbol_refine_left_ratio=float(raw.get("paddle_aux_symbol_refine_left_ratio", 0.0)),
        paddle_aux_symbol_refine_top_ratio=float(raw.get("paddle_aux_symbol_refine_top_ratio", 0.0)),
        paddle_aux_symbol_refine_right_ratio=float(raw.get("paddle_aux_symbol_refine_right_ratio", 1.0)),
        paddle_aux_symbol_refine_bottom_ratio=float(raw.get("paddle_aux_symbol_refine_bottom_ratio", 0.5)),
        paddle_aux_symbol_refine_item_padding_px=int(raw.get("paddle_aux_symbol_refine_item_padding_px", 12)),
        paddle_aux_symbol_refine_item_top_ratio=float(raw.get("paddle_aux_symbol_refine_item_top_ratio", 0.0)),
        paddle_aux_symbol_refine_item_bottom_ratio=float(raw.get("paddle_aux_symbol_refine_item_bottom_ratio", 1.0)),
        paddle_box_refine_enabled=bool(raw.get("paddle_box_refine_enabled", False)),
        paddle_box_refine_padding_px=int(raw.get("paddle_box_refine_padding_px", 20)),
        paddle_box_refine_min_aux_coverage=float(raw.get("paddle_box_refine_min_aux_coverage", 0.45)),
        paddle_box_refine_min_text_ratio=float(raw.get("paddle_box_refine_min_text_ratio", 0.55)),
        paddle_box_refine_long_horizontal_min_chars=int(raw.get("paddle_box_refine_long_horizontal_min_chars", 12)),
        paddle_box_refine_long_horizontal_min_conf_gain=float(raw.get("paddle_box_refine_long_horizontal_min_conf_gain", 0.02)),
        paddle_box_refine_log_changes=bool(raw.get("paddle_box_refine_log_changes", True)),
        enable_hpi=bool(raw.get("enable_hpi", False)),
        use_tensorrt=bool(raw.get("use_tensorrt", False)),
        precision=raw.get("precision", "fp16"),
        drop_old_frames=bool(raw.get("drop_old_frames", True)),
        exclude_overlay_from_capture=bool(raw.get("exclude_overlay_from_capture", True)),
        hide_overlay_during_capture=bool(raw.get("hide_overlay_during_capture", False)),
        bbox_line_width=int(raw.get("bbox_line_width", 2)),
        bbox_draw_mode=raw.get("bbox_draw_mode", "poly"),
        model_status_overlay_enabled=bool(cfg_get("model_status_overlay_enabled", True)),
        show_ocr_text_box=bool(raw.get("show_ocr_text_box", True)),
        text_box_font_size=int(raw.get("text_box_font_size", 18)),
        text_box_adaptive_font_size=bool(raw.get("text_box_adaptive_font_size", True)),
        text_box_font_scale=float(raw.get("text_box_font_scale", 0.72)),
        text_box_min_font_size=int(raw.get("text_box_min_font_size", 8)),
        text_box_max_font_size=int(raw.get("text_box_max_font_size", 36)),
        text_box_padding=int(raw.get("text_box_padding", 4)),
        text_box_margin=int(raw.get("text_box_margin", 4)),
        text_box_max_width=int(raw.get("text_box_max_width", 520)),
        text_box_min_width=int(raw.get("text_box_min_width", 80)),
        text_box_background_alpha=int(raw.get("text_box_background_alpha", 70)),
        text_box_border_alpha=int(raw.get("text_box_border_alpha", 230)),
        text_box_text_alpha=int(raw.get("text_box_text_alpha", 255)),
        text_box_show_confidence=bool(raw.get("text_box_show_confidence", False)),
        text_box_position=raw.get("text_box_position", "inside"),
        vertical_text_enabled=bool(raw.get("vertical_text_enabled", raw.get("rotate_vertical_text", True))),
        vertical_text_mode=raw.get("vertical_text_mode", "auto"),
        vertical_text_min_aspect_ratio=float(raw.get("vertical_text_min_aspect_ratio", raw.get("vertical_bbox_ratio", 1.55))),
        vertical_text_require_cjk=bool(raw.get("vertical_text_require_cjk", False)),
        vertical_text_char_spacing_px=int(raw.get("vertical_text_char_spacing_px", 1)),
        skip_small_text_bbox=bool(raw.get("skip_small_text_bbox", True)),
        min_horizontal_bbox_height=int(raw.get("min_horizontal_bbox_height", 14)),
        min_vertical_bbox_width=int(raw.get("min_vertical_bbox_width", 14)),
        sentence_grouping_enabled=bool(raw.get("sentence_grouping_enabled", True)),
        sentence_group_max_gap_px=float(raw.get("sentence_group_max_gap_px", 40.0)),
        sentence_group_min_overlap_ratio=float(raw.get("sentence_group_min_overlap_ratio", 0.20)),
        sentence_group_max_cross_axis_gap_px=float(raw.get("sentence_group_max_cross_axis_gap_px", 12.0)),
        sentence_group_horizontal_joiner=str(raw.get("sentence_group_horizontal_joiner", "")),
        sentence_group_vertical_joiner=str(raw.get("sentence_group_vertical_joiner", "")),
        sentence_group_debug_print=bool(raw.get("sentence_group_debug_print", True)),
        sentence_group_debug_every_n_frames=int(raw.get("sentence_group_debug_every_n_frames", 1)),
        horizontal_sentence_multiline_enabled=bool(raw.get("horizontal_sentence_multiline_enabled", True)),
        horizontal_sentence_line_gap_px=float(raw.get("horizontal_sentence_line_gap_px", 36.0)),
        horizontal_sentence_min_x_overlap_ratio=float(raw.get("horizontal_sentence_min_x_overlap_ratio", 0.45)),
        horizontal_sentence_require_continuation=bool(raw.get("horizontal_sentence_require_continuation", True)),
        language_select_on_start=bool(cfg_get("language_select_on_start", True)),
        enable_translation=bool(cfg_get("enable_translation", True)),
        translation_backend=str(cfg_get("translation_backend", "llama_cpp_gguf")),
        translation_model_path=str(cfg_get("translation_model_path", "")),
        translation_model_id=str(cfg_get("translation_model_id", "HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive")),
        translation_model_file=str(cfg_get("translation_model_file", "Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf")),
        translation_source_language=str(cfg_get("translation_source_language", "ja")),
        translation_target_language=translation_target_language,
        translation_device=translation_device,
        translation_device_index=translation_device_index,
        translation_n_ctx=int(cfg_get("translation_n_ctx", 2048)),
        translation_n_gpu_layers=translation_n_gpu_layers,
        translation_temperature=float(cfg_get("translation_temperature", 0.1)),
        translation_top_p=float(cfg_get("translation_top_p", 0.95)),
        translation_top_k=int(cfg_get("translation_top_k", 64)),
        translation_max_new_tokens=int(cfg_get("translation_max_new_tokens", 128)),
        translation_prompt_template=translation_prompt_template,
        translation_cache_path=cfg_path("translation_cache_path", "translation_cache_gemma4_e2b_gguf_sentence.json"),
        translation_debug_print=bool(cfg_get("translation_debug_print", True)),
        translation_split_across_boxes=bool(cfg_get("translation_split_across_boxes", True)),
        translation_hide_source_fallback=bool(cfg_get("translation_hide_source_fallback", True)),
        translation_context_enabled=bool(cfg_get("translation_context_enabled", True)),
        translation_context_mode=str(cfg_get("translation_context_mode", "auto")),
        translation_context_max_entries=int(cfg_get("translation_context_max_entries", 4)),
        translation_context_max_chars=int(cfg_get("translation_context_max_chars", 700)),
        translation_context_max_frame_age=int(cfg_get("translation_context_max_frame_age", 24)),
        translation_context_use_translated_history=bool(cfg_get("translation_context_use_translated_history", True)),
        translation_context_debug_print=bool(cfg_get("translation_context_debug_print", True)),
        vertical_en_bottom_caption_enabled=bool(cfg_get("vertical_en_bottom_caption_enabled", True)),
        vertical_en_bottom_caption_group_metric=str(cfg_get("vertical_en_bottom_caption_group_metric", "area")),
        vertical_en_bottom_caption_width_ratio=float(cfg_get("vertical_en_bottom_caption_width_ratio", 0.86)),
        vertical_en_bottom_caption_height_px=int(cfg_get("vertical_en_bottom_caption_height_px", 96)),
        vertical_en_bottom_caption_margin_bottom_px=int(cfg_get("vertical_en_bottom_caption_margin_bottom_px", 28)),
        vertical_en_bottom_caption_padding_px=int(cfg_get("vertical_en_bottom_caption_padding_px", 12)),
        vertical_en_bottom_caption_background_alpha=int(cfg_get("vertical_en_bottom_caption_background_alpha", 210)),
        vertical_en_bottom_caption_border_alpha=int(cfg_get("vertical_en_bottom_caption_border_alpha", 220)),
        vertical_en_bottom_caption_text_alpha=int(cfg_get("vertical_en_bottom_caption_text_alpha", 255)),
        hide_when_no_bbox=bool(raw.get("hide_when_no_bbox", True)),
        save_extracted_text=bool(raw.get("save_extracted_text", True)),
        output_dir=cfg_path("output_dir", "extracted_text"),
        write_live_snapshot=bool(raw.get("write_live_snapshot", True)),
        write_current_text_txt=bool(raw.get("write_current_text_txt", True)),
        write_events_jsonl=bool(raw.get("write_events_jsonl", True)),
        log_only_when_changed=bool(raw.get("log_only_when_changed", True)),
        save_paddle_visualization=bool(raw.get("save_paddle_visualization", True)),
        paddle_visualization_dir=cfg_path("paddle_visualization_dir", "paddle_vis"),
        save_vis_every_n_frames=int(raw.get("save_vis_every_n_frames", 10)),
        save_vis_latest_copy=bool(raw.get("save_vis_latest_copy", True)),
        app_start_to_tray=bool(cfg_get("app_start_to_tray", True)),
        app_ui_language=str(cfg_get("app_ui_language", "ko")),
        app_help_language=str(cfg_get("app_help_language", "ko")),
        app_hotkey_toggle_panel=str(cfg_get("app_hotkey_toggle_panel", "Ctrl+Alt+A")),
        demo_capture_enabled=bool(cfg_get("demo_capture_enabled", True)),
        demo_output_dir=cfg_path("demo_output_dir", "demo_capture"),
        demo_screenshot_dir=cfg_path("demo_screenshot_dir", cfg_get("demo_output_dir", "demo_capture")),
        demo_video_dir=cfg_path("demo_video_dir", cfg_get("demo_output_dir", "demo_capture")),
        demo_screenshot_hotkey=str(cfg_get("demo_screenshot_hotkey", "Ctrl+Alt+S")),
        demo_record_hotkey=str(cfg_get("demo_record_hotkey", "Ctrl+Alt+R")),
        demo_record_fps=int(cfg_get("demo_record_fps", 10)),
        demo_record_mp4=bool(cfg_get("demo_record_mp4", True)),
    )


LANGUAGE_OPTIONS = [
    {"key": "auto", "label": "Auto", "paddle_lang": "", "llama": "auto"},
    {"key": "en", "label": "English", "paddle_lang": "en", "llama": "en"},
    {"key": "ja", "label": "Japanese", "paddle_lang": "japan", "llama": "ja"},
    {"key": "ko", "label": "Korean", "paddle_lang": "korean", "llama": "ko"},
    {"key": "zh", "label": "Chinese Simplified", "paddle_lang": "ch", "llama": "zh"},
    {"key": "zh_tw", "label": "Chinese Traditional", "paddle_lang": "chinese_cht", "llama": "zh"},
    {"key": "it", "label": "Italian", "paddle_lang": "", "llama": "it", "target_only": True},
    {"key": "fr", "label": "French", "paddle_lang": "", "llama": "fr", "target_only": True},
    {"key": "pl", "label": "Polish", "paddle_lang": "", "llama": "pl", "target_only": True},
    {"key": "de", "label": "German", "paddle_lang": "", "llama": "de", "target_only": True},
    {"key": "es", "label": "Spanish", "paddle_lang": "", "llama": "es", "target_only": True},
]


def language_option_by_key(key: str) -> dict:
    for option in LANGUAGE_OPTIONS:
        if option["key"] == key:
            return option
    return LANGUAGE_OPTIONS[2]


def find_language_key_for_code(code: str, allow_auto: bool = True) -> str:
    normalized = str(code or "").strip()
    if allow_auto and normalized == "auto":
        return "auto"
    for option in LANGUAGE_OPTIONS:
        if not allow_auto and option["key"] == "auto":
            continue
        candidates = {str(option.get("llama", "")), option["key"], str(option.get("paddle_lang", ""))}
        if normalized in candidates:
            return option["key"]
    return "auto" if allow_auto else "ko"


def apply_language_selection_to_config(config: AppConfig, source_key: str, target_key: str):
    source_option = language_option_by_key(source_key)
    target_option = language_option_by_key(target_key)
    paddle_lang = str(source_option.get("paddle_lang") or "")
    if paddle_lang:
        config.paddle_lang = paddle_lang
    config.translation_source_language = str(source_option.get("llama") or source_option["key"])
    config.translation_target_language = str(target_option.get("llama") or target_option["key"])
    config.translation_prompt_template = translation_prompt_template_for_target(config.translation_target_language)
    print(
        f"[language] source={config.translation_source_language} target={config.translation_target_language} ocr={config.paddle_lang}",
        flush=True,
    )


class LanguageSelectionDialog(QDialog):
    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.setWindowTitle("Translation Language")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("원문 OCR 언어와 번역문 언어를 선택하세요."))

        form = QFormLayout()
        self.source_combo = QComboBox()
        self.target_combo = QComboBox()

        for option in LANGUAGE_OPTIONS:
            if not bool(option.get("target_only", False)):
                self.source_combo.addItem(option["label"], option["key"])
            if option["key"] != "auto":
                self.target_combo.addItem(option["label"], option["key"])

        source_key = find_language_key_for_code(config.translation_source_language, allow_auto=True)
        target_key = find_language_key_for_code(config.translation_target_language, allow_auto=False)
        self.source_combo.setCurrentIndex(max(0, self.source_combo.findData(source_key)))
        self.target_combo.setCurrentIndex(max(0, self.target_combo.findData(target_key)))

        form.addRow("Source", self.source_combo)
        form.addRow("Target", self.target_combo)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def selected_keys(self) -> tuple[str, str]:
        return str(self.source_combo.currentData()), str(self.target_combo.currentData())


def select_languages_modal(config: AppConfig) -> bool:
    dialog = LanguageSelectionDialog(config)
    result = dialog.exec()
    if result != QDialog.Accepted:
        return False
    source_key, target_key = dialog.selected_keys()
    apply_language_selection_to_config(config, source_key, target_key)
    return True


def apply_click_through(hwnd: int):
    if sys.platform != "win32":
        return
    user32 = ctypes.windll.user32
    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)


def apply_exclude_from_capture(hwnd: int):
    if sys.platform != "win32":
        return False
    WDA_EXCLUDEFROMCAPTURE = 0x00000011
    try:
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))
    except Exception:
        return False


def _rect_to_region(left: int, top: int, right: int, bottom: int):
    width = max(0, int(right) - int(left))
    height = max(0, int(bottom) - int(top))
    if width <= 0 or height <= 0:
        return None
    return {"left": int(left), "top": int(top), "width": width, "height": height}


def clamp_ratio(value: float, default: float) -> float:
    try:
        value = float(value)
    except Exception:
        value = default
    return max(0.0, min(1.0, value))


def apply_region_crop(region: dict, config_dict: dict) -> dict:
    if not bool(config_dict.get("capture_crop_enabled", False)):
        return region

    left_ratio = clamp_ratio(config_dict.get("capture_crop_left_ratio", 0.0), 0.0)
    top_ratio = clamp_ratio(config_dict.get("capture_crop_top_ratio", 0.0), 0.0)
    right_ratio = clamp_ratio(config_dict.get("capture_crop_right_ratio", 1.0), 1.0)
    bottom_ratio = clamp_ratio(config_dict.get("capture_crop_bottom_ratio", 1.0), 1.0)

    if right_ratio <= left_ratio or bottom_ratio <= top_ratio:
        return region

    left = int(region["left"])
    top = int(region["top"])
    width = max(1, int(region["width"]))
    height = max(1, int(region["height"]))

    crop_left = left + int(round(width * left_ratio))
    crop_top = top + int(round(height * top_ratio))
    crop_right = left + int(round(width * right_ratio))
    crop_bottom = top + int(round(height * bottom_ratio))

    cropped = _rect_to_region(crop_left, crop_top, crop_right, crop_bottom)
    return cropped or region


def find_window_by_title(title: str, match_mode: str = "contains"):
    if sys.platform != "win32" or not title:
        return None

    user32 = ctypes.windll.user32
    found = []
    query = str(title).lower().strip()
    mode = str(match_mode or "contains").lower().strip()
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            window_title = buf.value.strip()
            low = window_title.lower()
            ok = (low == query) if mode == "exact" else (query in low)
            if ok:
                found.append((int(hwnd), window_title))
                return False
        except Exception:
            pass
        return True

    user32.EnumWindows(enum_proc(callback), 0)
    return found[0] if found else None


def list_visible_windows(use_client_area: bool = True):
    if sys.platform != "win32":
        return []

    user32 = ctypes.windll.user32
    windows = []
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, lparam):
        try:
            if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            title_buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title_buf, length + 1)
            title = title_buf.value.strip()
            if not title:
                return True

            region, _ = get_hwnd_capture_region(int(hwnd), use_client_area)
            if not region:
                return True
            if region["width"] < 80 or region["height"] < 60:
                return True

            windows.append({"hwnd": int(hwnd), "title": title, "region": region})
        except Exception:
            pass
        return True

    user32.EnumWindows(enum_proc(callback), 0)
    windows.sort(key=lambda w: w["title"].lower())
    return windows


def get_window_title(hwnd: int) -> str:
    if sys.platform != "win32" or not hwnd:
        return ""
    user32 = ctypes.windll.user32
    try:
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value.strip()
    except Exception:
        return ""


def get_hwnd_capture_region(hwnd: int, use_client_area: bool = True):
    if sys.platform != "win32" or not hwnd:
        return None, None

    user32 = ctypes.windll.user32
    try:
        if not user32.IsWindow(hwnd):
            return None, None
        if user32.IsIconic(hwnd):
            return None, get_window_title(hwnd)
    except Exception:
        pass

    actual_title = get_window_title(hwnd)

    if use_client_area:
        rect = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return None, actual_title
        pt = wintypes.POINT(0, 0)
        if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
            return None, actual_title
        region = _rect_to_region(pt.x, pt.y, pt.x + rect.right - rect.left, pt.y + rect.bottom - rect.top)
        return region, actual_title

    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None, actual_title
    region = _rect_to_region(rect.left, rect.top, rect.right, rect.bottom)
    return region, actual_title


def get_window_capture_region(title: str, match_mode: str = "contains", use_client_area: bool = True):
    found = find_window_by_title(title, match_mode)
    if not found:
        return None, None
    hwnd, actual_title = found
    region, _ = get_hwnd_capture_region(hwnd, use_client_area)
    return region, actual_title


def get_foreground_window():
    if sys.platform != "win32":
        return None
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        return int(hwnd) if hwnd else None
    except Exception:
        return None


class WindowSelectorDialog(QDialog):
    def __init__(self, use_client_area: bool = True):
        super().__init__()
        self.selected_hwnd = None
        self.selected_title = ""
        self.selected_region = None
        self.windows = []

        self.setWindowTitle("OCR 창 선택")
        self.setMinimumSize(720, 460)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        self.status_label = QLabel("OCR할 창을 선택하세요.")
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("창 제목 검색")
        self.client_area_check = QCheckBox("제목 표시줄 제외")
        self.client_area_check.setChecked(bool(use_client_area))

        self.window_list = QListWidget()
        self.window_list.itemDoubleClicked.connect(self.accept_selection)

        self.refresh_button = QPushButton("새로고침")
        self.select_button = QPushButton("선택")
        self.cancel_button = QPushButton("취소")

        self.refresh_button.clicked.connect(self.refresh_windows)
        self.select_button.clicked.connect(self.accept_selection)
        self.cancel_button.clicked.connect(self.reject)
        self.search_edit.textChanged.connect(self.populate_list)
        self.client_area_check.toggled.connect(self.refresh_windows)

        top_row = QHBoxLayout()
        top_row.addWidget(self.search_edit, 1)
        top_row.addWidget(self.client_area_check)
        top_row.addWidget(self.refresh_button)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self.select_button)
        button_row.addWidget(self.cancel_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.status_label)
        layout.addLayout(top_row)
        layout.addWidget(self.window_list, 1)
        layout.addLayout(button_row)

        self.refresh_windows()

    def refresh_windows(self):
        self.windows = list_visible_windows(self.use_client_area())
        self.populate_list()

    def populate_list(self):
        query = self.search_edit.text().strip().lower()
        self.window_list.clear()

        for window in self.windows:
            title = str(window["title"])
            if query and query not in title.lower():
                continue

            region = window["region"]
            item = QListWidgetItem(
                f'{title}    [{region["left"]},{region["top"]} {region["width"]}x{region["height"]}]'
            )
            item.setData(Qt.UserRole, window)
            self.window_list.addItem(item)

        count = self.window_list.count()
        self.status_label.setText(f"선택 가능한 창 {count}개")
        if count:
            self.window_list.setCurrentRow(0)

    def use_client_area(self) -> bool:
        return bool(self.client_area_check.isChecked())

    def accept_selection(self):
        item = self.window_list.currentItem()
        if item is None:
            return

        window = item.data(Qt.UserRole)
        if not window:
            return

        self.selected_hwnd = int(window["hwnd"])
        self.selected_title = str(window["title"])
        self.selected_region = dict(window["region"])
        self.accept()


def normalize_text(text: str) -> str:
    text = str(text).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def visible_char_count(text: str) -> int:
    # 공백 문자만 제외하고 실제 보이는 글자 수를 계산합니다.
    return sum(1 for ch in str(text) if not ch.isspace())


def has_letter_character(text: str) -> bool:
    # 숫자/기호만 있는 OCR 결과를 제거하기 위한 판정입니다.
    # 한글, 일본어, 영어 같은 문자 계열은 대부분 isalpha()가 True입니다.
    for ch in str(text):
        if ch.isspace():
            continue
        if ch.isalpha():
            return True
        # 일부 문자가 isalpha()에서 누락될 경우를 대비해 Unicode category도 확인합니다.
        if unicodedata.category(ch).startswith("L"):
            return True
    return False


def should_keep_ocr_text(text: str, config_dict: dict) -> bool:
    text = normalize_text(text)
    if not text:
        return False

    if bool(config_dict.get("filter_single_character_text", True)):
        if visible_char_count(text) <= 1:
            return False

    if bool(config_dict.get("filter_numeric_symbol_only", True)):
        # 숫자만, 기호만, 숫자+기호 조합만 있는 경우 제거합니다.
        # 예: "1", "12", "!!!", "12%", "3/4", "-50", "< >"
        if not has_letter_character(text):
            return False

    return True


def contains_japanese_text(text: str) -> bool:
    return bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff]", str(text)))


def normalize_japanese_aux_text(text: str) -> str:
    text = normalize_text(text)
    if contains_japanese_text(text):
        text = re.sub(r"\s+", "", text)
    return text


def japanese_aux_symbol_chars(config_dict: dict | None = None) -> set[str]:
    cfg = config_dict or {}
    chars = str(cfg.get("japanese_aux_ocr_symbol_chars", "") or DEFAULT_JAPANESE_AUX_SYMBOL_CHARS)
    return {ch for ch in chars if ch and not ch.isspace()}


def is_japanese_aux_symbol_char(ch: str, symbol_chars: set[str]) -> bool:
    if not ch or ch.isspace():
        return False
    if ch in symbol_chars:
        return True

    code = ord(ch)
    category = unicodedata.category(ch)
    if not category.startswith(("P", "S")):
        return False

    return (
        0x3000 <= code <= 0x303F  # CJK Symbols and Punctuation
        or 0xFF00 <= code <= 0xFFEF  # Halfwidth and Fullwidth Forms
        or 0x3200 <= code <= 0x33FF  # Enclosed/compat CJK symbols
        or code > 0x7F
    )


def has_configured_japanese_aux_symbol(text: str, config_dict: dict | None = None) -> bool:
    symbol_chars = japanese_aux_symbol_chars(config_dict)
    return any(ch in symbol_chars for ch in str(text))


def text_without_japanese_aux_symbols(text: str, symbol_chars: set[str]) -> str:
    return "".join(ch for ch in str(text) if not is_japanese_aux_symbol_char(ch, symbol_chars))


def insertion_index_after_visible_chars(text: str, visible_count: int, symbol_chars: set[str]) -> int:
    if visible_count <= 0:
        return 0

    seen = 0
    for idx, ch in enumerate(str(text)):
        if is_japanese_aux_symbol_char(ch, symbol_chars) or ch.isspace():
            continue
        seen += 1
        if seen >= visible_count:
            return idx + 1

    return len(text)


def insertion_index_from_aux_symbol(paddle_text: str, aux_text: str, symbol_index: int, symbol_chars: set[str]) -> int:
    before_aux = text_without_japanese_aux_symbols(aux_text[:symbol_index], symbol_chars).strip()
    if before_aux:
        found = paddle_text.find(before_aux)
        if found >= 0:
            return found + len(before_aux)

    visible_before = sum(
        1
        for ch in aux_text[:symbol_index]
        if not is_japanese_aux_symbol_char(ch, symbol_chars) and not ch.isspace()
    )
    return insertion_index_after_visible_chars(paddle_text, visible_before, symbol_chars)


def add_missing_japanese_symbols_from_aux(paddle_text: str, aux_text: str, config_dict: dict | None = None) -> str:
    result = normalize_japanese_aux_text(paddle_text)
    aux_text = normalize_japanese_aux_text(aux_text)
    if not result or not aux_text:
        return result

    symbol_chars = japanese_aux_symbol_chars(config_dict)
    symbol_events = [
        (idx, ch)
        for idx, ch in enumerate(aux_text)
        if is_japanese_aux_symbol_char(ch, symbol_chars)
    ]
    if not symbol_events:
        return result

    aux_seen_counts: dict[str, int] = {}
    last_insert_end = 0
    for symbol_index, symbol in symbol_events:
        aux_seen_counts[symbol] = aux_seen_counts.get(symbol, 0) + 1
        if result.count(symbol) >= aux_seen_counts[symbol]:
            continue

        insert_at = insertion_index_from_aux_symbol(result, aux_text, symbol_index, symbol_chars)
        insert_at = max(insert_at, last_insert_end)
        if insert_at > 0 and result[insert_at - 1:insert_at] == symbol:
            continue
        if insert_at < len(result) and result[insert_at:insert_at + 1] == symbol:
            continue
        result = result[:insert_at] + symbol + result[insert_at:]
        last_insert_end = insert_at + len(symbol)

    return result


def xyxy_area(rect: tuple[float, float, float, float]) -> float:
    x1, y1, x2, y2 = rect
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def xyxy_intersection_area(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    x1 = max(ax1, bx1)
    y1 = max(ay1, by1)
    x2 = min(ax2, bx2)
    y2 = min(ay2, by2)
    return xyxy_area((x1, y1, x2, y2))


def xyxy_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    inter = xyxy_intersection_area(a, b)
    if inter <= 0:
        return 0.0
    union = xyxy_area(a) + xyxy_area(b) - inter
    return inter / max(1.0, union)


def xyxy_coverage_ratios(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> tuple[float, float]:
    inter = xyxy_intersection_area(a, b)
    if inter <= 0:
        return 0.0, 0.0
    return inter / max(1.0, xyxy_area(a)), inter / max(1.0, xyxy_area(b))


def xyxy_size_ratio(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    area_a = xyxy_area(a)
    area_b = xyxy_area(b)
    if area_a <= 0 or area_b <= 0:
        return 0.0
    return min(area_a, area_b) / max(area_a, area_b)


def xyxy_center_distance(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    acx = (ax1 + ax2) * 0.5
    acy = (ay1 + ay2) * 0.5
    bcx = (bx1 + bx2) * 0.5
    bcy = (by1 + by2) * 0.5
    return ((acx - bcx) ** 2 + (acy - bcy) ** 2) ** 0.5


def item_scaled_xyxy(item: OCRItem, scale: int) -> tuple[float, float, float, float]:
    return (
        float(item.rel_x) * scale,
        float(item.rel_y) * scale,
        float(item.rel_x + item.w) * scale,
        float(item.rel_y + item.h) * scale,
    )


def item_rel_xyxy(item: OCRItem) -> tuple[float, float, float, float]:
    return (
        float(item.rel_x),
        float(item.rel_y),
        float(item.rel_x + item.w),
        float(item.rel_y + item.h),
    )


def xy_overlap_ratio(a: OCRItem, b: OCRItem) -> float:
    ax1, _, ax2, _ = item_rel_xyxy(a)
    bx1, _, bx2, _ = item_rel_xyxy(b)
    overlap = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    return overlap / max(1.0, min(float(a.w), float(b.w)))


def y_overlap_ratio(a: OCRItem, b: OCRItem) -> float:
    _, ay1, _, ay2 = item_rel_xyxy(a)
    _, by1, _, by2 = item_rel_xyxy(b)
    overlap = max(0.0, min(ay2, by2) - max(ay1, by1))
    return overlap / max(1.0, min(float(a.h), float(b.h)))


def y_gap_px(a: OCRItem, b: OCRItem) -> float:
    _, ay1, _, ay2 = item_rel_xyxy(a)
    _, by1, _, by2 = item_rel_xyxy(b)
    return max(0.0, max(ay1, by1) - min(ay2, by2))


def x_center_distance_px(a: OCRItem, b: OCRItem) -> float:
    ax1, _, ax2, _ = item_rel_xyxy(a)
    bx1, _, bx2, _ = item_rel_xyxy(b)
    return abs(((ax1 + ax2) * 0.5) - ((bx1 + bx2) * 0.5))


def is_vertical_box_candidate(item: OCRItem, config_dict: dict) -> bool:
    width = max(1.0, float(item.w))
    height = max(1.0, float(item.h))
    min_aspect = float(config_dict.get("vertical_box_min_aspect_ratio", config_dict.get("vertical_text_min_aspect_ratio", 1.55)))
    min_height = float(config_dict.get("vertical_box_min_height_px", 50))
    min_width = float(config_dict.get("min_vertical_bbox_width", 14))

    if height / width < min_aspect:
        return False
    if height < min_height or width < min_width:
        return False
    if bool(config_dict.get("vertical_text_require_cjk", False)) and not contains_japanese_text(item.text):
        return False
    return True


def is_horizontal_box_candidate(item: OCRItem, config_dict: dict) -> bool:
    if not bool(config_dict.get("mixed_text_keep_horizontal", True)):
        return False

    width = max(1.0, float(item.w))
    height = max(1.0, float(item.h))
    min_width = float(config_dict.get("horizontal_box_min_width_px", 20))
    min_height = float(config_dict.get("min_horizontal_bbox_height", 14))
    min_aspect = float(config_dict.get("horizontal_box_min_aspect_ratio", 0.75))

    if width < min_width or height < min_height:
        return False
    if width / height < min_aspect:
        return False
    return True


def should_merge_vertical_column_item(a: OCRItem, b: OCRItem, config_dict: dict) -> bool:
    x_overlap = xy_overlap_ratio(a, b)
    y_overlap = y_overlap_ratio(a, b)
    center_gap = x_center_distance_px(a, b)
    max_center_gap = float(config_dict.get("vertical_box_column_gap_px", 24.0))

    x_aligned = x_overlap >= 0.35 or center_gap <= max_center_gap
    if not x_aligned:
        return False

    # Same-column fragments are usually stacked top-to-bottom. Adjacent Japanese
    # columns can be close in X, so large Y overlap is treated as a separate column.
    stacked_gap = y_gap_px(a, b)
    max_stacked_gap = max(48.0, min(float(a.h), float(b.h)) * 0.75)
    stacked = stacked_gap <= max_stacked_gap and y_overlap < 0.35
    duplicate_same_column = x_overlap >= 0.75 and y_overlap >= 0.50
    return stacked or duplicate_same_column


def merged_vertical_column_item(group: list[OCRItem]) -> OCRItem:
    ordered = sorted(group, key=lambda i: (float(i.rel_y), float(i.rel_x)))
    text_parts = [normalize_japanese_aux_text(i.text) for i in ordered if normalize_japanese_aux_text(i.text)]
    text = "".join(text_parts)

    left = min(float(i.rel_x) for i in group)
    top = min(float(i.rel_y) for i in group)
    right = max(float(i.rel_x + i.w) for i in group)
    bottom = max(float(i.rel_y + i.h) for i in group)
    width = max(1.0, right - left)
    height = max(1.0, bottom - top)

    first = ordered[0]
    region_left = float(first.abs_x) - float(first.rel_x)
    region_top = float(first.abs_y) - float(first.rel_y)
    poly_rel = [[left, top], [right, top], [right, bottom], [left, bottom]]
    poly_abs = [[x + region_left, y + region_top] for x, y in poly_rel]

    return OCRItem(
        text=text,
        confidence=max(float(i.confidence) for i in group),
        rel_x=left,
        rel_y=top,
        w=width,
        h=height,
        abs_x=left + region_left,
        abs_y=top + region_top,
        poly_abs=poly_abs,
        poly_rel=poly_rel,
        frame_id=first.frame_id,
        device=first.device,
        box_type="vertical",
    )


def sort_ocr_items_after_vertical_merge(items: list[OCRItem], config_dict: dict) -> list[OCRItem]:
    if not bool(config_dict.get("vertical_reading_order_enabled", True)):
        return sorted(items, key=lambda i: (round(float(i.rel_y) / 20), float(i.rel_x)))

    if not any(is_vertical_box_candidate(item, config_dict) for item in items):
        return sorted(items, key=lambda i: (round(float(i.rel_y) / 20), float(i.rel_x)))

    right_to_left = bool(config_dict.get("vertical_reading_right_to_left", True))

    def sort_key(item: OCRItem):
        center_x = float(item.rel_x) + float(item.w) * 0.5
        primary_x = -center_x if right_to_left else center_x
        vertical_rank = 0 if is_vertical_box_candidate(item, config_dict) else 1
        return vertical_rank, primary_x, float(item.rel_y)

    return sorted(items, key=sort_key)


def merge_vertical_box_columns(items: list[OCRItem], config_dict: dict) -> list[OCRItem]:
    if not items:
        return items
    if not bool(config_dict.get("vertical_box_filter_enabled", False)) and not bool(config_dict.get("vertical_box_merge_enabled", False)):
        return items

    candidates = [item for item in items if is_vertical_box_candidate(item, config_dict)]
    non_vertical_items = [item for item in items if item not in candidates]
    if bool(config_dict.get("vertical_box_filter_enabled", False)):
        others = [item for item in non_vertical_items if is_horizontal_box_candidate(item, config_dict)]
    else:
        others = non_vertical_items
    if not candidates:
        return sort_ocr_items_after_vertical_merge(others, config_dict)

    groups: list[list[OCRItem]] = []
    for item in sorted(candidates, key=lambda i: (float(i.rel_x) + float(i.w) * 0.5, float(i.rel_y))):
        matched_group = None
        for group in groups:
            if any(should_merge_vertical_column_item(item, existing, config_dict) for existing in group):
                matched_group = group
                break
        if matched_group is None:
            groups.append([item])
        else:
            matched_group.append(item)

    if bool(config_dict.get("vertical_box_merge_enabled", True)):
        vertical_items = [merged_vertical_column_item(group) if len(group) > 1 else group[0] for group in groups]
    else:
        vertical_items = [item for group in groups for item in group]

    return sort_ocr_items_after_vertical_merge(others + vertical_items, config_dict)


def meiki_line_bbox(line: dict) -> tuple[float, float, float, float] | None:
    raw_box = line.get("bbox") if isinstance(line, dict) else None
    if raw_box is not None:
        try:
            x1, y1, x2, y2 = [float(v) for v in raw_box[:4]]
            if x2 > x1 and y2 > y1:
                return x1, y1, x2, y2
        except Exception:
            pass

    boxes = []
    chars = line.get("chars", []) if isinstance(line, dict) else []
    for char_info in chars:
        try:
            x1, y1, x2, y2 = [float(v) for v in char_info.get("bbox", [])[:4]]
        except Exception:
            continue
        if x2 > x1 and y2 > y1:
            boxes.append((x1, y1, x2, y2))

    if not boxes:
        return None

    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def meiki_line_confidence(line: dict) -> float:
    confs = []
    chars = line.get("chars", []) if isinstance(line, dict) else []
    symbol_chars = japanese_aux_symbol_chars()
    non_symbols = [c for c in chars if not is_japanese_aux_symbol_char(str(c.get("char", "")), symbol_chars)]
    chars_for_confidence = non_symbols or chars
    for char_info in chars_for_confidence:
        try:
            confs.append(float(char_info.get("conf", 0.0)))
        except Exception:
            pass
    if confs:
        return sum(confs) / len(confs)
    try:
        return float(line.get("conf", 1.0))
    except Exception:
        return 1.0


def create_japanese_aux_ocr(config_dict: dict, device: str):
    if not bool(config_dict.get("japanese_aux_ocr_enabled", True)):
        return None

    mode = str(config_dict.get("japanese_aux_ocr_mode", "symbol_only") or "symbol_only").strip().lower()
    if mode in {"off", "disabled", "false", "paddle", "paddle_only"}:
        return None

    backend = str(config_dict.get("japanese_aux_ocr_backend", "meikiocr") or "").strip().lower()
    if backend not in {"meikiocr", "meiki", "meiki_ocr"}:
        print(f"[{device}] Japanese aux OCR backend is not supported: {backend}", flush=True)
        return None

    try:
        from meikiocr import MeikiOCR
    except Exception as e:
        print(f"[{device}] meikiocr unavailable; install with `pip install meikiocr`: {repr(e)}", flush=True)
        return None

    provider = str(config_dict.get("japanese_aux_ocr_provider", "auto") or "auto").strip()
    kwargs = {}
    if provider and provider.lower() != "auto":
        kwargs["provider"] = provider

    try:
        print(f"[{device}] meikiocr init...", flush=True)
        aux_ocr = MeikiOCR(**kwargs)
        print(f"[{device}] meikiocr ready", flush=True)
        return aux_ocr
    except Exception as e:
        print(f"[{device}] meikiocr init failed; Japanese aux OCR disabled: {repr(e)}", flush=True)
        return None


def run_meikiocr_raw(aux_ocr, img: np.ndarray, config_dict: dict, device: str) -> list[dict]:
    if aux_ocr is None:
        return []
    try:
        return aux_ocr.run_ocr(
            img,
            det_threshold=float(config_dict.get("japanese_aux_ocr_det_threshold", 0.45)),
            rec_threshold=float(config_dict.get("japanese_aux_ocr_rec_threshold", 0.08)),
            punct_conf_factor=0.2,
        )
    except Exception as e:
        print(f"[{device}] meikiocr run failed; using PaddleOCR only: {repr(e)}", flush=True)
        return []


def find_matching_item_for_aux_box(items: list[OCRItem], aux_box: tuple[float, float, float, float], used: set[int], scale: int, config_dict: dict) -> int | None:
    min_iou = float(config_dict.get("japanese_aux_ocr_match_iou", 0.05))
    min_overlap = float(config_dict.get("japanese_aux_ocr_match_overlap_ratio", 0.45))
    min_size_ratio = float(config_dict.get("japanese_aux_ocr_min_size_ratio", 0.25))
    max_center_dist = float(config_dict.get("japanese_aux_ocr_max_center_distance_px", 48.0)) * max(1, scale)

    best_idx = None
    best_score = -1.0
    for idx, item in enumerate(items):
        if idx in used:
            continue

        item_box = item_scaled_xyxy(item, scale)
        iou = xyxy_iou(aux_box, item_box)
        aux_coverage, item_coverage = xyxy_coverage_ratios(aux_box, item_box)
        size_ratio = xyxy_size_ratio(aux_box, item_box)
        center_dist = xyxy_center_distance(aux_box, item_box)

        has_iou_match = iou >= min_iou and size_ratio >= min_size_ratio
        has_coverage_match = aux_coverage >= min_overlap and item_coverage >= min_overlap
        has_center_match = center_dist <= max_center_dist and size_ratio >= min_size_ratio
        if not (has_iou_match or has_coverage_match or has_center_match):
            continue

        score = (
            iou * 3.0
            + min(aux_coverage, item_coverage) * 2.0
            + size_ratio
            + max(0.0, 1.0 - center_dist / max(1.0, max_center_dist))
        )
        if score > best_score:
            best_score = score
            best_idx = idx

    return best_idx


def merge_japanese_aux_symbols_only(items: list[OCRItem], aux_ocr, img: np.ndarray, scale: int, device: str, config_dict: dict) -> list[OCRItem]:
    if aux_ocr is None or not items:
        return items

    aux_results = run_meikiocr_raw(aux_ocr, img, config_dict, device)
    if not aux_results:
        return items

    min_line_conf = float(config_dict.get("japanese_aux_ocr_line_confidence", 0.60))
    log_changes = bool(config_dict.get("japanese_aux_ocr_log_changes", True))
    symbol_chars = japanese_aux_symbol_chars(config_dict)
    used: set[int] = set()
    changed = 0

    for line in aux_results:
        if not isinstance(line, dict):
            continue

        aux_text = normalize_japanese_aux_text(line.get("text", ""))
        if not any(is_japanese_aux_symbol_char(ch, symbol_chars) for ch in aux_text):
            continue

        aux_confidence = meiki_line_confidence(line)
        if aux_confidence < min_line_conf:
            continue

        aux_box = meiki_line_bbox(line)
        if aux_box is None or xyxy_area(aux_box) < 4:
            continue

        match_idx = find_matching_item_for_aux_box(items, aux_box, used, scale, config_dict)
        if match_idx is None:
            continue

        used.add(match_idx)
        item = items[match_idx]
        old_text = item.text
        new_text = add_missing_japanese_symbols_from_aux(old_text, aux_text, config_dict)
        if new_text != normalize_japanese_aux_text(old_text):
            item.text = new_text
            item.confidence = max(float(item.confidence), aux_confidence)
            changed += 1
            if log_changes:
                print(f"[{device}] meikiocr symbol補完: {old_text} -> {new_text} (meiki={aux_text})", flush=True)

    if changed and log_changes:
        print(f"[{device}] meikiocr symbol-only merged: changed_items={changed}", flush=True)

    return items


def crop_scaled_image_by_ratios(img: np.ndarray, left_ratio: float, top_ratio: float, right_ratio: float, bottom_ratio: float):
    height, width = img.shape[:2]
    left_ratio = clamp_ratio(left_ratio, 0.0)
    top_ratio = clamp_ratio(top_ratio, 0.0)
    right_ratio = clamp_ratio(right_ratio, 1.0)
    bottom_ratio = clamp_ratio(bottom_ratio, 1.0)
    if right_ratio <= left_ratio or bottom_ratio <= top_ratio:
        return None, 0, 0

    x1 = int(round(width * left_ratio))
    y1 = int(round(height * top_ratio))
    x2 = int(round(width * right_ratio))
    y2 = int(round(height * bottom_ratio))
    x1 = max(0, min(width - 1, x1))
    y1 = max(0, min(height - 1, y1))
    x2 = max(x1 + 1, min(width, x2))
    y2 = max(y1 + 1, min(height, y2))
    return img[y1:y2, x1:x2], x1, y1


def crop_scaled_image_by_item_segment(img: np.ndarray, item: OCRItem, scale: int, config_dict: dict):
    height, width = img.shape[:2]
    scale = max(1, int(scale))
    pad = max(0, int(config_dict.get("paddle_aux_symbol_refine_item_padding_px", 12)))
    top_ratio = clamp_ratio(config_dict.get("paddle_aux_symbol_refine_item_top_ratio", 0.0), 0.0)
    bottom_ratio = clamp_ratio(config_dict.get("paddle_aux_symbol_refine_item_bottom_ratio", 1.0), 1.0)
    if bottom_ratio <= top_ratio:
        bottom_ratio = min(1.0, top_ratio + 0.44)

    x1 = int(round((float(item.rel_x) - pad) * scale))
    x2 = int(round((float(item.rel_x + item.w) + pad) * scale))
    y1 = int(round((float(item.rel_y) + float(item.h) * top_ratio - pad) * scale))
    y2 = int(round((float(item.rel_y) + float(item.h) * bottom_ratio + pad) * scale))

    x1 = max(0, min(width - 1, x1))
    y1 = max(0, min(height - 1, y1))
    x2 = max(x1 + 1, min(width, x2))
    y2 = max(y1 + 1, min(height, y2))
    return img[y1:y2, x1:x2], x1, y1


def offset_ocr_item(item: OCRItem, dx_scaled: int, dy_scaled: int, scale: int) -> OCRItem:
    dx = float(dx_scaled) / max(1, int(scale))
    dy = float(dy_scaled) / max(1, int(scale))
    item.rel_x += dx
    item.rel_y += dy
    item.poly_rel = [[float(x) + dx, float(y) + dy] for x, y in item.poly_rel]
    return item


def parse_paddle_aux_result(raw_result: Any, api_mode: str, scale: int, region_left: int, region_top: int, frame_id: int, device: str, config_dict: dict) -> list[OCRItem]:
    min_conf = max(0.0, float(config_dict.get("min_ocr_confidence", 0.0)) - 0.15)
    min_len = int(config_dict.get("min_text_length", 1))
    if api_mode == "v3":
        return parse_paddle_v3_result(raw_result, min_conf, min_len, scale, region_left, region_top, frame_id, device, config_dict)
    return parse_paddle_v2_result(raw_result, min_conf, min_len, scale, region_left, region_top, frame_id, device, config_dict)


def merge_paddle_aux_symbols_for_vertical_items(
    items: list[OCRItem],
    ocr,
    img: np.ndarray,
    scale: int,
    region_left: int,
    region_top: int,
    frame_id: int,
    device: str,
    config_dict: dict,
) -> list[OCRItem]:
    symbol_chars = japanese_aux_symbol_chars(config_dict)
    changed = 0

    for item in items:
        if not is_vertical_box_candidate(item, config_dict):
            continue

        aux_img, dx_scaled, dy_scaled = crop_scaled_image_by_item_segment(img, item, scale, config_dict)
        if aux_img is None or aux_img.size == 0:
            continue

        try:
            raw_result, api_mode = predict_with_paddle(ocr, aux_img, config_dict)
            aux_region_left = int(region_left) + int(round(dx_scaled / max(1, scale)))
            aux_region_top = int(region_top) + int(round(dy_scaled / max(1, scale)))
            aux_items = parse_paddle_aux_result(raw_result, api_mode, scale, aux_region_left, aux_region_top, frame_id, f"{device}+paddle_aux", config_dict)
        except Exception as e:
            print(f"[{device}] paddle aux vertical symbol refine failed: {repr(e)}", flush=True)
            continue

        if not aux_items:
            continue

        aux_items = [offset_ocr_item(aux_item, dx_scaled, dy_scaled, scale) for aux_item in aux_items]
        item_box = item_scaled_xyxy(item, scale)
        accepted_aux_items: list[OCRItem] = []
        for aux_item in aux_items:
            aux_box = item_scaled_xyxy(aux_item, scale)
            aux_coverage, _ = xyxy_coverage_ratios(aux_box, item_box)
            if aux_coverage >= 0.45:
                accepted_aux_items.append(aux_item)

        old_text = item.text
        new_text = normalize_japanese_aux_text(old_text)
        best_confidence = float(item.confidence)
        for aux_item in accepted_aux_items:
            aux_text = normalize_japanese_aux_text(aux_item.text)
            if not any(is_japanese_aux_symbol_char(ch, symbol_chars) for ch in aux_text):
                continue
            new_text = add_missing_japanese_symbols_from_aux(new_text, aux_text, config_dict)
            best_confidence = max(best_confidence, float(aux_item.confidence))

        if new_text != normalize_japanese_aux_text(old_text):
            item.text = new_text
            item.confidence = best_confidence
            changed += 1
            if bool(config_dict.get("japanese_aux_ocr_log_changes", True)):
                aux_texts = [normalize_japanese_aux_text(aux_item.text) for aux_item in accepted_aux_items]
                print(f"[{device}] paddle aux vertical symbol補完: {old_text} -> {new_text} (aux={aux_texts})", flush=True)

    if changed and bool(config_dict.get("japanese_aux_ocr_log_changes", True)):
        print(f"[{device}] paddle aux vertical symbol refined: changed_items={changed}", flush=True)

    return items


def merge_paddle_aux_symbols_only(
    items: list[OCRItem],
    ocr,
    img: np.ndarray,
    scale: int,
    region_left: int,
    region_top: int,
    frame_id: int,
    device: str,
    config_dict: dict,
) -> list[OCRItem]:
    if not bool(config_dict.get("paddle_aux_symbol_refine_enabled", False)) or not items:
        return items

    mode = str(config_dict.get("paddle_aux_symbol_refine_mode", "ratio") or "ratio").strip().lower()
    if mode in {"vertical_box", "vertical_boxes", "item", "items", "box", "boxes"}:
        return merge_paddle_aux_symbols_for_vertical_items(items, ocr, img, scale, region_left, region_top, frame_id, device, config_dict)

    aux_img, dx_scaled, dy_scaled = crop_scaled_image_by_ratios(
        img,
        config_dict.get("paddle_aux_symbol_refine_left_ratio", 0.0),
        config_dict.get("paddle_aux_symbol_refine_top_ratio", 0.0),
        config_dict.get("paddle_aux_symbol_refine_right_ratio", 1.0),
        config_dict.get("paddle_aux_symbol_refine_bottom_ratio", 0.5),
    )
    if aux_img is None or aux_img.size == 0:
        return items

    try:
        raw_result, api_mode = predict_with_paddle(ocr, aux_img, config_dict)
        aux_region_left = int(region_left) + int(round(dx_scaled / max(1, scale)))
        aux_region_top = int(region_top) + int(round(dy_scaled / max(1, scale)))
        aux_items = parse_paddle_aux_result(raw_result, api_mode, scale, aux_region_left, aux_region_top, frame_id, f"{device}+paddle_aux", config_dict)
    except Exception as e:
        print(f"[{device}] paddle aux symbol refine failed: {repr(e)}", flush=True)
        return items

    if not aux_items:
        return items

    aux_items = [offset_ocr_item(item, dx_scaled, dy_scaled, scale) for item in aux_items]
    symbol_chars = japanese_aux_symbol_chars(config_dict)
    used: set[int] = set()
    changed = 0

    for aux_item in aux_items:
        aux_text = normalize_japanese_aux_text(aux_item.text)
        if not any(is_japanese_aux_symbol_char(ch, symbol_chars) for ch in aux_text):
            continue

        aux_box = item_scaled_xyxy(aux_item, scale)
        match_idx = find_matching_item_for_aux_box(items, aux_box, used, scale, config_dict)
        if match_idx is None:
            continue

        used.add(match_idx)
        item = items[match_idx]
        old_text = item.text
        new_text = add_missing_japanese_symbols_from_aux(old_text, aux_text, config_dict)
        if new_text != normalize_japanese_aux_text(old_text):
            item.text = new_text
            item.confidence = max(float(item.confidence), float(aux_item.confidence))
            changed += 1
            if bool(config_dict.get("japanese_aux_ocr_log_changes", True)):
                print(f"[{device}] paddle aux symbol補完: {old_text} -> {new_text} (aux={aux_text})", flush=True)

    if changed and bool(config_dict.get("japanese_aux_ocr_log_changes", True)):
        print(f"[{device}] paddle aux symbol refined: changed_items={changed}", flush=True)

    return items


def item_looks_vertical(item: OCRItem, config_dict: dict) -> bool:
    width = max(1.0, float(item.w))
    height = max(1.0, float(item.h))
    min_aspect = float(config_dict.get("vertical_box_min_aspect_ratio", config_dict.get("vertical_text_min_aspect_ratio", 1.55)))
    return height / width >= min_aspect


def text_from_refine_items(item: OCRItem, refine_items: list[OCRItem], config_dict: dict) -> str:
    if not refine_items:
        return ""

    if item_looks_vertical(item, config_dict):
        ordered = sorted(refine_items, key=lambda i: (float(i.rel_y), float(i.rel_x)))
        return "".join(normalize_japanese_aux_text(i.text) for i in ordered if normalize_japanese_aux_text(i.text))

    ordered = sorted(refine_items, key=lambda i: (round(float(i.rel_y) / 20), float(i.rel_x)))
    return normalize_text(" ".join(normalize_text(i.text) for i in ordered if normalize_text(i.text)))


def sentence_box_type(item: OCRItem, config_dict: dict) -> str:
    explicit_type = str(getattr(item, "box_type", "") or "").strip().lower()
    if explicit_type in {"vertical", "horizontal"}:
        return explicit_type
    return "vertical" if item_looks_vertical(item, config_dict) else "horizontal"


def horizontal_gap_px(a: OCRItem, b: OCRItem) -> float:
    ax1, _, ax2, _ = item_rel_xyxy(a)
    bx1, _, bx2, _ = item_rel_xyxy(b)
    return max(0.0, max(ax1, bx1) - min(ax2, bx2))


def vertical_gap_px(a: OCRItem, b: OCRItem) -> float:
    _, ay1, _, ay2 = item_rel_xyxy(a)
    _, by1, _, by2 = item_rel_xyxy(b)
    return max(0.0, max(ay1, by1) - min(ay2, by2))


HORIZONTAL_CONTINUATION_END_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "but",
    "by",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "under",
    "with",
    "without",
}
HORIZONTAL_TERMINAL_PUNCTUATION = set(".!?。！？")
HORIZONTAL_CLOSING_CHARS = "\"'”’」』）)]}>"


def text_ends_with_terminal_punctuation(text: str) -> bool:
    text = normalize_text(text).rstrip()
    while text and text[-1] in HORIZONTAL_CLOSING_CHARS:
        text = text[:-1].rstrip()
    return bool(text) and text[-1] in HORIZONTAL_TERMINAL_PUNCTUATION


def text_starts_like_line_continuation(text: str) -> bool:
    text = normalize_text(text).lstrip()
    if not text:
        return False
    first = text[0]
    return first in ",;:)]}、，" or first.islower()


def text_ends_with_continuation_word(text: str) -> bool:
    text = normalize_text(text).rstrip()
    words = re.findall(r"[A-Za-z]+", text)
    return bool(words) and words[-1].lower() in HORIZONTAL_CONTINUATION_END_WORDS


def horizontal_texts_look_continuous(a: OCRItem, b: OCRItem, config_dict: dict) -> bool:
    if not bool(config_dict.get("horizontal_sentence_require_continuation", True)):
        return True

    first, second = sorted([a, b], key=lambda i: (float(i.rel_y), float(i.rel_x)))
    first_text = normalize_text(first.text)
    second_text = normalize_text(second.text)
    if not first_text or not second_text:
        return False
    if text_ends_with_terminal_punctuation(first_text):
        return False
    if text_starts_like_line_continuation(second_text):
        return True
    if text_ends_with_continuation_word(first_text):
        return True
    return visible_char_count(first_text) >= 24 and visible_char_count(second_text) >= 8


def should_group_sentence_items(a: OCRItem, b: OCRItem, config_dict: dict) -> bool:
    box_type = sentence_box_type(a, config_dict)
    if box_type != sentence_box_type(b, config_dict):
        return False

    max_gap = max(0.0, float(config_dict.get("sentence_group_max_gap_px", 40.0)))
    min_overlap = max(0.0, min(1.0, float(config_dict.get("sentence_group_min_overlap_ratio", 0.20))))
    max_cross_gap = max(0.0, float(config_dict.get("sentence_group_max_cross_axis_gap_px", 12.0)))
    x_gap = horizontal_gap_px(a, b)
    y_gap = vertical_gap_px(a, b)

    if box_type == "vertical":
        same_column = xy_overlap_ratio(a, b) >= min_overlap or x_gap <= max_cross_gap
        stacked_close = same_column and y_gap <= max_gap
        adjacent_column = x_gap <= max_gap and (y_overlap_ratio(a, b) >= min_overlap or y_gap <= max_cross_gap)
        return stacked_close or adjacent_column

    same_line = y_overlap_ratio(a, b) >= min_overlap or y_gap <= max_cross_gap
    if same_line and x_gap <= max_gap:
        return True

    if bool(config_dict.get("horizontal_sentence_multiline_enabled", True)):
        line_gap = max(0.0, float(config_dict.get("horizontal_sentence_line_gap_px", 36.0)))
        min_x_overlap = max(0.0, min(1.0, float(config_dict.get("horizontal_sentence_min_x_overlap_ratio", 0.45))))
        multiline_close = y_gap <= line_gap and xy_overlap_ratio(a, b) >= min_x_overlap
        return multiline_close and horizontal_texts_look_continuous(a, b, config_dict)

    return False


def sentence_item_sort_key(item: OCRItem, config_dict: dict):
    box_type = sentence_box_type(item, config_dict)
    if box_type == "vertical":
        center_x = float(item.rel_x) + float(item.w) * 0.5
        primary_x = -center_x if bool(config_dict.get("vertical_reading_right_to_left", True)) else center_x
        return 0, primary_x, float(item.rel_y)
    return 1, round(float(item.rel_y) / 20), float(item.rel_x)


def vertical_item_center_x(item: OCRItem) -> float:
    return float(item.rel_x) + float(item.w) * 0.5


def vertical_items_same_column(a: OCRItem, b: OCRItem, config_dict: dict) -> bool:
    same_column_gap = float(config_dict.get("vertical_sentence_same_column_gap_px", config_dict.get("vertical_box_column_gap_px", 24.0)))
    min_overlap = max(0.0, min(1.0, float(config_dict.get("sentence_group_min_overlap_ratio", 0.20))))
    center_gap = abs(vertical_item_center_x(a) - vertical_item_center_x(b))
    if center_gap <= same_column_gap or xy_overlap_ratio(a, b) >= min_overlap:
        return True

    max_gap = max(0.0, float(config_dict.get("sentence_group_max_gap_px", 40.0)))
    stacked_close = y_gap_px(a, b) <= max_gap and y_overlap_ratio(a, b) < 0.35
    return stacked_close and horizontal_gap_px(a, b) <= same_column_gap


def ordered_vertical_sentence_group_items(group: list[OCRItem], config_dict: dict) -> list[OCRItem]:
    if len(group) <= 1:
        return list(group)

    right_to_left = bool(config_dict.get("vertical_reading_right_to_left", True))
    columns: list[list[OCRItem]] = []

    seed_order = sorted(group, key=lambda i: (-vertical_item_center_x(i) if right_to_left else vertical_item_center_x(i), float(i.rel_y)))
    for item in seed_order:
        matched_indices = [
            idx
            for idx, column in enumerate(columns)
            if any(vertical_items_same_column(item, existing, config_dict) for existing in column)
        ]
        if not matched_indices:
            columns.append([item])
            continue

        first_idx = matched_indices[0]
        columns[first_idx].append(item)
        for idx in reversed(matched_indices[1:]):
            columns[first_idx].extend(columns[idx])
            del columns[idx]

    def column_center_x(column: list[OCRItem]) -> float:
        return sum(vertical_item_center_x(item) for item in column) / max(1, len(column))

    columns.sort(key=lambda column: -column_center_x(column) if right_to_left else column_center_x(column))

    ordered: list[OCRItem] = []
    for column in columns:
        ordered.extend(sorted(column, key=lambda i: (float(i.rel_y), float(i.rel_x))))
    return ordered


def ordered_sentence_group_items(group: list[OCRItem], config_dict: dict) -> list[OCRItem]:
    if not group:
        return []

    box_type = sentence_box_type(group[0], config_dict)
    if box_type == "vertical":
        return ordered_vertical_sentence_group_items(group, config_dict)

    return sorted(group, key=lambda i: (round(float(i.rel_y) / 20), float(i.rel_x)))


def should_insert_ascii_word_space(left: str, right: str) -> bool:
    left = str(left or "")
    right = str(right or "")
    if not left or not right:
        return False
    return left[-1].isascii() and right[0].isascii() and left[-1].isalnum() and right[0].isalnum()


def join_horizontal_sentence_parts(parts: list[str], joiner: str) -> str:
    cleaned = [normalize_text(part) for part in parts if normalize_text(part)]
    if not cleaned:
        return ""
    if joiner:
        return normalize_text(joiner.join(cleaned))

    result = cleaned[0]
    for part in cleaned[1:]:
        if should_insert_ascii_word_space(result, part):
            result += " "
        result += part
    return normalize_text(result)


def sentence_group_text(group: list[OCRItem], config_dict: dict) -> str:
    ordered = ordered_sentence_group_items(group, config_dict)
    if not ordered:
        return ""

    box_type = sentence_box_type(ordered[0], config_dict)
    joiner_key = "sentence_group_vertical_joiner" if box_type == "vertical" else "sentence_group_horizontal_joiner"
    joiner = str(config_dict.get(joiner_key, ""))

    if box_type == "vertical":
        parts = [normalize_japanese_aux_text(i.text) for i in ordered if normalize_japanese_aux_text(i.text)]
        return normalize_text(joiner.join(parts))
    else:
        parts = [normalize_text(i.text) for i in ordered if normalize_text(i.text)]
        return join_horizontal_sentence_parts(parts, joiner)


def sentence_group_sort_key(group: list[OCRItem], config_dict: dict):
    ordered = ordered_sentence_group_items(group, config_dict)
    if not ordered:
        return 9, 0, 0
    return sentence_item_sort_key(ordered[0], config_dict)


def build_sentence_groups(items: list[OCRItem], config_dict: dict) -> list[list[OCRItem]]:
    if not bool(config_dict.get("sentence_grouping_enabled", True)) or not items:
        return [[item] for item in items]

    groups: list[list[OCRItem]] = []
    for item in sorted(items, key=lambda i: sentence_item_sort_key(i, config_dict)):
        matched_indices = [
            idx
            for idx, group in enumerate(groups)
            if any(should_group_sentence_items(item, existing, config_dict) for existing in group)
        ]
        if not matched_indices:
            groups.append([item])
            continue

        first_idx = matched_indices[0]
        groups[first_idx].append(item)
        for idx in reversed(matched_indices[1:]):
            groups[first_idx].extend(groups[idx])
            del groups[idx]

    groups.sort(key=lambda group: sentence_group_sort_key(group, config_dict))
    return groups


def print_sentence_groups(groups: list[list[OCRItem]], config_dict: dict, frame_id: int = 0, device: str = ""):
    if not bool(config_dict.get("sentence_group_debug_print", True)):
        return

    every = max(1, int(config_dict.get("sentence_group_debug_every_n_frames", 1)))
    if int(frame_id) % every != 0:
        return

    max_gap = float(config_dict.get("sentence_group_max_gap_px", 40.0))
    print(f"[sentence debug {device} frame={frame_id}] sentences={len(groups)} max_gap={max_gap:g}px", flush=True)
    for idx, group in enumerate(groups, start=1):
        ordered = ordered_sentence_group_items(group, config_dict)
        box_type = sentence_box_type(ordered[0], config_dict) if ordered else "unknown"
        text = sentence_group_text(group, config_dict)
        print(f"  {idx:02d}. {box_type} boxes={len(group)} text={text}", flush=True)


def group_items_into_sentences(items: list[OCRItem], config_dict: dict, frame_id: int = 0, device: str = "") -> list[OCRItem]:
    groups = build_sentence_groups(items, config_dict)
    print_sentence_groups(groups, config_dict, frame_id, device)
    return items


JAPANESE_CONTINUATION_SUFFIXES = (
    "が",
    "は",
    "を",
    "に",
    "で",
    "と",
    "へ",
    "の",
    "も",
    "や",
    "から",
    "ので",
    "けど",
    "けれど",
    "って",
    "と言って",
    "と言う",
    "なら",
    "たら",
    "ば",
    "し",
    "たり",
    "ながら",
    "つつ",
    "て",
)
JAPANESE_CONTINUATION_PREFIXES = ("、", "，", ",", "。", "って", "と", "で", "が", "は", "を", "に", "も", "から", "ので", "けど")


def has_unclosed_japanese_quote(text: str) -> bool:
    text = normalize_text(text)
    return (text.count("「") + text.count("『")) > (text.count("」") + text.count("』"))


def text_ends_like_continuation(text: str) -> bool:
    text = normalize_text(text).rstrip("、，, ")
    if not text or not contains_japanese_text(text):
        return False
    if has_unclosed_japanese_quote(text):
        return True
    return text.endswith(JAPANESE_CONTINUATION_SUFFIXES)


def text_starts_like_continuation(text: str) -> bool:
    text = normalize_text(text).lstrip()
    if not text or not contains_japanese_text(text):
        return False
    return text.startswith(JAPANESE_CONTINUATION_PREFIXES)


class LlamaCppGGUFTranslator:
    def __init__(self, config_dict: dict):
        self.config = config_dict
        self.loaded = False
        self.load_lock = threading.Lock()
        self.generate_lock = threading.Lock()
        self.llama = None
        self.runtime_device = "unknown"
        self.runtime_n_gpu_layers = 0

    def language_name(self, code: str) -> str:
        normalized = str(code or "").strip()
        mapping = {
            "auto": "the source language",
            "eng_Latn": "English",
            "en": "English",
            "english": "English",
            "jpn_Jpan": "Japanese",
            "ja": "Japanese",
            "japan": "Japanese",
            "japanese": "Japanese",
            "kor_Hang": "Korean",
            "ko": "Korean",
            "korean": "Korean",
            "it": "Italian",
            "italian": "Italian",
            "fr": "French",
            "french": "French",
            "pl": "Polish",
            "polish": "Polish",
            "zho_Hans": "Simplified Chinese",
            "zho_Hant": "Traditional Chinese",
            "zh": "Chinese",
            "ch": "Chinese",
            "chinese": "Chinese",
            "chinese_cht": "Traditional Chinese",
        }
        return mapping.get(normalized, normalized or "the source language")

    def model_path(self) -> str:
        path = str(self.config.get("translation_model_path", "") or "").strip()
        if not path:
            return ""

        candidate = Path(path)
        if candidate.is_file():
            return str(candidate)

        model_file = str(self.config.get("translation_model_file", "") or "").strip()
        if candidate.is_dir() and model_file:
            nested = candidate / model_file
            if nested.is_file():
                return str(nested)

        return path

    def desired_device(self) -> str:
        return resolve_translation_device(self.config)

    def llama_kwargs_for_device(self, device: str) -> dict:
        n_gpu_layers = int(self.config.get("translation_n_gpu_layers", -1))
        if device == "cpu":
            n_gpu_layers = 0
        elif n_gpu_layers == 0:
            n_gpu_layers = -1
        return dict(
            n_ctx=int(self.config.get("translation_n_ctx", 2048)),
            n_gpu_layers=n_gpu_layers,
            main_gpu=device_index(device),
            verbose=False,
        )

    def load_llama(self, Llama, model_path: str, device: str):
        kwargs = self.llama_kwargs_for_device(device)
        log_message(f"[translate] loading GGUF on {device} n_gpu_layers={kwargs['n_gpu_layers']}: {model_path}")
        llama = Llama(model_path=model_path, **kwargs)
        self.runtime_device = device
        self.runtime_n_gpu_layers = int(kwargs["n_gpu_layers"])
        return llama

    def load(self):
        if self.loaded:
            return

        with self.load_lock:
            if self.loaded:
                return

            configure_llama_cpp_dlls()

            from llama_cpp import Llama

            desired_device = self.desired_device()

            model_path = self.model_path()
            if model_path:
                print(f"[translate] loading GGUF: {model_path}", flush=True)
                resolved_model_path = model_path
            else:
                repo_id = str(self.config.get("translation_model_id", "") or "").strip()
                filename = str(self.config.get("translation_model_file", "") or "").strip()
                if not repo_id or not filename:
                    raise ValueError("translation_model_id and translation_model_file are required for llama_cpp_gguf")

                cached_path = find_cached_hf_file(repo_id, filename)
                if cached_path:
                    print(f"[translate] loading cached GGUF: {cached_path}", flush=True)
                    log_message(f"[translate] loading cached GGUF: {cached_path}")
                    resolved_model_path = str(cached_path)
                else:
                    print(f"[translate] downloading/loading GGUF: {repo_id} / {filename}", flush=True)
                    log_message(f"[translate] downloading/loading GGUF on {desired_device}: {repo_id} / {filename}")
                    from huggingface_hub import hf_hub_download

                    downloaded_path = hf_hub_download(repo_id=repo_id, filename=filename)
                    resolved_model_path = str(downloaded_path)

            try:
                self.llama = self.load_llama(Llama, resolved_model_path, desired_device)
            except Exception:
                if desired_device == "cpu":
                    raise
                log_message(f"[translate] GPU load failed on {desired_device}; trying CPU fallback:\n{traceback.format_exc()}")
                print(f"[translate] GPU load failed on {desired_device}; trying CPU fallback", flush=True)
                self.llama = self.load_llama(Llama, resolved_model_path, "cpu")
                self.runtime_device = f"cpu(fallback:{desired_device})"

            self.loaded = True
            print(f"[translate] GGUF ready on {self.runtime_device}", flush=True)

    def build_prompt(self, text: str, context: str = "") -> str:
        source = self.language_name(str(self.config.get("translation_source_language", "auto")))
        target = self.language_name(str(self.config.get("translation_target_language", "ko")))
        template = str(self.config.get("translation_prompt_template", "") or "")
        if not template:
            template = "Translate the following text from {source} to natural {target}.\n\nText: {text}"
        prompt = template.format(source=source, target=target, text=text)
        context = normalize_text(context)
        if not context:
            return prompt
        return (
            "Use the previous OCR context only to resolve continuity, omitted subjects, and speaker attribution. "
            "Do not translate the context again. Translate only the current text. "
            "If the current text continues the previous sentence, carry over only the minimal missing words needed for a natural overlay.\n\n"
            f"Previous context:\n{context}\n\n"
            f"{prompt}"
        )

    def clean_output(self, output: str, fallback: str) -> str:
        text = normalize_text(output)
        text = re.sub(
            r"^(translation|answer|번역|english|japanese|korean|chinese|simplified chinese|traditional chinese)\s*[:：]\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = text.strip().strip("\"'`“”‘’")
        return text or fallback

    def translate_one(self, text: str, context: str = "") -> str:
        source = normalize_text(text)
        if not source:
            return ""

        self.load()
        prompt = self.build_prompt(source, context)

        with self.generate_lock:
            try:
                response = self.llama.create_chat_completion(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=float(self.config.get("translation_temperature", 0.1)),
                    top_p=float(self.config.get("translation_top_p", 0.95)),
                    top_k=int(self.config.get("translation_top_k", 64)),
                    max_tokens=int(self.config.get("translation_max_new_tokens", 128)),
                )
                translated = response["choices"][0]["message"]["content"]
                return self.clean_output(translated, source)
            except Exception as chat_error:
                try:
                    response = self.llama(
                        prompt,
                        max_tokens=int(self.config.get("translation_max_new_tokens", 128)),
                        temperature=float(self.config.get("translation_temperature", 0.1)),
                        top_p=float(self.config.get("translation_top_p", 0.95)),
                        top_k=int(self.config.get("translation_top_k", 64)),
                        echo=False,
                    )
                    translated = response["choices"][0]["text"]
                    return self.clean_output(translated, source)
                except Exception as plain_error:
                    print(f"[translate] GGUF failed: chat={repr(chat_error)} plain={repr(plain_error)}", flush=True)
                    return source


class SentenceTranslationManager:
    def __init__(self, config_dict: dict):
        self.config = config_dict
        self.cache_path = Path(str(config_dict.get("translation_cache_path", "translation_cache_gemma4_e2b_gguf_sentence.json")))
        self.cache = self._load_cache()
        self.translator = LlamaCppGGUFTranslator(config_dict)
        self.context_history: list[dict[str, Any]] = []

    def _load_cache(self) -> dict:
        if not self.cache_path.exists():
            return {}
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_cache(self):
        try:
            self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[translate] cache save failed: {repr(e)}", flush=True)

    def cache_key(self, source: str, context: str = "") -> str:
        prompt = str(self.config.get("translation_prompt_template", "") or "")
        prompt_key = hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:10] if prompt else "noprompt"
        context = normalize_text(context)
        context_key = hashlib.sha1(context.encode("utf-8")).hexdigest()[:10] if context else "nocontext"
        model_id = str(self.config.get("translation_model_id", "") or "")
        model_file = str(self.config.get("translation_model_file", "") or "")
        source_lang = str(self.config.get("translation_source_language", "auto") or "auto")
        target_lang = str(self.config.get("translation_target_language", "ko") or "ko")
        return f"__sentence_tr__:{model_id}/{model_file}:{source_lang}->{target_lang}:p={prompt_key}:c={context_key}:{normalize_text(source)}"

    def recent_context_entries(self, source: str, frame_id: int) -> list[dict[str, Any]]:
        if not bool(self.config.get("translation_context_enabled", True)):
            return []
        source = normalize_text(source)
        if not source:
            return []

        max_entries = max(0, int(self.config.get("translation_context_max_entries", 4)))
        if max_entries <= 0:
            return []
        max_age = int(self.config.get("translation_context_max_frame_age", 24))

        entries: list[dict[str, Any]] = []
        for entry in self.context_history:
            entry_source = normalize_text(entry.get("source", ""))
            if not entry_source or entry_source == source:
                continue
            try:
                age = int(frame_id) - int(entry.get("frame_id", frame_id))
            except Exception:
                age = 0
            if max_age >= 0 and age > max_age:
                continue
            entries.append(entry)
        return entries[-max_entries:]

    def should_use_context(self, source: str, entries: list[dict[str, Any]]) -> bool:
        if not entries:
            return False
        mode = str(self.config.get("translation_context_mode", "auto") or "auto").strip().lower()
        if mode in {"off", "false", "disabled", "none"}:
            return False
        if mode in {"always", "on", "true"}:
            return True

        if text_starts_like_continuation(source) or text_ends_like_continuation(source):
            return True
        return any(text_ends_like_continuation(str(entry.get("source", ""))) for entry in entries)

    def build_translation_context(self, source: str, frame_id: int) -> str:
        entries = self.recent_context_entries(source, frame_id)
        if not self.should_use_context(source, entries):
            return ""

        use_translated = bool(self.config.get("translation_context_use_translated_history", True))
        lines: list[str] = []
        for entry in entries:
            entry_source = normalize_text(entry.get("source", ""))
            if not entry_source:
                continue
            lines.append(f"Source: {entry_source}")
            entry_translation = normalize_text(entry.get("translated", ""))
            if use_translated and entry_translation and entry_translation != entry_source:
                lines.append(f"Translation: {entry_translation}")

        context = "\n".join(lines).strip()
        max_chars = max(0, int(self.config.get("translation_context_max_chars", 700)))
        if max_chars and len(context) > max_chars:
            context = context[-max_chars:].lstrip()
        return context

    def remember_context(self, source: str, translated: str, frame_id: int, box_type: str):
        if not bool(self.config.get("translation_context_enabled", True)):
            return
        source = normalize_text(source)
        if not source:
            return

        translated = normalize_text(translated)
        if translated == source:
            translated = ""

        for entry in reversed(self.context_history[-4:]):
            if normalize_text(entry.get("source", "")) == source:
                entry["translated"] = translated
                entry["frame_id"] = int(frame_id)
                entry["box_type"] = box_type
                return

        self.context_history.append(
            {
                "source": source,
                "translated": translated,
                "frame_id": int(frame_id),
                "box_type": box_type,
            }
        )
        max_entries = max(1, int(self.config.get("translation_context_max_entries", 4)))
        keep = max(12, max_entries * 4)
        if len(self.context_history) > keep:
            self.context_history = self.context_history[-keep:]

    def translate_text(self, source: str, context: str = "") -> str:
        source = normalize_text(source)
        if not source:
            return ""

        key = self.cache_key(source, context)
        cached = self.cache.get(key)
        if cached:
            return cached

        translated = self.translator.translate_one(source, context)
        self.cache[key] = translated
        self._save_cache()
        return translated

    def box_text_length_px(self, item: OCRItem) -> float:
        box_type = sentence_box_type(item, self.config)
        if box_type == "vertical":
            return max(1.0, float(item.h))
        return max(1.0, float(item.w))

    def split_translation_for_group(self, group: list[OCRItem], translated: str) -> dict[int, str]:
        ordered = ordered_sentence_group_items(group, self.config)
        text = normalize_text(translated)
        if not ordered:
            return {}
        if not text:
            return {id(item): "" for item in ordered}
        if len(ordered) == 1 or not bool(self.config.get("translation_split_across_boxes", True)):
            return {id(item): text if item is ordered[0] else "" for item in ordered}

        chars = list(text)
        weights = [self.box_text_length_px(item) for item in ordered]
        total_weight = max(1.0, sum(weights))
        result: dict[int, str] = {}
        start = 0
        cumulative = 0.0

        for idx, item in enumerate(ordered):
            if idx == len(ordered) - 1:
                end = len(chars)
            else:
                cumulative += weights[idx]
                ideal_end = int(round(len(chars) * cumulative / total_weight))
                remaining_boxes = len(ordered) - idx - 1
                if len(chars) - start > remaining_boxes:
                    ideal_end = max(start + 1, ideal_end)
                end = min(max(start, ideal_end), max(start, len(chars) - remaining_boxes))

            result[id(item)] = "".join(chars[start:end]).strip()
            start = end

        return result

    def should_use_bottom_caption_for_vertical_english(self) -> bool:
        if not bool(self.config.get("vertical_en_bottom_caption_enabled", True)):
            return False
        source = str(self.config.get("translation_source_language", "") or "").strip().lower()
        paddle_lang = str(self.config.get("paddle_lang", "") or "").strip().lower()
        target = str(self.config.get("translation_target_language", "") or "").strip().lower()
        japanese_sources = {"ja", "japan", "japanese", "jpn_jpan"}
        english_targets = {"en", "english", "eng_latn"}
        source_is_japanese = source in japanese_sources or (source == "auto" and paddle_lang in japanese_sources)
        return source_is_japanese and target in english_targets

    def vertical_group_score(self, group: list[OCRItem]) -> tuple[float, float, float]:
        metric = str(self.config.get("vertical_en_bottom_caption_group_metric", "area") or "area").strip().lower()
        area = sum(max(1.0, float(item.w)) * max(1.0, float(item.h)) for item in group)
        length = sum(visible_char_count(item.text) for item in group)
        boxes = float(len(group))
        if metric in {"text", "text_length", "chars", "characters"}:
            return float(length), area, boxes
        if metric in {"box", "boxes", "box_count", "count"}:
            return boxes, area, float(length)
        return area, float(length), boxes

    def largest_vertical_group_index(self, groups: list[list[OCRItem]]) -> int | None:
        best_idx = None
        best_score = None
        for idx, group in enumerate(groups, start=1):
            ordered = ordered_sentence_group_items(group, self.config)
            if not ordered or sentence_box_type(ordered[0], self.config) != "vertical":
                continue
            score = self.vertical_group_score(group)
            if best_score is None or score > best_score:
                best_idx = idx
                best_score = score
        return best_idx

    def annotate_and_print(self, items: list[OCRItem], groups: list[list[OCRItem]], frame_id: int, device: str) -> list[OCRItem]:
        if not bool(self.config.get("enable_translation", True)):
            return items
        if str(self.config.get("translation_backend", "llama_cpp_gguf")).strip().lower() != "llama_cpp_gguf":
            return items

        debug = bool(self.config.get("translation_debug_print", True))
        if debug:
            print(f"[translation debug {device} frame={frame_id}] sentences={len(groups)}", flush=True)

        bottom_caption_group_id = self.largest_vertical_group_index(groups) if self.should_use_bottom_caption_for_vertical_english() else None

        for idx, group in enumerate(groups, start=1):
            source = sentence_group_text(group, self.config)
            ordered = ordered_sentence_group_items(group, self.config)
            box_type = sentence_box_type(ordered[0], self.config) if ordered else "unknown"
            context = self.build_translation_context(source, frame_id)
            raw_translated = self.translate_text(source, context)
            translated = raw_translated
            if bool(self.config.get("translation_hide_source_fallback", True)) and normalize_text(translated) == normalize_text(source):
                translated = ""
            use_bottom_caption = bottom_caption_group_id == idx and bool(translated)
            per_box_translation = {} if use_bottom_caption else self.split_translation_for_group(group, translated)
            for item in group:
                item.sentence_id = idx
                item.sentence_text = source
                item.sentence_translated_text = translated
                item.translated_text = "" if use_bottom_caption else per_box_translation.get(id(item), "")
                item.bottom_caption_text = translated if use_bottom_caption else ""
                item.bottom_caption_group_id = idx if use_bottom_caption else 0
            if debug:
                caption_note = " bottom_caption" if use_bottom_caption else ""
                print(f"  {idx:02d}. {box_type} boxes={len(group)}{caption_note}", flush=True)
                print(f"      source: {source}", flush=True)
                if context and bool(self.config.get("translation_context_debug_print", True)):
                    compact_context = context.replace("\n", " | ")
                    if len(compact_context) > 220:
                        compact_context = compact_context[-220:]
                    print(f"      context: {compact_context}", flush=True)
                print(f"      target: {translated}", flush=True)
                if len(group) > 1 and translated and not use_bottom_caption:
                    for box_idx, item in enumerate(ordered, start=1):
                        print(f"      box {box_idx:02d}: {per_box_translation.get(id(item), '')}", flush=True)
            self.remember_context(source, raw_translated, frame_id, box_type)

        return items


def should_apply_box_refined_text(item: OCRItem, refined_text: str, aux_confidence: float, config_dict: dict) -> bool:
    old_text = item.text
    refined_text = normalize_text(refined_text)
    if not refined_text:
        return False
    if not should_keep_ocr_text(refined_text, config_dict):
        return False

    old_count = visible_char_count(old_text)
    new_count = visible_char_count(refined_text)
    min_ratio = float(config_dict.get("paddle_box_refine_min_text_ratio", 0.55))
    if old_count >= 4 and new_count < old_count * min_ratio:
        return False

    long_horizontal_min_chars = int(config_dict.get("paddle_box_refine_long_horizontal_min_chars", 12))
    if not item_looks_vertical(item, config_dict) and old_count >= long_horizontal_min_chars:
        min_gain = float(config_dict.get("paddle_box_refine_long_horizontal_min_conf_gain", 0.02))
        if aux_confidence < float(item.confidence) + min_gain:
            return False
    return True


def preserve_japanese_symbols(base_text: str, source_texts: list[str], config_dict: dict) -> str:
    result = base_text
    for source_text in source_texts:
        if has_configured_japanese_aux_symbol(source_text, config_dict):
            result = add_missing_japanese_symbols_from_aux(result, source_text, config_dict)
    return result


def refine_items_with_paddle_box_crops(
    items: list[OCRItem],
    ocr,
    img: np.ndarray,
    scale: int,
    region_left: int,
    region_top: int,
    frame_id: int,
    device: str,
    config_dict: dict,
) -> list[OCRItem]:
    if not bool(config_dict.get("paddle_box_refine_enabled", False)) or not items:
        return items

    changed = 0
    min_coverage = float(config_dict.get("paddle_box_refine_min_aux_coverage", 0.45))
    padding = int(config_dict.get("paddle_box_refine_padding_px", config_dict.get("paddle_aux_symbol_refine_item_padding_px", 20)))

    for item in items:
        crop_config = dict(config_dict)
        crop_config["paddle_aux_symbol_refine_item_padding_px"] = padding
        crop_config["paddle_aux_symbol_refine_item_top_ratio"] = 0.0
        crop_config["paddle_aux_symbol_refine_item_bottom_ratio"] = 1.0

        aux_img, dx_scaled, dy_scaled = crop_scaled_image_by_item_segment(img, item, scale, crop_config)
        if aux_img is None or aux_img.size == 0:
            continue

        try:
            raw_result, api_mode = predict_with_paddle(ocr, aux_img, config_dict)
            aux_region_left = int(region_left) + int(round(dx_scaled / max(1, scale)))
            aux_region_top = int(region_top) + int(round(dy_scaled / max(1, scale)))
            aux_items = parse_paddle_aux_result(raw_result, api_mode, scale, aux_region_left, aux_region_top, frame_id, f"{device}+box_refine", config_dict)
        except Exception as e:
            print(f"[{device}] paddle box refine failed: {repr(e)}", flush=True)
            continue

        if not aux_items:
            continue

        aux_items = [offset_ocr_item(aux_item, dx_scaled, dy_scaled, scale) for aux_item in aux_items]
        item_box = item_scaled_xyxy(item, scale)
        accepted_aux_items = []
        for aux_item in aux_items:
            aux_box = item_scaled_xyxy(aux_item, scale)
            aux_coverage, _ = xyxy_coverage_ratios(aux_box, item_box)
            if aux_coverage >= min_coverage:
                accepted_aux_items.append(aux_item)

        refined_text = text_from_refine_items(item, accepted_aux_items, config_dict)
        old_text = item.text
        refined_text = preserve_japanese_symbols(
            refined_text,
            [old_text] + [normalize_japanese_aux_text(aux_item.text) for aux_item in accepted_aux_items],
            config_dict,
        )
        aux_confidence = max(float(aux_item.confidence) for aux_item in accepted_aux_items) if accepted_aux_items else 0.0

        if not should_apply_box_refined_text(item, refined_text, aux_confidence, config_dict):
            continue

        if refined_text != normalize_text(old_text):
            item.text = refined_text
            item.confidence = max(float(item.confidence), aux_confidence)
            changed += 1
            if bool(config_dict.get("paddle_box_refine_log_changes", True)):
                aux_texts = [normalize_text(aux_item.text) for aux_item in accepted_aux_items]
                print(f"[{device}] paddle box refine: {old_text} -> {refined_text} (aux={aux_texts})", flush=True)

    if changed and bool(config_dict.get("paddle_box_refine_log_changes", True)):
        print(f"[{device}] paddle box refined: changed_items={changed}", flush=True)

    return items


def preprocess_for_ocr(pil_img: Image.Image, scale: int) -> np.ndarray:
    img = np.array(pil_img.convert("RGB"))
    if scale > 1:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)


def bbox_to_item(poly, scale: int, region_left: int, region_top: int, text: str, score: float, frame_id: int, device: str):
    arr = np.array(poly, dtype=np.float32)

    if arr.ndim == 1 and arr.size == 4:
        x1, y1, x2, y2 = arr.tolist()
        points = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
    else:
        points = arr.reshape(-1, 2).tolist()

    poly_rel = [[float(x) / scale, float(y) / scale] for x, y in points]
    xs = [p[0] for p in poly_rel]
    ys = [p[1] for p in poly_rel]

    rel_x = float(min(xs))
    rel_y = float(min(ys))
    w = float(max(xs) - rel_x)
    h = float(max(ys) - rel_y)

    poly_abs = [[float(x + region_left), float(y + region_top)] for x, y in poly_rel]

    return OCRItem(
        text=text,
        confidence=score,
        rel_x=rel_x,
        rel_y=rel_y,
        w=w,
        h=h,
        abs_x=rel_x + region_left,
        abs_y=rel_y + region_top,
        poly_abs=poly_abs,
        poly_rel=poly_rel,
        frame_id=frame_id,
        device=device,
    )


def get_result_dict(res: Any) -> dict:
    if isinstance(res, dict):
        return res.get("res", res)

    for name in ("json", "dict", "to_dict", "as_dict"):
        attr = getattr(res, name, None)
        if attr is None:
            continue
        try:
            value = attr() if callable(attr) else attr
            if isinstance(value, dict):
                return value.get("res", value)
        except Exception:
            pass

    attr = getattr(res, "res", None)
    if isinstance(attr, dict):
        return attr

    if hasattr(res, "__dict__"):
        d = dict(res.__dict__)
        return d.get("res", d)

    return {}


def first_present_mapping_value(mapping: dict, *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def parse_paddle_v3_result(result: Any, min_conf: float, min_len: int, scale: int, region_left: int, region_top: int, frame_id: int, device: str, config_dict: dict):
    items = []
    if not isinstance(result, (list, tuple)):
        result = [result]

    for res in result:
        data = get_result_dict(res)
        if not data:
            continue

        texts = first_present_mapping_value(data, "rec_texts", "texts", default=[])
        scores = first_present_mapping_value(data, "rec_scores", "scores", default=[])
        locs = first_present_mapping_value(data, "rec_polys", "dt_polys", "polys", "rec_boxes", "boxes")
        if locs is None:
            continue

        n = min(len(texts), len(locs))
        for idx in range(n):
            text = normalize_text(texts[idx])
            if not text or len(text) < min_len:
                continue
            if not should_keep_ocr_text(text, config_dict):
                continue

            try:
                score = float(scores[idx]) if idx < len(scores) else 1.0
            except Exception:
                score = 1.0
            if score < min_conf:
                continue

            try:
                item = bbox_to_item(locs[idx], scale, region_left, region_top, text, score, frame_id, device)
            except Exception:
                continue

            if item.w < 10 or item.h < 10:
                continue
            items.append(item)

    items.sort(key=lambda i: (round(i.rel_y / 20), i.rel_x))
    return items


def parse_paddle_v2_result(result: Any, min_conf: float, min_len: int, scale: int, region_left: int, region_top: int, frame_id: int, device: str, config_dict: dict):
    items = []
    if result is None:
        return items

    candidates = result
    if isinstance(result, list) and len(result) == 1 and isinstance(result[0], list):
        candidates = result[0]

    for line in candidates:
        try:
            box = line[0]
            text = normalize_text(line[1][0])
            score = float(line[1][1])
        except Exception:
            continue

        if not text or len(text) < min_len or score < min_conf:
            continue
        if not should_keep_ocr_text(text, config_dict):
            continue

        try:
            item = bbox_to_item(box, scale, region_left, region_top, text, score, frame_id, device)
        except Exception:
            continue

        if item.w < 10 or item.h < 10:
            continue
        items.append(item)

    items.sort(key=lambda i: (round(i.rel_y / 20), i.rel_x))
    return items


def create_paddle_ocr(config_dict: dict, device: str):
    from paddleocr import PaddleOCR

    kwargs = dict(
        lang=config_dict["paddle_lang"],
        ocr_version=config_dict["ocr_version"],
        device=device,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        enable_hpi=config_dict["enable_hpi"],
        use_tensorrt=config_dict["use_tensorrt"],
        precision=config_dict["precision"],
    )
    det_model_dir = str(config_dict.get("ocr_text_detection_model_dir", "") or "").strip()
    rec_model_dir = str(config_dict.get("ocr_text_recognition_model_dir", "") or "").strip()
    if det_model_dir:
        kwargs["text_detection_model_dir"] = det_model_dir
    if rec_model_dir:
        kwargs["text_recognition_model_dir"] = rec_model_dir

    try:
        return PaddleOCR(**kwargs), "v3"
    except TypeError:
        pass

    legacy_kwargs = dict(
        lang=config_dict["paddle_lang"],
        use_gpu=device.startswith("gpu"),
        gpu_id=int(device.split(":")[1]) if device.startswith("gpu:") else 0,
        use_angle_cls=False,
        show_log=False,
    )
    if det_model_dir:
        legacy_kwargs["det_model_dir"] = det_model_dir
    if rec_model_dir:
        legacy_kwargs["rec_model_dir"] = rec_model_dir

    try:
        return PaddleOCR(**legacy_kwargs), "v2"
    except TypeError:
        return PaddleOCR(lang=config_dict["paddle_lang"]), "unknown"


def predict_with_paddle(ocr, img, config_dict: dict):
    if hasattr(ocr, "predict"):
        kwargs = dict(
            text_det_thresh=config_dict["text_det_thresh"],
            text_det_box_thresh=config_dict["text_det_box_thresh"],
            text_det_unclip_ratio=config_dict["text_det_unclip_ratio"],
            text_rec_score_thresh=config_dict["text_rec_score_thresh"],
        )
        try:
            return ocr.predict(img, **kwargs), "v3"
        except TypeError:
            return ocr.predict(img), "v3"

    return ocr.ocr(img, cls=False), "v2"


def warmup_paddle_ocr(ocr, config_dict: dict):
    warmup_img = np.zeros((64, 64, 3), dtype=np.uint8)
    predict_with_paddle(ocr, warmup_img, config_dict)


def publish_model_status(out_q: mp.Queue, device: str, model: str, status: str, message: str = ""):
    log_message(f"model_status device={device} model={model} status={status} message={message}")
    try:
        out_q.put(
            {
                "type": "model_status",
                "device": device,
                "model": model,
                "status": status,
                "message": message,
            }
        )
    except Exception:
        pass


def preload_translation_model_status(translation_manager: SentenceTranslationManager, out_q: mp.Queue, device: str):
    try:
        translation_manager.translator.load()
        runtime_device = str(getattr(translation_manager.translator, "runtime_device", device) or device)
        status = "fallback" if "fallback:" in runtime_device else "ready"
        publish_model_status(out_q, runtime_device, "translation", status, f"GGUF ready on {runtime_device}")
    except Exception as exc:
        log_message(f"translation preload failed on {device}:\n{traceback.format_exc()}")
        publish_model_status(out_q, device, "translation", "error", repr(exc))



def save_paddle_visualization(raw_result: Any, frame_id: int, device: str, config_dict: dict):
    # PaddleOCR Result 시각화 저장.
    # 1순위: res.save_to_img(path)
    # 2순위: res.img["ocr_res_img"].save(path)
    if not bool(config_dict.get("save_paddle_visualization", True)):
        return

    every = max(1, int(config_dict.get("save_vis_every_n_frames", 10)))
    if frame_id % every != 0:
        return

    out_dir = Path(config_dict.get("paddle_visualization_dir", "paddle_vis"))
    out_dir.mkdir(parents=True, exist_ok=True)

    safe_device = str(device).replace(":", "_").replace("/", "_").replace("\\", "_")
    frame_path = out_dir / f"frame_{frame_id:08d}_{safe_device}.png"
    latest_path = out_dir / "latest.png"

    if not isinstance(raw_result, (list, tuple)):
        results = [raw_result]
    else:
        results = list(raw_result)

    if not results:
        return

    res = results[0]
    saved = False

    try:
        if hasattr(res, "save_to_img"):
            res.save_to_img(str(frame_path))
            saved = frame_path.exists()
    except Exception as e:
        print(f"[vis] save_to_img failed: {repr(e)}", flush=True)

    if not saved:
        try:
            img_dict = getattr(res, "img", None)
            if isinstance(img_dict, dict) and "ocr_res_img" in img_dict:
                img_dict["ocr_res_img"].save(str(frame_path))
                saved = True
        except Exception as e:
            print(f"[vis] res.img fallback failed: {repr(e)}", flush=True)

    if saved and bool(config_dict.get("save_vis_latest_copy", True)):
        try:
            from shutil import copyfile
            copyfile(frame_path, latest_path)
        except Exception as e:
            print(f"[vis] latest copy failed: {repr(e)}", flush=True)


def ocr_worker_process(device: str, config_dict: dict, in_q: mp.Queue, out_q: mp.Queue):
    requested_device = device
    run_device = device
    try:
        configure_accelerator_dlls()
        print(f"[{requested_device}] PaddleOCR init...", flush=True)
        log_message(f"[{requested_device}] PaddleOCR init")
        publish_model_status(out_q, requested_device, "ocr", "loading", "PaddleOCR loading")
        try:
            ocr, _ = create_paddle_ocr(config_dict, requested_device)
            warmup_paddle_ocr(ocr, config_dict)
        except Exception as gpu_exc:
            log_message(f"[{requested_device}] PaddleOCR init failed:\n{traceback.format_exc()}")
            if not str(requested_device).startswith("gpu"):
                raise
            run_device = "cpu"
            print(f"[{requested_device}] PaddleOCR GPU init failed; trying CPU fallback: {repr(gpu_exc)}", flush=True)
            publish_model_status(out_q, requested_device, "ocr", "loading", "GPU failed; trying CPU fallback")
            ocr, _ = create_paddle_ocr(config_dict, run_device)
            warmup_paddle_ocr(ocr, config_dict)

        ready_message = "PaddleOCR ready" if run_device == requested_device else f"PaddleOCR ready on CPU fallback from {requested_device}"
        ready_status = "ready" if run_device == requested_device else "fallback"
        print(f"[{run_device}] {ready_message}", flush=True)
        log_message(f"[{requested_device}] {ready_message}")
        publish_model_status(out_q, requested_device, "ocr", ready_status, ready_message)
    except Exception as e:
        tb = traceback.format_exc()
        log_message(f"[{requested_device}] PaddleOCR init failed permanently:\n{tb}")
        publish_model_status(out_q, requested_device, "ocr", "error", repr(e))
        out_q.put({"type": "error", "device": requested_device, "message": f"init failed: {repr(e)}\n{tb}"})
        return

    aux_ocr = create_japanese_aux_ocr(config_dict, run_device)
    translation_manager = None
    if bool(config_dict.get("enable_translation", True)):
        backend = str(config_dict.get("translation_backend", "llama_cpp_gguf") or "").strip().lower()
        if backend == "llama_cpp_gguf":
            translation_manager = SentenceTranslationManager(config_dict)
            translation_device = resolve_translation_device(config_dict)
            repo_id = str(config_dict.get("translation_model_id", "") or "").strip()
            filename = str(config_dict.get("translation_model_file", "") or "").strip()
            cached = bool(translation_manager.translator.model_path()) or bool(find_cached_hf_file(repo_id, filename))
            loading_message = f"GGUF loading on {translation_device}" if cached else f"GGUF downloading/loading on {translation_device}"
            publish_model_status(out_q, translation_device, "translation", "loading", loading_message)
            threading.Thread(
                target=preload_translation_model_status,
                args=(translation_manager, out_q, translation_device),
                daemon=True,
            ).start()
        else:
            publish_model_status(out_q, requested_device, "translation", "error", f"unsupported backend: {backend}")
            print(f"[translate] unsupported backend for this script: {backend}", flush=True)
    else:
        publish_model_status(out_q, requested_device, "translation", "disabled", "translation disabled")

    min_conf = float(config_dict["min_ocr_confidence"])
    min_len = int(config_dict.get("min_text_length", 2))
    scale = float(config_dict["ocr_scale"])
    result_device = run_device if run_device == requested_device else f"{run_device}(fallback:{requested_device})"

    while True:
        job = in_q.get()
        if job is None:
            break

        if isinstance(job, tuple) and len(job) >= 4:
            frame_id, img, region_left, region_top = job[:4]
        else:
            frame_id, img = job
            region_left = int(config_dict["capture_region"]["left"])
            region_top = int(config_dict["capture_region"]["top"])

        start = time.perf_counter()
        try:
            raw_result, api_mode = predict_with_paddle(ocr, img, config_dict)
            save_paddle_visualization(raw_result, frame_id, result_device, config_dict)
            if api_mode == "v3":
                items = parse_paddle_v3_result(raw_result, min_conf, min_len, scale, region_left, region_top, frame_id, result_device, config_dict)
            else:
                items = parse_paddle_v2_result(raw_result, min_conf, min_len, scale, region_left, region_top, frame_id, result_device, config_dict)

            items = merge_vertical_box_columns(items, config_dict)
            items = merge_paddle_aux_symbols_only(items, ocr, img, scale, region_left, region_top, frame_id, result_device, config_dict)
            items = refine_items_with_paddle_box_crops(items, ocr, img, scale, region_left, region_top, frame_id, result_device, config_dict)

            japanese_ocr_mode = str(config_dict.get("japanese_aux_ocr_mode", "symbol_only") or "symbol_only").strip().lower()
            if japanese_ocr_mode in {"symbol_only", "symbols", "punctuation_only", "punctuation", "quote_only", "quotes", "quote"}:
                items = merge_japanese_aux_symbols_only(items, aux_ocr, img, scale, result_device, config_dict)

            sentence_groups = build_sentence_groups(items, config_dict)
            print_sentence_groups(sentence_groups, config_dict, frame_id, result_device)
            if translation_manager is not None:
                items = translation_manager.annotate_and_print(items, sentence_groups, frame_id, result_device)

            out_q.put({
                "type": "result",
                "device": result_device,
                "frame_id": frame_id,
                "elapsed_ms": (time.perf_counter() - start) * 1000.0,
                "items": [asdict(i) for i in items],
            })
        except Exception as e:
            log_message(f"[{result_device}] OCR loop failed:\n{traceback.format_exc()}")
            out_q.put({"type": "error", "device": result_device, "message": repr(e)})


class BBoxOverlay(QWidget):
    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.items = []
        self.bottom_caption_text = ""
        self.model_statuses = {
            "ocr": {"status": "unknown", "message": "", "device": ""},
            "translation": {"status": "unknown", "message": "", "device": ""},
        }
        region = config.capture_region
        self.current_region = None

        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.update_region(region)
        self.apply_window_flags()

    def model_status_overlay_enabled(self) -> bool:
        return bool(getattr(self.config, "model_status_overlay_enabled", True))

    def set_model_status(self, model: str, status: str, message: str = "", device: str = ""):
        key = str(model or "").strip().lower()
        if key in {"tr", "translate", "translator"}:
            key = "translation"
        if key not in self.model_statuses:
            return
        self.model_statuses[key] = {
            "status": str(status or "unknown").strip().lower(),
            "message": str(message or ""),
            "device": str(device or ""),
        }
        if self.model_status_overlay_enabled() and not self.isVisible():
            self.show()
            self.raise_()
        self.update()

    def update_region(self, region: dict):
        if not region:
            return
        left = int(region.get("left", 0))
        top = int(region.get("top", 0))
        width = max(1, int(region.get("width", 1)))
        height = max(1, int(region.get("height", 1)))
        new_region = {"left": left, "top": top, "width": width, "height": height}
        if self.current_region == new_region:
            return

        self.current_region = new_region
        self.setGeometry(left, top, width, height)
        self.update()

    def apply_window_flags(self):
        hwnd = int(self.winId())
        apply_click_through(hwnd)
        if self.config.exclude_overlay_from_capture:
            ok = apply_exclude_from_capture(hwnd)
            if not ok:
                print("[bbox overlay] exclude from capture failed. Set hide_overlay_during_capture: true if feedback occurs.", flush=True)

    def showEvent(self, event):
        super().showEvent(event)
        self.apply_window_flags()

    def _is_drawable_item(self, item: OCRItem) -> bool:
        """작은 OCR bbox는 bbox와 텍스트를 모두 표시하지 않습니다.

        - 가로형 bbox: 높이가 min_horizontal_bbox_height 미만이면 숨김
        - 세로형 bbox: 너비가 min_vertical_bbox_width 미만이면 숨김
        """
        if not self.config.skip_small_text_bbox:
            return True

        w = max(0.0, float(item.w))
        h = max(0.0, float(item.h))

        if w >= h:
            return h >= int(self.config.min_horizontal_bbox_height)
        return w >= int(self.config.min_vertical_bbox_width)

    def set_items(self, items):
        self.bottom_caption_text = ""
        for item in items:
            caption = normalize_text(getattr(item, "bottom_caption_text", "") or "")
            if caption:
                self.bottom_caption_text = caption
                break

        self.items = [item for item in items if self._is_drawable_item(item)]
        has_status_overlay = self.model_status_overlay_enabled()
        if self.config.hide_when_no_bbox and not self.items and not self.bottom_caption_text and not has_status_overlay:
            self.hide()
        else:
            if not self.isVisible():
                self.show()
            self.raise_()
        self.update()

    def _draw_single_bbox(self, painter: QPainter, item: OCRItem):
        if self.config.bbox_draw_mode == "rect":
            painter.drawRect(int(item.rel_x), int(item.rel_y), int(item.w), int(item.h))
            return

        points = [QPointF(float(x), float(y)) for x, y in item.poly_rel]
        if len(points) >= 3:
            painter.drawPolygon(QPolygonF(points))
        else:
            painter.drawRect(int(item.rel_x), int(item.rel_y), int(item.w), int(item.h))

    def _model_status_color(self, status: str) -> QColor:
        normalized = str(status or "").strip().lower()
        if normalized == "ready":
            return QColor(46, 220, 115, 235)
        if normalized == "fallback":
            return QColor(255, 196, 54, 235)
        if normalized == "error":
            return QColor(255, 75, 75, 235)
        return QColor(145, 150, 160, 220)

    def _model_status_display_text(self, label: str, data: dict) -> str:
        message = str(data.get("message", "") or "")
        device = str(data.get("device", "") or "").strip()
        if "fallback:" in message.lower() or "cpu fallback" in message.lower():
            return f"{label} cpu fallback"
        if " on " in message:
            detail = message.rsplit(" on ", 1)[-1].strip()
            if detail:
                return f"{label} {detail}"
        if device:
            return f"{label} {device}"
        return label

    def _draw_model_status_overlay(self, painter: QPainter):
        if not self.model_status_overlay_enabled():
            return

        rows = [
            ("OCR", self.model_statuses.get("ocr", {})),
            ("TR", self.model_statuses.get("translation", {})),
        ]
        display_rows = [(self._model_status_display_text(label, data), data) for label, data in rows]

        painter.save()
        font = QFont("Segoe UI")
        font.setPixelSize(11)
        font.setBold(True)
        painter.setFont(font)
        metrics = QFontMetrics(font)

        dot_size = 8
        row_h = 17
        pad_x = 8
        pad_y = 6
        label_gap = 6
        label_w = max(metrics.horizontalAdvance(label) for label, _ in display_rows)
        rect_w = pad_x * 2 + dot_size + label_gap + label_w
        rect_h = pad_y * 2 + row_h * len(rows)
        rect = QRectF(8, 8, float(rect_w), float(rect_h))

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 135))
        painter.drawRoundedRect(rect, 5, 5)

        x = rect.left() + pad_x
        y = rect.top() + pad_y
        for label, data in display_rows:
            status = str(data.get("status", "unknown") or "unknown")
            dot_y = y + (row_h - dot_size) / 2.0
            painter.setBrush(self._model_status_color(status))
            painter.setPen(QColor(0, 0, 0, 150))
            painter.drawEllipse(QRectF(float(x), float(dot_y), float(dot_size), float(dot_size)))

            text_x = x + dot_size + label_gap
            baseline_y = y + (row_h + metrics.ascent() - metrics.descent()) / 2.0
            painter.setPen(QColor(0, 0, 0, 210))
            painter.drawText(QPointF(float(text_x + 1), float(baseline_y + 1)), label)
            painter.setPen(QColor(255, 255, 255, 235))
            painter.drawText(QPointF(float(text_x), float(baseline_y)), label)
            y += row_h

        painter.restore()

    def _fit_text_lines(self, text: str, metrics: QFontMetrics, max_text_width: int):
        # OCR 텍스트가 긴 경우 박스 안에서 자동 줄바꿈합니다.
        text = normalize_text(text)
        if not text:
            return []

        tokens = text.split(" ")
        lines = []
        current = ""

        def append_wrapped_token(token: str):
            chunk = ""
            for ch in token:
                candidate = chunk + ch
                if metrics.horizontalAdvance(candidate) <= max_text_width:
                    chunk = candidate
                else:
                    if chunk:
                        lines.append(chunk)
                    chunk = ch
            return chunk

        for token in tokens:
            candidate = token if not current else current + " " + token
            if metrics.horizontalAdvance(candidate) <= max_text_width:
                current = candidate
                continue

            if current:
                lines.append(current)
                if metrics.horizontalAdvance(token) <= max_text_width:
                    current = token
                else:
                    current = append_wrapped_token(token)
            else:
                # 공백 없는 긴 문자열은 글자 단위로 끊습니다.
                current = append_wrapped_token(token)

        if current:
            lines.append(current)
        return lines

    def _fit_horizontal_text(self, text: str, start_size: int, width_limit: float, height_limit: float):
        text = normalize_text(text)
        min_size = max(1, int(self.config.text_box_min_font_size))
        max_size = max(min_size, int(self.config.text_box_max_font_size))
        size = max(min_size, min(max_size, int(start_size)))
        width_limit = max(1, int(width_limit))
        height_limit = max(1, int(height_limit))

        while size >= min_size:
            font = self._make_font(size)
            metrics = QFontMetrics(font)
            lines = self._fit_text_lines(text, metrics, width_limit)
            if not lines:
                return [], font

            line_height = max(1, metrics.lineSpacing())
            total_h = len(lines) * line_height
            max_line_w = max(metrics.horizontalAdvance(line) for line in lines)
            if total_h <= height_limit + 1 and max_line_w <= width_limit + 1:
                return lines, font

            size -= 1

        font = self._make_font(min_size)
        metrics = QFontMetrics(font)
        if metrics.horizontalAdvance(text) <= width_limit + 1:
            return [text], font
        return self._fit_text_lines(text, metrics, width_limit), font

    def _adaptive_font_size_for_bbox(self, item: OCRItem) -> int:
        """bbox 방향에 따라 폰트 크기를 자동 결정합니다.

        - 가로형 bbox: 높이 기준
        - 세로형 bbox: 너비 기준
        즉, 텍스트가 들어갈 수 있는 짧은 축을 기준으로 폰트를 잡습니다.
        """
        if not self.config.text_box_adaptive_font_size:
            return max(1, int(self.config.text_box_font_size))

        w = max(1.0, float(item.w))
        h = max(1.0, float(item.h))
        base = h if w >= h else w
        size = int(base * float(self.config.text_box_font_scale))

        min_size = max(1, int(self.config.text_box_min_font_size))
        max_size = max(min_size, int(self.config.text_box_max_font_size))
        return max(min_size, min(max_size, size))

    def _make_font(self, pixel_size: int) -> QFont:
        font = QFont("Malgun Gothic")
        font.setPixelSize(max(1, int(pixel_size)))
        font.setBold(True)
        return font

    def _is_cjk_text(self, text: str) -> bool:
        return bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", normalize_text(text)))

    def _is_vertical_text_item(self, item: OCRItem) -> bool:
        if not self.config.vertical_text_enabled:
            return False

        mode = str(self.config.vertical_text_mode or "auto").lower().strip()
        if mode in {"off", "false", "none", "never", "horizontal"}:
            return False
        if mode in {"on", "true", "always", "vertical"}:
            return True

        explicit_type = str(getattr(item, "box_type", "") or "").strip().lower()
        if explicit_type == "horizontal":
            return False
        if explicit_type == "vertical":
            if self.config.vertical_text_require_cjk:
                return self._is_cjk_text(item.text)
            return True

        w = max(1.0, float(item.w))
        h = max(1.0, float(item.h))
        if h / w < float(self.config.vertical_text_min_aspect_ratio):
            return False

        if self.config.vertical_text_require_cjk:
            return self._is_cjk_text(item.text)
        return True

    def _vertical_text_chars(self, text: str) -> list[str]:
        mapping = {
            "(": "︵", ")": "︶",
            "[": "︻", "]": "︼",
            "{": "︷", "}": "︸",
            "-": "｜", "ー": "｜",
        }
        return [mapping.get(ch, ch) for ch in normalize_text(text) if not ch.isspace()]

    def _fit_vertical_text(self, text: str, start_size: int, width_limit: float, height_limit: float):
        chars = self._vertical_text_chars(text)
        if not chars:
            return [], self._make_font(start_size)

        spacing = max(0, int(self.config.vertical_text_char_spacing_px))
        min_size = max(1, int(self.config.text_box_min_font_size))
        max_size = max(min_size, int(self.config.text_box_max_font_size))
        size = max(min_size, min(max_size, int(start_size)))
        width_limit = max(1.0, float(width_limit))
        height_limit = max(1.0, float(height_limit))

        while size >= min_size:
            font = self._make_font(size)
            metrics = QFontMetrics(font)
            max_char_w = max(metrics.horizontalAdvance(ch) for ch in chars)
            char_h = metrics.lineSpacing()
            total_h = len(chars) * char_h + max(0, len(chars) - 1) * spacing
            if max_char_w <= width_limit and total_h <= height_limit + 1:
                return chars, font
            size -= 1

        font = self._make_font(min_size)
        metrics = QFontMetrics(font)
        char_h = max(1, metrics.lineSpacing())
        max_chars = max(1, int((height_limit + spacing) // max(1, char_h + spacing)))
        if len(chars) > max_chars:
            chars = chars[:max(1, max_chars)]
        return chars, font

    def _draw_vertical_text(self, painter: QPainter, rect: QRectF, chars: list[str], font: QFont, text_alpha: int):
        if not chars:
            return

        painter.setFont(font)
        metrics = QFontMetrics(font)
        spacing = max(0, int(self.config.vertical_text_char_spacing_px))
        line_h = metrics.lineSpacing()
        total_h = len(chars) * line_h + max(0, len(chars) - 1) * spacing
        x_center = rect.left() + rect.width() / 2.0
        y = rect.top() + max(0.0, (rect.height() - total_h) / 2.0) + metrics.ascent()

        for ch in chars:
            char_w = metrics.horizontalAdvance(ch)
            x = x_center - char_w / 2.0

            painter.setPen(QColor(0, 0, 0, min(220, text_alpha)))
            painter.drawText(QPointF(float(x + 1), float(y + 1)), ch)

            painter.setPen(QColor(255, 255, 255, text_alpha))
            painter.drawText(QPointF(float(x), float(y)), ch)

            y += line_h + spacing

    def _display_text_for_item(self, item: OCRItem) -> str:
        return normalize_text(getattr(item, "translated_text", "") or "")

    def _draw_text_box(self, painter: QPainter, item: OCRItem):
        """기존 OCR bbox 안에 텍스트를 직접 그립니다.

        별도의 말풍선/라벨 박스를 새로 만들지 않고, PaddleOCR이 잡은 원래 영역
        내부에만 텍스트를 표시합니다.
        폰트 크기는 bbox 방향에 따라 자동 조절됩니다.
        """
        text = self._display_text_for_item(item)
        if not self.config.show_ocr_text_box or not text:
            return

        padding = max(0, int(self.config.text_box_padding))
        text_alpha = max(0, min(255, int(self.config.text_box_text_alpha)))
        bg_alpha = max(0, min(255, int(self.config.text_box_background_alpha)))

        # 원래 OCR bbox 자체를 텍스트 영역으로 사용합니다.
        x = float(item.rel_x)
        y = float(item.rel_y)
        w = max(1.0, float(item.w))
        h = max(1.0, float(item.h))
        bbox_rect = QRectF(x, y, w, h)

        # 선택 사항: 텍스트 가독성을 위해 기존 bbox 내부만 아주 살짝 어둡게 칠합니다.
        # 완전히 투명하게 하려면 config에서 text_box_background_alpha: 0 으로 두면 됩니다.
        if bg_alpha > 0:
            painter.fillRect(bbox_rect, QColor(0, 0, 0, bg_alpha))

        if self.config.text_box_show_confidence:
            text = f"{text}  ({item.confidence:.2f})"

        # drawText(QPointF)는 y가 텍스트 상단이 아니라 baseline 기준이라 아래로 밀립니다.
        # QRectF + AlignCenter 방식으로 그리면 bbox 중앙에 배치되고 아래 잘림이 줄어듭니다.
        inner_rect = QRectF(
            x + padding,
            y + padding,
            max(1.0, w - padding * 2),
            max(1.0, h - padding * 2),
        )

        # bbox 밖으로 글자가 튀어나가지 않도록 clip을 겁니다.
        painter.save()
        painter.setClipRect(bbox_rect)

        font_size = self._adaptive_font_size_for_bbox(item)
        if self._is_vertical_text_item(item):
            chars, font = self._fit_vertical_text(text, font_size, inner_rect.width(), inner_rect.height())
            self._draw_vertical_text(painter, inner_rect, chars, font, text_alpha)
            painter.restore()
            return

        lines, font = self._fit_horizontal_text(text, font_size, inner_rect.width(), inner_rect.height())
        if not lines:
            painter.restore()
            return

        painter.setFont(font)
        draw_text = "\n".join(lines)

        flags = Qt.AlignCenter | Qt.TextWordWrap

        # 흰 글자 + 검은 그림자로 게임 화면 위에서도 읽히게 합니다.
        shadow_rect = QRectF(
            inner_rect.x() + 1,
            inner_rect.y() + 1,
            inner_rect.width(),
            inner_rect.height(),
        )
        painter.setPen(QColor(0, 0, 0, min(220, text_alpha)))
        painter.drawText(shadow_rect, flags, draw_text)

        painter.setPen(QColor(255, 255, 255, text_alpha))
        painter.drawText(inner_rect, flags, draw_text)

        painter.restore()

    def _draw_bottom_caption(self, painter: QPainter):
        text = normalize_text(self.bottom_caption_text)
        if not text:
            return

        width_ratio = max(0.2, min(1.0, float(self.config.vertical_en_bottom_caption_width_ratio)))
        caption_w = max(120.0, float(self.width()) * width_ratio)
        caption_h = max(24.0, float(self.config.vertical_en_bottom_caption_height_px))
        margin_bottom = max(0.0, float(self.config.vertical_en_bottom_caption_margin_bottom_px))
        padding = max(0, int(self.config.vertical_en_bottom_caption_padding_px))

        x = (float(self.width()) - caption_w) / 2.0
        y = max(0.0, float(self.height()) - caption_h - margin_bottom)
        rect = QRectF(x, y, caption_w, min(caption_h, float(self.height())))
        inner_rect = QRectF(
            rect.x() + padding,
            rect.y() + padding,
            max(1.0, rect.width() - padding * 2),
            max(1.0, rect.height() - padding * 2),
        )

        bg_alpha = max(0, min(255, int(self.config.vertical_en_bottom_caption_background_alpha)))
        border_alpha = max(0, min(255, int(self.config.vertical_en_bottom_caption_border_alpha)))
        text_alpha = max(0, min(255, int(self.config.vertical_en_bottom_caption_text_alpha)))

        painter.save()
        if bg_alpha > 0:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, bg_alpha))
            painter.drawRoundedRect(rect, 6, 6)

        if border_alpha > 0:
            pen = QPen(QColor(255, 255, 255, border_alpha))
            pen.setWidth(1)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect, 6, 6)

        start_size = int(min(float(self.config.text_box_max_font_size), max(float(self.config.text_box_font_size), inner_rect.height() * 0.36)))
        lines, font = self._fit_horizontal_text(text, start_size, inner_rect.width(), inner_rect.height())
        if lines:
            painter.setFont(font)
            draw_text = "\n".join(lines)
            flags = Qt.AlignCenter | Qt.TextWordWrap
            shadow_rect = QRectF(inner_rect.x() + 1, inner_rect.y() + 1, inner_rect.width(), inner_rect.height())
            painter.setPen(QColor(0, 0, 0, min(230, text_alpha)))
            painter.drawText(shadow_rect, flags, draw_text)
            painter.setPen(QColor(255, 255, 255, text_alpha))
            painter.drawText(inner_rect, flags, draw_text)
        painter.restore()

    def paintEvent(self, event):
        if not self.items and not self.bottom_caption_text and not self.model_status_overlay_enabled():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # pen = QPen(QColor(0, 255, 120, 230)) # 녹색 윤곽선
        pen = QPen(Qt.PenStyle.NoPen)   # 윤곽선 없음

        pen.setWidth(self.config.bbox_line_width)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        for item in self.items:
            self._draw_single_bbox(painter, item)

        for item in self.items:
            self._draw_text_box(painter, item)

        self._draw_bottom_caption(painter)
        self._draw_model_status_overlay(painter)

    def render_demo_overlay_rgba(self) -> np.ndarray:
        pixmap = QPixmap(max(1, self.width()), max(1, self.height()))
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        self.render(painter, QPoint(0, 0))
        painter.end()

        image = pixmap.toImage().convertToFormat(QImage.Format_RGBA8888)
        width = image.width()
        height = image.height()
        bytes_per_line = image.bytesPerLine()
        buffer = image.bits()
        arr = np.frombuffer(buffer, dtype=np.uint8).reshape((height, bytes_per_line // 4, 4))
        return arr[:, :width, :].copy()


class ExtractedTextWriter:
    def __init__(self, config: AppConfig):
        self.config = config
        self.out_dir = Path(config.output_dir)
        self.out_dir.mkdir(exist_ok=True)
        self.last_hash = ""

    def write(self, items, meta):
        if not self.config.save_extracted_text:
            return

        sentence_rows = []
        seen_sentence_ids = set()
        for i in items:
            sentence_id = int(getattr(i, "sentence_id", 0) or 0)
            if sentence_id <= 0 or sentence_id in seen_sentence_ids:
                continue
            seen_sentence_ids.add(sentence_id)
            source = normalize_text(getattr(i, "sentence_text", "") or i.text)
            translation = normalize_text(getattr(i, "sentence_translated_text", "") or getattr(i, "translated_text", "") or "")
            sentence_rows.append(
                {
                    "id": sentence_id,
                    "source": source,
                    "translation": translation,
                    "text": translation or source,
                    "bottom_caption": bool(normalize_text(getattr(i, "bottom_caption_text", "") or "")),
                }
            )

        snapshot_text = " ".join(row["text"] for row in sentence_rows if row["text"])
        if not snapshot_text:
            snapshot_text = " ".join(i.text for i in items)

        snapshot = {
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "timestamp": time.time(),
            "meta": meta,
            "text": snapshot_text,
            "sentences": sentence_rows,
            "items": [
                {
                    "text": i.text,
                    "sentence_id": getattr(i, "sentence_id", 0),
                    "sentence_text": getattr(i, "sentence_text", ""),
                    "sentence_translated_text": getattr(i, "sentence_translated_text", ""),
                    "translated_text": getattr(i, "translated_text", ""),
                    "bottom_caption_text": getattr(i, "bottom_caption_text", ""),
                    "bottom_caption_group_id": getattr(i, "bottom_caption_group_id", 0),
                    "confidence": i.confidence,
                    "bbox_abs": {"left": i.abs_x, "top": i.abs_y, "width": i.w, "height": i.h},
                    "bbox_rel": {"left": i.rel_x, "top": i.rel_y, "width": i.w, "height": i.h},
                    "poly_abs": i.poly_abs,
                    "poly_rel": i.poly_rel,
                    "frame_id": i.frame_id,
                    "device": i.device,
                }
                for i in items
            ],
        }

        payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        current_hash = hashlib.sha1(payload.encode("utf-8")).hexdigest()
        if self.config.log_only_when_changed and current_hash == self.last_hash:
            return
        self.last_hash = current_hash

        if self.config.write_live_snapshot:
            (self.out_dir / "live_snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        if self.config.write_current_text_txt:
            (self.out_dir / "current_text.txt").write_text(snapshot["text"], encoding="utf-8")
        if self.config.write_events_jsonl:
            with (self.out_dir / "events.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")


class CaptureController:
    def __init__(self, config: AppConfig, overlay: BBoxOverlay, writer: ExtractedTextWriter, selected_hwnd: int | None = None):
        self.config = config
        self.overlay = overlay
        self.writer = writer

        self.in_queues = []
        self.out_q = mp.Queue()
        self.workers = []
        self.round_robin = 0
        self.frame_id = 0
        self.latest_frame_id = -1
        self.sct = mss.mss()
        self.last_window_title = None
        self.window_missing_logged = False
        self.selected_hwnd = selected_hwnd
        self.select_start_time = time.time()
        self.click_select_notice_printed = False

        self.active_devices = []
        self.model_status_by_device = {}
        self.start_workers()

    def _active_devices_from_config(self) -> list[str]:
        if self.config.worker_mode == "selected":
            active_devices = [normalize_compute_device(self.config.selected_device, self.config.available_devices)]
        else:
            active_devices = [normalize_compute_device(device, self.config.available_devices) for device in self.config.available_devices]
        active_devices = [device for idx, device in enumerate(active_devices) if device and device not in active_devices[:idx]]
        return active_devices or ["cpu"]

    def start_workers(self):
        self.active_devices = self._active_devices_from_config()
        print("Active devices:", self.active_devices, flush=True)
        self.in_queues = []
        self.workers = []
        self.reset_model_statuses()
        for device in self.active_devices:
            iq = mp.Queue(maxsize=1)
            proc = mp.Process(target=ocr_worker_process, args=(device, asdict(self.config), iq, self.out_q), daemon=True)
            proc.start()
            self.in_queues.append(iq)
            self.workers.append(proc)

    def reset_model_statuses(self):
        translation_enabled = bool(self.config.enable_translation)
        backend = str(self.config.translation_backend or "").strip().lower()
        if not translation_enabled:
            translation_status = "disabled"
            translation_message = "translation disabled"
        elif backend != "llama_cpp_gguf":
            translation_status = "error"
            translation_message = f"unsupported backend: {backend}"
        else:
            translation_status = "loading"
            translation_message = "GGUF loading"

        self.model_status_by_device = {}
        for device in self.active_devices:
            self.model_status_by_device[str(device)] = {
                "ocr": {"status": "loading", "message": "PaddleOCR loading", "device": str(device)},
            }
        translation_device = resolve_translation_device(asdict(self.config))
        self.model_status_by_device.setdefault(str(translation_device), {})["translation"] = {
            "status": translation_status,
            "message": translation_message,
            "device": str(translation_device),
        }
        self.update_overlay_model_status("ocr")
        self.update_overlay_model_status("translation")

    def aggregate_model_status(self, model: str) -> dict:
        states = []
        for device_status in self.model_status_by_device.values():
            state = device_status.get(model)
            if state:
                states.append(state)

        if not states:
            return {"status": "unknown", "message": "", "device": ""}

        statuses = [str(state.get("status", "unknown") or "unknown").lower() for state in states]
        if any(status == "error" for status in statuses):
            status = "error"
        elif any(status == "loading" for status in statuses):
            status = "loading"
        elif any(status == "fallback" for status in statuses):
            status = "fallback"
        elif all(status == "ready" for status in statuses):
            status = "ready"
        elif all(status == "disabled" for status in statuses):
            status = "disabled"
        else:
            status = "unknown"

        messages = [
            f"{state.get('device', '')}: {state.get('message', '')}".strip()
            for state in states
            if str(state.get("message", "") or "").strip()
        ]
        devices = [
            str(state.get("device", "") or "").strip()
            for state in states
            if str(state.get("device", "") or "").strip()
        ]
        return {"status": status, "message": " | ".join(messages), "device": ",".join(devices)}

    def update_overlay_model_status(self, model: str):
        state = self.aggregate_model_status(model)
        self.overlay.set_model_status(
            model,
            str(state.get("status", "unknown")),
            str(state.get("message", "")),
            str(state.get("device", "")),
        )

    def handle_model_status(self, msg: dict):
        device = str(msg.get("device", "") or "")
        model = str(msg.get("model", "") or "").strip().lower()
        if model in {"tr", "translate", "translator"}:
            model = "translation"
        if model not in {"ocr", "translation"}:
            return
        if not device:
            device = "default"

        device_status = self.model_status_by_device.setdefault(device, {})
        device_status[model] = {
            "status": str(msg.get("status", "unknown") or "unknown").strip().lower(),
            "message": str(msg.get("message", "") or ""),
            "device": device,
        }
        print(
            f"[model status] {device} {model}={device_status[model]['status']} {device_status[model]['message']}",
            flush=True,
        )
        self.update_overlay_model_status(model)

    def restart_workers(self):
        self.stop(clear_overlay=False)
        self.latest_frame_id = -1
        self.round_robin = 0
        self.start_workers()

    def _default_capture_region(self):
        return {
            "left": int(self.config.capture_region["left"]),
            "top": int(self.config.capture_region["top"]),
            "width": int(self.config.capture_region["width"]),
            "height": int(self.config.capture_region["height"]),
        }

    def set_selected_window(self, hwnd: int, region: dict | None = None, title: str = ""):
        self.selected_hwnd = int(hwnd) if hwnd else None
        self.config.capture_mode = "selected_hwnd" if self.selected_hwnd else "region"
        if region:
            self.config.capture_region = region
            self.overlay.update_region(region)
        self.last_window_title = title or (get_window_title(self.selected_hwnd) if self.selected_hwnd else "")
        self.window_missing_logged = False

    def current_capture_region_for_demo(self) -> dict | None:
        return self._resolve_capture_region()

    def _resolve_capture_region(self):
        mode = str(self.config.capture_mode or "region").lower().strip()
        config_dict = asdict(self.config)

        if mode in {"click", "click_window", "active_window", "select_window", "window_picker", "window_ui", "select_ui", "selected_hwnd"}:
            if sys.platform != "win32":
                if self.config.window_follow_log and not self.window_missing_logged:
                    print("[click window] Windows에서만 활성 창 선택을 지원합니다. capture_region으로 fallback합니다.", flush=True)
                    self.window_missing_logged = True
                return apply_region_crop(self._default_capture_region(), config_dict)

            if self.selected_hwnd is None:
                delay = max(0.0, float(self.config.click_select_delay_sec))
                elapsed = time.time() - self.select_start_time
                if not self.click_select_notice_printed:
                    print(f"[click window] {delay:.1f}초 안에 OCR할 게임/앱 창을 한 번 클릭하세요.", flush=True)
                    self.click_select_notice_printed = True
                if elapsed < delay:
                    self.overlay.set_items([])
                    return None

                hwnd = get_foreground_window()
                title = get_window_title(hwnd) if hwnd else ""
                if not hwnd:
                    if self.config.window_follow_log:
                        print("[click window] 활성 창을 찾지 못했습니다. capture_region으로 fallback합니다.", flush=True)
                    if not self.config.window_follow_fallback_to_region:
                        return None
                    return apply_region_crop(self._default_capture_region(), config_dict)

                self.selected_hwnd = hwnd
                self.last_window_title = title or f"HWND {hwnd}"
                if self.config.window_follow_log:
                    print(f'[click window] selected="{self.last_window_title}" hwnd={hwnd}', flush=True)

            region, actual_title = get_hwnd_capture_region(self.selected_hwnd, self.config.use_client_area)
            if region:
                title = actual_title or self.last_window_title
                if self.config.window_follow_log and title != self.last_window_title:
                    print(f'[click window] target="{title}" region={region}', flush=True)
                self.last_window_title = title
                self.window_missing_logged = False
                return apply_region_crop(region, config_dict)

            if self.config.window_follow_log and not self.window_missing_logged:
                print(f'[click window] 선택한 창을 캡처할 수 없습니다: "{self.last_window_title}"', flush=True)
                self.window_missing_logged = True

            if not self.config.window_follow_fallback_to_region:
                self.overlay.set_items([])
                return None
            return apply_region_crop(self._default_capture_region(), config_dict)

        if mode in {"window", "window_client", "window_follow"}:
            region, actual_title = get_window_capture_region(
                self.config.target_window_title,
                self.config.window_match_mode,
                self.config.use_client_area,
            )
            if region:
                if self.config.window_follow_log and actual_title != self.last_window_title:
                    print(f'[window follow] target="{actual_title}" region={region}', flush=True)
                self.last_window_title = actual_title
                self.window_missing_logged = False
                return apply_region_crop(region, config_dict)

            if self.config.window_follow_log and not self.window_missing_logged:
                print(f'[window follow] window not found: "{self.config.target_window_title}"', flush=True)
                self.window_missing_logged = True

            if not self.config.window_follow_fallback_to_region:
                self.overlay.set_items([])
                return None

        return apply_region_crop(self._default_capture_region(), config_dict)

    def capture_once(self):
        region = self._resolve_capture_region()
        if not region:
            return

        self.overlay.update_region(region)

        if self.config.hide_overlay_during_capture:
            self.overlay.hide()
            QApplication.processEvents()
            time.sleep(0.01)

        raw = self.sct.grab(region)

        if self.config.hide_overlay_during_capture and self.overlay.items:
            self.overlay.show()
            QApplication.processEvents()

        pil_img = Image.frombytes("RGB", raw.size, raw.rgb)
        img = preprocess_for_ocr(pil_img, self.config.ocr_scale)

        if not self.in_queues:
            return

        if self.config.worker_mode == "selected":
            idx = 0
        else:
            idx = self.round_robin % len(self.in_queues)
            self.round_robin += 1

        iq = self.in_queues[idx]

        if self.config.drop_old_frames:
            try:
                while True:
                    iq.get_nowait()
            except queue.Empty:
                pass
            except Exception:
                pass

        try:
            self.frame_id += 1
            iq.put_nowait((self.frame_id, img, int(region["left"]), int(region["top"])))
        except queue.Full:
            pass

    def poll_results(self):
        while True:
            try:
                msg = self.out_q.get_nowait()
            except queue.Empty:
                break

            if msg.get("type") == "model_status":
                self.handle_model_status(msg)
                continue

            if msg.get("type") == "error":
                print(f'{msg.get("device")}: ERROR {msg.get("message")}', flush=True)
                self.overlay.set_items([])
                self.writer.write([], {"status": "error", "message": msg.get("message")})
                continue

            if msg.get("type") != "result":
                continue

            frame_id = int(msg.get("frame_id", 0))
            if frame_id < self.latest_frame_id:
                continue

            self.latest_frame_id = frame_id
            device = msg.get("device", "?")
            elapsed = float(msg.get("elapsed_ms", 0.0))
            items = [OCRItem(**d) for d in msg.get("items", [])]

            print(f"[{device} {elapsed:.0f}ms frame={frame_id}] bboxes={len(items)}", flush=True)

            self.overlay.set_items(items)
            self.writer.write(items, {"device": device, "elapsed_ms": elapsed, "frame_id": frame_id, "bboxes": len(items)})

    def stop(self, clear_overlay: bool = True):
        for iq in self.in_queues:
            try:
                iq.put(None)
            except Exception:
                pass
        for proc in self.workers:
            try:
                proc.terminate()
            except Exception:
                pass
        self.in_queues = []
        self.workers = []
        if clear_overlay:
            self.overlay.set_items([])


APP_LANGUAGE_LABELS = {
    "ko": "한국어",
    "en": "English",
    "ja": "日本語",
}

APP_TEXT = {
    "title": {
        "ko": "Boku No Translator",
        "en": "Boku No Translator",
        "ja": "Boku No Translator",
    },
    "status_idle": {
        "ko": "트레이에서 실행 중입니다.",
        "en": "Running in the system tray.",
        "ja": "トレイで実行中です。",
    },
    "select_window": {
        "ko": "대상 창 선택",
        "en": "Select Target Window",
        "ja": "対象ウィンドウを選択",
    },
    "screenshot": {
        "ko": "스크린샷 저장",
        "en": "Save Screenshot",
        "ja": "スクリーンショット保存",
    },
    "record_start": {
        "ko": "녹화 시작",
        "en": "Start Recording",
        "ja": "録画開始",
    },
    "record_stop": {
        "ko": "녹화 중지",
        "en": "Stop Recording",
        "ja": "録画停止",
    },
    "open_output": {
        "ko": "저장 폴더 열기",
        "en": "Open Output Folder",
        "ja": "保存フォルダを開く",
    },
    "quit": {
        "ko": "종료",
        "en": "Quit",
        "ja": "終了",
    },
    "ui_language": {
        "ko": "어플리케이션 언어",
        "en": "Application Language",
        "ja": "アプリ言語",
    },
    "help_language": {
        "ko": "설명 언어",
        "en": "Guide Language",
        "ja": "説明言語",
    },
    "hotkeys": {
        "ko": "단축키",
        "en": "Hotkeys",
        "ja": "ホットキー",
    },
    "settings": {
        "ko": "설정",
        "en": "Settings",
        "ja": "設定",
    },
    "usage": {
        "ko": "사용법",
        "en": "Usage",
        "ja": "使い方",
    },
    "language": {
        "ko": "언어",
        "en": "Language",
        "ja": "言語",
    },
    "ocr_source_language": {
        "ko": "OCR 원문 언어",
        "en": "OCR Source Language",
        "ja": "OCR原文言語",
    },
    "translation_target_language": {
        "ko": "번역 대상 언어",
        "en": "Translation Target Language",
        "ja": "翻訳先言語",
    },
    "translation_language_note": {
        "ko": "OCR/번역 언어는 앱 UI 언어와 별개입니다. 변경 후 저장하면 OCR worker가 재시작됩니다.",
        "en": "OCR/translation languages are separate from the app UI language. Saving restarts OCR workers.",
        "ja": "OCR/翻訳言語はアプリUI言語とは別です。保存するとOCR workerが再起動します。",
    },
    "apply_settings": {
        "ko": "설정 저장 및 재시작",
        "en": "Save Settings and Restart",
        "ja": "設定を保存して再起動",
    },
}

APP_HELP_TEXT = {
    "ko": (
        "사용법\n"
        "1. 대상 창 선택을 눌러 OCR할 게임/앱 창을 고릅니다.\n"
        "2. 번역 오버레이는 선택한 창 위에 자동으로 표시됩니다.\n"
        "3. 데모가 필요하면 스크린샷 저장 또는 녹화 시작을 누릅니다.\n\n"
        "처음 실행\n"
        "- ZIP으로 받은 경우 preload_models.bat를 먼저 실행하면 OCR/번역 모델을 다운로드하고 로드까지 검증합니다.\n"
        "- 번역 GGUF 모델은 수 GB라서 첫 다운로드가 오래 걸릴 수 있습니다.\n"
        "- Python, venv, llama.cpp를 따로 설치할 필요는 없습니다. ZIP 패키지에 포함되어 있습니다.\n\n"
        "장치 상태\n"
        "- GPU가 있으면 기본은 gpu:0, 없으면 cpu입니다. 잘못된 gpu:1 값은 자동 보정됩니다.\n"
        "- 설정에서 OCR device와 Translation device를 따로 지정할 수 있습니다.\n"
        "- 초록색은 표시된 장치에서 준비 완료, 노란색은 CPU fallback, 회색은 로딩/다운로드, 빨간색은 실패입니다.\n\n"
        "단축키\n"
        "{toggle}: 이 패널 열기/숨기기\n"
        "{screenshot}: 대상 창 + 오버레이 스크린샷 저장\n"
        "{record}: 대상 창 + 오버레이 동영상 녹화 시작/중지\n\n"
        "이 언어 설정은 앱 UI와 설명만 바꾸며 OCR source/target 언어와는 별개입니다."
    ),
    "en": (
        "How to use\n"
        "1. Click Select Target Window and choose the game/app window.\n"
        "2. The translated overlay follows the selected window.\n"
        "3. Use Save Screenshot or Start Recording for demos.\n\n"
        "First run\n"
        "- If you use the ZIP package, run preload_models.bat first to download and verify OCR/translation models.\n"
        "- The GGUF translation model is several GB, so the first download can take a long time.\n"
        "- Python, venv, and llama.cpp do not need to be installed separately. They are bundled in the ZIP package.\n\n"
        "Device status\n"
        "- If a GPU exists, the default is gpu:0; otherwise it is cpu. Invalid values like gpu:1 are corrected automatically.\n"
        "- OCR device and Translation device can be assigned separately in Settings.\n"
        "- Green means ready on the shown device, yellow means CPU fallback, gray means loading/downloading, red means failed.\n\n"
        "Hotkeys\n"
        "{toggle}: show/hide this panel\n"
        "{screenshot}: save target window + overlay screenshot\n"
        "{record}: start/stop target window + overlay video recording\n\n"
        "These language settings affect only the app UI and guide text, not OCR source/target languages."
    ),
    "ja": (
        "使い方\n"
        "1. 対象ウィンドウを選択して、OCRするゲーム/アプリを選びます。\n"
        "2. 翻訳オーバーレイは選択したウィンドウに追従します。\n"
        "3. デモ用にはスクリーンショット保存または録画開始を使います。\n\n"
        "ホットキー\n"
        "{toggle}: このパネルを表示/非表示\n"
        "{screenshot}: 対象ウィンドウ + オーバーレイの画像保存\n"
        "{record}: 対象ウィンドウ + オーバーレイの録画開始/停止\n\n"
        "この言語設定はアプリUIと説明だけに適用され、OCR source/target言語とは別です。"
    ),
}


def app_text(config: AppConfig, key: str) -> str:
    lang = str(getattr(config, "app_ui_language", "ko") or "ko")
    return APP_TEXT.get(key, {}).get(lang) or APP_TEXT.get(key, {}).get("ko") or key


def app_help_text(config: AppConfig) -> str:
    lang = str(getattr(config, "app_help_language", "ko") or "ko")
    template = APP_HELP_TEXT.get(lang) or APP_HELP_TEXT["ko"]
    return template.format(
        toggle=getattr(config, "app_hotkey_toggle_panel", "Ctrl+Alt+A"),
        screenshot=getattr(config, "demo_screenshot_hotkey", "Ctrl+Alt+S"),
        record=getattr(config, "demo_record_hotkey", "Ctrl+Alt+R"),
    )


def timestamp_for_filename() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


class DemoCaptureManager:
    def __init__(self, config: AppConfig, overlay: BBoxOverlay, controller: CaptureController):
        self.config = config
        self.overlay = overlay
        self.controller = controller
        self.output_dir = Path(config.demo_output_dir)
        self.screenshot_dir = Path(config.demo_screenshot_dir)
        self.video_dir = Path(config.demo_video_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.video_dir.mkdir(parents=True, exist_ok=True)
        self.record_timer = QTimer()
        self.record_timer.timeout.connect(self.capture_record_frame)
        self.record_writer = None
        self.record_path: Path | None = None
        self.record_frame_size: tuple[int, int] | None = None
        self.recording = False
        self.status_callback = None

    def update_output_dirs(self):
        self.output_dir = Path(self.config.demo_output_dir)
        self.screenshot_dir = Path(self.config.demo_screenshot_dir)
        self.video_dir = Path(self.config.demo_video_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.video_dir.mkdir(parents=True, exist_ok=True)

    def set_status_callback(self, callback):
        self.status_callback = callback

    def notify(self, message: str):
        print(f"[demo] {message}", flush=True)
        if callable(self.status_callback):
            self.status_callback(message)

    def capture_rgba(self) -> np.ndarray | None:
        region = self.controller.current_capture_region_for_demo()
        if not region:
            self.notify("capture region is not available")
            return None

        self.overlay.update_region(region)
        QApplication.processEvents()

        raw = self.controller.sct.grab(region)
        base = np.array(Image.frombytes("RGB", raw.size, raw.rgb).convert("RGBA"), dtype=np.uint8)
        overlay_rgba = self.overlay.render_demo_overlay_rgba()
        if overlay_rgba.shape[:2] != base.shape[:2]:
            overlay_rgba = cv2.resize(overlay_rgba, (base.shape[1], base.shape[0]), interpolation=cv2.INTER_LINEAR)

        alpha = overlay_rgba[:, :, 3:4].astype(np.float32) / 255.0
        composed_rgb = overlay_rgba[:, :, :3].astype(np.float32) * alpha + base[:, :, :3].astype(np.float32) * (1.0 - alpha)
        composed = np.concatenate([composed_rgb.astype(np.uint8), np.full_like(base[:, :, 3:4], 255)], axis=2)
        return composed

    def save_screenshot(self) -> Path | None:
        if not self.config.demo_capture_enabled:
            self.notify("demo capture is disabled")
            return None
        frame = self.capture_rgba()
        if frame is None:
            return None
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        path = self.screenshot_dir / f"ocr_overlay_demo_{timestamp_for_filename()}.png"
        Image.fromarray(frame, mode="RGBA").save(path)
        self.notify(f"screenshot saved: {path}")
        return path

    def start_recording(self) -> Path | None:
        if self.recording:
            return self.record_path
        frame = self.capture_rgba()
        if frame is None:
            return None

        fps = max(1, int(self.config.demo_record_fps))
        ext = "mp4" if self.config.demo_record_mp4 else "avi"
        fourcc = cv2.VideoWriter_fourcc(*("mp4v" if ext == "mp4" else "XVID"))
        height, width = frame.shape[:2]
        width -= width % 2
        height -= height % 2
        if width <= 0 or height <= 0:
            self.notify("invalid record frame size")
            return None

        self.video_dir.mkdir(parents=True, exist_ok=True)
        path = self.video_dir / f"ocr_overlay_demo_{timestamp_for_filename()}.{ext}"
        writer = cv2.VideoWriter(str(path), fourcc, float(fps), (width, height))
        if not writer.isOpened():
            self.notify(f"failed to open video writer: {path}")
            return None

        self.record_writer = writer
        self.record_path = path
        self.record_frame_size = (width, height)
        self.recording = True
        self.write_record_frame(frame)
        self.record_timer.start(max(1, int(round(1000 / fps))))
        self.notify(f"recording started: {path}")
        return path

    def write_record_frame(self, frame: np.ndarray):
        if not self.record_writer or not self.record_frame_size:
            return
        width, height = self.record_frame_size
        frame = frame[:height, :width, :3]
        bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        self.record_writer.write(bgr)

    def capture_record_frame(self):
        if not self.recording:
            return
        frame = self.capture_rgba()
        if frame is not None:
            self.write_record_frame(frame)

    def stop_recording(self) -> Path | None:
        if not self.recording:
            return self.record_path
        self.record_timer.stop()
        if self.record_writer is not None:
            self.record_writer.release()
        self.record_writer = None
        self.recording = False
        path = self.record_path
        self.notify(f"recording saved: {path}")
        return path

    def toggle_recording(self):
        if self.recording:
            return self.stop_recording()
        return self.start_recording()

    def open_output_dir(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(self.output_dir))
        else:
            print(f"[demo] output directory: {self.output_dir}", flush=True)


def parse_hotkey(hotkey: str):
    normalized = str(hotkey or "").replace(" ", "")
    parts = [p for p in re.split(r"[+]", normalized) if p]
    if not parts:
        return None

    modifiers = 0
    key = ""
    for part in parts:
        low = part.lower()
        if low in {"ctrl", "control"}:
            modifiers |= 0x0002
        elif low == "alt":
            modifiers |= 0x0001
        elif low == "shift":
            modifiers |= 0x0004
        elif low in {"win", "windows", "meta"}:
            modifiers |= 0x0008
        else:
            key = part.upper()

    if not key:
        return None
    if len(key) == 1:
        vk = ord(key)
    elif re.fullmatch(r"F([1-9]|1[0-9]|2[0-4])", key):
        vk = 0x70 + int(key[1:]) - 1
    else:
        named = {"ESC": 0x1B, "SPACE": 0x20, "TAB": 0x09}
        vk = named.get(key)
        if vk is None:
            return None
    return modifiers | 0x4000, vk


class GlobalHotkeyFilter(QAbstractNativeEventFilter):
    WM_HOTKEY = 0x0312

    def __init__(self):
        super().__init__()
        self.callbacks = {}

    def register(self, hotkey_id: int, hotkey: str, callback):
        if sys.platform != "win32":
            return False
        parsed = parse_hotkey(hotkey)
        if not parsed:
            return False
        modifiers, vk = parsed
        ok = bool(ctypes.windll.user32.RegisterHotKey(None, int(hotkey_id), int(modifiers), int(vk)))
        if ok:
            self.callbacks[int(hotkey_id)] = callback
        else:
            print(f"[hotkey] failed to register {hotkey}", flush=True)
        return ok

    def unregister_all(self):
        if sys.platform != "win32":
            return
        for hotkey_id in list(self.callbacks):
            try:
                ctypes.windll.user32.UnregisterHotKey(None, int(hotkey_id))
            except Exception:
                pass
        self.callbacks.clear()

    def nativeEventFilter(self, event_type, message):
        if sys.platform != "win32":
            return False, 0
        try:
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == self.WM_HOTKEY:
                callback = self.callbacks.get(int(msg.wParam))
                if callback:
                    QTimer.singleShot(0, callback)
                    return True, 0
        except Exception:
            pass
        return False, 0


class ControlPanel(QWidget):
    def __init__(self, runtime):
        super().__init__()
        self.runtime = runtime
        self.config = runtime.config
        self.setWindowIcon(load_app_icon(runtime.app))
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setMinimumWidth(620)

        self.title_label = QLabel()
        self.title_label.setStyleSheet("font-size: 18px; font-weight: 700;")
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.help_label = QLabel()
        self.help_label.setWordWrap(True)
        self.translation_language_note_label = QLabel()
        self.translation_language_note_label.setWordWrap(True)
        self.device_note_label = QLabel()
        self.device_note_label.setWordWrap(True)
        self.source_language_label = QLabel()
        self.target_language_label = QLabel()
        self.ui_language_label = QLabel()
        self.help_language_label = QLabel()

        self.select_button = QPushButton()
        self.screenshot_button = QPushButton()
        self.record_button = QPushButton()
        self.output_button = QPushButton()
        self.quit_button = QPushButton()
        self.apply_button = QPushButton()
        self.language_apply_button = QPushButton()

        self.ocr_scale_spin = self.double_spin(0.5, 4.0, 0.25, self.config.ocr_scale)
        self.min_conf_spin = self.double_spin(0.0, 1.0, 0.05, self.config.min_ocr_confidence)
        self.det_thresh_spin = self.double_spin(0.0, 1.0, 0.05, self.config.text_det_thresh)
        self.det_box_thresh_spin = self.double_spin(0.0, 1.0, 0.05, self.config.text_det_box_thresh)
        self.unclip_spin = self.double_spin(0.5, 4.0, 0.1, self.config.text_det_unclip_ratio)
        self.rec_score_spin = self.double_spin(0.0, 1.0, 0.05, self.config.text_rec_score_thresh)
        self.vertical_same_column_gap_spin = self.double_spin(0.0, 200.0, 1.0, self.config.vertical_sentence_same_column_gap_px)
        self.horizontal_multiline_checkbox = QCheckBox()
        self.horizontal_multiline_checkbox.setChecked(bool(self.config.horizontal_sentence_multiline_enabled))
        self.horizontal_line_gap_spin = self.double_spin(0.0, 200.0, 1.0, self.config.horizontal_sentence_line_gap_px)
        self.horizontal_x_overlap_spin = self.double_spin(0.0, 1.0, 0.05, self.config.horizontal_sentence_min_x_overlap_ratio)
        self.horizontal_require_continuation_checkbox = QCheckBox()
        self.horizontal_require_continuation_checkbox.setChecked(bool(self.config.horizontal_sentence_require_continuation))

        self.gpu_combo = QComboBox()
        self.translation_device_combo = QComboBox()
        device_options = ["cpu"] + [str(d) for d in self.config.available_devices if str(d) != "cpu"]
        for current_device in (self.config.selected_device, self.config.translation_device):
            if current_device not in device_options:
                device_options.append(current_device)
        for device in device_options:
            self.gpu_combo.addItem(device_display_name(device), device)
            self.translation_device_combo.addItem(device_display_name(device), device)
        self.gpu_combo.setCurrentIndex(max(0, self.gpu_combo.findData(self.config.selected_device)))
        self.translation_device_combo.setCurrentIndex(max(0, self.translation_device_combo.findData(self.config.translation_device)))

        self.ocr_det_model_edit = QLineEdit(self.config.ocr_text_detection_model_dir)
        self.ocr_rec_model_edit = QLineEdit(self.config.ocr_text_recognition_model_dir)
        self.translation_model_path_edit = QLineEdit(self.config.translation_model_path)
        self.toggle_hotkey_edit = QLineEdit(self.config.app_hotkey_toggle_panel)
        self.screenshot_hotkey_edit = QLineEdit(self.config.demo_screenshot_hotkey)
        self.record_hotkey_edit = QLineEdit(self.config.demo_record_hotkey)
        self.screenshot_dir_edit = QLineEdit(self.config.demo_screenshot_dir)
        self.video_dir_edit = QLineEdit(self.config.demo_video_dir)

        self.ui_combo = QComboBox()
        self.help_combo = QComboBox()
        self.source_language_combo = QComboBox()
        self.target_language_combo = QComboBox()
        for option in LANGUAGE_OPTIONS:
            if not bool(option.get("target_only", False)):
                self.source_language_combo.addItem(option["label"], option["key"])
            if option["key"] != "auto":
                self.target_language_combo.addItem(option["label"], option["key"])
        source_key = find_language_key_for_code(self.config.translation_source_language, allow_auto=True)
        target_key = find_language_key_for_code(self.config.translation_target_language, allow_auto=False)
        self.source_language_combo.setCurrentIndex(max(0, self.source_language_combo.findData(source_key)))
        self.target_language_combo.setCurrentIndex(max(0, self.target_language_combo.findData(target_key)))

        for code, label in APP_LANGUAGE_LABELS.items():
            self.ui_combo.addItem(label, code)
            self.help_combo.addItem(label, code)
        self.ui_combo.setCurrentIndex(max(0, self.ui_combo.findData(self.config.app_ui_language)))
        self.help_combo.setCurrentIndex(max(0, self.help_combo.findData(self.config.app_help_language)))

        self.tabs = QTabWidget()
        self.tabs.addTab(self.build_settings_tab(), app_text(self.config, "settings"))
        self.tabs.addTab(self.build_usage_tab(), app_text(self.config, "usage"))
        self.tabs.addTab(self.build_language_tab(), app_text(self.config, "language"))

        layout = QVBoxLayout()
        layout.addWidget(self.title_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.tabs)
        layout.addWidget(self.quit_button)
        self.setLayout(layout)

        self.select_button.clicked.connect(self.runtime.select_target_window)
        self.screenshot_button.clicked.connect(self.runtime.save_demo_screenshot)
        self.record_button.clicked.connect(self.runtime.toggle_demo_recording)
        self.output_button.clicked.connect(self.runtime.demo_capture.open_output_dir)
        self.quit_button.clicked.connect(self.runtime.quit)
        self.apply_button.clicked.connect(self.apply_settings)
        self.language_apply_button.clicked.connect(self.apply_settings)
        self.ui_combo.currentIndexChanged.connect(self.on_ui_language_changed)
        self.help_combo.currentIndexChanged.connect(self.on_help_language_changed)
        self.refresh()

    def double_spin(self, minimum: float, maximum: float, step: float, value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setDecimals(3)
        spin.setValue(float(value))
        return spin

    def path_row(self, edit: QLineEdit, button_text: str, mode: str = "dir") -> QWidget:
        row = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        button = QPushButton(button_text)
        if mode == "file":
            button.clicked.connect(lambda: self.choose_file(edit))
        else:
            button.clicked.connect(lambda: self.choose_dir(edit))
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        row.setLayout(layout)
        return row

    def choose_dir(self, edit: QLineEdit):
        start = edit.text().strip() or str(USER_DATA_DIR)
        selected = QFileDialog.getExistingDirectory(self, "폴더 선택", start)
        if selected:
            edit.setText(selected)

    def choose_file(self, edit: QLineEdit):
        start = edit.text().strip() or str(USER_DATA_DIR)
        selected, _ = QFileDialog.getOpenFileName(self, "파일 선택", start, "Model Files (*.gguf *.pdmodel *.onnx *.*)")
        if selected:
            edit.setText(selected)

    def build_settings_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout()
        form.addRow(self.select_button)
        form.addRow(self.screenshot_button)
        form.addRow(self.record_button)
        form.addRow(self.output_button)
        form.addRow("OCR scale", self.ocr_scale_spin)
        form.addRow("min_ocr_confidence", self.min_conf_spin)
        form.addRow(self.device_note_label)
        form.addRow("OCR device", self.gpu_combo)
        form.addRow("Translation device", self.translation_device_combo)
        form.addRow("text_det_thresh", self.det_thresh_spin)
        form.addRow("text_det_box_thresh", self.det_box_thresh_spin)
        form.addRow("text_det_unclip_ratio", self.unclip_spin)
        form.addRow("text_rec_score_thresh", self.rec_score_spin)
        form.addRow("vertical same-column gap px", self.vertical_same_column_gap_spin)
        form.addRow("horizontal multiline enabled", self.horizontal_multiline_checkbox)
        form.addRow("horizontal line gap px", self.horizontal_line_gap_spin)
        form.addRow("horizontal min x-overlap", self.horizontal_x_overlap_spin)
        form.addRow("horizontal require continuation", self.horizontal_require_continuation_checkbox)
        form.addRow("OCR detection model dir", self.path_row(self.ocr_det_model_edit, "찾기"))
        form.addRow("OCR recognition model dir", self.path_row(self.ocr_rec_model_edit, "찾기"))
        form.addRow("Translation model path", self.path_row(self.translation_model_path_edit, "찾기", mode="file"))
        form.addRow("Panel hotkey", self.toggle_hotkey_edit)
        form.addRow("Screenshot hotkey", self.screenshot_hotkey_edit)
        form.addRow("Record hotkey", self.record_hotkey_edit)
        form.addRow("Screenshot folder", self.path_row(self.screenshot_dir_edit, "찾기"))
        form.addRow("Video folder", self.path_row(self.video_dir_edit, "찾기"))
        form.addRow(self.apply_button)
        tab.setLayout(form)
        return tab

    def build_usage_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout()
        layout.addWidget(self.help_label)
        layout.addStretch(1)
        tab.setLayout(layout)
        return tab

    def build_language_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout()
        form.addRow(self.translation_language_note_label)
        form.addRow(self.source_language_label, self.source_language_combo)
        form.addRow(self.target_language_label, self.target_language_combo)
        form.addRow(self.language_apply_button)
        form.addRow(QLabel(""))
        form.addRow(self.ui_language_label, self.ui_combo)
        form.addRow(self.help_language_label, self.help_combo)
        tab.setLayout(form)
        return tab

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def on_ui_language_changed(self):
        code = str(self.ui_combo.currentData() or "ko")
        self.runtime.set_app_ui_language(code)

    def on_help_language_changed(self):
        code = str(self.help_combo.currentData() or "ko")
        self.runtime.set_app_help_language(code)

    def set_status(self, message: str):
        self.status_label.setText(message or app_text(self.config, "status_idle"))

    def apply_settings(self):
        old_hotkeys = (
            self.config.app_hotkey_toggle_panel,
            self.config.demo_screenshot_hotkey,
            self.config.demo_record_hotkey,
        )

        self.config.ocr_scale = float(self.ocr_scale_spin.value())
        self.config.min_ocr_confidence = float(self.min_conf_spin.value())
        self.config.worker_mode = "selected"
        self.config.selected_device = str(self.gpu_combo.currentData() or self.gpu_combo.currentText() or "cpu")
        self.config.translation_device = str(self.translation_device_combo.currentData() or self.translation_device_combo.currentText() or "cpu")
        self.config.translation_device_index = device_index(self.config.translation_device)
        if self.config.translation_device == "cpu":
            self.config.translation_n_gpu_layers = 0
        elif self.config.translation_n_gpu_layers == 0:
            self.config.translation_n_gpu_layers = -1
        if self.config.selected_device not in self.config.available_devices and self.config.selected_device != "cpu":
            self.config.available_devices.append(self.config.selected_device)
        if self.config.translation_device not in self.config.available_devices and self.config.translation_device != "cpu":
            self.config.available_devices.append(self.config.translation_device)
        self.config.text_det_thresh = float(self.det_thresh_spin.value())
        self.config.text_det_box_thresh = float(self.det_box_thresh_spin.value())
        self.config.text_det_unclip_ratio = float(self.unclip_spin.value())
        self.config.text_rec_score_thresh = float(self.rec_score_spin.value())
        self.config.vertical_sentence_same_column_gap_px = float(self.vertical_same_column_gap_spin.value())
        self.config.horizontal_sentence_multiline_enabled = bool(self.horizontal_multiline_checkbox.isChecked())
        self.config.horizontal_sentence_line_gap_px = float(self.horizontal_line_gap_spin.value())
        self.config.horizontal_sentence_min_x_overlap_ratio = float(self.horizontal_x_overlap_spin.value())
        self.config.horizontal_sentence_require_continuation = bool(self.horizontal_require_continuation_checkbox.isChecked())
        self.config.ocr_text_detection_model_dir = self.ocr_det_model_edit.text().strip()
        self.config.ocr_text_recognition_model_dir = self.ocr_rec_model_edit.text().strip()
        self.config.translation_model_path = self.translation_model_path_edit.text().strip()
        self.config.app_hotkey_toggle_panel = self.toggle_hotkey_edit.text().strip() or "Ctrl+Alt+A"
        self.config.demo_screenshot_hotkey = self.screenshot_hotkey_edit.text().strip() or "Ctrl+Alt+S"
        self.config.demo_record_hotkey = self.record_hotkey_edit.text().strip() or "Ctrl+Alt+R"
        self.config.demo_screenshot_dir = self.screenshot_dir_edit.text().strip() or str(USER_DATA_DIR / "demo_capture" / "screenshots")
        self.config.demo_video_dir = self.video_dir_edit.text().strip() or str(USER_DATA_DIR / "demo_capture" / "videos")
        source_key = str(self.source_language_combo.currentData() or "ja")
        target_key = str(self.target_language_combo.currentData() or "ko")
        apply_language_selection_to_config(self.config, source_key, target_key)

        self.runtime.save_config_settings(
            {
                "ocr_scale": self.config.ocr_scale,
                "min_ocr_confidence": self.config.min_ocr_confidence,
                "worker_mode": self.config.worker_mode,
                "selected_device": self.config.selected_device,
                "available_devices": self.config.available_devices,
                "translation_device": self.config.translation_device,
                "translation_device_index": self.config.translation_device_index,
                "translation_n_gpu_layers": self.config.translation_n_gpu_layers,
                "text_det_thresh": self.config.text_det_thresh,
                "text_det_box_thresh": self.config.text_det_box_thresh,
                "text_det_unclip_ratio": self.config.text_det_unclip_ratio,
                "text_rec_score_thresh": self.config.text_rec_score_thresh,
                "vertical_sentence_same_column_gap_px": self.config.vertical_sentence_same_column_gap_px,
                "horizontal_sentence_multiline_enabled": self.config.horizontal_sentence_multiline_enabled,
                "horizontal_sentence_line_gap_px": self.config.horizontal_sentence_line_gap_px,
                "horizontal_sentence_min_x_overlap_ratio": self.config.horizontal_sentence_min_x_overlap_ratio,
                "horizontal_sentence_require_continuation": self.config.horizontal_sentence_require_continuation,
                "ocr_text_detection_model_dir": self.config.ocr_text_detection_model_dir,
                "ocr_text_recognition_model_dir": self.config.ocr_text_recognition_model_dir,
                "paddle_lang": self.config.paddle_lang,
                "translation_source_language": self.config.translation_source_language,
                "translation_target_language": self.config.translation_target_language,
                "translation_model_path": self.config.translation_model_path,
                "app_hotkey_toggle_panel": self.config.app_hotkey_toggle_panel,
                "demo_screenshot_hotkey": self.config.demo_screenshot_hotkey,
                "demo_record_hotkey": self.config.demo_record_hotkey,
                "demo_screenshot_dir": self.config.demo_screenshot_dir,
                "demo_video_dir": self.config.demo_video_dir,
            }
        )
        self.runtime.demo_capture.update_output_dirs()
        if old_hotkeys != (
            self.config.app_hotkey_toggle_panel,
            self.config.demo_screenshot_hotkey,
            self.config.demo_record_hotkey,
        ):
            self.runtime.rebind_hotkeys()
        self.runtime.restart_ocr_workers()
        self.runtime.set_status("설정을 저장했고 OCR worker를 재시작했습니다.")
        self.refresh()

    def refresh(self):
        self.setWindowTitle(app_text(self.config, "title"))
        self.title_label.setText(app_text(self.config, "title"))
        self.tabs.setTabText(0, app_text(self.config, "settings"))
        self.tabs.setTabText(1, app_text(self.config, "usage"))
        self.tabs.setTabText(2, app_text(self.config, "language"))
        self.select_button.setText(app_text(self.config, "select_window"))
        self.screenshot_button.setText(app_text(self.config, "screenshot"))
        self.record_button.setText(app_text(self.config, "record_stop" if self.runtime.demo_capture.recording else "record_start"))
        self.output_button.setText(app_text(self.config, "open_output"))
        self.quit_button.setText(app_text(self.config, "quit"))
        self.apply_button.setText(app_text(self.config, "apply_settings"))
        self.language_apply_button.setText(app_text(self.config, "apply_settings"))
        self.translation_language_note_label.setText(app_text(self.config, "translation_language_note"))
        self.device_note_label.setText(
            f"Detected GPUs: {', '.join(device_display_name(d) for d in self.config.available_devices) if self.config.available_devices else 'none'} | "
            f"OCR: {device_display_name(self.config.selected_device)} | TR: {device_display_name(self.config.translation_device)}"
        )
        self.source_language_label.setText(app_text(self.config, "ocr_source_language"))
        self.target_language_label.setText(app_text(self.config, "translation_target_language"))
        self.ui_language_label.setText(app_text(self.config, "ui_language"))
        self.help_language_label.setText(app_text(self.config, "help_language"))
        self.help_label.setText(app_help_text(self.config))
        if not self.status_label.text():
            self.status_label.setText(app_text(self.config, "status_idle"))


class TrayRuntime:
    HOTKEY_TOGGLE_PANEL = 1
    HOTKEY_SCREENSHOT = 2
    HOTKEY_RECORD = 3

    def __init__(self, app: QApplication, config: AppConfig, overlay: BBoxOverlay, controller: CaptureController, demo_capture: DemoCaptureManager):
        self.app = app
        self.config = config
        self.overlay = overlay
        self.controller = controller
        self.demo_capture = demo_capture
        self.demo_capture.set_status_callback(self.set_status)
        self.panel = ControlPanel(self)
        self.tray = QSystemTrayIcon(self.make_icon(), self.app)
        self.tray.activated.connect(self.on_tray_activated)
        self.hotkeys = GlobalHotkeyFilter()
        self.app.installNativeEventFilter(self.hotkeys)
        self.register_hotkeys()
        self.rebuild_tray_menu()
        self.tray.show()
        self.set_status(app_text(self.config, "status_idle"))

    def make_icon(self) -> QIcon:
        return load_app_icon(self.app)

    def register_hotkeys(self):
        self.hotkeys.register(self.HOTKEY_TOGGLE_PANEL, self.config.app_hotkey_toggle_panel, self.toggle_panel)
        self.hotkeys.register(self.HOTKEY_SCREENSHOT, self.config.demo_screenshot_hotkey, self.save_demo_screenshot)
        self.hotkeys.register(self.HOTKEY_RECORD, self.config.demo_record_hotkey, self.toggle_demo_recording)

    def rebind_hotkeys(self):
        self.hotkeys.unregister_all()
        self.register_hotkeys()

    def rebuild_tray_menu(self):
        menu = QMenu()
        menu.addAction(app_text(self.config, "title"), self.show_panel)
        menu.addSeparator()
        menu.addAction(app_text(self.config, "select_window"), self.select_target_window)
        menu.addAction(app_text(self.config, "screenshot"), self.save_demo_screenshot)
        menu.addAction(app_text(self.config, "record_stop" if self.demo_capture.recording else "record_start"), self.toggle_demo_recording)
        menu.addAction(app_text(self.config, "open_output"), self.demo_capture.open_output_dir)
        menu.addSeparator()

        ui_menu = menu.addMenu(app_text(self.config, "ui_language"))
        help_menu = menu.addMenu(app_text(self.config, "help_language"))
        for code, label in APP_LANGUAGE_LABELS.items():
            ui_action = QAction(label, ui_menu)
            ui_action.setCheckable(True)
            ui_action.setChecked(code == self.config.app_ui_language)
            ui_action.triggered.connect(lambda checked=False, c=code: self.set_app_ui_language(c))
            ui_menu.addAction(ui_action)

            help_action = QAction(label, help_menu)
            help_action.setCheckable(True)
            help_action.setChecked(code == self.config.app_help_language)
            help_action.triggered.connect(lambda checked=False, c=code: self.set_app_help_language(c))
            help_menu.addAction(help_action)

        menu.addSeparator()
        menu.addAction(app_text(self.config, "quit"), self.quit)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip(app_text(self.config, "title"))

    def on_tray_activated(self, reason):
        if reason in {QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick}:
            self.toggle_panel()

    def show_panel(self):
        self.panel.refresh()
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()

    def toggle_panel(self):
        if self.panel.isVisible():
            self.panel.hide()
        else:
            self.show_panel()

    def set_status(self, message: str):
        self.panel.set_status(message)
        if self.tray.isVisible():
            self.tray.showMessage(app_text(self.config, "title"), message, QSystemTrayIcon.Information, 2500)

    def save_config_setting(self, key: str, value):
        self.save_config_settings({key: value})

    def save_config_settings(self, values: dict):
        try:
            raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
            raw.update(values)
            CONFIG_PATH.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        except Exception as exc:
            print(f"[config] failed to save settings: {repr(exc)}", flush=True)

    def restart_ocr_workers(self):
        self.controller.restart_workers()

    def set_app_ui_language(self, code: str):
        self.config.app_ui_language = code
        self.save_config_setting("app_ui_language", code)
        self.panel.refresh()
        self.rebuild_tray_menu()

    def set_app_help_language(self, code: str):
        self.config.app_help_language = code
        self.save_config_setting("app_help_language", code)
        self.panel.refresh()
        self.rebuild_tray_menu()

    def select_target_window(self):
        if sys.platform != "win32":
            self.set_status("Window selection is only available on Windows.")
            return
        dialog = WindowSelectorDialog(self.config.use_client_area)
        if dialog.exec() != QDialog.Accepted or not dialog.selected_hwnd:
            return
        self.config.use_client_area = dialog.use_client_area()
        self.controller.set_selected_window(dialog.selected_hwnd, dialog.selected_region, dialog.selected_title)
        self.set_status(f"target: {dialog.selected_title}")

    def save_demo_screenshot(self):
        path = self.demo_capture.save_screenshot()
        if path:
            self.set_status(f"screenshot saved: {path}")

    def toggle_demo_recording(self):
        path = self.demo_capture.toggle_recording()
        self.panel.refresh()
        self.rebuild_tray_menu()
        if path:
            state = "recording" if self.demo_capture.recording else "recording saved"
            self.set_status(f"{state}: {path}")

    def quit(self):
        self.demo_capture.stop_recording()
        self.hotkeys.unregister_all()
        self.tray.hide()
        self.app.quit()


def preload_translation_model(config: AppConfig):
    if not config.enable_translation:
        preload_message("[preload] translation disabled; skipping GGUF download")
        return
    if str(config.translation_backend or "").strip().lower() != "llama_cpp_gguf":
        preload_message(f"[preload] translation backend is {config.translation_backend}; skipping GGUF download")
        return

    config_dict = asdict(config)
    translator = LlamaCppGGUFTranslator(config_dict)
    translation_device = resolve_translation_device(config_dict)
    preload_message(f"[preload] preparing GGUF translation model on {translation_device}")
    local_path = translator.model_path()
    if local_path:
        candidate = Path(local_path)
        if candidate.is_file():
            preload_message(f"[preload] GGUF already available: {candidate}")
        else:
            raise FileNotFoundError(f"translation_model_path does not exist: {local_path}")

    if not local_path:
        repo_id = str(config.translation_model_id or "").strip()
        filename = str(config.translation_model_file or "").strip()
        if not repo_id or not filename:
            raise ValueError("translation_model_id and translation_model_file are required")

        cached_path = find_cached_hf_file(repo_id, filename)
        if cached_path:
            preload_message(f"[preload] GGUF already cached: {cached_path}")
        else:
            preload_message(f"[preload] downloading GGUF: {repo_id} / {filename}")
            from huggingface_hub import hf_hub_download

            downloaded = hf_hub_download(repo_id=repo_id, filename=filename)
            preload_message(f"[preload] GGUF cached: {downloaded}")

    translator.load()
    preload_message(f"[preload] GGUF ready ({translator.runtime_device})")


def preload_paddle_models(config: AppConfig):
    config_dict = asdict(config)
    if not str(config_dict.get("paddle_lang", "") or "").strip():
        config_dict["paddle_lang"] = "en"
    device = normalize_compute_device(config_dict.get("selected_device", "auto"), config_dict.get("available_devices", []))
    configure_accelerator_dlls()
    preload_message(f"[preload] preparing PaddleOCR models for lang={config_dict['paddle_lang']} device={device}")
    try:
        ocr, api_version = create_paddle_ocr(config_dict, device)
        warmup_paddle_ocr(ocr, config_dict)
    except Exception:
        log_message(f"[preload] PaddleOCR init failed on {device}:\n{traceback.format_exc()}")
        if not device.startswith("gpu"):
            raise
        preload_message(f"[preload] PaddleOCR GPU init failed on {device}; trying CPU fallback")
        ocr, api_version = create_paddle_ocr(config_dict, "cpu")
        warmup_paddle_ocr(ocr, config_dict)
        device = "cpu"
    del ocr
    preload_message(f"[preload] PaddleOCR ready ({api_version}, device={device})")


def preload_models() -> int:
    try:
        PRELOAD_LOG_PATH.unlink()
    except Exception:
        pass
    config = load_config()
    preload_message(f"[preload] app data: {USER_DATA_DIR}")
    preload_message(f"[preload] model cache: {MODEL_CACHE_DIR}")
    preload_message(f"[preload] config: {CONFIG_PATH}")

    errors = []
    for step in (preload_translation_model, preload_paddle_models):
        try:
            step(config)
        except Exception as exc:
            errors.append(f"{step.__name__}: {repr(exc)}")
            preload_message(f"[preload] failed: {step.__name__}: {repr(exc)}")

    if errors:
        preload_message("[preload] completed with errors:")
        for error in errors:
            preload_message(f"  - {error}")
        return 1

    preload_message("[preload] All configured models are ready.")
    return 0


def main():
    mp.freeze_support()
    try:
        mp.set_start_method("spawn", force=True)
    except RuntimeError:
        pass

    if "--preload-models" in sys.argv:
        sys.exit(preload_models())

    config = load_config()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_DISPLAY_NAME)
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setOrganizationName(APP_SLUG)
    app.setWindowIcon(load_app_icon(app))
    app.setQuitOnLastWindowClosed(False)

    start_to_tray = bool(getattr(config, "app_start_to_tray", True))

    if bool(getattr(config, "language_select_on_start", True)) and not start_to_tray:
        if not select_languages_modal(config):
            sys.exit(0)

    selected_hwnd = None
    mode = str(config.capture_mode or "region").lower().strip()
    if start_to_tray and mode in {"window_picker", "window_ui", "select_ui"}:
        config.capture_mode = "region"
        print("[tray] startup window picker is deferred. Use tray panel > Select Target Window.", flush=True)
    elif mode in {"window_picker", "window_ui", "select_ui"}:
        if sys.platform == "win32":
            selector = WindowSelectorDialog(config.use_client_area)
            if selector.exec() == QDialog.Accepted and selector.selected_hwnd:
                selected_hwnd = selector.selected_hwnd
                config.use_client_area = selector.use_client_area()
                config.capture_mode = "selected_hwnd"
                if selector.selected_region:
                    config.capture_region = selector.selected_region
                print(
                    f'[window picker] selected="{selector.selected_title}" hwnd={selected_hwnd} '
                    f"region={selector.selected_region}",
                    flush=True,
                )
            elif not config.window_follow_fallback_to_region:
                sys.exit(0)
            else:
                config.capture_mode = "region"
                print("[window picker] 선택이 취소되어 capture_region으로 fallback합니다.", flush=True)
        else:
            config.capture_mode = "region"
            print("[window picker] Windows에서만 창 선택 UI를 지원합니다. capture_region으로 fallback합니다.", flush=True)

    overlay = BBoxOverlay(config)
    writer = ExtractedTextWriter(config)
    controller = CaptureController(config, overlay, writer, selected_hwnd=selected_hwnd)
    demo_capture = DemoCaptureManager(config, overlay, controller)
    runtime = TrayRuntime(app, config, overlay, controller, demo_capture)
    if not start_to_tray:
        runtime.show_panel()

    capture_timer = QTimer()
    capture_timer.timeout.connect(controller.capture_once)
    capture_timer.start(config.capture_interval_ms)

    poll_timer = QTimer()
    poll_timer.timeout.connect(controller.poll_results)
    poll_timer.start(30)

    exit_code = app.exec()
    demo_capture.stop_recording()
    runtime.hotkeys.unregister_all()
    controller.stop()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
