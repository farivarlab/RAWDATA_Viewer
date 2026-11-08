"""Run each unittest module in a fresh process to isolate Qt global state.

Use the project's virtual-environment interpreter. Test-generated scanner files
live in temporary directories; no external raw-data fixture is required.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]


def run_module(path: Path, timeout: float) -> dict:
    """Capture one module's result; timeouts count as failures rather than hangs."""
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        result = subprocess.run(
            [sys.executable, "-u", "-m", "unittest", "discover", "-s", "tests", "-p", path.name],
            cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
        output = result.stdout + result.stderr
        count = re.search(r"Ran (\d+) tests?", output)
        return {"module": path.name, "passed": result.returncode == 0 and count is not None,
                "tests": int(count[1]) if count else 0, "output": output if result.returncode or not count else ""}
    except subprocess.TimeoutExpired:
        return {"module": path.name, "passed": False, "tests": 0,
                "output": f"Module exceeded its {timeout:g}-second timeout."}


def main(argv=None) -> int:
    """Print concise results and optionally save a machine-readable JSON report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pattern", default="test_*.py", help="Test-module filename glob")
    parser.add_argument("--jobs", type=int, default=2, help="Concurrent isolated modules (default: 2)")
    parser.add_argument("--timeout", type=float, default=180, help="Timeout per module in seconds")
    parser.add_argument("--report", type=Path, help="Optional JSON report path")
    args = parser.parse_args(argv)
    if args.jobs < 1 or args.timeout <= 0:
        parser.error("--jobs and --timeout must be positive")
    modules = sorted((ROOT / "tests").glob(args.pattern))
    if not modules:
        parser.error("No test modules matched --pattern")
    started = time.monotonic()
    # Processes, rather than threads sharing QApplication, isolate styles and
    # native-window state. Threads here only coordinate those child processes.
    results = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for result in pool.map(lambda path: run_module(path, args.timeout), modules):
            results.append(result)
            print(f"{result['module']}: {'PASS' if result['passed'] else 'FAIL'} ({result['tests']})", flush=True)
            if result["output"]:
                print(result["output"], flush=True)
    report = {"passed": all(result["passed"] for result in results),
              "tests": sum(result["tests"] for result in results),
              "seconds": round(time.monotonic() - started, 2), "modules": results}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{report['tests']} tests in {report['seconds']:.1f}s: {'PASS' if report['passed'] else 'FAIL'}", flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
