"""Rebuilds the whole graph with one command.

Runs the three loaders in order: git history (with a reset), code
structure, then issue text and parent links from GitHub.

Usage:
  python -m repograph.build
"""

import subprocess
import sys

STEPS = [
    ["repograph.ingest_git", "--reset"],
    ["repograph.ingest_code"],
    ["repograph.ingest_issues"],
]


def main():
    for module, *args in STEPS:
        print(f"\n== {module} {' '.join(args)}".rstrip())
        subprocess.run([sys.executable, "-m", module, *args], check=True)
    print("\nGraph rebuilt.")


if __name__ == "__main__":
    main()