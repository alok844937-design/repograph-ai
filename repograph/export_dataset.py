"""Exports the evaluation dataset as a frozen JSON file.

For every evaluated issue it saves the issue text exactly as it is in the
graph, the commit that fixed it, and the files that fix changed (the
labels). With this file the evaluation can be checked or reused without
the graph or the GitHub API.

Usage:
  python -m repograph.export_dataset
Writes data/evaluation_dataset.json.
"""

import json
import os

from repograph.db import get_graph
from repograph.evaluate import HELD_OUT, fix_info

OUTPUT = os.path.join("data", "evaluation_dataset.json")
LARGE = os.path.join("results", "evaluation_large.json")


def main():
    issues = [(n, group, "held_out") for group, numbers in HELD_OUT.items()
              for n in numbers]
    if os.path.exists(LARGE):
        with open(LARGE) as f:
            issues += [(item["issue"], None, "large") for item in json.load(f)]

    graph = get_graph()
    records = []
    for number, group, split in issues:
        title, body, created = graph.ro_query("""
            MATCH (i:Issue {number: $n}) RETURN i.title, i.body, i.created
        """, {"n": number}).result_set[0]
        sha, fixed = fix_info(graph, number)
        date = graph.ro_query("""
            MATCH (c:Commit {sha: $s}) RETURN c.date
        """, {"s": sha}).result_set[0][0]
        records.append({
            "issue": number, "split": split, "group": group,
            "url": f"https://github.com/oppia/oppia/issues/{number}",
            "title": title, "body": body, "created": created,
            "fix_commit": sha, "fix_date": date,
            "keyword_search_revision": f"{sha}^",
            "fixed_files": sorted(fixed),
        })

    os.makedirs("data", exist_ok=True)
    with open(OUTPUT, "w") as f:
        json.dump({"description": "RepoGraph AI evaluation issues. "
                   "fixed_files are the non-test backend files changed by "
                   "the fix commits. Issue comments are not included.",
                   "issues": records}, f, indent=1)
    print(f"Wrote {len(records)} issues to {OUTPUT}")


if __name__ == "__main__":
    main()