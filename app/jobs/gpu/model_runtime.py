"""Local model runtime for the GPU worker (isolated from PostgreSQL)."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from typing import Any

logger = logging.getLogger("sentinel.gpu.ai")

_LOCK = threading.Lock()
_RUNTIME: dict[str, Any] = {
    "llm": None,
    "backend": None,
    "model_path": None,
    "gpu_layers": None,
    "context_size": None,
    "model_memory_mb": None,
    "vram_before_mb": None,
    "vram_after_load_mb": None,
    "cuda_build": None,
    "warmed": False,
}


def env_provider() -> str:
    return (os.environ.get("AI_PROVIDER") or "local").strip().lower()


def env_model_name() -> str:
    return (os.environ.get("AI_MODEL_NAME") or "Qwen2.5-1.5B-Instruct-Q4_K_M").strip()


def env_model_path() -> str:
    explicit = (os.environ.get("AI_MODEL_PATH") or "").strip()
    if explicit:
        return explicit
    cache = (os.environ.get("AI_MODEL_CACHE") or "/models").strip()
    filename = (os.environ.get("AI_MODEL_FILENAME") or "").strip()
    if not filename:
        # Prefer the Sentinel alias, then the official Hugging Face filename.
        for candidate in (
            f"{env_model_name()}.gguf",
            "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf",
            "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        ):
            path = os.path.join(cache, candidate)
            if os.path.isfile(path):
                return path
        filename = f"{env_model_name()}.gguf"
    return os.path.join(cache, filename)


def env_gpu_layers() -> int:
    return int(os.environ.get("AI_GPU_LAYERS", "20"))


def env_context() -> int:
    return int(os.environ.get("AI_MAX_CONTEXT", "2048"))


def env_max_output_tokens() -> int:
    return int(os.environ.get("AI_MAX_OUTPUT_TOKENS", "768"))


def env_temperature() -> float:
    return float(os.environ.get("AI_TEMPERATURE", "0"))


def query_vram_mb() -> dict[str, int | None]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total,memory.used,memory.free",
                "--format=csv,noheader,nounits",
                "-i",
                os.environ.get("GPU_DEVICE", "0"),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {"total": None, "used": None, "free": None}
    if result.returncode != 0:
        return {"total": None, "used": None, "free": None}
    line = (result.stdout or "").strip().splitlines()
    if not line:
        return {"total": None, "used": None, "free": None}
    parts = [part.strip() for part in line[0].split(",")]
    if len(parts) < 3:
        return {"total": None, "used": None, "free": None}
    try:
        return {"total": int(float(parts[0])), "used": int(float(parts[1])), "free": int(float(parts[2]))}
    except ValueError:
        return {"total": None, "used": None, "free": None}


def query_vram_used_mb() -> int | None:
    return query_vram_mb().get("used")


def detect_cuda_build() -> dict[str, Any]:
    info: dict[str, Any] = {"llama_cpp_import": False, "cuda_available": False, "supports_sm_61_hint": None}
    try:
        import llama_cpp
        from llama_cpp import llama_cpp as native
    except ImportError:
        return info
    info["llama_cpp_import"] = True
    info["llama_cpp_path"] = getattr(llama_cpp, "__file__", None)
    for attr in ("llama_supports_gpu_offload", "ggml_cpu_has_cuda", "llama_print_system_info"):
        fn = getattr(native, attr, None)
        if callable(fn):
            try:
                value = fn()
                info[attr] = value if not isinstance(value, bytes) else value.decode(errors="replace")
            except Exception as exc:
                info[attr] = f"error:{type(exc).__name__}"
    cuda_flag = os.environ.get("LLAMA_CUDA", "") or os.environ.get("CMAKE_ARGS", "")
    info["build_env_hint"] = cuda_flag
    info["cuda_available"] = bool(
        info.get("llama_supports_gpu_offload") is True
        or "cuda" in str(info.get("llama_print_system_info", "")).lower()
        or "GGML_CUDA" in str(cuda_flag).upper()
        or os.environ.get("SENTINEL_LLAMA_CUDA_BUILD") == "1"
    )
    info["supports_sm_61_hint"] = os.environ.get("SENTINEL_CUDA_ARCHS", "61")
    return info


def preload_model() -> dict[str, Any]:
    """Load the GGUF once. Does not generate any token."""
    logger.info("ai_preload_started")
    if env_provider() == "stub" or os.environ.get("SENTINEL_AI_STUB") == "1":
        payload = {
            "ok": True,
            "backend": "stub",
            "gpu_layers": None,
            "status": "skipped",
            "skipped": True,
        }
        logger.info(
            "ai_preload_finished backend=%s gpu_layers=%s status=%s",
            payload["backend"],
            payload["gpu_layers"],
            payload["status"],
        )
        return payload

    model_path = env_model_path()
    if not os.path.isfile(model_path):
        logger.warning("ai_preload_skipped reason=model_missing path=%s", model_path)
        payload = {
            "ok": False,
            "backend": None,
            "gpu_layers": None,
            "status": "model_missing",
            "error": "model_missing",
            "path": model_path,
        }
        logger.info(
            "ai_preload_finished backend=%s gpu_layers=%s status=%s",
            payload["backend"],
            payload["gpu_layers"],
            payload["status"],
        )
        return payload

    with _LOCK:
        if _RUNTIME.get("llm") is None:
            _load_llama_locked()
        backend = _RUNTIME.get("backend")
        gpu_layers = _RUNTIME.get("gpu_layers")
    status = runtime_status()
    payload = {
        **status,
        "ok": True,
        "backend": backend,
        "gpu_layers": gpu_layers,
        "status": "loaded",
    }
    logger.info(
        "ai_preload_finished backend=%s gpu_layers=%s status=%s",
        backend,
        gpu_layers,
        payload["status"],
    )
    return payload


def warmup_model() -> dict[str, Any]:
    """Load the GGUF once. Kept for callers; boot uses preload_model and generates nothing."""
    return preload_model()


def generate_structured_output(payload: dict) -> tuple[str, dict, dict]:
    """Return raw text, parsed object, and generation metadata."""
    meta: dict[str, Any] = {
        "backend": "stub",
        "tokens_input": None,
        "tokens_output": None,
        "repair_attempted": False,
    }
    if env_provider() == "stub" or os.environ.get("SENTINEL_AI_STUB") == "1":
        raw, parsed = _stub_output(payload)
        return raw, parsed, meta
    model_path = env_model_path()
    if not os.path.isfile(model_path):
        raise FileNotFoundError(f"GGUF model missing at {model_path}")
    prompt = _build_prompt(payload)
    raw, usage = _run_llama_cpp(prompt)
    meta["backend"] = runtime_status().get("model_backend") or "unknown"
    meta["tokens_input"] = usage.get("prompt_tokens")
    meta["tokens_output"] = usage.get("completion_tokens")
    try:
        parsed = json.loads(_extract_json(raw))
        return raw, parsed, meta
    except Exception:
        meta["repair_attempted"] = True
        repaired_raw, repair_usage = _run_llama_cpp(_repair_prompt(raw))
        meta["tokens_output"] = (meta["tokens_output"] or 0) + (repair_usage.get("completion_tokens") or 0)
        parsed = json.loads(_extract_json(repaired_raw))
        return repaired_raw, parsed, meta


def runtime_status() -> dict:
    with _LOCK:
        return {
            "ai_provider": env_provider(),
            "model_name": env_model_name(),
            "model_loaded": bool(_RUNTIME.get("llm") is not None),
            "model_path": _RUNTIME.get("model_path"),
            "model_backend": _RUNTIME.get("backend"),
            "gpu_layers": _RUNTIME.get("gpu_layers"),
            "context_size": _RUNTIME.get("context_size"),
            "model_memory_mb": _RUNTIME.get("model_memory_mb"),
            "vram_before_mb": _RUNTIME.get("vram_before_mb"),
            "vram_after_load_mb": _RUNTIME.get("vram_after_load_mb"),
            "cuda_build": _RUNTIME.get("cuda_build"),
            "provider": env_provider(),
            "device": _RUNTIME.get("backend"),
        }


def _build_prompt(payload: dict) -> str:
    from app.jobs.gpu.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

    user = USER_PROMPT_TEMPLATE.format(
        document_id=payload.get("document_id"),
        title=payload.get("title") or "",
        published_at=payload.get("published_at") or "",
        source_name=payload.get("source_name") or "",
        source_type=payload.get("source_type") or "",
        trust_level=payload.get("trust_level") or "",
        company_context=json.dumps(payload.get("company_context") or [], ensure_ascii=False),
        content_truncated=payload.get("content_truncated", False),
        original_char_count=payload.get("original_char_count", 0),
        analyzed_char_count=payload.get("analyzed_char_count", 0),
        content_text=payload.get("content_text") or "",
    )
    return f"{SYSTEM_PROMPT}\n\n{user}"


def _repair_prompt(raw: str) -> str:
    return (
        "Fix the following into a single valid JSON object only. "
        "Keep the same keys if present. No markdown.\n\n"
        f"{raw[:6000]}"
    )


def _run_llama_cpp(prompt: str) -> tuple[str, dict]:
    with _LOCK:
        if _RUNTIME.get("llm") is None:
            _load_llama_locked()
        llm = _RUNTIME["llm"]
    response = llm.create_chat_completion(
        messages=[{"role": "user", "content": prompt}],
        temperature=env_temperature(),
        max_tokens=env_max_output_tokens(),
        response_format={"type": "json_object"},
    )
    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Model returned empty content")
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    return content, usage


def _load_llama_locked() -> None:
    try:
        from llama_cpp import Llama
    except ImportError as exc:
        raise RuntimeError("llama-cpp-python is not installed on the GPU worker") from exc

    model_path = env_model_path()
    n_ctx = env_context()
    requested_layers = max(0, env_gpu_layers())
    cuda_info = detect_cuda_build()
    _RUNTIME["cuda_build"] = cuda_info
    vram_before = query_vram_mb()
    _RUNTIME["vram_before_mb"] = vram_before.get("used")

    if not cuda_info.get("cuda_available") and requested_layers > 0:
        logger.warning(
            "ai_cuda_build_missing falling_back=CPU reason=llama_cpp_without_cuda_offload"
        )
        llm, backend, layers = _instantiate(Llama, model_path, n_ctx, 0)
    else:
        try:
            llm, backend, layers = _instantiate(Llama, model_path, n_ctx, requested_layers)
        except Exception as first_exc:
            reduced = max(1, requested_layers // 2) if requested_layers > 1 else 0
            logger.warning(
                "ai_load_oom_or_error error_type=%s retry_gpu_layers=%s",
                type(first_exc).__name__,
                reduced,
            )
            try:
                if reduced > 0:
                    llm, backend, layers = _instantiate(Llama, model_path, n_ctx, reduced)
                else:
                    raise first_exc
            except Exception as second_exc:
                logger.warning(
                    "ai_fallback_cpu error_type=%s previous=%s",
                    type(second_exc).__name__,
                    type(first_exc).__name__,
                )
                llm, backend, layers = _instantiate(Llama, model_path, n_ctx, 0)

    vram_after = query_vram_mb()
    before_used = vram_before.get("used")
    after_used = vram_after.get("used")
    memory = None
    if isinstance(before_used, int) and isinstance(after_used, int):
        memory = max(0, after_used - before_used)
    _RUNTIME["llm"] = llm
    _RUNTIME["backend"] = backend
    _RUNTIME["model_path"] = model_path
    _RUNTIME["gpu_layers"] = layers
    _RUNTIME["context_size"] = n_ctx
    _RUNTIME["model_memory_mb"] = memory
    _RUNTIME["vram_after_load_mb"] = after_used
    logger.info(
        "ai_model_loaded runtime=llama.cpp backend=%s gpu_layers=%s model=%s context=%s "
        "vram_before_mb=%s vram_after_mb=%s model_memory_mb=%s",
        backend,
        layers,
        model_path,
        n_ctx,
        before_used,
        after_used,
        memory,
    )


def _instantiate(Llama, model_path: str, n_ctx: int, n_gpu_layers: int):
    llm = Llama(
        model_path=model_path,
        n_ctx=n_ctx,
        n_gpu_layers=n_gpu_layers,
        verbose=False,
    )
    backend = "CUDA" if n_gpu_layers > 0 else "CPU"
    if n_gpu_layers == 0:
        logger.warning("ai_backend=CPU model=%s context=%s", model_path, n_ctx)
    return llm, backend, n_gpu_layers


def _extract_json(raw: str) -> str:
    text = raw.strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        raise RuntimeError("Model output is not JSON")
    return match.group(0)


def _stub_output(payload: dict) -> tuple[str, dict]:
    text = (payload.get("content_text") or "").lower()
    title = (payload.get("title") or "Document").strip()
    companies = []
    for item in payload.get("company_context") or []:
        name = item.get("name") or "Unknown"
        ticker = item.get("ticker")
        role = "SUBJECT" if item.get("relation_type") == "SUBJECT" else "OTHER"
        companies.append(
            {
                "name": name,
                "ticker": ticker,
                "role": role,
                "confidence": 85 if role == "SUBJECT" else 70,
                "evidence": f"Mentionné dans le contexte documentaire: {name}.",
            }
        )
    events = []
    if any(token in text for token in ("partnership", "partenariat", "accord", "contract")):
        events.append(
            {
                "type": "PARTNERSHIP",
                "importance": "MEDIUM",
                "confidence": 72,
                "description": "Le document évoque un partenariat ou un accord.",
                "evidence": "Mot-clé partnership/accord présent dans le texte fourni.",
            }
        )
    if any(token in text for token in ("launch", "lancement", "product", "produit")):
        events.append(
            {
                "type": "PRODUCT_LAUNCH",
                "importance": "MEDIUM",
                "confidence": 68,
                "description": "Le document mentionne un lancement ou un produit.",
                "evidence": "Mot-clé launch/product présent dans le texte fourni.",
            }
        )
    result = {
        "summary": f"Analyse stub du document « {title} » basée uniquement sur le texte fourni.",
        "companies": companies,
        "events": events,
        "relationships": [],
        "strategic_signals": [],
        "risks": [],
        "analysis_confidence": 65 if companies else 50,
    }
    raw = json.dumps(result, ensure_ascii=False)
    return raw, result
