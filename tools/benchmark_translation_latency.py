import argparse
import statistics
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app import LlamaCppGGUFTranslator, load_config, normalize_text


DEFAULT_TEXTS = [
    "The enemy responded with a lethal curse that reached for her heart like a set of talons.",
    "Humming with power, it meant to rend Aoko's heart and burn itself out in the process.",
    "She took one step forward and raised her hand.",
]


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * pct)))
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Boku No Translator GGUF translation latency.")
    parser.add_argument("--rounds", type=int, default=2, help="How many passes to run over the sample texts.")
    parser.add_argument("--text", action="append", default=[], help="Text to translate. Can be passed multiple times.")
    parser.add_argument("--profile", action="store_true", help="Also write per-call profile logs to the app log.")
    parser.add_argument("--prompt-preset", choices=["fast", "balanced"], default=None)
    parser.add_argument("--max-new-tokens", type=int, default=None)
    parser.add_argument("--llama-cache", action="store_true", help="Enable llama.cpp RAM prompt-state cache for this run.")
    args = parser.parse_args()

    config = asdict(load_config())
    if args.prompt_preset:
        from app import translation_prompt_template_for_target

        config["translation_prompt_preset"] = args.prompt_preset
        config["translation_prompt_template"] = translation_prompt_template_for_target(
            str(config.get("translation_target_language", "ko")),
            args.prompt_preset,
        )
    if args.max_new_tokens is not None:
        config["translation_max_new_tokens"] = int(args.max_new_tokens)
    if args.profile:
        config["translation_profile"] = True
    if args.llama_cache:
        config["translation_llama_cache_enabled"] = True

    texts = [normalize_text(text) for text in (args.text or DEFAULT_TEXTS)]
    texts = [text for text in texts if text]
    if not texts:
        raise SystemExit("No benchmark texts were provided.")

    translator = LlamaCppGGUFTranslator(config)

    load_start = time.perf_counter()
    translator.load()
    load_ms = (time.perf_counter() - load_start) * 1000.0

    print(f"model_load_ms={load_ms:.1f}")
    print(
        "runtime="
        f"device={translator.runtime_device} "
        f"n_gpu_layers={translator.runtime_n_gpu_layers} "
        f"n_ctx={config.get('translation_n_ctx')} "
        f"n_batch={config.get('translation_n_batch')} "
        f"n_ubatch={config.get('translation_n_ubatch')} "
        f"flash_attn={config.get('translation_flash_attn')} "
        f"max_new_tokens={config.get('translation_max_new_tokens')} "
        f"prompt_preset={config.get('translation_prompt_preset')}"
    )

    latencies: list[float] = []
    for round_index in range(max(1, int(args.rounds))):
        for text in texts:
            prompt = translator.build_prompt(text)
            try:
                prompt_tokens = len(translator.llama.tokenize(prompt.encode("utf-8")))
            except Exception:
                prompt_tokens = -1

            started = time.perf_counter()
            translated = translator.translate_one(text)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            latencies.append(elapsed_ms)
            print(
                f"round={round_index + 1} elapsed_ms={elapsed_ms:.1f} "
                f"prompt_tokens={prompt_tokens} source_chars={len(text)} "
                f"output_chars={len(translated)}"
            )

    print(
        "summary "
        f"count={len(latencies)} "
        f"avg_ms={statistics.mean(latencies):.1f} "
        f"p50_ms={statistics.median(latencies):.1f} "
        f"p90_ms={percentile(latencies, 0.90):.1f} "
        f"max_ms={max(latencies):.1f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
