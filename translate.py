"""Language detection and translation for OTT title localization.

Uses OpenRouter text LLM (default: openai/gpt-4o-mini) to:
  - detect(title): identify whether a title is Korean / English / Chinese
  - translate(title, src, targets): translate to other languages with
    OTT-localization sensibilities (creative, length-aware, official-style).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Literal

import requests

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - yaml is optional at import time
    yaml = None  # type: ignore


Lang = Literal["kr", "en", "zh"]

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-4o-mini"
CONFIG_PATH = Path(__file__).parent / "config.yaml"
MAX_RETRIES = 3
TIMEOUT_SEC = 30

_LANG_NAMES = {
    "kr": "Korean (한국어)",
    "en": "English",
    "zh": "Simplified Chinese (简体中文)",
}


def _load_model() -> str:
    """Read text_llm from config.yaml; fall back to DEFAULT_MODEL."""
    if yaml is None or not CONFIG_PATH.exists():
        return DEFAULT_MODEL
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return str(cfg.get("text_llm") or DEFAULT_MODEL)
    except Exception:
        return DEFAULT_MODEL


def _api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENROUTER_API_KEY environment variable is not set. "
            "Add it to your environment or .env file."
        )
    return key


def _call_llm(
    system: str,
    user: str,
    *,
    temperature: float = 0.3,
    max_tokens: int = 200,
) -> str:
    """POST to OpenRouter chat completions with retry x3.

    Returns the assistant's message content (stripped).
    Raises RuntimeError after exhausting retries.
    """
    model = _load_model()
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/odkmedia/poster-automation",
        "X-Title": "ODK Poster Automation",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                OPENROUTER_URL,
                headers=headers,
                data=json.dumps(payload),
                timeout=TIMEOUT_SEC,
            )
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", "2"))
                time.sleep(retry_after)
                raise RuntimeError(f"rate limited (429): {resp.text[:200]}")
            if resp.status_code >= 500:
                raise RuntimeError(f"server error {resp.status_code}: {resp.text[:200]}")
            if resp.status_code >= 400:
                raise RuntimeError(f"client error {resp.status_code}: {resp.text[:200]}")

            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError(f"empty response from model: {data}")
            return content.strip()
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < MAX_RETRIES:
                time.sleep(2 ** (attempt - 1))  # 1s, 2s
                continue
    raise RuntimeError(
        f"OpenRouter call failed after {MAX_RETRIES} attempts: {last_err}"
    )


def _clean_title(raw: str) -> str:
    """Strip wrapping quotes / whitespace from model output."""
    s = raw.strip()
    # Remove surrounding quotes (straight or smart) up to 2 layers.
    for _ in range(2):
        if len(s) >= 2 and s[0] in "\"'“”‘’「『《" and s[-1] in "\"'“”‘’」』》":
            s = s[1:-1].strip()
    # If model returned multiple lines, take the first non-empty line.
    for line in s.splitlines():
        line = line.strip()
        if line:
            return line
    return s


def detect(title: str) -> Lang:
    """Detect the language of a title.

    Returns 'kr', 'en', or 'zh'. On any uncertainty, errs toward the
    script-dominant language (Hangul -> kr, Han -> zh, Latin -> en).
    """
    if not title or not title.strip():
        raise ValueError("title must be a non-empty string")

    system = (
        "You are a language identifier for OTT streaming title strings. "
        "Given a single title, decide whether it is primarily Korean, "
        "English, or Simplified/Traditional Chinese. "
        "Reply with EXACTLY one token: 'kr', 'en', or 'zh'. "
        "No punctuation, no explanation, lowercase only."
    )
    user = f"Title: {title.strip()}"

    raw = _call_llm(system, user, temperature=0.0, max_tokens=4)
    token = raw.strip().lower().strip(".,'\"`")
    if token in ("kr", "ko", "korean"):
        return "kr"
    if token in ("zh", "cn", "chinese", "zh-cn", "zh_cn"):
        return "zh"
    if token in ("en", "eng", "english"):
        return "en"
    # Heuristic fallback if the model misbehaves.
    for ch in title:
        if "가" <= ch <= "힣":
            return "kr"
        if "一" <= ch <= "鿿":
            return "zh"
    return "en"


def _translate_one(title: str, src: Lang, tgt: Lang) -> str:
    """Translate `title` from src lang to tgt lang via the LLM."""
    src_name = _LANG_NAMES.get(src, src)
    tgt_name = _LANG_NAMES.get(tgt, tgt)

    system = (
        "You are an expert OTT (streaming service) title localizer for a "
        "Korean media company distributing globally. "
        "You translate show / movie titles between Korean, English, and "
        "Simplified Chinese. Rules: "
        "(1) Prefer the officially licensed title if one is well known; "
        "otherwise produce a natural, culturally idiomatic localization — "
        "not a literal word-for-word translation. "
        "(2) Preserve the genre, tone, and any numeric season/sequel "
        "markers (e.g., '3', 'II'). "
        "(3) Keep the length reasonably close to the original so it fits "
        "on a poster. "
        "(4) For Chinese, always use Simplified Chinese (简体中文). "
        "(5) For English, use title case suitable for a streaming poster. "
        "(6) Output ONLY the translated title. No quotes, no romanization, "
        "no explanation, no alternatives, no trailing punctuation."
    )
    user = (
        f"Source language: {src_name}\n"
        f"Target language: {tgt_name}\n"
        f"Title: {title.strip()}\n\n"
        f"Translated title in {tgt_name}:"
    )

    raw = _call_llm(system, user, temperature=0.4, max_tokens=80)
    return _clean_title(raw)


def translate(title: str, src: str, targets: list[str]) -> dict[str, str]:
    """Translate `title` (in `src` language) to each language in `targets`.

    Returns: {lang: translated_title} including src (unchanged) for the
    convenience of callers iterating over all three languages.
    """
    if not title or not title.strip():
        raise ValueError("title must be a non-empty string")
    if src not in _LANG_NAMES:
        raise ValueError(f"src must be one of {list(_LANG_NAMES)}; got {src!r}")

    out: dict[str, str] = {src: title.strip()}
    for tgt in targets:
        if tgt == src:
            continue
        if tgt not in _LANG_NAMES:
            raise ValueError(
                f"target lang must be one of {list(_LANG_NAMES)}; got {tgt!r}"
            )
        out[tgt] = _translate_one(title.strip(), src, tgt)  # type: ignore[arg-type]
    return out


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import sys

    sample = sys.argv[1] if len(sys.argv) > 1 else "뭉쳐야찬다3"
    lang = detect(sample)
    print(f"detected: {lang}")
    others = [l for l in ("kr", "en", "zh") if l != lang]
    print(translate(sample, lang, others))
