"""Export MuseScore .mscz files to PDF, with optional transposed versions and part selection."""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Annotated, Any
from xml.etree import ElementTree

import cyclopts
import runcorder
from cyclopts import Parameter


class UserError(Exception):
    """Raised for invalid CLI input or export failures."""


@dataclass(frozen=True)
class ExportJob:
    """Record a requested export and the MuseScore batch payload that creates it."""

    score: Path
    output: Path
    action: str
    edition_label: str | None
    payload: dict[str, Any]


@dataclass(frozen=True)
class PartSpec:
    """Request one part export, optionally transposed to a key."""

    name: str
    key: str | None
    jump: int


@dataclass(frozen=True)
class ScorePart:
    """Describe one part defined by the score: display name plus filename aliases."""

    name: str
    aliases: tuple[str, ...] = ()


MAIN_SCORE = "Main score"


app = cyclopts.App(
    help="Export MuseScore .mscz files to PDF, with optional transposed versions.",
)

# MuseScore CLI interval IDs for common semitone shifts (by_interval mode).
SEMITONE_INTERVALS: dict[int, int] = {
    1: 3,  # minor 2nd
    2: 4,  # major 2nd
    3: 7,  # minor 3rd
    4: 8,  # major 3rd
    5: 11,  # perfect 4th
    6: 12,  # tritone
    7: 13,  # perfect 5th
    8: 15,  # minor 6th
    9: 16,  # major 6th
    10: 18,  # minor 7th
    11: 19,  # major 7th
    12: 22,  # octave
}

KEY_NUMBERS: dict[str, int] = {
    "C": 0,
    "Db": 1,
    "C#": 1,
    "D": 2,
    "Eb": 3,
    "D#": 3,
    "E": 4,
    "F": 5,
    "Gb": 6,
    "F#": 6,
    "G": 7,
    "Ab": 8,
    "G#": 8,
    "A": 9,
    "Bb": 10,
    "A#": 10,
    "B": 11,
}

KEY_ALIASES: dict[str, tuple[int, str]] = {
    "C": (0, "C"),
    "DB": (1, "Db"),
    "C#": (1, "C#"),
    "CSHARP": (1, "C#"),
    "D": (2, "D"),
    "EB": (3, "Eb"),
    "D#": (3, "D#"),
    "DSHARP": (3, "D#"),
    "E": (4, "E"),
    "F": (5, "F"),
    "GB": (6, "Gb"),
    "F#": (6, "F#"),
    "FSHARP": (6, "F#"),
    "G": (7, "G"),
    "AB": (8, "Ab"),
    "G#": (8, "G#"),
    "GSHARP": (8, "G#"),
    "A": (9, "A"),
    "BB": (10, "Bb"),
    "A#": (10, "A#"),
    "ASHARP": (10, "A#"),
    "B": (11, "B"),
}


def find_mscore() -> Path:
    candidates = [
        Path("/Applications/MuseScore 4.app/Contents/MacOS/mscore"),
        Path("/Applications/MuseScore 3.app/Contents/MacOS/mscore"),
    ]
    which = shutil.which("mscore")
    if which:
        candidates.append(Path(which))

    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate

    raise UserError(
        "Could not find MuseScore. Install MuseScore 4 or add `mscore` to your PATH."
    )


def validate_score(score: Path) -> None:
    """Validate that a score path points at a readable .mscz file."""
    if not score.is_file():
        raise UserError(f"Score not found: {score}")
    if score.suffix.lower() != ".mscz":
        raise UserError(f"Expected a .mscz file: {score}")


def is_main_score(name: str) -> bool:
    """Check whether a requested part name selects the virtual full score."""
    return name.lower() == MAIN_SCORE.lower()


def ensure_compatible_options(keys: list[str], parts: list[str]) -> None:
    """Reject combining --key with --part before any export work starts."""
    if keys and parts:
        raise UserError("--part and --key cannot be used together.")


def resolve_output_dir(resolved_score: Path, output: Path | None) -> Path:
    """Resolve the PDF output directory for one already-resolved score path."""
    return output.resolve() if output else resolved_score.parent


def score_parts(score: Path) -> list[ScorePart]:
    """List parts defined by the score: staff instruments plus saved excerpts.

    Display names follow the Export dialog: the instrument long name
    (e.g. Lead, not the Electric Guitar track name), then excerpt titles.
    """
    try:
        with zipfile.ZipFile(score) as archive:
            trees = {
                member: ElementTree.fromstring(archive.read(member))
                for member in archive.namelist()
                if member.endswith(".mscx")
            }
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, OSError) as exc:
        raise UserError(f"Could not read parts of {score}: {exc}") from exc
    if not trees:
        raise UserError(f"Could not read parts of {score}: no score data found.")

    catalog: list[ScorePart] = []

    def add(name: str | None, *aliases: str | None) -> None:
        if not name or any(part.name == name for part in catalog):
            return
        clean = tuple(dict.fromkeys(a for a in aliases if a and a != name))
        catalog.append(ScorePart(name, clean))

    main = next((tree for member, tree in trees.items() if "/" not in member), None)
    if main is not None:
        score_el = main.find("Score")
        if score_el is not None:
            for part in score_el.findall("Part"):
                longs = [i.findtext("longName") for i in part.findall("Instrument")]
                primary = next((n for n in longs if n and n.strip()), None)
                primary = primary.strip() if primary else part.findtext("trackName")
                add(primary and primary.strip(), part.findtext("trackName"))

    for member, tree in trees.items():
        if "/" not in member:
            continue
        score_el = tree.find("Score")
        if score_el is None:
            continue
        tags = {
            tag.get("name"): (tag.text or "").strip()
            for tag in score_el.findall("metaTag")
        }
        title = tags.get("partName") or None
        if title is None:
            name_el = score_el.findtext("name")
            title = name_el.strip() if name_el and name_el.strip() else None
        tracks = [p.findtext("trackName") for p in score_el.findall("Part")]
        add(title, *tracks)

    return catalog


def resolve_part(name: str, catalog: list[ScorePart]) -> ScorePart | None:
    """Match a requested part against display names, then aliases, then case."""
    for part in catalog:
        if name == part.name or name in part.aliases:
            return part
    lowered = name.lower()
    for part in catalog:
        if lowered == part.name.lower() or lowered in [a.lower() for a in part.aliases]:
            return part
    return None


def _canonicalize_key_token(key: str) -> str:
    token = key.strip().replace("♯", "#").replace("♭", "b")
    token = re.sub(r"\s+", "", token)
    token = re.sub(r"(?i)(?<=[A-Ga-g])-?sharp", "#", token)
    token = re.sub(r"(?i)(?<=[A-Ga-g])-?flat", "b", token)
    return token


def normalize_key_name(key: str) -> str:
    token = _canonicalize_key_token(key)
    alias = KEY_ALIASES.get(token.upper())
    if alias is not None:
        return alias[1]

    raise UserError(
        f"Invalid key {key!r}. Use note names like Bb, Eb, F#, Gb, or F-sharp."
    )


def jump_for_key(key: str) -> int:
    number = KEY_NUMBERS[normalize_key_name(key)]
    return (12 - number) if number >= 7 else -number


def expand_keys(keys: list[str]) -> tuple[list[tuple[str, int]], bool]:
    expanded: list[tuple[str, int]] = []
    concert_requested = False
    for raw in keys:
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            normalized = normalize_key_name(part)
            jump = jump_for_key(normalized)
            if jump == 0:
                concert_requested = True
                continue
            expanded.append((normalized, jump))
    return expanded, concert_requested


def expand_parts(parts: list[str]) -> list[PartSpec]:
    """Expand CLI part specs into validated part/key requests."""
    specs: list[PartSpec] = []
    for raw in parts:
        name, sep, key = raw.partition(":")
        name = name.strip() or MAIN_SCORE
        if not sep or not key.strip():
            specs.append(PartSpec(name=name, key=None, jump=0))
            continue
        normalized = normalize_key_name(key)
        jump = jump_for_key(normalized)
        if jump == 0:
            specs.append(PartSpec(name=name, key=None, jump=0))
        else:
            specs.append(PartSpec(name=name, key=normalized, jump=jump))
    return specs


def transpose_options(semitones: int) -> dict[str, Any]:
    magnitude = abs(semitones)
    if magnitude not in SEMITONE_INTERVALS:
        raise UserError(
            f"Transpose jump must be between -12 and +12 semitones (got {semitones:+d})."
        )

    return {
        "mode": "by_interval",
        "direction": "up" if semitones > 0 else "down",
        "transposeInterval": SEMITONE_INTERVALS[magnitude],
        "transposeKeySignatures": True,
        "transposeChordNames": True,
        "useDoubleSharpsFlats": False,
    }


def _export_succeeded(output: Path) -> bool:
    return output.is_file() and output.stat().st_size > 0


def stamp_edition_label(pdf: Path, edition_label: str) -> None:
    try:
        from pypdf import PdfReader, PdfWriter
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise UserError(
            "PDF edition stamping requires `pypdf` and `reportlab`. "
            "Reinstall pdfscore to restore them."
        ) from exc

    reader = PdfReader(str(pdf))
    writer = PdfWriter()
    label = f"{edition_label} edition"

    for page in reader.pages:
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        overlay_buffer = BytesIO()
        overlay_canvas = canvas.Canvas(overlay_buffer, pagesize=(width, height))
        overlay_canvas.setFont("Helvetica-Oblique", 10)
        overlay_canvas.setFillGray(0.2)
        overlay_canvas.drawString(28, height - 24, label)
        overlay_canvas.save()
        overlay_buffer.seek(0)

        overlay_page = PdfReader(overlay_buffer).pages[0]
        page.merge_page(overlay_page)
        writer.add_page(page)

    temp_pdf = pdf.with_name(f"{pdf.stem}.stamped{pdf.suffix}")
    with temp_pdf.open("wb") as output_file:
        writer.write(output_file)
    temp_pdf.replace(pdf)


def stamp_edition_labels(jobs: list[ExportJob]) -> None:
    for job in jobs:
        if job.edition_label is not None:
            stamp_edition_label(job.output, job.edition_label)


def _run_batch(mscore: Path, payloads: list[dict[str, Any]]) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".json", delete=False
    ) as batch_file:
        json.dump(payloads, batch_file, indent=2)
        batch_file.write("\n")
        batch_path = Path(batch_file.name)

    try:
        return subprocess.run(
            [str(mscore), "-f", "-j", str(batch_path)],
            capture_output=True,
            text=True,
        )
    finally:
        batch_path.unlink(missing_ok=True)


def run_mscore_batch(mscore: Path, jobs: list[ExportJob]) -> None:
    if not jobs:
        return

    result = _run_batch(mscore, [job.payload for job in jobs])

    failed_jobs = [job for job in jobs if not _export_succeeded(job.output)]
    if not failed_jobs:
        if result.returncode != 0:
            print(
                f"Warning: MuseScore exited with code {result.returncode}, "
                "but all requested PDFs were created.",
                file=sys.stderr,
            )
        return

    details = (result.stderr or result.stdout or "").strip()
    failed = "\n".join(
        f"- Failed to {job.action} {job.score} -> {job.output}"
        for job in failed_jobs
    )
    message = failed if not details else f"{failed}\n{details}"
    raise UserError(message)


def build_export_jobs(
    score: Path,
    keys: list[tuple[str, int]],
    output_dir: Path,
    *,
    include_original: bool,
) -> list[ExportJob]:
    validate_score(score)

    output_dir.mkdir(parents=True, exist_ok=True)
    jobs: list[ExportJob] = []

    if include_original:
        original_pdf = output_dir / f"{score.stem}.pdf"
        print(f"Exporting {original_pdf.name}")
        jobs.append(
            ExportJob(
                score=score,
                output=original_pdf,
                action="export",
                edition_label=None,
                payload={"in": str(score), "out": str(original_pdf)},
            )
        )

    for key_name, jump in keys:
        key_pdf = output_dir / f"{score.stem}.{key_name}.pdf"
        print(f"Exporting {key_pdf.name}")
        jobs.append(
            ExportJob(
                score=score,
                output=key_pdf,
                action=f"transpose to {key_name} and export",
                edition_label=key_name,
                payload={
                    "in": str(score),
                    "out": str(key_pdf),
                    "transpose": transpose_options(jump),
                },
            )
        )

    return jobs


def export_parts(
    mscore: Path,
    score: Path,
    specs: list[PartSpec],
    output_dir: Path,
) -> list[ExportJob]:
    validate_score(score)

    output_dir.mkdir(parents=True, exist_ok=True)
    catalog = score_parts(score)
    if catalog:
        unknown = [
            spec.name
            for spec in specs
            if not is_main_score(spec.name)
            and resolve_part(spec.name, catalog) is None
        ]
        if unknown:
            defined = ", ".join(part.name for part in catalog)
            raise UserError(
                f"Unknown part(s): {', '.join(unknown)}. Score defines: {defined}."
            )

    mains = [
        export_main_score(score, spec, output_dir)
        for spec in specs
        if is_main_score(spec.name)
    ]
    part_specs = [spec for spec in specs if not is_main_score(spec.name)]

    jobs: list[ExportJob] = list(mains)
    if part_specs:
        with tempfile.TemporaryDirectory(prefix="pdfscore-extract-") as tmp:
            extracted = extract_part_scores(mscore, score, Path(tmp))
            for spec in part_specs:
                src = match_extracted(spec.name, catalog, extracted)
                if spec.key is None:
                    dest = output_dir / f"{score.stem}.{spec.name}.pdf"
                    action = f"export part {spec.name}"
                else:
                    dest = output_dir / f"{score.stem}.{spec.name}.{spec.key}.pdf"
                    action = f"transpose to {spec.key} and export part {spec.name}"
                if src is None:
                    hints: list[str] = []
                    if extracted:
                        hints.append(f"extracted: {', '.join(sorted(extracted))}")
                    if catalog:
                        defined = ", ".join(part.name for part in catalog)
                        hints.append(f"score defines: {defined}")
                    known = f" ({'; '.join(hints)})" if hints else ""
                    raise UserError(
                        f"- Failed to {action} {score} -> {dest}: "
                        f"part {spec.name!r} not found{known}"
                    )
                payload: dict[str, Any] = {"in": str(src), "out": str(dest)}
                if spec.jump:
                    payload["transpose"] = transpose_options(spec.jump)
                print(f"Exporting {dest.name}")
                jobs.append(
                    ExportJob(
                        score=score,
                        output=dest,
                        action=action,
                        edition_label=spec.key,
                        payload=payload,
                    )
                )
            if jobs:
                run_mscore_batch(mscore, jobs)
    elif mains:
        run_mscore_batch(mscore, mains)
    return jobs


def export_main_score(score: Path, spec: PartSpec, output_dir: Path) -> ExportJob:
    """Export the virtual Main score part: the full score, optionally transposed."""
    if spec.key is None:
        dest = output_dir / f"{score.stem}.pdf"
        action = "export"
        payload: dict[str, Any] = {"in": str(score), "out": str(dest)}
        label = None
    else:
        dest = output_dir / f"{score.stem}.{spec.key}.pdf"
        action = f"transpose to {spec.key} and export"
        payload = {
            "in": str(score),
            "out": str(dest),
            "transpose": transpose_options(spec.jump),
        }
        label = spec.key
    print(f"Exporting {dest.name}")
    return ExportJob(
        score=score, output=dest, action=action, edition_label=label, payload=payload
    )


def extract_part_scores(mscore: Path, score: Path, tmp_dir: Path) -> dict[str, Path]:
    """Materialize each staff part as its own .mscz via --score-parts.

    MuseScore prints the parts JSON on stdout and may crash on exit,
    so a parseable payload matters more than the return code.
    """
    result = subprocess.run(
        [str(mscore), str(score), "--score-parts"],
        capture_output=True,
        text=True,
    )
    try:
        data = json.loads(result.stdout)
        names, blobs = data["parts"], data["partsBin"]
        if (
            not isinstance(names, list)
            or not isinstance(blobs, list)
            or len(names) != len(blobs)
        ):
            raise ValueError("malformed parts payload")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        details = (result.stderr or result.stdout or "")[-2000:].strip()
        raise UserError(
            f"Could not read parts of {score}" + (f": {details}" if details else "")
        )
    files: dict[str, Path] = {}
    for name, blob in zip(names, blobs):
        try:
            raw = base64.b64decode(blob)
        except (ValueError, TypeError):
            raise UserError(f"Could not decode part {name!r} of {score}")
        path = tmp_dir / f"{name}.mscz"
        path.write_bytes(raw)
        files[name] = path
    return files


def match_extracted(
    name: str, catalog: list[ScorePart], extracted: dict[str, Path]
) -> Path | None:
    """Resolve a requested part to an extracted file: exact, then one case-fold hit."""
    part = resolve_part(name, catalog) if catalog else None
    candidates = [name] if part is None else [part.name, *part.aliases]
    for candidate in candidates:
        if candidate in extracted:
            return extracted[candidate]
    lowered = {key.lower(): path for key, path in extracted.items()}
    hits = {
        lowered[candidate.lower()]
        for candidate in candidates
        if candidate.lower() in lowered
    }
    if len(hits) == 1:
        return next(iter(hits))
    return None


@app.default
@runcorder.instrument
def main(
    scores: Annotated[
        list[Path],
        Parameter(help="One or more MuseScore .mscz files to export."),
    ],
    *,
    keys: Annotated[
        list[str],
        Parameter(
            alias="-k",
            help="Target key. C or omitted exports $stem.pdf; other keys export $stem.$key.pdf.",
        ),
    ] = [],
    parts: Annotated[
        list[str],
        Parameter(
            alias="-p",
            help="Part to export as NAME or NAME:KEY (e.g. -p Violin -p Trumpet:Bb). 'Main score' or empty (-p '') exports the full score. Repeat for multiple parts. Cannot be combined with --key.",
        ),
    ] = [],
    output: Annotated[
        Path | None,
        Parameter(
            alias="-o",
            help="Output directory for PDF exports (defaults to each score's directory).",
        ),
    ] = None,
) -> None:
    """Export scores to PDF in concert or transposed keys."""
    if not scores:
        raise UserError("Provide at least one score.")
    ensure_compatible_options(keys, parts)

    mscore = find_mscore()
    if parts:
        part_specs = expand_parts(parts)
        jobs: list[ExportJob] = []
        for score in scores:
            resolved = score.resolve()
            output_dir = resolve_output_dir(resolved, output)
            jobs.extend(export_parts(mscore, resolved, part_specs, output_dir))
        stamp_edition_labels(jobs)
        return
    key_jumps, concert_requested = expand_keys(keys)
    include_original = not keys or concert_requested

    if not include_original and not key_jumps:
        raise UserError("Nothing to export.")

    jobs: list[ExportJob] = []
    for score in scores:
        resolved = score.resolve()
        output_dir = resolve_output_dir(resolved, output)
        jobs.extend(
            build_export_jobs(
                resolved,
                key_jumps,
                output_dir,
                include_original=include_original,
            )
        )

    run_mscore_batch(mscore, jobs)
    stamp_edition_labels(jobs)


def cli() -> None:
    try:
        app()
    except UserError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    cli()
