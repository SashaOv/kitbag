# kitbag

Small utilities I use in different projects. Each tool is its own package.

## Use

Each subdirectory is a package with its own `pyproject.toml`.

**Library** (import it in a project):

```bash
uv add "sleekprint @ git+ssh://git@github.com/SashaOv/kitbag.git#subdirectory=sleekprint"
```

**CLI** (put a command on `PATH`):

```bash
uv tool install "git+ssh://git@github.com/SashaOv/kitbag.git#subdirectory=pdfscore"
```

**Local, not pushed yet** — point the same dependency at this checkout:

```toml
[tool.uv.sources]
sleekprint = { path = "../kitbag/sleekprint", editable = true }
```

```bash
uv tool install -e ./pdfscore
```

**Override an installed tool** with this checkout (installed from git, now hacking locally):

```bash
uv tool install --force -e ./pdfscore
```

**Install as a local package** (import it, editable — replaces any existing install, no `--force` needed):

```bash
uv pip install -e ./pdfscore
```
