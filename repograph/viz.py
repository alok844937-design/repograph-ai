"""Draws the evidence behind an investigation as a Graphviz (DOT) graph.

Each evidence line the investigation produced is turned back into the
graph path it came from, so the picture shows exactly the edges that
gave a file its score. The picture reads left to right: the issue on
the left, the files it leads to on the right.
"""

import re

PARENT = re.compile(r"changed by (\w+) \(([\d-]+)\) fixing parent #(\d+)")
SIBLING = re.compile(r"changed by (\w+) \(([\d-]+)\) fixing sibling #(\d+)")
SIMILAR = re.compile(r"changed by (\w+) \(([\d-]+)\) fixing similar #(\d+)")
IMPORTED = re.compile(r"imported from (\S+) \((.+)\)")
ROUTED = re.compile(r"(\S+) uses (\w+), routed to a handler here")
CO_CHANGED = re.compile(r"changed in (\d+) of (\S+)'s (\d+) commits")

# Hidden unless asked for: they add many lines and little explanation.
WEAK_KINDS = {"co_changed", "imports_2_hops"}

STYLE = {
    "issue": 'shape=box style="rounded,filled" fillcolor="#f6d7b0"',
    "commit": 'shape=box style="rounded,filled" fillcolor="#e3d3f2"',
    "file": 'shape=box style="filled" fillcolor="#e8f1f8" color="#7d9fbd"',
    "top": 'shape=box style="filled,bold" fillcolor="#9fc9ec" penwidth=2',
    "constant": 'shape=box style="rounded,filled" fillcolor="#f3c6cf"',
}


def _short(path):
    return path.replace("core/", "", 1)


class Dot:
    def __init__(self):
        self.nodes = {}
        self.edges = set()

    def node(self, key, label, kind):
        if key not in self.nodes or kind == "top":
            self.nodes[key] = (label, kind)
        return key

    def edge(self, a, b, label, dashed=False, back=False):
        """Adds a -> b. With back=True the arrow is drawn pointing at a,
        which keeps the layout flowing left to right."""
        self.edges.add((a, b, label, dashed, back))

    def source(self):
        lines = ["digraph evidence {", "rankdir=LR;", "nodesep=0.35;",
                 'node [fontname="Helvetica" fontsize=11];',
                 'edge [fontname="Helvetica" fontsize=9 color="#555555"];']
        for key, (label, kind) in sorted(self.nodes.items()):
            lines.append(f'"{key}" [label="{label}" {STYLE[kind]}];')
        for a, b, label, dashed, back in sorted(self.edges):
            extra = ' style="dashed"' if dashed else ""
            extra += " dir=back" if back else ""
            lines.append(f'"{a}" -> "{b}" [label="{label}"{extra}];')
        lines.append("}")
        return "\n".join(lines)


def _fix_path(dot, issue, file_node, sha, date, other, relation):
    """Issue -> related issue <-FIXES- commit -TOUCHES-> file."""
    other_node = dot.node(f"issue:{other}", f"Issue #{other}", "issue")
    commit = dot.node(f"commit:{sha}", f"commit {sha[:7]}\\n{date}",
                      "commit")
    if relation == "parent":
        dot.edge(issue, other_node, "CHILD_OF")
    elif relation == "sibling":
        parent = dot.node("parent", "shared parent issue", "issue")
        dot.edge(issue, parent, "CHILD_OF")
        dot.edge(parent, other_node, "CHILD_OF", back=True)
    else:
        dot.edge(issue, other_node, "similar title", dashed=True)
    dot.edge(other_node, commit, "FIXES", back=True)
    dot.edge(commit, file_node, "TOUCHES")


def evidence_dot(result, paths=None, show_weak=False, max_files=5):
    """Returns DOT source for an investigate() result.

    paths: the files to draw (default: the top max_files).
    show_weak: also draw co-changes and 2-hop imports.
    """
    dot = Dot()
    issue = dot.node(f"issue:{result['issue']}",
                     f"Issue #{result['issue']}", "issue")
    items = [item for item in result["files"]
             if paths is None or item["path"] in paths]
    if paths is None:
        items = items[:max_files]
    for item in items:
        path = item["path"]
        file_node = dot.node(f"file:{path}", _short(path), "top")
        drawn = False
        for reason in item["evidence"]:
            kind, detail = reason["kind"], reason["detail"]
            if kind in WEAK_KINDS and not show_weak:
                continue
            drawn = True
            if kind == "issue_text_path":
                dot.edge(issue, file_node, "path named in issue")
            elif kind == "issue_text_symbol":
                dot.edge(issue, file_node, "names a function defined here")
            elif kind == "todo_comment":
                dot.edge(issue, file_node, "MENTIONS (TODO comment)",
                         back=True)
            elif kind in ("parent_issue_fix", "sibling_issue_fix",
                          "similar_issue_fix"):
                relation = kind.split("_")[0]
                pattern = {"parent": PARENT, "sibling": SIBLING,
                           "similar": SIMILAR}[relation]
                match = pattern.search(detail)
                if match:
                    _fix_path(dot, issue, file_node, *match.groups(),
                              relation)
            elif kind in ("imports_1_hop", "imports_2_hops"):
                match = IMPORTED.search(detail)
                if match:
                    seed = dot.node(f"file:{match.group(1)}",
                                    _short(match.group(1)), "file")
                    hops = "1 hop" if kind == "imports_1_hop" else "2 hops"
                    dot.edge(seed, file_node, f"IMPORTS ({hops})")
            elif kind == "route_to_handler":
                match = ROUTED.search(detail)
                if match:
                    seed = dot.node(f"file:{match.group(1)}",
                                    _short(match.group(1)), "file")
                    constant = dot.node(f"const:{match.group(2)}",
                                        match.group(2), "constant")
                    dot.edge(seed, constant, "USES")
                    dot.edge(constant, file_node, "ROUTES_TO")
            elif kind == "co_changed":
                match = CO_CHANGED.search(detail)
                if match:
                    together, seed_path, _ = match.groups()
                    seed = dot.node(f"file:{seed_path}", _short(seed_path),
                                    "file")
                    dot.edge(seed, file_node, f"co-changed x{together}",
                             dashed=True)
        if not drawn:
            dot.edge(issue, file_node, "weak evidence only", dashed=True)
    return dot.source()