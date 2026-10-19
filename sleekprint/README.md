# sleekprint

Width-aware YAML pretty-printer. Each node is printed inline (`{a: 1, b: [x, y]}`) when it fits the line budget; otherwise it expands to block style, and its children are tried independently.

```python
from sleekprint import sp, format_yaml

sp({"ok": True, "storage": "ok"})        # {ok: true, storage: ok}
text = format_yaml(data, width=80)       # same, as a string
```

- Accepts dicts, lists, enums and Pydantic models (anything with `model_dump`).
- Mapping entries whose value is `None` are omitted.
- Output round-trips through `yaml.safe_load`: strings that would read back as another type (`"12345"`, `"yes"`, `"2026-01-01"`) and keys with special characters are quoted.

Fuzz the round-trip property (manual, not part of the test run):

```bash
python test_sleekprint.py --fuzz
```

Install:

```bash
uv add "sleekprint @ https://git@github.com/SashaOv/kitbag.git#subdirectory=sleekprint"
```
