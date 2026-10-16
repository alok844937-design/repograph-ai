"""Loads issue titles, bodies and parent links from GitHub.

Only the issue body is stored. Comments are never fetched, because they
often name the fix and would leak the answer during evaluation.

Edge: (Issue)-[:CHILD_OF {provenance: 'github_parent'}]->(Issue)
"""

import json
import urllib.request

from repograph import config
from repograph.db import get_graph, write_batches

GRAPHQL_URL = "https://api.github.com/graphql"
BATCH_SIZE = 40
MAX_BODY_CHARS = 20000
MAX_ROUNDS = 3


def run_query(query):
    """Sends one GraphQL query to GitHub and returns the parsed JSON."""
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=json.dumps({"query": query}).encode("utf-8"),
        headers={
            "Authorization": f"bearer {config.GITHUB_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def fetch_issues(numbers):
    """Returns issue records for the given numbers.

    Numbers that are pull requests, or do not exist, come back as null
    and are left out.
    """
    fields = "number title body createdAt closedAt parent { number }"
    parts = [f"i{n}: issue(number: {n}) {{ {fields} }}" for n in numbers]
    query = ('query { repository(owner: "oppia", name: "oppia") { '
             + " ".join(parts) + " } }")
    data = run_query(query)
    # A pull request number gives a NOT_FOUND error for that alias only.
    # Any other error (bad token, unknown field) is a real problem.
    for error in data.get("errors", []):
        if error.get("type") != "NOT_FOUND":
            raise SystemExit(f"GitHub API error: {error.get('message')}")
    repository = (data.get("data") or {}).get("repository") or {}
    return [issue for issue in repository.values() if issue]


def unfetched_numbers(graph):
    """Returns numbers of Issue nodes not yet looked up on GitHub."""
    rows = graph.ro_query("""
        MATCH (i:Issue) WHERE i.fetched IS NULL
        RETURN i.number ORDER BY i.number
    """).result_set
    return [row[0] for row in rows]


def main():
    if not config.GITHUB_TOKEN:
        raise SystemExit("GITHUB_TOKEN is missing from .env")

    graph = get_graph()
    for round_number in range(1, MAX_ROUNDS + 1):
        numbers = unfetched_numbers(graph)
        if not numbers:
            break
        print(f"Round {round_number}: looking up {len(numbers)} issues.")

        found_rows, parent_rows = [], []
        for start in range(0, len(numbers), BATCH_SIZE):
            batch = numbers[start:start + BATCH_SIZE]
            for issue in fetch_issues(batch):
                found_rows.append({
                    "number": issue["number"],
                    "title": issue["title"],
                    "body": (issue["body"] or "")[:MAX_BODY_CHARS],
                    "created": issue["createdAt"][:10],
                    "closed": (issue["closedAt"] or "")[:10],
                })
                if issue.get("parent"):
                    parent_rows.append({"child": issue["number"],
                                        "parent": issue["parent"]["number"]})
            print(f"  {min(start + BATCH_SIZE, len(numbers))}/{len(numbers)}")

        found = {row["number"] for row in found_rows}
        missing_rows = [{"number": n} for n in numbers if n not in found]

        write_batches(graph, """
            UNWIND $rows AS r
            MATCH (i:Issue {number: r.number})
            SET i.title = r.title, i.body = r.body,
                i.created = r.created, i.closed = r.closed, i.fetched = true
        """, found_rows, size=100)
        # Pull requests and deleted issues are marked so they are not retried.
        write_batches(graph, """
            UNWIND $rows AS r
            MATCH (i:Issue {number: r.number})
            SET i.fetched = false
        """, missing_rows)
        write_batches(graph, """
            UNWIND $rows AS r
            MATCH (c:Issue {number: r.child})
            MERGE (p:Issue {number: r.parent})
            MERGE (c)-[e:CHILD_OF]->(p)
            SET e.provenance = 'github_parent'
        """, parent_rows)

        print(f"  found {len(found_rows)}, not issues {len(missing_rows)}, "
              f"parent links {len(parent_rows)}")


if __name__ == "__main__":
    main()