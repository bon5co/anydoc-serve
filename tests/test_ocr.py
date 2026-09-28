"""OCR language selection, output shaping and configuration (no Tesseract needed)."""

from __future__ import annotations

import pytest

from anydoc_serve.ocr import TesseractEngine, normalize_langs, select_lang, text_to_markdown
from anydoc_serve.settings import Settings

ALL = ["eng", "jpn", "tha"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (["eng", "jpn", "tha"], ["eng", "jpn", "tha"]),
        (["en", "ja", "th"], ["eng", "jpn", "tha"]),
        (["Thai", "ENG", "tha"], ["tha", "eng"]),
        (["jpn"], ["jpn"]),
    ],
)
def test_normalize_langs(raw, expected):
    assert normalize_langs(raw) == expected


@pytest.mark.parametrize("bad", [["deu"], [], ["eng", "chi_sim"]])
def test_normalize_langs_rejects(bad):
    with pytest.raises(ValueError):
        normalize_langs(bad)


@pytest.mark.parametrize(
    ("script", "conf", "langs", "expected"),
    [
        ("Japanese", 2.3, ALL, "jpn"),
        ("Han", 1.0, ALL, "jpn"),
        ("Katakana", 1.0, ALL, "jpn"),
        ("Thai", 70.6, ALL, "tha"),
        ("Latin", 10.6, ALL, "eng"),
        # nothing detected, or too unsure: read with every configured language
        (None, 0.0, ALL, "eng+jpn+tha"),
        ("Latin", 0.1, ALL, "eng+jpn+tha"),
        # a script whose language is not configured
        ("Thai", 70.0, ["eng", "jpn"], "eng+jpn"),
        ("Cyrillic", 9.0, ALL, "eng+jpn+tha"),
        # one configured language is always that language
        ("Thai", 70.0, ["jpn"], "jpn"),
    ],
)
def test_select_lang(script, conf, langs, expected):
    assert select_lang(script, conf, langs) == expected


def test_markdown_drops_spaces_between_japanese_characters_only():
    text = "吾 輩 は 猫 で あ る 。 名 前 は INV-2026 0042 ま だ 無 い\n"
    assert text_to_markdown(text) == "吾輩は猫である。名前は INV-2026 0042 まだ無い\n"


def test_markdown_keeps_thai_and_english_spacing():
    text = "ประกาศบริษัท เรื่อง การปรับปรุง\nFour score and seven\n"
    assert text_to_markdown(text) == "ประกาศบริษัท เรื่อง การปรับปรุง\nFour score and seven\n"


def test_markdown_paragraphs_and_escaping():
    text = "# not a heading\n- not a list\n\n\n1. not numbered\nplain  \n\n\x0c"
    assert text_to_markdown(text) == "\\# not a heading\n\\- not a list\n\n1\\. not numbered\nplain\n"


def test_markdown_of_nothing_is_empty():
    assert text_to_markdown("  \n\x0c") == ""


def test_engine_reports_missing_models(tmp_path):
    (tmp_path / "eng.traineddata").write_bytes(b"")
    with pytest.raises(FileNotFoundError, match=r"osd\.traineddata, tha\.traineddata"):
        TesseractEngine(["eng", "tha"], tessdata=str(tmp_path))


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("PORT", "4321")
    monkeypatch.setenv("OCR_LANGS", "eng+tha")
    monkeypatch.setenv("API_KEY", "k")
    monkeypatch.setenv("MAX_UPLOAD_MB", "5")
    s = Settings(_env_file=None)
    assert s.port == 4321
    assert s.ocr_langs == ["eng", "tha"]
    assert s.api_key == "k"
    assert s.max_upload_bytes == 5 * 1024 * 1024


def test_settings_comma_langs(monkeypatch):
    monkeypatch.setenv("OCR_LANGS", " jpn , eng ")
    assert Settings(_env_file=None).ocr_langs == ["jpn", "eng"]
