"""Gemini 2.5 Flash Image wrapper via OpenRouter.

Public surface (per ARCHITECTURE.md §3):

    clean(image_bytes)                  -> bytes   # remove all text
    outpaint(image_bytes, target)       -> bytes   # 'landscape' | 'banner'
    title_swap(clean_base, title, lang) -> bytes
    title_extract(poster, title, lang)  -> bytes   # title on flat #FF00FF

All functions accept an optional `model_id` keyword (defaults to
`models.default` from config.yaml, falling back to
``google/gemini-2.5-flash-image``).

------------------------------------------------------------------
OpenRouter contract (verified 2026-06-03)
------------------------------------------------------------------
Endpoint:  POST https://openrouter.ai/api/v1/chat/completions
Auth:      Authorization: Bearer <OPENROUTER_API_KEY>

Request body for image *editing* (image-in + image-out):

    {
      "model": "google/gemini-2.5-flash-image",
      "modalities": ["image", "text"],
      "messages": [{
        "role": "user",
        "content": [
          {"type": "text",      "text": "<prompt>"},
          {"type": "image_url", "image_url": {
              "url": "data:image/png;base64,<...>"
          }}
        ]
      }]
    }

Response shape — the generated image is returned in
``choices[0].message.images[*].image_url.url`` as a base64 data URL
(``data:image/png;base64,...``).  Any narrative text the model
includes lands in ``choices[0].message.content`` and is ignored here.

Pricing: token-based at $0.30 / 1M input tokens and $2.50 / 1M output
tokens; one generated image is ~1290 output tokens, giving roughly
$0.039 per generated image — matching the ~$0.04/image figure used
in the PRD cost model.

Fallback: if OpenRouter ever drops image-output support for this
model, switch ``OPENROUTER_URL`` to the direct Google AI Studio
endpoint (``https://generativelanguage.googleapis.com/v1beta/models
/gemini-2.5-flash-image:generateContent``) and adapt
``_extract_image_bytes`` to read ``candidates[0].content.parts[*]
.inlineData.data``.  All call sites here stay unchanged.

------------------------------------------------------------------
Design choices worth flagging
------------------------------------------------------------------
* ``title_extract`` requests the title on a flat ``#FF00FF`` magenta
  background rather than a true alpha channel.  Gemini 2.5 Flash
  Image does not reliably emit an alpha channel on arbitrary edits,
  so ``dims.py`` chroma-keys the magenta to transparent downstream.
* Each call asks for ~1.2x the final target size; ``dims.snap``
  later center-crops + Lanczos-resizes to the exact spec pixel size.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Literal

import requests

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - yaml is optional at import time
    yaml = None  # type: ignore


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GOOGLE_GENERATE_URL_TMPL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
GOOGLE_FILES_UPLOAD_URL = (
    "https://generativelanguage.googleapis.com/upload/v1beta/files"
)
DEFAULT_MODEL_ID = "google/gemini-2.5-flash-image"

PROMPTS_DIR = Path(__file__).parent / "prompts"
CONFIG_PATH = Path(__file__).parent / "config.yaml"

MAX_RETRIES = 3
REQUEST_TIMEOUT_SEC = 180  # image generation can be slow
GOOGLE_INLINE_MAX_BYTES = 18 * 1024 * 1024  # use Files API above this

# Logging: stderr per call (model, prompt label, bytes returned).
_logger = logging.getLogger("gemini")
if not _logger.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("[gemini] %(message)s"))
    _logger.addHandler(_h)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False


Target = Literal["landscape", "banner"]
Lang = Literal["kr", "en", "zh"]

_LANG_NAMES: dict[str, str] = {
    "kr": "Korean (한국어)",
    "en": "English",
    "zh": "Simplified Chinese (简体中文)",
}

# ---------------------------------------------------------------------------
# Config + prompt loading
# ---------------------------------------------------------------------------


def _load_default_model() -> str:
    """Read ``models.default`` from config.yaml; fall back to DEFAULT_MODEL_ID."""
    if yaml is None or not CONFIG_PATH.exists():
        return DEFAULT_MODEL_ID
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        models = cfg.get("models") or {}
        return str(models.get("default") or DEFAULT_MODEL_ID)
    except Exception as e:  # noqa: BLE001 - never let config errors break callers
        _logger.warning("config.yaml read failed (%s); using %s", e, DEFAULT_MODEL_ID)
        return DEFAULT_MODEL_ID


def _load_prompt_override(model_id: str, prompt_label: str) -> str | None:
    """Look up an optional per-model prompt override path in config.yaml."""
    if yaml is None or not CONFIG_PATH.exists():
        return None
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        overrides = (cfg.get("prompts_override") or {}).get(model_id) or {}
        rel = overrides.get(prompt_label)
        if not rel:
            return None
        path = Path(rel)
        if not path.is_absolute():
            path = Path(__file__).parent / path
        return path.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        _logger.warning("prompt override lookup failed (%s)", e)
        return None


def _load_prompt(label: str, model_id: str) -> str:
    """Load a prompt template by label (e.g. 'clean', 'outpaint_landscape').

    Honours per-model overrides from ``config.yaml``; otherwise reads
    ``prompts/<label>.txt``.
    """
    override = _load_prompt_override(model_id, label)
    if override is not None:
        return override
    path = PROMPTS_DIR / f"{label}.txt"
    if not path.exists():
        raise FileNotFoundError(f"prompt template not found: {path}")
    return path.read_text(encoding="utf-8")


def _api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENROUTER_API_KEY environment variable is not set. "
            "Add it to your environment or .env file."
        )
    return key


# ---------------------------------------------------------------------------
# Internal: OpenRouter call + image extraction
# ---------------------------------------------------------------------------


_MAGIC_PNG = b"\x89PNG\r\n\x1a\n"
_MAGIC_JPEG = b"\xff\xd8\xff"
_MAGIC_WEBP_RIFF = b"RIFF"
_MAGIC_WEBP_WEBP = b"WEBP"
_MAGIC_GIF = b"GIF8"


def _sniff_mime(image_bytes: bytes) -> str:
    if image_bytes.startswith(_MAGIC_PNG):
        return "image/png"
    if image_bytes.startswith(_MAGIC_JPEG):
        return "image/jpeg"
    if image_bytes.startswith(_MAGIC_GIF):
        return "image/gif"
    if (image_bytes[:4] == _MAGIC_WEBP_RIFF
            and image_bytes[8:12] == _MAGIC_WEBP_WEBP):
        return "image/webp"
    return "image/png"


def _image_to_data_url(image_bytes: bytes, mime: str | None = None) -> str:
    if mime is None:
        mime = _sniff_mime(image_bytes)
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _extract_image_bytes(resp_json: dict[str, Any]) -> bytes:
    """Pull the first generated image out of an OpenRouter chat response.

    Expects ``choices[0].message.images[*].image_url.url`` as a base64
    data URL.  Raises ``RuntimeError`` if no image is present (callers
    treat that as a retryable failure).
    """
    try:
        message = resp_json["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"malformed response (no choices/message): {e}") from e

    images = message.get("images") or []
    for img in images:
        url = (img.get("image_url") or {}).get("url") or img.get("url")
        if not url:
            continue
        if url.startswith("data:"):
            try:
                _, b64 = url.split(",", 1)
            except ValueError as e:
                raise RuntimeError(f"malformed data URL in response: {e}") from e
            return base64.b64decode(b64)
        # Bare http(s) URL — fetch it.
        r = requests.get(url, timeout=REQUEST_TIMEOUT_SEC)
        r.raise_for_status()
        return r.content

    # Some OpenAI-compat servers stash image parts inside content.
    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in ("image_url", "output_image", "image"):
                url = (part.get("image_url") or {}).get("url") or part.get("url")
                if url and url.startswith("data:"):
                    _, b64 = url.split(",", 1)
                    return base64.b64decode(b64)
                b64 = part.get("b64_json") or part.get("image_base64")
                if b64:
                    return base64.b64decode(b64)

    raise RuntimeError(
        "no image returned in response; "
        f"message keys={list(message.keys())} content_type={type(message.get('content')).__name__}"
    )


def _call_openrouter(
    prompt: str,
    image: bytes | None,
    model_id: str,
    *,
    prompt_label: str = "unknown",
    extra_body: dict[str, Any] | None = None,
    extra_images: list[bytes] | None = None,
) -> bytes:
    """Single OpenRouter chat-completions call returning generated image bytes.

    Args:
        prompt: text instruction for the model.
        image: optional input image (bytes); when provided it is attached
            as a base64 data URL ``image_url`` content part — this is the
            "primary" image to edit.
        model_id: e.g. ``google/gemini-2.5-flash-image``.
        prompt_label: short tag used in log lines (e.g. ``"clean"``).
        extra_body: any extra top-level keys merged into the request body
            (e.g. ``image_config``).
        extra_images: optional additional images (e.g. typography reference)
            appended after `image`. The text prompt is responsible for
            telling the model how to use them.

    Retries up to ``MAX_RETRIES`` times on 5xx / timeout / missing-image
    responses with exponential backoff (1s, 2s, 4s).  Honours the
    ``Retry-After`` header on 429 responses.
    """
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/odkmedia/poster-automation",
        "X-Title": "ODK Poster Automation",
    }

    user_content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    if image is not None:
        user_content.append(
            {"type": "image_url", "image_url": {"url": _image_to_data_url(image)}}
        )
    for extra in extra_images or []:
        user_content.append(
            {"type": "image_url", "image_url": {"url": _image_to_data_url(extra)}}
        )

    payload: dict[str, Any] = {
        "model": model_id,
        "modalities": ["image", "text"],
        "messages": [{"role": "user", "content": user_content}],
    }
    if extra_body:
        payload.update(extra_body)

    body = json.dumps(payload)
    in_bytes = len(image) if image is not None else 0
    in_bytes += sum(len(b) for b in (extra_images or []))

    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                OPENROUTER_URL,
                headers=headers,
                data=body,
                timeout=REQUEST_TIMEOUT_SEC,
            )

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else 2 ** (attempt - 1)
                _logger.warning(
                    "429 rate-limited on %s (attempt %d/%d); sleeping %.1fs",
                    prompt_label, attempt, MAX_RETRIES, wait,
                )
                time.sleep(wait)
                raise RuntimeError(f"rate limited: {resp.text[:200]}")

            if resp.status_code >= 500:
                raise RuntimeError(
                    f"server error {resp.status_code}: {resp.text[:200]}"
                )
            if resp.status_code >= 400:
                # 4xx is non-retryable (auth, bad input, etc.)
                raise RuntimeError(
                    f"client error {resp.status_code}: {resp.text[:300]}"
                ) from None

            data = resp.json()
            out = _extract_image_bytes(data)
            _logger.info(
                "model=%s label=%s in_bytes=%d out_bytes=%d attempt=%d",
                model_id, prompt_label, in_bytes, len(out), attempt,
            )
            return out

        except requests.Timeout as e:
            last_err = e
            _logger.warning(
                "timeout on %s (attempt %d/%d): %s",
                prompt_label, attempt, MAX_RETRIES, e,
            )
        except RuntimeError as e:
            last_err = e
            msg = str(e)
            # Bail immediately on 4xx (non-rate-limit) — won't fix on retry.
            if msg.startswith("client error") and "429" not in msg:
                _logger.error("non-retryable %s: %s", prompt_label, e)
                raise
            _logger.warning(
                "retryable error on %s (attempt %d/%d): %s",
                prompt_label, attempt, MAX_RETRIES, e,
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            _logger.warning(
                "unexpected error on %s (attempt %d/%d): %s",
                prompt_label, attempt, MAX_RETRIES, e,
            )

        if attempt < MAX_RETRIES:
            time.sleep(2 ** (attempt - 1))  # 1s, 2s, 4s

    raise RuntimeError(
        f"OpenRouter call failed after {MAX_RETRIES} attempts "
        f"(model={model_id}, label={prompt_label}): {last_err}"
    )


# ---------------------------------------------------------------------------
# Google AI Studio direct backend (Files API for >18MB inputs, inline below)
# ---------------------------------------------------------------------------


def _google_api_key() -> str | None:
    """Return GOOGLE_API_KEY if set, else None. Triggers Google direct backend."""
    key = os.environ.get("GOOGLE_API_KEY")
    return key.strip() if key and key.strip() else None


def _use_google_direct() -> bool:
    return _google_api_key() is not None


def _strip_provider_prefix(model_id: str) -> str:
    """Convert OpenRouter model id 'google/foo' -> 'foo' for direct Google API."""
    return model_id.split("/", 1)[1] if "/" in model_id else model_id


def _upload_to_google_files(image_bytes: bytes, mime: str) -> str:
    """Upload bytes to Gemini Files API; return the file URI for fileData refs.

    Uses the single-request resumable upload protocol. Returns the file's
    'uri' field suitable for use in {fileData: {fileUri, mimeType}} parts.
    """
    key = _google_api_key()
    if not key:
        raise RuntimeError("GOOGLE_API_KEY not set; cannot use Files API")
    headers = {
        "X-Goog-Upload-Command": "start, upload, finalize",
        "X-Goog-Upload-Header-Content-Length": str(len(image_bytes)),
        "X-Goog-Upload-Header-Content-Type": mime,
        "Content-Type": mime,
    }
    params = {"key": key}
    resp = requests.post(
        GOOGLE_FILES_UPLOAD_URL,
        params=params,
        headers=headers,
        data=image_bytes,
        timeout=REQUEST_TIMEOUT_SEC,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Files API upload failed {resp.status_code}: {resp.text[:300]}"
        )
    data = resp.json()
    uri = (data.get("file") or {}).get("uri")
    if not uri:
        raise RuntimeError(f"Files API response missing file.uri: {data}")
    return uri


def _extract_google_image(resp_json: dict[str, Any]) -> bytes:
    """Pull generated image from Google generateContent response.

    Image lives in candidates[0].content.parts[*].inlineData.data (base64).
    """
    try:
        candidates = resp_json["candidates"]
        parts = candidates[0]["content"]["parts"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(
            f"malformed Google response (no candidates/content/parts): {e}; "
            f"top-level keys={list(resp_json.keys()) if isinstance(resp_json, dict) else type(resp_json).__name__}"
        ) from e
    for part in parts:
        inline = part.get("inlineData") or part.get("inline_data")
        if inline and inline.get("data"):
            return base64.b64decode(inline["data"])
    raise RuntimeError(
        f"no image in Google response parts; part types={[list(p.keys()) for p in parts]}"
    )


def _call_google_direct(
    prompt: str,
    image: bytes | None,
    model_id: str,
    *,
    prompt_label: str = "unknown",
    extra_images: list[bytes] | None = None,
) -> bytes:
    """Single Google AI Studio generateContent call returning image bytes.

    Uses Files API for inputs over GOOGLE_INLINE_MAX_BYTES (~18MB); embeds
    inline (base64) below that. Same retry semantics as OpenRouter.
    `extra_images` are appended after the primary image in the same order.
    """
    key = _google_api_key()
    if not key:
        raise RuntimeError("GOOGLE_API_KEY not set")

    short_model = _strip_provider_prefix(model_id)
    url = GOOGLE_GENERATE_URL_TMPL.format(model=short_model)
    headers = {"Content-Type": "application/json"}
    params = {"key": key}

    def _attach(img_bytes: bytes) -> tuple[dict[str, Any], bool]:
        mime = _sniff_mime(img_bytes)
        if len(img_bytes) > GOOGLE_INLINE_MAX_BYTES:
            uri = _upload_to_google_files(img_bytes, mime)
            return {"fileData": {"fileUri": uri, "mimeType": mime}}, True
        b64 = base64.b64encode(img_bytes).decode("ascii")
        return {"inlineData": {"mimeType": mime, "data": b64}}, False

    parts: list[dict[str, Any]] = [{"text": prompt}]
    in_bytes = 0
    used_files_api = False

    if image is not None:
        part, used = _attach(image)
        parts.append(part)
        in_bytes += len(image)
        used_files_api = used_files_api or used
    for extra in extra_images or []:
        part, used = _attach(extra)
        parts.append(part)
        in_bytes += len(extra)
        used_files_api = used_files_api or used

    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
    }
    body = json.dumps(payload)

    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                url, params=params, headers=headers,
                data=body, timeout=REQUEST_TIMEOUT_SEC,
            )
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else 2 ** (attempt - 1)
                _logger.warning(
                    "429 rate-limited on %s (attempt %d/%d); sleeping %.1fs",
                    prompt_label, attempt, MAX_RETRIES, wait,
                )
                time.sleep(wait)
                raise RuntimeError(f"rate limited: {resp.text[:200]}")
            if resp.status_code >= 500:
                raise RuntimeError(
                    f"server error {resp.status_code}: {resp.text[:200]}"
                )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"client error {resp.status_code}: {resp.text[:300]}"
                ) from None
            out = _extract_google_image(resp.json())
            _logger.info(
                "backend=google model=%s label=%s in_bytes=%d out_bytes=%d "
                "files_api=%s attempt=%d",
                short_model, prompt_label, in_bytes, len(out),
                used_files_api, attempt,
            )
            return out
        except requests.Timeout as e:
            last_err = e
            _logger.warning(
                "timeout on %s (attempt %d/%d): %s",
                prompt_label, attempt, MAX_RETRIES, e,
            )
        except RuntimeError as e:
            last_err = e
            msg = str(e)
            if msg.startswith("client error") and "429" not in msg:
                _logger.error("non-retryable %s: %s", prompt_label, e)
                raise
            _logger.warning(
                "retryable error on %s (attempt %d/%d): %s",
                prompt_label, attempt, MAX_RETRIES, e,
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            _logger.warning(
                "unexpected error on %s (attempt %d/%d): %s",
                prompt_label, attempt, MAX_RETRIES, e,
            )
        if attempt < MAX_RETRIES:
            time.sleep(2 ** (attempt - 1))

    raise RuntimeError(
        f"Google direct call failed after {MAX_RETRIES} attempts "
        f"(model={short_model}, label={prompt_label}): {last_err}"
    )


def _call_image_api(
    prompt: str,
    image: bytes | None,
    model_id: str,
    *,
    prompt_label: str = "unknown",
    extra_body: dict[str, Any] | None = None,
    extra_images: list[bytes] | None = None,
) -> bytes:
    """Backend dispatcher: Google direct if GOOGLE_API_KEY set, else OpenRouter."""
    if _use_google_direct():
        return _call_google_direct(
            prompt, image, model_id,
            prompt_label=prompt_label, extra_images=extra_images,
        )
    return _call_openrouter(
        prompt, image, model_id,
        prompt_label=prompt_label, extra_body=extra_body,
        extra_images=extra_images,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def clean(image_bytes: bytes, *,
          model_id: str | None = None,
          prompt_override: str | None = None) -> bytes:
    """Strip every text element from a poster, returning a clean base.

    If `prompt_override` is provided, it replaces the prompts/clean.txt content.
    """
    mid = model_id or _load_default_model()
    prompt = prompt_override if prompt_override else _load_prompt("clean", mid)
    return _call_image_api(prompt, image_bytes, mid, prompt_label="clean")


def outpaint(
    image_bytes: bytes,
    target: Target,
    *,
    model_id: str | None = None,
    prompt_override: str | None = None,
) -> bytes:
    """Outpaint a poster to a wider aspect ratio.

    Args:
        target: ``"landscape"`` for 16:9, ``"banner"`` for ~2.83:1 CN hero.
        prompt_override: replaces prompts/outpaint_{target}.txt content if set.
    """
    if target not in ("landscape", "banner"):
        raise ValueError(f"target must be 'landscape' or 'banner'; got {target!r}")
    mid = model_id or _load_default_model()
    label = f"outpaint_{target}"
    prompt = prompt_override if prompt_override else _load_prompt(label, mid)
    return _call_image_api(prompt, image_bytes, mid, prompt_label=label)


def _append_style_notes(prompt: str, style_notes: str | None) -> str:
    """Append free-form designer instructions to a formatted prompt."""
    notes = (style_notes or "").strip()
    if not notes:
        return prompt
    return (
        f"{prompt}\n\n"
        f"Designer notes — these override any conflicting instruction above:\n"
        f"{notes}"
    )


def title_swap(
    clean_base: bytes,
    title: str,
    lang: str,
    *,
    model_id: str | None = None,
    prompt_override: str | None = None,
    reference_bytes: bytes | None = None,
    style_notes: str | None = None,
) -> bytes:
    """Composite a localized title onto a clean poster base.

    Args:
        clean_base: the image to EDIT — typically a clean (text-less)
            poster, or the original source poster when you want the
            model to replace its existing title.
        title, lang: localized title text + language code.
        model_id, prompt_override: see other public functions.
        reference_bytes: optional second image carrying the ORIGINAL
            title's typography. When provided, the model is shown both
            images and instructed (via the prompt) to copy the reference
            title's font/color/material onto the new language text.
            Used when `clean_base` has no visible title to inherit from
            (e.g. landscape outpaint output).
        style_notes: optional free-form designer instructions appended to
            the prompt (e.g. "keep the gold-foil texture", "make the EN
            title slightly smaller").

    `prompt_override` (if set) is used as the template; it must still contain
    `{title}` and `{language}` placeholders so substitution can run.
    """
    if not title or not title.strip():
        raise ValueError("title must be a non-empty string")
    if lang not in _LANG_NAMES:
        raise ValueError(f"lang must be one of {list(_LANG_NAMES)}; got {lang!r}")
    mid = model_id or _load_default_model()
    tpl = prompt_override if prompt_override else _load_prompt("title_swap", mid)
    prompt = tpl.format(title=title.strip(), language=_LANG_NAMES[lang])
    prompt = _append_style_notes(prompt, style_notes)
    extra = [reference_bytes] if reference_bytes else None
    return _call_image_api(
        prompt, clean_base, mid,
        prompt_label=f"title_swap_{lang}",
        extra_images=extra,
    )


def title_extract(
    poster: bytes,
    title: str,
    lang: str,
    *,
    model_id: str | None = None,
    prompt_override: str | None = None,
    reference_bytes: bytes | None = None,
    style_notes: str | None = None,
) -> bytes:
    """Isolate the title text on a flat #FF00FF magenta background.

    The magenta background is a deliberate workaround: Gemini 2.5 Flash
    Image does not reliably emit an alpha channel on arbitrary edits, so
    ``dims.py`` will chroma-key the magenta to transparent downstream.

    `reference_bytes` optionally provides an additional image (typically
    the original source poster) so the model can copy the ORIGINAL title's
    color/font when extracting in a different language. The prompt is
    responsible for describing how to use it.

    `prompt_override` (if set) is used as the template; it must still contain
    `{title}` and `{language}` placeholders so substitution can run.
    """
    if not title or not title.strip():
        raise ValueError("title must be a non-empty string")
    if lang not in _LANG_NAMES:
        raise ValueError(f"lang must be one of {list(_LANG_NAMES)}; got {lang!r}")
    mid = model_id or _load_default_model()
    tpl = prompt_override if prompt_override else _load_prompt("title_extract", mid)
    prompt = tpl.format(title=title.strip(), language=_LANG_NAMES[lang])
    prompt = _append_style_notes(prompt, style_notes)
    extra = [reference_bytes] if reference_bytes else None
    return _call_image_api(
        prompt, poster, mid,
        prompt_label=f"title_extract_{lang}",
        extra_images=extra,
    )


# ---------------------------------------------------------------------------
# Manual smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser(description="gemini.py smoke test")
    ap.add_argument("op", choices=["clean", "landscape", "banner", "swap", "extract"])
    ap.add_argument("image", type=Path, help="input image path")
    ap.add_argument("--title", default="Sample Title")
    ap.add_argument("--lang", default="en", choices=["kr", "en", "zh"])
    ap.add_argument("--out", type=Path, default=Path("out.png"))
    args = ap.parse_args()

    img = args.image.read_bytes()
    if args.op == "clean":
        result = clean(img)
    elif args.op == "landscape":
        result = outpaint(img, "landscape")
    elif args.op == "banner":
        result = outpaint(img, "banner")
    elif args.op == "swap":
        result = title_swap(img, args.title, args.lang)
    else:
        result = title_extract(img, args.title, args.lang)
    args.out.write_bytes(result)
    print(f"wrote {len(result)} bytes -> {args.out}")
