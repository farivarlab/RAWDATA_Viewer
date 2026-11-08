# Contributing

## Development setup

Use a 64-bit Python installation and a local virtual environment. Windows is
the primary desktop platform; CI exercises Python 3.12 and 3.13 on Windows.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m siemens_explorer --windowed
```

Runtime dependencies are declared in `pyproject.toml`; keep `requirements.txt`
in sync when changing them. The launch scripts use the latter to bootstrap a
fresh checkout. Appearance assets can be edited and reloaded from the View menu.

## Checks

```powershell
.\.venv\Scripts\python.exe tools/run_tests.py
git diff --check
```

The runner starts a fresh process per test module because Qt application state,
styles, and native windows are global within a process. Use `--jobs 1` to run
modules sequentially, `--pattern test_twix.py` for one module, or `--report
.cache/tests.json` for a JSON report. Individual tests can also run through
`python -m unittest discover -s tests -p test_twix.py -v`.

Tests generate small fixtures in temporary directories. They require no scanner
file, account, network connection, or API key after dependencies are installed.
Windows launcher checks exercise a project-local shortcut and create ignored
launcher logs/cache files.

To check packaging:

```powershell
.\.venv\Scripts\python.exe -m pip install build
.\.venv\Scripts\python.exe -m build
```

Build outputs belong in ignored `dist/` and `build/` directories. The source
distribution includes launch scripts, tests, and documentation; the wheel
includes the importable package, appearance resources, entry points, and license.

## Code and data conventions

- Explain units, coordinate order, limits, and non-obvious decisions in docstrings
  and nearby comments. Preserve the original scanner values at the reader layer.
- Keep binary parsing independent of Qt. Validate file bounds before reads and
  memory budgets before allocating sample matrices.
- Keep widgets on the GUI thread. Worker cancellation is cooperative, and stale
  preview results must not replace a newer selection.
- Preserve parser scan IDs through sorting/filtering and distinguish channel
  ordinals from physical receiver IDs.
- Add focused regression coverage for behavior changes; use generated fixtures
  for format edge cases and numerical checks for reconstruction changes.
- Keep raw `.dat` files, exported arrays, patient metadata, local paths, virtual
  environments, and caches out of commits and public issue attachments. Describe
  a problem using an anonymized header fragment or a generated reproduction.

See [the architecture guide](docs/ARCHITECTURE.md) for module responsibilities
and the contracts shared by the reader, numerical code, and UI. Contributions
to this project are distributed under its [MIT license](LICENSE).
