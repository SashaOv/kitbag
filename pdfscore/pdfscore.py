"""Export MuseScore .mscz files to PDF, with optional transposed versions."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Annotated, Any

import cyclopts
import runcorder
from cyclopts import Parameter


class UserError(Exception):
    """Raised for invalid CLI input or export failures."""


@dataclass(frozen=True)
class ExportJob:
    """A requested export and the MuseScore batch payload that creates it."""

    score: Path
    output: Path
    action: str
    edition_label: str | None
    payload: dict[str, Any]


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
            "Run `uv sync --project apps/musistant` to install dependencies."
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


def run_mscore_batch(mscore: Path, jobs: list[ExportJob]) -> None:
    if not jobs:
        return

    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".json", delete=False
    ) as batch_file:
        json.dump([job.payload for job in jobs], batch_file, indent=2)
        batch_file.write("\n")
        batch_path = Path(batch_file.name)

    try:
        result = subprocess.run(
            [str(mscore), "-f", "-j", str(batch_path)],
            capture_output=True,
            text=True,
        )
    finally:
        batch_path.unlink(missing_ok=True)

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
    if not score.is_file():
        raise UserError(f"Score not found: {score}")
    if score.suffix.lower() != ".mscz":
        raise UserError(f"Expected a .mscz file: {score}")

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

    mscore = find_mscore()
    key_jumps, concert_requested = expand_keys(keys)
    include_original = not keys or concert_requested

    if not include_original and not key_jumps:
        raise UserError("Nothing to export.")

    jobs: list[ExportJob] = []
    for score in scores:
        resolved = score.resolve()
        output_dir = output.resolve() if output else resolved.parent
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
