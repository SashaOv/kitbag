"""Tests for part selection in pdfscore."""

import base64
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from pdfscore import (
    MAIN_SCORE,
    ScorePart,
    UserError,
    ensure_compatible_options,
    expand_parts,
    export_parts,
    extract_part_scores,
    is_main_score,
    match_extracted,
    resolve_output_dir,
    resolve_part,
    score_parts,
    validate_score,
)


def write_score(path: Path, members: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for member, text in members.items():
            archive.writestr(member, text)
    return path


def main_mscx(track: str, long: str | None = None) -> str:
    instrument = f"<Instrument><longName>{long}</longName></Instrument>" if long else ""
    return (
        "<museScore><Score>"
        f"<Part><trackName>{track}</trackName>{instrument}</Part>"
        "</Score></museScore>"
    )


def excerpt_mscx(title: str, track: str) -> str:
    return (
        "<museScore><Score>"
        f'<metaTag name="partName">{title}</metaTag>'
        f"<Part><trackName>{track}</trackName></Part>"
        "</Score></museScore>"
    )


@pytest.mark.parametrize("raw", ["", "   ", "Main score", "main SCORE", "MAIN SCORE"])
def test_expand_parts_defaults_to_main_score(raw: str) -> None:
    assert is_main_score(expand_parts([raw])[0].name)


@pytest.mark.parametrize(
    ("raw", "name", "key"),
    [
        ("Trumpet:Bb", "Trumpet", "Bb"),
        ("Violin:C", "Violin", None),
        ("Violin:", "Violin", None),
        ("Violin:  ", "Violin", None),
    ],
)
def test_expand_parts_splits_name_and_key(raw: str, name: str, key: str | None) -> None:
    spec = expand_parts([raw])[0]
    assert (spec.name, spec.key) == (name, key)
    assert (spec.jump == 0) == (key is None)


def test_expand_parts_rejects_bad_key() -> None:
    with pytest.raises(UserError):
        expand_parts(["Violin:H#"])


def test_ensure_compatible_options_rejects_combined_modes() -> None:
    with pytest.raises(UserError):
        ensure_compatible_options(["C"], ["Violin"])
    ensure_compatible_options(["C"], [])
    ensure_compatible_options([], ["Violin"])


@pytest.mark.parametrize(("name", "expected"), [("Main score", True), ("MAIN SCORE", True), ("Violin", False)])
def test_is_main_score_matches_case_insensitively(name: str, expected: bool) -> None:
    assert is_main_score(name) is expected


def test_validate_score_rejects_missing_and_non_mscz(tmp_path: Path) -> None:
    with pytest.raises(UserError):
        validate_score(tmp_path / "missing.mscz")
    pdf = tmp_path / "score.pdf"
    pdf.write_bytes(b"%PDF")
    with pytest.raises(UserError):
        validate_score(pdf)
    score = tmp_path / "score.mscz"
    score.write_bytes(b"data")
    validate_score(score)


def test_resolve_output_dir_defaults_to_score_parent(tmp_path: Path) -> None:
    score = tmp_path / "sub" / "score.mscz"
    assert resolve_output_dir(score, None) == score.parent
    out = tmp_path / "out"
    assert resolve_output_dir(score, out) == out.resolve()


def test_score_parts_lists_instruments_and_excerpts(tmp_path: Path) -> None:
    score = write_score(
        tmp_path / "score.mscz",
        {
            "score.mscx": main_mscx("Electric Guitar", "Lead"),
            "Excerpts/Violin.mscx": excerpt_mscx("Violin", "ViolinExcerpt"),
        },
    )
    catalog = score_parts(score)
    assert [part.name for part in catalog] == ["Lead", "Violin"]
    assert "Electric Guitar" in catalog[0].aliases


@pytest.mark.parametrize("kind", ["garbage", "no-mscx", "malformed-xml"])
def test_score_parts_rejects_unreadable_scores(tmp_path: Path, kind: str) -> None:
    score = tmp_path / "score.mscz"
    if kind == "garbage":
        score.write_bytes(b"not a zip")
    elif kind == "no-mscx":
        write_score(score, {"META-INF/container.xml": "<container/>"})
    else:
        write_score(score, {"score.mscx": "<museScore><Score>"})
    with pytest.raises(UserError, match="Could not read parts"):
        score_parts(score)


def test_resolve_part_matches_display_alias_and_case() -> None:
    catalog = [ScorePart("Lead", ("Electric Guitar",))]
    assert resolve_part("Lead", catalog) is not None
    assert resolve_part("Electric Guitar", catalog) is not None
    assert resolve_part("lead", catalog) is not None
    assert resolve_part("Oboe", catalog) is None


def test_match_extracted_prefers_exact_then_casefold() -> None:
    catalog = [ScorePart("Lead", ("Electric Guitar",))]
    target = Path("/tmp/Lead.mscz")
    assert match_extracted("Lead", catalog, {"Lead": target}) == target
    assert match_extracted("Electric Guitar", catalog, {"Lead": target}) == target
    assert match_extracted("lead", catalog, {"Lead": target}) == target


def test_match_extracted_rejects_ambiguous_casefold() -> None:
    catalog = [ScorePart("Lead", ("Leed",))]
    extracted = {"lead": Path("/tmp/lead.mscz"), "leed": Path("/tmp/leed.mscz")}
    assert match_extracted("Lead", catalog, extracted) is None


def test_export_parts_rejects_unknown_part(tmp_path: Path) -> None:
    score = write_score(tmp_path / "score.mscz", {"score.mscx": main_mscx("Guitar", "Lead")})
    out = tmp_path / "out"
    with pytest.raises(UserError, match="Unknown part.*Score defines: Lead"):
        export_parts(Path("/nonexistent-mscore"), score, expand_parts(["Oboe"]), out)


def test_extract_part_scores_decodes_parts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blob = base64.b64encode(b"fake-mscz").decode()
    payload = {"parts": ["Lead"], "partsBin": [blob]}
    monkeypatch.setattr(
        "pdfscore.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(payload), stderr="", returncode=1),
    )
    files = extract_part_scores(Path("/fake-mscore"), tmp_path / "s.mscz", tmp_path)
    assert files["Lead"].read_bytes() == b"fake-mscz"


@pytest.mark.parametrize(
    "stdout", ['not json', '{"parts": ["A"], "partsBin": []}', '{"nope": 1}', ""],
)
def test_extract_part_scores_rejects_malformed_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: str
) -> None:
    monkeypatch.setattr(
        "pdfscore.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout=stdout, stderr="boom", returncode=1),
    )
    with pytest.raises(UserError, match="Could not read parts"):
        extract_part_scores(Path("/fake-mscore"), tmp_path / "s.mscz", tmp_path)


def test_extract_part_scores_rejects_bad_blob(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"parts": ["Lead"], "partsBin": ["!!!not-base64!!!"]}
    monkeypatch.setattr(
        "pdfscore.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(payload), stderr="", returncode=0),
    )
    with pytest.raises(UserError, match="Could not decode part"):
        extract_part_scores(Path("/fake-mscore"), tmp_path / "s.mscz", tmp_path)
