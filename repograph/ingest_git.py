"""Loads Commit, Issue and File nodes from Oppia's git history.

Edges created:
  (Commit)-[:FIXES {provenance: 'git_commit'}]->(Issue)
  (Commit)-[:TOUCHES {provenance: 'git_commit'}]->(File)

All history is loaded. The time rule for held-out issues (only commits
merged before an issue's fix) is applied at query time, not here.
"""

import argparse
import re
import subprocess

from repograph import config
from repograph.db import create_index, get_graph, write_batches

RECORD_SEP = "\x1e"
FIELD_SEP = "\x1f"

# Matches "Fix #123", "Fixes #123", "Fixed #123" at the start of a subject.
FIX_PATTERN = re.compile(r"^\s*fix(?:es|ed)?\s*#(\d+)\b", re.IGNORECASE)
PART_OF_PATTERN = re.compile(r"part\s+of", re.IGNORECASE)


def is_backend_python(path):
    """True for backend Python files that belong in the graph."""
    return path.endswith(".py") and (path.startswith("core/") or path == "main.py")


def read_commits():
    """Returns a list of commits with sha, date, subject and files."""
    log_format = f"{RECORD_SEP}%H{FIELD_SEP}%cs{FIELD_SEP}%s"
    output = subprocess.run(
        [
            "git", "-C", config.OPPIA_REPO, "log", config.OPPIA_BRANCH,
            "--no-merges", f"--since={config.HISTORY_SINCE}",
            "--name-only", f"--format={log_format}",
        ],
        capture_output=True, text=True, check=True,
    ).stdout

    commits = []
    for record in output.split(RECORD_SEP)[1:]:
        lines = record.strip().split("\n")
        sha, date, subject = lines[0].split(FIELD_SEP, 2)
        files = [f for f in lines[1:] if f and is_backend_python(f)]
        commits.append(
            {"sha": sha, "date": date, "subject": subject, "files": files}
        )
    return commits


def fixed_issue(subject):
    """Returns the issue number a commit fully fixes, or None."""
    if PART_OF_PATTERN.search(subject):
        return None
    match = FIX_PATTERN.match(subject)
    return int(match.group(1)) if match else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reset", action="store_true", help="delete the graph first"
    )
    args = parser.parse_args()

    graph = get_graph()
    if args.reset:
        try:
            graph.delete()
            print("Deleted existing graph.")
        except Exception:
            print("No existing graph to delete.")
        graph = get_graph()

    create_index(graph, "Commit", "sha")
    create_index(graph, "Issue", "number")
    create_index(graph, "File", "path")

    commits = read_commits()
    print(f"Read {len(commits)} commits since {config.HISTORY_SINCE}.")

    commit_rows = [
        {"sha": c["sha"], "date": c["date"], "subject": c["subject"]}
        for c in commits
    ]
    fix_rows = []
    touch_rows = []
    for c in commits:
        issue = fixed_issue(c["subject"])
        if issue is not None:
            fix_rows.append({"sha": c["sha"], "issue": issue})
        for path in c["files"]:
            touch_rows.append({"sha": c["sha"], "path": path})

    write_batches(graph, """
        UNWIND $rows AS r
        MERGE (c:Commit {sha: r.sha})
        SET c.date = r.date, c.subject = r.subject
    """, commit_rows)

    write_batches(graph, """
        UNWIND $rows AS r
        MATCH (c:Commit {sha: r.sha})
        MERGE (i:Issue {number: r.issue})
        MERGE (c)-[e:FIXES]->(i)
        SET e.provenance = 'git_commit'
    """, fix_rows)

    write_batches(graph, """
        UNWIND $rows AS r
        MATCH (c:Commit {sha: r.sha})
        MERGE (f:File {path: r.path})
        MERGE (c)-[e:TOUCHES]->(f)
        SET e.provenance = 'git_commit'
    """, touch_rows)

    print(f"Wrote {len(commit_rows)} commits, {len(fix_rows)} FIXES, "
          f"{len(touch_rows)} TOUCHES.")


if __name__ == "__main__":
    main()
