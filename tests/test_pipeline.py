"""Tests for pipeline.process — orchestration with mocked gemini calls."""
from __future__ import annotations

import json
import shutil
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

import pipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _png_bytes(w: int, h: int, color=(40, 60, 80), mode="RGB") -> bytes:
    if mode == "RGBA":
        img = Image.new("RGBA", (w, h), (*color, 255))
    else:
        img = Image.new("RGB", (w, h), color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _magenta_bytes(w: int = 200, h: int = 80) -> bytes:
    """A magenta-keyed 'title_extract' result the pipeline will chroma-key."""
    img = Image.new("RGB", (w, h), color=(255, 0, 255))
    # a small black 'title' shape in the middle
    for x in range(40, 160):
        for y in range(20, 60):
            img.putpixel((x, y), (10, 10, 10))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def isolated_output_dir(monkeypatch, tmp_path):
    """Redirect pipeline output to a tmp dir by changing CWD-relative behavior.

    pipeline.process resolves a non-absolute output_folder relative to the
    pipeline module's parent directory. We patch the module file path so that
    `ready/<slug>/` lands inside tmp_path.
    """
    fake_root = tmp_path / "design"
    fake_root.mkdir()
    # The pipeline uses Path(__file__).parent / out_dir when not absolute.
    # Easier: monkeypatch __file__ -> a file inside our tmp dir.
    fake_pipeline_file = fake_root / "pipeline.py"
    fake_pipeline_file.write_text("")
    monkeypatch.setattr(pipeline, "__file__", str(fake_pipeline_file))
    yield fake_root
    shutil.rmtree(fake_root, ignore_errors=True)


@pytest.fixture
def mock_gemini(rgb_portrait_bytes):
    """Patch all gemini.* calls to return predictable bytes.

    Returns the dict of patchers (so tests can inspect call counts).
    """
    with patch.object(pipeline.gemini, "clean") as m_clean, \
         patch.object(pipeline.gemini, "outpaint") as m_outpaint, \
         patch.object(pipeline.gemini, "title_swap") as m_swap, \
         patch.object(pipeline.gemini, "title_extract") as m_extract:

        m_clean.return_value = _png_bytes(1080, 1920)
        m_outpaint.side_effect = lambda img, target, **kw: (
            _png_bytes(1920, 1080) if target == "landscape" else _png_bytes(1824, 644)
        )
        m_swap.return_value = _png_bytes(1080, 1920)
        m_extract.return_value = _magenta_bytes()

        yield {
            "clean": m_clean,
            "outpaint": m_outpaint,
            "title_swap": m_swap,
            "title_extract": m_extract,
        }


# ---------------------------------------------------------------------------
# Happy path — 14 outputs, 12 Gemini calls
# ---------------------------------------------------------------------------


def test_process_makes_exactly_12_gemini_calls(
    rgb_portrait_bytes, mock_gemini, isolated_output_dir
):
    """PRD 부록 B specifies exactly 12 image API calls per title."""
    titles = {"kr": "뭉쳐야찬다3", "en": "Lets Play Soccer 3", "zh": "一起踢足球3"}

    result = pipeline.process(rgb_portrait_bytes, titles, slug="test_show")

    total_calls = (
        mock_gemini["clean"].call_count
        + mock_gemini["outpaint"].call_count
        + mock_gemini["title_swap"].call_count
        + mock_gemini["title_extract"].call_count
    )
    assert total_calls == 12, (
        f"expected 12 Gemini image calls, got {total_calls} "
        f"(clean={mock_gemini['clean'].call_count}, "
        f"outpaint={mock_gemini['outpaint'].call_count}, "
        f"swap={mock_gemini['title_swap'].call_count}, "
        f"extract={mock_gemini['title_extract'].call_count})"
    )

    # Breakdown: 1 clean + 2 outpaint + 6 swap (3 portrait + 3 landscape) + 3 extract
    assert mock_gemini["clean"].call_count == 1
    assert mock_gemini["outpaint"].call_count == 2
    assert mock_gemini["title_swap"].call_count == 6
    assert mock_gemini["title_extract"].call_count == 3


def test_process_produces_11_output_files(
    rgb_portrait_bytes, mock_gemini, isolated_output_dir
):
    titles = {"kr": "뭉쳐야찬다3", "en": "Soccer 3", "zh": "一起踢足球3"}

    result = pipeline.process(rgb_portrait_bytes, titles, slug="test_show")

    assert len(result["outputs"]) == 11, (
        f"expected 11 outputs, got {len(result['outputs'])}"
    )
    for seq, path in result["outputs"].items():
        assert path.exists(), f"output #{seq} missing on disk: {path}"
    # _meta.json sits alongside the outputs.
    meta_path = next(iter(result["outputs"].values())).parent / "_meta.json"
    assert meta_path.exists()


def test_process_output_dimensions_match_specs(
    rgb_portrait_bytes, mock_gemini, isolated_output_dir
):
    """Every output PNG must match its spec pixel-perfect."""
    titles = {"kr": "K", "en": "E", "zh": "Z"}
    result = pipeline.process(rgb_portrait_bytes, titles, slug="dims_check")

    expected: dict[int, tuple[int, int]] = {
        1: (900, 1600), 2: (900, 1600), 3: (900, 1600),
        4: (1600, 900), 5: (1600, 900), 6: (1600, 900),
        7: (1600, 900),
        8: (580, 200), 9: (580, 200), 10: (580, 200),
        11: (1520, 536),
    }
    for seq, expected_size in expected.items():
        path = result["outputs"][seq]
        with Image.open(path) as img:
            assert img.size == expected_size, (
                f"#{seq}: expected {expected_size}, got {img.size}"
            )


# ---------------------------------------------------------------------------
# Filename template
# ---------------------------------------------------------------------------


def test_process_filename_template_substitution(
    rgb_portrait_bytes, mock_gemini, isolated_output_dir
):
    titles = {"kr": "TestKR", "en": "TestEN", "zh": "TestZH"}
    result = pipeline.process(rgb_portrait_bytes, titles, slug="my_slug")

    # Per default template "{lang}-{type}-title.png".
    expected_names = {
        1: "kr-portrait-title.png",
        2: "en-portrait-title.png",
        3: "cn-portrait-title.png",
        4: "kr-landscape-title.png",
        5: "en-landscape-title.png",
        6: "cn-landscape-title.png",
        7: "clean-landscape-title.png",
        8: "kr-logo-title.png",
        9: "en-logo-title.png",
        10: "cn-logo-title.png",
        11: "cn-main-banner-title.png",
    }
    for seq, name in expected_names.items():
        assert result["outputs"][seq].name == name, (
            f"#{seq}: expected {name}, got {result['outputs'][seq].name}"
        )


def test_process_output_folder_uses_slug(
    rgb_portrait_bytes, mock_gemini, isolated_output_dir
):
    titles = {"kr": "K", "en": "E", "zh": "Z"}
    result = pipeline.process(rgb_portrait_bytes, titles, slug="my_unique_slug")

    folder = next(iter(result["outputs"].values())).parent
    assert folder.name == "my_unique_slug"


# ---------------------------------------------------------------------------
# _meta.json schema completeness
# ---------------------------------------------------------------------------


def test_process_meta_json_schema_completeness(
    rgb_portrait_bytes, mock_gemini, isolated_output_dir
):
    titles = {"kr": "K", "en": "E", "zh": "Z"}
    result = pipeline.process(rgb_portrait_bytes, titles, slug="meta_check")
    meta = result["meta"]

    # Required top-level keys per ARCHITECTURE §5.
    for key in ("slug", "input", "outputs", "stats", "failures"):
        assert key in meta, f"_meta.json missing top-level key: {key}"

    # input section
    assert "titles" in meta["input"]
    assert set(meta["input"]["titles"]) == {"kr", "en", "zh"}
    assert "file" in meta["input"]

    # outputs section — 11 entries, each with path/dim/status
    assert len(meta["outputs"]) == 11
    for seq_str, entry in meta["outputs"].items():
        assert "path" in entry
        assert "dim" in entry and len(entry["dim"]) == 2
        assert "status" in entry
        assert entry["status"] in ("ok", "failed")

    # stats section
    stats = meta["stats"]
    for key in ("started_at", "finished_at", "duration_sec",
                "api_calls", "estimated_cost_usd"):
        assert key in stats, f"stats missing key: {key}"
    assert stats["api_calls"]["gemini_image"] == 12

    # source field reserved for v2 — should be 'local'.
    assert meta.get("source") == "local"

    # File on disk matches.
    meta_path = next(iter(result["outputs"].values())).parent / "_meta.json"
    on_disk = json.loads(meta_path.read_text(encoding="utf-8"))
    assert on_disk["slug"] == meta["slug"]
    assert len(on_disk["outputs"]) == 11


def test_process_meta_records_estimated_cost(
    rgb_portrait_bytes, mock_gemini, isolated_output_dir
):
    titles = {"kr": "K", "en": "E", "zh": "Z"}
    result = pipeline.process(rgb_portrait_bytes, titles, slug="cost_check")
    # Cost = default model's cost_per_image × 12 image calls.
    import yaml
    from pathlib import Path
    with (Path(__file__).parent.parent / "config.yaml").open() as f:
        cfg = yaml.safe_load(f)
    default_id = cfg["models"]["default"]
    per_img = next(m["cost_per_image"] for m in cfg["models"]["available"]
                   if m["id"] == default_id)
    expected = per_img * 12
    assert result["meta"]["stats"]["estimated_cost_usd"] == pytest.approx(expected, abs=1e-4)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_process_rejects_missing_titles(rgb_portrait_bytes, mock_gemini):
    with pytest.raises(ValueError, match="missing"):
        pipeline.process(rgb_portrait_bytes, {"kr": "K", "en": "", "zh": "Z"})


def test_process_rejects_non_dict_titles(rgb_portrait_bytes, mock_gemini):
    with pytest.raises(TypeError):
        pipeline.process(rgb_portrait_bytes, ["K", "E", "Z"])  # type: ignore[arg-type]


def test_process_progress_callback_fires(
    rgb_portrait_bytes, mock_gemini, isolated_output_dir
):
    titles = {"kr": "K", "en": "E", "zh": "Z"}
    calls: list[tuple[int, int, str]] = []
    pipeline.process(
        rgb_portrait_bytes, titles, slug="cb_check",
        progress_cb=lambda c, t, m: calls.append((c, t, m)),
    )
    # Should fire 11 times (one per produced output).
    assert len(calls) == 11
    # 'total' is always 11 per docstring.
    assert all(t == 11 for _c, t, _m in calls)
    # Monotonically increasing 'current'.
    currents = [c for c, _t, _m in calls]
    assert currents == sorted(currents)


# ---------------------------------------------------------------------------
# model_id override flows through to gemini calls
# ---------------------------------------------------------------------------


def test_process_passes_model_id_to_gemini(
    rgb_portrait_bytes, mock_gemini, isolated_output_dir
):
    titles = {"kr": "K", "en": "E", "zh": "Z"}
    pipeline.process(
        rgb_portrait_bytes, titles, slug="mid_check",
        model_id="openai/gpt-image-1",
    )
    # Inspect any gemini.clean call.
    call = mock_gemini["clean"].call_args
    assert call.kwargs.get("model_id") == "openai/gpt-image-1"
