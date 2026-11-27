# Release packaging

| Platform | Channel | Source artifact |
| --- | --- | --- |
| macOS (and Linux) | Homebrew tap `farivarlab/tap` | `raw_mri_data_explorer-<version>.tar.gz` (sdist) |
| Windows | Scoop bucket `farivarlab` | `RawMRIDataExplorer-<version>-windows-x64.zip` (PyInstaller bundle) |
| Any | `pip install` | wheel / sdist |

Homebrew does not run on Windows; Scoop is the closest equivalent (a "bucket"
is a Git repo of JSON manifests, the same way a tap is a Git repo of formulae).

## Cutting a release

1. Bump the version in `pyproject.toml` **and** `siemens_explorer/__init__.py`.
2. Commit, then tag and push: `git tag v0.3.0 && git push origin v0.3.0`.
3. `.github/workflows/release.yml` then:
   - builds the sdist and wheel and checks the tag matches both version strings;
   - builds the standalone Windows app with PyInstaller, launches it as a smoke
     test, and zips it;
   - installs the Homebrew formula on macOS from the fresh sdist and runs
     `brew test`;
   - creates a **draft** GitHub release with every artifact, `SHA256SUMS.txt`,
     and the filled-in `raw-mri-data-explorer.rb` and `raw-mri-data-explorer.json`.
4. Review the draft release and publish it. The download URLs in the formula
   and manifest only work once the release is public.
5. Copy the rendered files into the distribution repos (below).

## One-time setup of the tap and bucket

Create two public repos under the `farivarlab` organization:

- **`homebrew-tap`**: put the rendered formula at `Formula/raw-mri-data-explorer.rb`.
  Users install with:

  ```sh
  brew install farivarlab/tap/raw-mri-data-explorer
  raw-mri-data-explorer path/to/meas.dat
  ```

- **`scoop-bucket`**: put the rendered manifest at `bucket/raw-mri-data-explorer.json`.
  Users install with:

  ```powershell
  scoop bucket add farivarlab https://github.com/farivarlab/scoop-bucket
  scoop install farivarlab/raw-mri-data-explorer
  ```

  Scoop adds a Start-menu shortcut. The manifest's `checkver`/`autoupdate`
  blocks let `scoop`'s Excavator action, or a maintainer running
  `checkver.ps1 -u`, pick up later releases without editing by hand.

## Local builds

```powershell
# Standalone Windows bundle -> dist\Raw MRI Data Explorer\
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe -m PyInstaller packaging/pyinstaller/raw-mri-data-explorer.spec --noconfirm

# Render manifests against local artifacts
.\.venv\Scripts\python.exe tools/render_package_manifests.py --version 0.2.0 `
    --sdist dist/raw_mri_data_explorer-0.2.0.tar.gz `
    --windows-zip dist/RawMRIDataExplorer-0.2.0-windows-x64.zip --out dist
```

## Notes

- The Homebrew formula installs PySide6, NumPy, pandas and h5py from their
  PyPI wheels into a private virtualenv under the Cellar. That is fine for a
  tap but not accepted by homebrew-core.
- The Windows bundle is unsigned, so SmartScreen may warn on first launch.
  Code signing (e.g. Azure Trusted Signing) can be added to the
  `windows-bundle` job later.
- A winget manifest would need an installer (MSI/MSIX/Inno Setup) rather than
  a zip. Add one if winget distribution becomes a goal.
