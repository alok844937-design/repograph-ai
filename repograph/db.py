"""FalkorDB connection helpers."""

from falkordb import FalkorDB

from repograph import config


def get_graph():
    """Returns the RepoGraph graph handle."""
    client = FalkorDB(
        host=config.FALKOR_HOST,
        port=config.FALKOR_PORT,
        username=config.FALKOR_USERNAME,
        password=config.FALKOR_PASSWORD,
    )
    return client.select_graph(config.GRAPH_NAME)


def create_index(graph, label, prop):
    """Creates an index, ignoring the error if it already exists."""
    try:
        graph.query(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
    except Exception as e:  # FalkorDB raises if the index exists.
        if "already indexed" not in str(e).lower():
            print(f"Index on {label}.{prop}: {e}")


def write_batches(graph, query, rows, size=500):
    """Runs an UNWIND query over rows in fixed-size batches."""
    for start in range(0, len(rows), size):
        graph.query(query, {"rows": rows[start:start + size]})
