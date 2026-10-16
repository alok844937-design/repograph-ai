"""Sanity checks for the code part of the graph, using issue #25307."""

from repograph.db import get_graph

CHECKS = [
    ("Node counts", """
        MATCH (n)
        RETURN labels(n)[0] AS label, count(n) AS total
        ORDER BY label
    """),
    ("Files that mention issue #25307 in a TODO comment", """
        MATCH (f:File)-[e:MENTIONS]->(:Issue {number: 25307})
        RETURN f.path, e.provenance
    """),
    ("Handler routed from TASK_URL_RETRY_FAILED_EMAIL", """
        MATCH (c:Constant {name: 'TASK_URL_RETRY_FAILED_EMAIL'})
              -[e:ROUTES_TO]->(fn:Function)
        RETURN fn.id, e.provenance
    """),
    ("Files that use TASK_URL_RETRY_FAILED_EMAIL", """
        MATCH (f:File)-[:USES]->(:Constant {name: 'TASK_URL_RETRY_FAILED_EMAIL'})
        RETURN f.path ORDER BY f.path
    """),
    ("email_manager.py to platform email modules in up to 2 import hops", """
        MATCH p = (:File {path: 'core/domain/email_manager.py'})
                  -[:IMPORTS*1..2]->(f:File)
        WHERE f.path STARTS WITH 'core/platform/email/'
        RETURN DISTINCT f.path, [e IN relationships(p) | e.provenance]
    """),
    ("Tests for tasks.py and email_manager.py", """
        MATCH (f:File)-[e:TESTED_BY]->(t:Test)
        WHERE f.path IN ['core/controllers/tasks.py',
                         'core/domain/email_manager.py']
        RETURN f.path, t.path, e.provenance
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