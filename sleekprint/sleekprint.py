"""Width-aware YAML pretty-printer (sleek / bottom-up collapse).

Each node is rendered inline when it fits within a line budget; otherwise it expands to block
style while children are tried independently.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any

import yaml

DEFAULT_WIDTH = 100

_YAML_SPECIAL = set("#:{}[]|>&*!%@,?")
_YAML_KEYWORDS = frozenset({"true", "false", "null", "yes", "no", "on", "off", "~"})

# YAML double-quoted scalars forbid raw control/separator characters; they must be
# written as escape sequences (spec 5.7, "Escaped Characters").
_YAML_SHORT_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\0": "\\0",
    "\a": "\\a",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\v": "\\v",
    "\f": "\\f",
    "\r": "\\r",
    "\x1b": "\\e",
    "\x85": "\\N",
    "\xa0": "\\_",
    "\u2028": "\\L",
    "\u2029": "\\P",
}


def _escape_double_quoted(s: str) -> str:
    out = []
    for ch in s:
        if ch in _YAML_SHORT_ESCAPES:
            out.append(_YAML_SHORT_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F or 0x80 <= ord(ch) <= 0x9F:
            out.append(f"\\x{ord(ch):02X}")
        else:
            out.append(ch)
    return "".join(out)


def _is_container(value: object) -> bool:
    """True for Mapping/Sequence values that render as YAML collections (not str/bytes)."""
    return isinstance(value, (Mapping, Sequence)) and not isinstance(value, (str, bytes, bytearray))


def _rendered_len(value: object) -> int:
    """Number of entries a container actually renders (mapping entries with a None value

    are omitted; sequence entries are not, since None there renders as "~").
    """
    if isinstance(value, Mapping):
        return sum(1 for v in value.values() if v is not None)
    return len(value)  # type: ignore[arg-type]


def _resolves_to_non_string(s: str) -> bool:
    """True if a YAML parser would read plain scalar `s` back as a non-string type."""
    try:
        return yaml.safe_load(s) != s
    except yaml.YAMLError:
        return True


def to_plain(obj: object) -> Any:
    """Normalize Pydantic models, enums, and containers to JSON-like values."""
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Mapping):
        return {str(k): to_plain(v) for k, v in obj.items()}
    if isinstance(obj, Sequence) and not isinstance(obj, (str, bytes, bytearray)):
        return [to_plain(v) for v in obj]
    return obj


def yaml_string(s: str) -> str:
    """Quote a string if it contains whitespace or YAML-special characters."""
    needs_quotes = (
        not s
        or any(c.isspace() or c in _YAML_SPECIAL for c in s)
        or s in _YAML_KEYWORDS
        or _resolves_to_non_string(s)
    )
    if needs_quotes:
        return f'"{_escape_double_quoted(s)}"'
    return s


def yaml_inline(value: object) -> str:
    """Render `value` as a YAML flow (inline) string. No newlines."""
    if isinstance(value, Mapping):
        parts = [
            f"{yaml_string(str(k))}: {yaml_inline(v)}"
            for k, v in value.items()
            if v is not None
        ]
        return "{" + ", ".join(parts) + "}"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "[" + ", ".join(yaml_inline(v) for v in value) + "]"
    if isinstance(value, str):
        return yaml_string(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "~"
    if isinstance(value, (int, float)):
        # Match JSON/serde number printing: avoid trailing .0 noise for ints.
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    return yaml_string(str(value))


def yaml_block(value: object, level: int, width: int) -> str:
    """Render `value` as YAML block style.

    Each output line is already indented for `level` (starts with `level * 2`
    spaces). No leading newline.
    """
    pad = " " * (level * 2)

    if isinstance(value, Mapping):
        lines: list[str] = []
        for k, v in value.items():
            if v is None:
                continue
            key = yaml_string(str(k))
            if _is_container(v) and _rendered_len(v) > 0:
                # A block-rendered container (e.g. "2026: {...}" or "- item") can be a
                # single line with no embedded "\n" yet still be block syntax, which is
                # never safe to glue onto "key: " — only genuine flow syntax is.
                inline = yaml_inline(v)
                if (level + 1) * 2 + len(inline) <= width:
                    lines.append(f"{pad}{key}: {inline}")
                else:
                    lines.append(f"{pad}{key}:\n{yaml_block(v, level + 1, width)}")
            else:
                child = yaml_value(v, level + 1, width)
                lines.append(f"{pad}{key}: {child}")
        return "\n".join(lines)

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items: list[str] = []
        for v in value:
            # Single-key objects drop outer {} — YAML allows "- key: val". Only when the
            # value isn't None: a None value is omitted, so the dict renders as "{}", not
            # "key: ~" (see _rendered_len).
            if isinstance(v, Mapping) and len(v) == 1 and next(iter(v.values())) is not None:
                k, val = next(iter(v.items()))
                inline = f"{yaml_string(str(k))}: {yaml_inline(val)}"
            else:
                inline = yaml_inline(v)
            # Containers that render empty (literally empty, or all-None-valued mappings)
            # have no smaller/blockier form than "[]"/"{}" — block-rendering one produces
            # an empty line, which YAML reads back as a null entry, not empty.
            fits = not _is_container(v) or _rendered_len(v) == 0 or level * 2 + 2 + len(inline) <= width
            if fits:
                items.append(f"{pad}- {inline}")
                continue
            # Expand: child block lines start with (level+1)*2 spaces.
            # Replace leading spaces on the first line with "- ".
            block = yaml_block(v, level + 1, width)
            nl = block.find("\n")
            if nl >= 0:
                first = block[:nl].lstrip()
                rest = block[nl + 1 :]
                items.append(f"{pad}- {first}\n{rest}")
            else:
                items.append(f"{pad}- {block.lstrip()}")
        return "\n".join(items)

    return yaml_inline(value)


def yaml_value(value: object, level: int, width: int) -> str:
    """Inline `value` if it fits within `width` at this indent, else block it."""
    inline = yaml_inline(value)
    if (_is_container(value) and _rendered_len(value) == 0) or level * 2 + len(inline) <= width:
        return inline
    return yaml_block(value, level, width)


def format_yaml(obj: object, *, width: int = DEFAULT_WIDTH) -> str:
    """Format `obj` as width-aware YAML (does not print)."""
    return yaml_value(to_plain(obj), 0, width)


def sleek_print_yaml(obj: object, *, width: int = DEFAULT_WIDTH) -> None:
    """Format object as YAML using the sleek (width-aware) print algorithm."""
    print(format_yaml(obj, width=width))


def sp(obj: object, *, width: int = DEFAULT_WIDTH) -> None:
    """Short alias for `sleek_print_yaml`."""
    sleek_print_yaml(obj, width=width)
