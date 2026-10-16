"""Sanity checks for issue text and parent links."""

from repograph.db import get_graph

CHECKS = [
    ("Issue lookup status", """
        MATCH (i:Issue)
        RETURN i.fetched, count(i) ORDER BY i.fetched
    """),
    ("Parent links", """
        MATCH (:Issue)-[e:CHILD_OF]->(:Issue)
        RETURN e.provenance, count(e)
    """),
    ("Issue #25307 and its parent", """
        MATCH (i:Issue {number: 25307})
        OPTIONAL MATCH (i)-[:CHILD_OF]->(p:Issue)
        RETURN i.title, i.created, p.number, p.title
    """),
    ("Start of the #25307 body (no comments are stored)", """
        MATCH (i:Issue {number: 25307})
        RETURN substring(i.body, 0, 200)
    """),
]


def main():
    graph = get_graph()
    for title, query in CHECKS:
        print(f"\n{title}:")
        for row in graph.ro_query(query).result_set:
            print("  " + " | ".join(str(value) for value in row))


if __name__ == "__main__":
    main()