"""Smoke test for a fresh setup: connection, graph contents, one investigation.

Usage:
  python -m repograph.smoke

Exits with status 1 if any check fails.
"""

import os
import sys
import time

from repograph.db import get_graph
from repograph.investigate import investigate

# A known case: the fix for #25307 changed core/controllers/tasks.py, and
# the graph reaches it through a parent-issue fix and a TODO comment.
KNOWN_ISSUE = 25307
KNOWN_FILE = "core/controllers/tasks.py"
EXPECTED_LABELS = ["Commit", "File", "Function", "Issue", "Test", "Constant"]


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{': ' + detail if detail else ''}")
    return ok


def main():
    results = []
    try:
        graph = get_graph()
        graph.ro_query("RETURN 1")
        results.append(check("connect to FalkorDB", True))
    except Exception as e:
        check("connect to FalkorDB", False, f"{type(e).__name__}: {e}")
        print("Check FALKOR_HOST, FALKOR_PORT, FALKOR_USERNAME and "
              "FALKOR_PASSWORD in .env.")
        sys.exit(1)

    counts = dict(graph.ro_query(
        "MATCH (n) RETURN labels(n)[0], count(n)").result_set)
    missing = [label for label in EXPECTED_LABELS if not counts.get(label)]
    results.append(check(
        "graph has every node type", not missing,
        ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        + (f" (missing: {', '.join(missing)})" if missing else "")))

    start = time.time()
    result = investigate(graph, KNOWN_ISSUE)
    seconds = time.time() - start
    top = [item["path"] for item in result["files"][:5]]
    results.append(check(
        f"investigate #{KNOWN_ISSUE} finds {KNOWN_FILE} in the top 5",
        KNOWN_FILE in top, f"{seconds:.1f}s, top 5: {', '.join(top)}"))

    has_key = bool(os.environ.get("GEMINI_API_KEY"))
    print(f"[INFO] Gemini agent: "
          f"{'key found' if has_key else 'no key, graph-only plans'}")
    repo = os.environ.get("OPPIA_REPO", "")
    clone = bool(repo) and os.path.isdir(repo)
    print(f"[INFO] keyword search: "
          f"{'Oppia clone found' if clone else 'no Oppia clone, disabled'}")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()