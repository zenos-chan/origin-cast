#!/usr/bin/env python3
"""
Staging Linter for OriginCast.
Checks git staged files for Python style, syntax, and formatting issues before commit.
"""

import sys
import subprocess
from pathlib import Path


def get_staged_python_files():
    """Returns list of staged Python (.py) files."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True,
            text=True,
            check=True
        )
        files = [
            f.strip() for f in result.stdout.strip().split("\n")
            if f.strip().endswith(".py") and Path(f.strip()).exists()
        ]
        return files
    except subprocess.CalledProcessError as e:
        print(f"[!] Error getting staged files: {e}")
        return []


def run_staging_lint():
    """Lints only staged Python files."""
    staged_files = get_staged_python_files()

    if not staged_files:
        print("[✓] Staging Lint: No staged Python files to check.")
        return 0

    print(f"[*] Staging Lint: Checking {len(staged_files)} staged file(s)...")
    for f in staged_files:
        print(f"    - {f}")

    # Detect flake8 executable (virtualenv or system)
    venv_flake8 = Path("./bin/flake8")
    flake8_cmd = str(venv_flake8) if venv_flake8.exists() else "flake8"

    try:
        proc = subprocess.run(
            [flake8_cmd] + staged_files,
            capture_output=True,
            text=True
        )
        if proc.returncode == 0:
            print("\n[✓] Staging Lint Passed! All staged files meet quality guidelines.\n")
            return 0
        else:
            print("\n[✗] Staging Lint Failed! Please fix the following issues before committing:\n")
            print(proc.stdout)
            if proc.stderr:
                print(proc.stderr)
            return 1
    except FileNotFoundError:
        print("[!] Warning: flake8 not found in PATH or venv. Skipping style checks.")
        return 0


if __name__ == "__main__":
    sys.exit(run_staging_lint())
