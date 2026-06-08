"""Tests for gemini.py — OpenRouter image API wrapper (mocked HTTP)."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import gemini


PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
EXPECTED_PROMPTS = ("clean", "outpaint_landscape", "outpaint_banner",
                    "title_swap", "title_extract")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(status_code: int = 200, json_body: dict | None = None,
                   text: str = "", headers: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    resp.text = text or (json.dumps(json_body) if json_body else "")
    resp.headers = headers or {}
    return resp


# ---------------------------------------------------------------------------
# Prompt files exist + are non-empty
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", EXPECTED_PROMPTS)
def test_prompt_files_exist_and_non_empty(name):
    path = PROMPTS_DIR / f"{name}.txt"
    assert path.exists(), f"missing prompt template: {path}"
    text = path.read_text(encoding="utf-8").strip()
    assert text, f"prompt template is empty: {path}"


# ---------------------------------------------------------------------------
# clean() — request payload + response handling
# ---------------------------------------------------------------------------


def test_clean_builds_correct_openrouter_payload(
    rgb_portrait_bytes, fake_openrouter_image_response, small_png_bytes
):
    with patch("gemini.requests.post") as mock_post:
        mock_post.return_value = _make_response(200, fake_openrouter_image_response)

        out = gemini.clean(rgb_portrait_bytes, model_id="google/gemini-2.5-flash-image")

        assert out == small_png_bytes
        assert mock_post.call_count == 1
        call = mock_post.call_args

        # URL
        assert call.args[0] == gemini.OPENROUTER_URL

        # Headers — Authorization + JSON content type
        headers = call.kwargs["headers"]
        assert headers["Authorization"].startswith("Bearer ")
        assert headers["Content-Type"] == "application/json"

        # Body — model, modalities, messages structure
        body = json.loads(call.kwargs["data"])
        assert body["model"] == "google/gemini-2.5-flash-image"
        assert "image" in body["modalities"]
        msgs = body["messages"]
        assert len(msgs) == 1 and msgs[0]["role"] == "user"
        parts = msgs[0]["content"]
        # First part is the prompt text; second part is the base64 image URL.
        assert parts[0]["type"] == "text"
        assert "text" in parts[0] and parts[0]["text"]
        assert parts[1]["type"] == "image_url"
        url = parts[1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        # The base64 payload must round-trip to the original image bytes.
        _, b64 = url.split(",", 1)
        assert base64.b64decode(b64) == rgb_portrait_bytes


# ---------------------------------------------------------------------------
# outpaint() — prompt contents include target aspect signal
# ---------------------------------------------------------------------------


def test_outpaint_landscape_prompt_mentions_landscape_aspect(
    rgb_portrait_bytes, fake_openrouter_image_response
):
    with patch("gemini.requests.post") as mock_post:
        mock_post.return_value = _make_response(200, fake_openrouter_image_response)
        gemini.outpaint(rgb_portrait_bytes, "landscape")

    body = json.loads(mock_post.call_args.kwargs["data"])
    prompt_text = body["messages"][0]["content"][0]["text"].lower()
    # Landscape prompt should describe a 16:9 / landscape conversion.
    assert "landscape" in prompt_text
    assert "1600x900" in prompt_text or "16:9" in prompt_text or "1920x1080" in prompt_text


def test_outpaint_banner_prompt_mentions_banner_aspect(
    rgb_portrait_bytes, fake_openrouter_image_response
):
    with patch("gemini.requests.post") as mock_post:
        mock_post.return_value = _make_response(200, fake_openrouter_image_response)
        gemini.outpaint(rgb_portrait_bytes, "banner")

    body = json.loads(mock_post.call_args.kwargs["data"])
    prompt_text = body["messages"][0]["content"][0]["text"].lower()
    # Banner prompt should reference the wide banner dimensions.
    assert "banner" in prompt_text or "1520" in prompt_text or "ultra-wide" in prompt_text


def test_outpaint_rejects_unknown_target(rgb_portrait_bytes):
    with pytest.raises(ValueError):
        gemini.outpaint(rgb_portrait_bytes, "square")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# title_swap() — title + language placeholders
# ---------------------------------------------------------------------------


def test_title_swap_substitutes_title_and_language(
    rgb_portrait_bytes, fake_openrouter_image_response
):
    with patch("gemini.requests.post") as mock_post:
        mock_post.return_value = _make_response(200, fake_openrouter_image_response)
        gemini.title_swap(rgb_portrait_bytes, "뭉쳐야찬다3", "kr")

    body = json.loads(mock_post.call_args.kwargs["data"])
    prompt_text = body["messages"][0]["content"][0]["text"]
    assert "뭉쳐야찬다3" in prompt_text, "rendered prompt must contain the title"
    assert "Korean" in prompt_text, "rendered prompt must mention the language label"
    # Placeholders must have been substituted (no stray braces).
    assert "{title}" not in prompt_text
    assert "{language}" not in prompt_text


def test_title_swap_rejects_empty_title(rgb_portrait_bytes):
    with pytest.raises(ValueError):
        gemini.title_swap(rgb_portrait_bytes, "   ", "en")


def test_title_swap_rejects_unknown_lang(rgb_portrait_bytes):
    with pytest.raises(ValueError):
        gemini.title_swap(rgb_portrait_bytes, "Foo", "vi")


# ---------------------------------------------------------------------------
# Retry behavior on 5xx
# ---------------------------------------------------------------------------


def test_retry_on_500_then_success(
    rgb_portrait_bytes, fake_openrouter_image_response, small_png_bytes
):
    responses = [
        _make_response(500, text="boom"),
        _make_response(500, text="still boom"),
        _make_response(200, fake_openrouter_image_response),
    ]
    with patch("gemini.requests.post", side_effect=responses) as mock_post, \
         patch("gemini.time.sleep") as mock_sleep:
        out = gemini.clean(rgb_portrait_bytes)

    assert out == small_png_bytes
    assert mock_post.call_count == 3
    # At least one backoff sleep should have happened.
    assert mock_sleep.call_count >= 1


def test_no_retry_on_4xx_client_error(rgb_portrait_bytes):
    with patch("gemini.requests.post") as mock_post:
        mock_post.return_value = _make_response(400, text="bad request")
        with pytest.raises(RuntimeError, match="client error"):
            gemini.clean(rgb_portrait_bytes)
    # 4xx should not retry.
    assert mock_post.call_count == 1


# ---------------------------------------------------------------------------
# model_id override
# ---------------------------------------------------------------------------


def test_model_id_override_takes_precedence_over_config(
    rgb_portrait_bytes, fake_openrouter_image_response
):
    with patch("gemini.requests.post") as mock_post:
        mock_post.return_value = _make_response(200, fake_openrouter_image_response)
        gemini.clean(rgb_portrait_bytes, model_id="openai/gpt-image-1")

    body = json.loads(mock_post.call_args.kwargs["data"])
    assert body["model"] == "openai/gpt-image-1"


def test_default_model_used_when_no_override(
    rgb_portrait_bytes, fake_openrouter_image_response
):
    with patch("gemini.requests.post") as mock_post:
        mock_post.return_value = _make_response(200, fake_openrouter_image_response)
        gemini.clean(rgb_portrait_bytes)

    body = json.loads(mock_post.call_args.kwargs["data"])
    # Default comes from config.yaml `models.default`.
    import yaml
    from pathlib import Path
    with (Path(__file__).parent.parent / "config.yaml").open() as f:
        expected = yaml.safe_load(f)["models"]["default"]
    assert body["model"] == expected
