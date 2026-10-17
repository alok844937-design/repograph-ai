"""Loads Oppia issues that are open right now, for the triage agent.

These issues have no fix yet, so the investigation uses the full history
(there is nothing to hide). Only the title, body, labels and parent link
are stored; comments are never fetched.

Usage:
  python -m repograph.ingest_open --limit 50
"""

import argparse
import json
import urllib.request

from repograph import config
from repograph.db import get_graph, write_batches
from repograph.ingest_issues import GRAPHQL_URL, MAX_BODY_CHARS

SEARCH = "repo:oppia/oppia is:issue is:open sort:created-desc"
QUERY = """
query($q: String!, $n: Int!) {
  search(query: $q, type: ISSUE, first: $n) {
    nodes {
      ... on Issue {
        number title body createdAt
        parent { number }
        labels(first: 10) { nodes { name } }
      }
    }
  }
}"""


def fetch_open(limit, extra=""):
    """Returns the newest open issues, optionally narrowed by extra
    GitHub search terms such as 'label:"good first issue"'."""
    payload = {"query": QUERY,
               "variables": {"q": f"{SEARCH} {extra}".strip(),
                             "n": min(limit, 100)}}
    request = urllib.request.Request(
        GRAPHQL_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"bearer {config.GITHUB_TOKEN}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.load(response)
    if data.get("errors"):
        raise SystemExit(f"GitHub API error: {data['errors'][0]['message']}")
    return [node for node in data["data"]["search"]["nodes"] if node]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--search", default="",
                        help="extra GitHub search terms")
    args = parser.parse_args()

    issues = fetch_open(args.limit, args.search)
    rows = [{
        "number": issue["number"],
        "title": issue["title"],
        "body": (issue["body"] or "")[:MAX_BODY_CHARS],
        "created": issue["createdAt"][:10],
        "labels": [label["name"] for label in issue["labels"]["nodes"]],
        "parent": (issue.get("parent") or {}).get("number"),
    } for issue in issues]

    graph = get_graph()
    write_batches(graph, """
        UNWIND $rows AS r
        MERGE (i:Issue {number: r.number})
        SET i.title = r.title, i.body = r.body, i.created = r.created,
            i.labels = r.labels, i.state = 'open', i.fetched = true
    """, rows)
    write_batches(graph, """
        UNWIND $rows AS r
        MATCH (i:Issue {number: r.number})
        MERGE (p:Issue {number: r.parent})
        MERGE (i)-[e:CHILD_OF]->(p)
        SET e.provenance = 'github_parent'
    """, [r for r in rows if r["parent"]])

    with_parent = sum(1 for r in rows if r["parent"])
    print(f"Open issues loaded: {len(rows)} ({with_parent} with a parent)")
    for r in rows[:5]:
        print(f"  #{r['number']}: {r['title'][:70]}")


if __name__ == "__main__":
    main()