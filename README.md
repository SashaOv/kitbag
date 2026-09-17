# kitbag

This is a [monorepo](https://en.wikipedia.org/wiki/Monorepo) for small projects that don't yet deserve their own repo. Each project is its own package and can be installed independenly.

## Use

Each subdirectory is a package with its own `pyproject.toml`.

**Library** (import it in a project):

```bash
uv add "sleekprint @ https://git@github.com/SashaOv/kitbag.git#subdirectory=sleekprint"
```

**Note**: you can use 'git+ssh:' URL schema if you are set up for it.

**CLI** (put a command on `PATH`):

```bash
uv tool install "https://git@github.com/SashaOv/kitbag.git#subdirectory=pdfscore"
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
