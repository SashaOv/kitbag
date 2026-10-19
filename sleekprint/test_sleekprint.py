"""Tests for width-aware YAML sleek print (mirrors niva_lang format.rs)."""

from __future__ import annotations

import sys
from enum import Enum
from importlib.metadata import version

import pytest
import yaml
from hypothesis import given, seed, settings
from hypothesis import strategies as st

from sleekprint import format_yaml, sp, to_plain, yaml_inline, yaml_string, yaml_value


class Role(str, Enum):
    MEMBER = "member"


class Subscriber:
    """Duck-typed stand-in for a Pydantic model (`model_dump` only)."""

    def __init__(self, id: int, email: str, role: Role, name: str | None = None) -> None:
        self.id = id
        self.email = email
        self.name = name
        self.role = role

    def model_dump(self, *, mode: str = "python") -> dict[str, object]:
        role: object = self.role.value if mode == "json" else self.role
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "role": role,
        }


def test_inline_fits() -> None:
    v = {"root": "G", "quality": "m7"}
    out = yaml_value(v, 0, 100)
    assert "\n" not in out
    assert "root: G" in out
    assert "quality: m7" in out


def test_expands_when_too_wide() -> None:
    v = {"root": "G", "quality": "m7"}
    out = yaml_value(v, 0, 10)
    assert "\n" in out
    assert "root: G" in out


def test_children_collapsed_inside_expanded_parent() -> None:
    v = [{"root": "G"}, {"root": "A"}, {"root": "B"}, {"root": "C"}, {"root": "D"}]
    out = yaml_value(v, 0, 20)
    assert "\n" in out
    # Single-key objects drop braces when inline as array items
    assert "- root: G" in out


def test_string_quoting() -> None:
    assert yaml_string("hello") == "hello"
    assert yaml_string("hello world") == '"hello world"'
    assert yaml_string("m7b5") == "m7b5"
    assert yaml_string("true") == '"true"'
    assert yaml_string("") == '""'


def test_array_block_item_stripped() -> None:
    # Items that expand to block should use "- key: val" not "- \n  key: val"
    v = [{"very_long_key_name": "very_long_value_name_here_yes"}]
    out = yaml_value(v, 0, 30)
    assert out.startswith("- ")
    assert not out.startswith("- \n")


def test_empty() -> None:
    assert yaml_inline({}) == "{}"
    assert yaml_inline([]) == "[]"


def test_null_entries_omitted() -> None:
    assert yaml_inline({"a": 1, "b": None}) == "{a: 1}"
    out = yaml_value({"a": 1, "b": None, "c": "x"}, 0, 5)
    assert "b:" not in out
    assert "a: 1" in out


def test_bool_and_null_scalars() -> None:
    assert yaml_inline(True) == "true"
    assert yaml_inline(False) == "false"
    assert yaml_inline(None) == "~"


def test_to_plain_model_dump_and_list() -> None:
    rows = [
        Subscriber(id=1, email="a@b.com", name=None, role=Role.MEMBER),
        Subscriber(id=2, email="c@d.com", name="Pat", role=Role.MEMBER),
    ]
    plain = to_plain(rows)
    assert plain == [
        {"id": 1, "email": "a@b.com", "name": None, "role": "member"},
        {"id": 2, "email": "c@d.com", "name": "Pat", "role": "member"},
    ]
    # Null optional fields are omitted (not printed as ~)
    out = format_yaml(rows, width=40)
    assert "name: Pat" in out
    assert out.count("name:") == 1
    assert "role: member" in out
    assert "null" not in out
    assert "~" not in out


def test_format_yaml_list_of_models_inline_when_wide() -> None:
    row = Subscriber(id=7, email="band@example.com", role=Role.MEMBER)
    out = format_yaml([row], width=200)
    assert out.startswith("[") or out.startswith("- ")
    assert "id: 7" in out
    # @ is YAML-special, so addresses are quoted
    assert 'email: "band@example.com"' in out


@pytest.mark.parametrize(
    "s",
    [
        "12345",
        "1.5",
        "True",
        "yes",  # already quoted by yaml_string as a YAML keyword; kept for contrast
        "2026-01-01",
        "-1",
        "1e5",
        "null",
    ],
)
def test_sleek_print_round_trip(s: str) -> None:
    """A plain string value must come back as the same string via yaml.safe_load.

    format_yaml only quotes strings containing whitespace or YAML-special
    characters (see yaml_string), so a string that looks like a number, bool,
    date, or other YAML scalar type is written unquoted and a standard YAML
    parser reads it back as that type instead of as a string.
    """
    out = format_yaml({"v": s})
    loaded = yaml.safe_load(out)["v"]
    assert loaded == s
    assert type(loaded) is str


@pytest.mark.parametrize("width", [100, 130])
def test_nested_single_key_mapping_round_trip(width: int) -> None:
    """A nested single-key mapping that block-renders to one line must not be

    glued onto the parent's "key: " on the same line — that produces invalid
    YAML ("mapping values are not allowed here") because a one-line block
    render (e.g. "2026: {...}") is block syntax, not flow syntax, even though
    it contains no embedded newline.
    """
    # format_yaml (via to_plain) always stringifies mapping keys, so "2026" is
    # the correct round-trip target regardless of whether the source key was
    # an int or a str.
    doc = {
        "safe_harbor": {
            "2026": {
                "prior_year_federal_tax": 33510,
                "prior_year_federal_agi": 248664,
                "prior_year_state_tax": 14161,
                "prior_year_state_agi": 248664,
            }
        }
    }
    out = format_yaml(doc, width=width)
    assert yaml.safe_load(out) == doc


@pytest.mark.parametrize("width", [10, 40, 60, 80])
def test_nested_single_item_sequence_round_trip(width: int) -> None:
    """Same bug, different container: a single-item sequence that block-renders

    to "- key: value" on one line must not be glued onto the parent's "key: ".
    """
    doc = {"outer": [{"a_fairly_long_key_name_to_force_expansion": "some longer value text here"}]}
    out = format_yaml(doc, width=width)
    assert yaml.safe_load(out) == doc


@pytest.mark.parametrize("width", [40, 100])
def test_special_char_key_round_trip(width: int) -> None:
    """Mapping keys must be quoted the same way values are (see yaml_string).

    yaml_block/yaml_inline write `k` verbatim, so a key containing a colon,
    whitespace, or a YAML keyword is emitted unquoted and either breaks
    parsing outright or silently changes meaning/type on reload.
    """
    doc = {"a: b": 1, "true": 2, "has space": 3}
    out = format_yaml(doc, width=width)
    assert yaml.safe_load(out) == doc


@pytest.mark.parametrize("width", [5, 100])
def test_control_char_key_round_trip(width: int) -> None:
    """Quoting a string isn't enough — control characters inside a quoted YAML

    scalar must themselves be escaped (\\r, \\x1F, ...), or they end up embedded
    raw inside the quotes and either corrupt parsing or get rejected outright.
    Found by the fuzz property test at the bottom of this file (Hypothesis
    shrunk it to a single-key dict with a "\\r" key).
    """
    doc = {"\r": []}
    out = format_yaml(doc, width=width)
    assert yaml.safe_load(out) == doc


def test_control_char_value_round_trip() -> None:
    """Same escaping gap, but for a value instead of a key (\\x1F is fully

    disallowed unescaped anywhere in a YAML stream, even quoted).
    """
    doc = {"v": "a\x1fb"}
    out = format_yaml(doc, width=100)
    assert yaml.safe_load(out) == doc


def test_empty_nested_container_round_trip() -> None:
    """An empty list/dict has no smaller block form than "[]"/"{}" — at a width too

    narrow to fit even that, yaml_block used to render nothing for it, and a
    sequence entry with nothing after "- " parses back as null, not empty.
    Found by the fuzz test in test_sleekprint.py (Hypothesis shrunk it to {"0": [[]]}).
    """
    doc = {"0": [[]]}
    out = format_yaml(doc, width=5)
    assert yaml.safe_load(out) == doc


@pytest.mark.parametrize("width", [5, 10, 100])
def test_all_none_mapping_round_trip(width: int) -> None:
    """A mapping whose values are all None renders empty (each entry is omitted), the

    same as a literally empty dict — it must get the same "always inline {}" treatment,
    not fall into block-rendering and produce an empty line ("- " -> null instead of {}).
    Found by the fuzz property test (shrunk to a single-key dict {"": None} nested in a
    list, but a multi-key all-None dict hits the same path).
    """
    for doc in ({"0": [{"": None}]}, {"0": [{"a": None, "b": None}]}):
        out = format_yaml(doc, width=width)
        assert yaml.safe_load(out) == {"0": [{}]}


@pytest.mark.parametrize("s", ["0?", "?0", "foo?bar"])
def test_question_mark_key_round_trip(s: str) -> None:
    """"?" is YAML's explicit-key indicator; embedded in an unquoted flow-mapping key

    it breaks the parser, even though the same string round-trips fine as a
    standalone document (which is why _resolves_to_non_string didn't catch it —
    that check parses the string in isolation, not in flow-key position). Found
    by the fuzz property test (shrunk to {"0?": []} / {"?0": []}).
    """
    doc = {s: []}
    out = format_yaml(doc, width=8)
    assert yaml.safe_load(out) == doc


def test_sp_print(capsys: pytest.CaptureFixture[str]) -> None:
    sp({"ok": True, "storage": "ok"}, width=100)
    captured = capsys.readouterr()
    assert captured.out.strip() == "{ok: true, storage: ok}"


# --- Property-based fuzzing (manual only) -----------------------------------
#
# Not a pytest test: no "test_" prefix, so pytest's default collector skips it
# regardless of markers/addopts. Run it directly:
#
#   python test_sleekprint.py --fuzz
#
# When it finds a failure, take Hypothesis's shrunk counterexample and freeze
# it as a plain test above — this is a bug-hunting tool, not a CI gate.


def _drop_none_map_entries(x: object) -> object:
    """Mirror format_yaml: mapping entries with a None value are omitted, list entries are not."""
    if isinstance(x, dict):
        return {k: _drop_none_map_entries(v) for k, v in x.items() if v is not None}
    if isinstance(x, list):
        return [_drop_none_map_entries(v) for v in x]
    return x


def _fuzz_round_trip(seed_value: object) -> None:
    leaf = st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-10_000_000, max_value=10_000_000),
        st.floats(allow_nan=False, allow_infinity=False, width=32),
        st.text(max_size=20),
    )
    plain_value = st.recursive(
        leaf,
        lambda children: st.one_of(
            st.lists(children, max_size=4),
            st.dictionaries(st.text(max_size=15), children, max_size=4),
        ),
        max_leaves=20,
    )
    doc_strategy = st.dictionaries(st.text(min_size=1, max_size=15), plain_value, min_size=1, max_size=5)

    @seed(seed_value)
    @settings(max_examples=300)
    @given(doc=doc_strategy, width=st.integers(min_value=5, max_value=200))
    def round_trips(doc: dict, width: int) -> None:
        out = format_yaml(doc, width=width)
        loaded = yaml.safe_load(out)
        assert loaded == _drop_none_map_entries(doc)

    round_trips()


if __name__ == "__main__":
    if "--fuzz" in sys.argv:
        project_version = version("sleekprint")
        print(f"fuzz: seeding with project version {project_version!r}")
        _fuzz_round_trip(seed_value=project_version)
        print("fuzz: no failures found")
    else:
        print("Run with --fuzz to property-test format_yaml round-tripping.")
