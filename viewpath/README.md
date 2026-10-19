# viewpath

A `pathlib.Path` that is navigated the way a user sees it: lexically, through links.

- `.` and `..` collapse textually. A directory symlink is never resolved, so going up from inside a link returns through the link, not to the linked tree's physical parent.
- Derived paths (`/`, `parent`, `iterdir`, `glob`, `walk`) stay `ViewPath` and keep the same base.
- A relative path does its I/O against its base (default: the logical working directory, `$PWD`), not the process directory. `str()` still shows the path as given.
- `walk()` returns sorted names and, with `follow_symlinks=True`, never enters a real directory twice, so linked cycles end.
- `resolve()` leaves the view and returns a plain physical `Path`.

Example:

```python
from viewpath import ViewPath

shows = ViewPath("GDrive/Shows", base="/home/me/project")  # GDrive is a symlink
shows.parent                      # GDrive, not the linked tree's parent
(shows / "a.pdf").read_text()     # I/O goes through the link
for dirpath, dirnames, filenames in shows.walk(follow_symlinks=True):
    ...
```

Install:

```bash
uv add "viewpath @ https://git@github.com/SashaOv/kitbag.git#subdirectory=viewpath"
```
