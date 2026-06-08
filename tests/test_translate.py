"""Tests for translate.py — language detection + translation (mocked HTTP)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import translate


def _make_response(status_code: int = 200, json_body: dict | None = None,
                   text: str = "", headers: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    resp.text = text or (json.dumps(json_body) if json_body else "")
    resp.headers = headers or {}
    return resp


# ---------------------------------------------------------------------------
# detect()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("kr", "kr"),
        (" KR ", "kr"),
        ("ko", "kr"),
        ("Korean", "kr"),
        ("en", "en"),
        ("english", "en"),
        ("zh", "zh"),
        ("cn", "zh"),
        ("chinese", "zh"),
    ],
)
def test_detect_parses_llm_response(raw, expected, fake_openrouter_text_response):
    with patch("translate.requests.post") as mock_post:
        mock_post.return_value = _make_response(
            200, fake_openrouter_text_response(raw)
        )
        assert translate.detect("Some title") == expected


def test_detect_falls_back_to_script_heuristic_when_model_misbehaves(
    fake_openrouter_text_response,
):
    with patch("translate.requests.post") as mock_post:
        mock_post.return_value = _make_response(
            200, fake_openrouter_text_response("nonsense-token")
        )
        assert translate.detect("뭉쳐야찬다3") == "kr"
        assert translate.detect("一起踢足球3") == "zh"
        assert translate.detect("Let's Play Soccer 3") == "en"


def test_detect_rejects_empty_title():
    with pytest.raises(ValueError):
        translate.detect("")
    with pytest.raises(ValueError):
        translate.detect("   ")


# ---------------------------------------------------------------------------
# translate()
# ---------------------------------------------------------------------------


def test_translate_calls_llm_once_per_target(fake_openrouter_text_response):
    """translate.translate iterates over targets and calls the LLM per target.

    The current implementation (_translate_one inside a loop) makes one HTTP
    request per non-src target. With src='kr' and targets=['en','zh'] we
    expect exactly 2 calls.
    """
    responses = [
        _make_response(200, fake_openrouter_text_response("Let's Play Soccer 3")),
        _make_response(200, fake_openrouter_text_response("一起踢足球3")),
    ]
    with patch("translate.requests.post", side_effect=responses) as mock_post:
        out = translate.translate("뭉쳐야찬다3", "kr", ["en", "zh"])

    assert mock_post.call_count == 2
    assert out == {
        "kr": "뭉쳐야찬다3",
        "en": "Let's Play Soccer 3",
        "zh": "一起踢足球3",
    }


def test_translate_skips_target_equal_to_src(fake_openrouter_text_response):
    """If targets includes src, that target should be a no-op (no API call)."""
    responses = [
        _make_response(200, fake_openrouter_text_response("Let's Play Soccer 3")),
    ]
    with patch("translate.requests.post", side_effect=responses) as mock_post:
        out = translate.translate("뭉쳐야찬다3", "kr", ["kr", "en"])

    assert mock_post.call_count == 1
    assert out["kr"] == "뭉쳐야찬다3"
    assert out["en"] == "Let's Play Soccer 3"


def test_translate_strips_quotes_from_llm_output(fake_openrouter_text_response):
    """Model output wrapped in quotes should be cleaned by _clean_title."""
    responses = [
        _make_response(200, fake_openrouter_text_response('"Let\'s Play Soccer 3"')),
    ]
    with patch("translate.requests.post", side_effect=responses):
        out = translate.translate("뭉쳐야찬다3", "kr", ["en"])
    assert out["en"] == "Let's Play Soccer 3"


def test_translate_rejects_empty_title():
    with pytest.raises(ValueError):
        translate.translate("", "kr", ["en"])


def test_translate_rejects_invalid_src():
    with pytest.raises(ValueError):
        translate.translate("foo", "vi", ["en"])


def test_translate_rejects_invalid_target():
    with pytest.raises(ValueError):
        translate.translate("foo", "en", ["vi"])


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_translate_retries_then_raises_on_persistent_500(
    fake_openrouter_text_response,
):
    """5xx errors retry MAX_RETRIES times then raise RuntimeError."""
    responses = [
        _make_response(500, text="boom"),
        _make_response(500, text="boom"),
        _make_response(500, text="boom"),
    ]
    with patch("translate.requests.post", side_effect=responses) as mock_post, \
         patch("translate.time.sleep"):
        with pytest.raises(RuntimeError, match="failed after"):
            translate.detect("Some title")

    assert mock_post.call_count == translate.MAX_RETRIES


def test_translate_retries_then_succeeds(fake_openrouter_text_response):
    responses = [
        _make_response(500, text="boom"),
        _make_response(200, fake_openrouter_text_response("en")),
    ]
    with patch("translate.requests.post", side_effect=responses) as mock_post, \
         patch("translate.time.sleep"):
        assert translate.detect("Hello World") == "en"
    assert mock_post.call_count == 2


def test_translate_handles_empty_content(fake_openrouter_text_response):
    """An empty assistant response is treated as an error and retried."""
    responses = [
        _make_response(200, fake_openrouter_text_response("")),
        _make_response(200, fake_openrouter_text_response("")),
        _make_response(200, fake_openrouter_text_response("")),
    ]
    with patch("translate.requests.post", side_effect=responses), \
         patch("translate.time.sleep"):
        with pytest.raises(RuntimeError):
            translate.detect("Hello")


def test_translate_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        translate.detect("Hello")
