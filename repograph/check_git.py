"""Sanity checks for the git history part of the graph."""

from repograph.db import get_graph


def main():
    graph = get_graph()

    for label in ["Commit", "Issue", "File"]:
        count = graph.query(f"MATCH (n:{label}) RETURN count(n)").result_set[0][0]
        print(f"{label}: {count}")

    print("\nFiles touched by the commit that fixed #18276:")
    rows = graph.ro_query("""
        MATCH (c:Commit)-[:FIXES]->(:Issue {number: 18276})
        MATCH (c)-[:TOUCHES]->(f:File)
        RETURN c.sha, c.date, f.path
        ORDER BY f.path
    """).result_set
    for sha, date, path in rows:
        print(f"  {sha[:9]} {date} {path}")


if __name__ == "__main__":
    main()
