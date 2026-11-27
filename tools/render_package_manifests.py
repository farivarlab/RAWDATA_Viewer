"""Fill the Homebrew formula and Scoop manifest templates for a release.

Usage (the release workflow runs this after building the artifacts):

    python tools/render_package_manifests.py --version 0.2.0 \
        --sdist dist/raw_mri_data_explorer-0.2.0.tar.gz \
        --windows-zip dist/RawMRIDataExplorer-0.2.0-windows-x64.zip \
        --out dist/

Writes ``raw-mri-data-explorer.rb`` and ``raw-mri-data-explorer.json`` to the
output directory, ready to copy into the tap and bucket repositories.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = {
    "raw-mri-data-explorer.rb": ROOT / "packaging" / "homebrew" / "raw-mri-data-explorer.rb.in",
    "raw-mri-data-explorer.json": ROOT / "packaging" / "scoop" / "raw-mri-data-explorer.json.in",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def render(template: str, values: dict[str, str]) -> str:
    result = template
    for key, value in values.items():
        result = result.replace(f"@{key}@", value)
    leftover = re.findall(r"@[A-Z0-9_]+@", result)
    if leftover:
        raise ValueError(f"Unfilled template placeholders: {sorted(set(leftover))}")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True, help="Release version without the leading v")
    parser.add_argument("--sdist", type=Path, required=True)
    parser.add_argument("--windows-zip", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"\d+\.\d+\.\d+([.-]?\w+)?", args.version):
        parser.error(f"Unexpected version format: {args.version}")
    values = {
        "VERSION": args.version,
        "SDIST_SHA256": sha256(args.sdist),
        "WINDOWS_SHA256": sha256(args.windows_zip),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    for name, template in TEMPLATES.items():
        text = render(template.read_text(encoding="utf-8"), values)
        if name.endswith(".json"):
            json.loads(text)  # Scoop rejects malformed manifests; fail here instead.
        (args.out / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {args.out / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
