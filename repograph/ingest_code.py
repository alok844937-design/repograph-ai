"""Parses Oppia's backend Python code into the graph.

Reads files from config.OPPIA_BRANCH through git, so the checked-out
working branch of the Oppia clone is never used.

Nodes: File, Function, Constant, Test, Issue
Edges and provenance:
  (File)-[:IMPORTS {provenance: 'python_ast'}]->(File)
  (File)-[:IMPORTS {provenance: 'registry_mapping'}]->(File)
  (File)-[:CONTAINS {provenance: 'python_ast'}]->(Function)
  (File)-[:DEFINES {provenance: 'python_ast'}]->(Constant)
  (File)-[:USES {provenance: 'python_ast'}]->(Constant)
  (Constant)-[:ROUTES_TO {provenance: 'route_table'}]->(Function)
  (File)-[:MENTIONS {provenance: 'todo_comment'}]->(Issue)
  (File)-[:TESTED_BY {provenance: 'test_import' | 'naming_convention'}]->(Test)
"""

import argparse
import ast
import re
import subprocess

from repograph import config
from repograph.db import create_index, get_graph, write_batches

FECONF_PATH = "core/feconf.py"
MODELS_PATH = "core/platform/models.py"
MAIN_PATH = "main.py"
TODO_PATTERN = re.compile(r"TODO\(#(\d+)\)")
CONSTANT_NAME = re.compile(r"^[A-Z][A-Z0-9_]+$")


def git(*args):
    """Runs a git command against the Oppia clone and returns stdout."""
    return subprocess.run(
        ["git", "-C", config.OPPIA_REPO, *args],
        capture_output=True, text=True, check=True,
    ).stdout


def list_backend_files():
    """Returns backend Python file paths on the configured branch."""
    paths = git("ls-tree", "-r", "--name-only", config.OPPIA_BRANCH).split("\n")
    return sorted(
        p for p in paths
        if p.endswith(".py")
        and (p == MAIN_PATH or (p.startswith("core/")
                                and not p.startswith("core/templates/")))
    )


def read_file(path):
    """Returns the text of a file on the configured branch."""
    return git("show", f"{config.OPPIA_BRANCH}:{path}")


def module_to_path(module, known_paths):
    """Maps a dotted module name to a repo file path, or None."""
    as_file = module.replace(".", "/") + ".py"
    if as_file in known_paths:
        return as_file
    as_package = module.replace(".", "/") + "/__init__.py"
    if as_package in known_paths:
        return as_package
    return None


def resolve_imports(tree, known_paths):
    """Returns {local_name: path} for internal modules a file imports."""
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                path = module_to_path(name.name, known_paths)
                if path:
                    aliases[name.asname or name.name] = path
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for name in node.names:
                # "from core.domain import email_manager" imports a module.
                path = module_to_path(f"{node.module}.{name.name}", known_paths)
                if path is None:
                    # "from core.utils import foo" imports a name from a module.
                    path = module_to_path(node.module, known_paths)
                if path:
                    aliases[name.asname or name.name] = path
    return aliases


def read_registry_mapping(known_paths):
    """Maps each models.Registry.import_*() method to the files it loads.

    Read from the imports inside each import_* method of models.py, so
    the mapping comes from the code itself, not from a guess.
    """
    tree = ast.parse(read_file(MODELS_PATH))
    mapping = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.FunctionDef) and node.name.startswith("import_")
                and node.name != "import_models"):
            targets = set()
            for inner in ast.walk(node):
                if isinstance(inner, ast.ImportFrom) and inner.module:
                    for name in inner.names:
                        path = module_to_path(f"{inner.module}.{name.name}",
                                              known_paths)
                        if path:
                            targets.add(path)
            if targets:
                mapping.setdefault(node.name, set()).update(targets)
    return mapping


def registry_targets(tree, registry, known_paths):
    """Returns (target_path, method) pairs for models.Registry.import_*() calls.

    import_models() is resolved from its arguments: models.Names.EMAIL
    maps to core/storage/email/gae_models.py. Other import_*() methods
    use the mapping read from models.py.
    """
    pairs = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr.startswith("import_")
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "Registry"):
            continue
        method = node.func.attr
        if method == "import_models":
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Attribute)
                        and isinstance(inner.value, ast.Attribute)
                        and inner.value.attr == "Names"):
                    path = f"core/storage/{inner.attr.lower()}/gae_models.py"
                    if path in known_paths:
                        pairs.add((path, method))
        else:
            for path in registry.get(method, ()):
                pairs.add((path, method))
    return pairs


def definitions(tree, path):
    """Returns Function rows for top-level classes, functions and methods."""
    rows = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            rows.append({"id": f"{path}::{node.name}", "name": node.name,
                         "path": path, "line": node.lineno})
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        qualname = f"{node.name}.{item.name}"
                        rows.append({"id": f"{path}::{qualname}",
                                     "name": qualname, "path": path,
                                     "line": item.lineno})
    return rows


def feconf_constants(tree):
    """Returns UPPER_CASE names assigned at module level in feconf.py."""
    names = set()
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and CONSTANT_NAME.match(target.id):
                names.add(target.id)
    return names


def feconf_uses(tree, aliases, constants):
    """Returns feconf constant names a file reads as feconf.NAME."""
    feconf_names = {name for name, path in aliases.items() if path == FECONF_PATH}
    used = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in feconf_names
                and node.attr in constants):
            used.add(node.attr)
    return used


def routes(tree, aliases, constants):
    """Returns (constant, handler_id) pairs from main.py's routing table."""
    feconf_names = {name for name, path in aliases.items() if path == FECONF_PATH}
    pairs = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "get_redirect_route" and len(node.args) >= 2):
            continue
        url_arg, handler_arg = node.args[0], node.args[1]
        constant = None
        for inner in ast.walk(url_arg):
            if (isinstance(inner, ast.Attribute)
                    and isinstance(inner.value, ast.Name)
                    and inner.value.id in feconf_names
                    and inner.attr in constants):
                constant = inner.attr
                break
        if (constant and isinstance(handler_arg, ast.Attribute)
                and isinstance(handler_arg.value, ast.Name)
                and handler_arg.value.id in aliases):
            handler_path = aliases[handler_arg.value.id]
            pairs.append((constant, f"{handler_path}::{handler_arg.attr}"))
    return pairs


def build_rows():
    """Parses every backend file and returns rows for each edge type."""
    paths = list_backend_files()
    known = set(paths)
    registry = read_registry_mapping(known)
    feconf_tree = ast.parse(read_file(FECONF_PATH))
    constants = feconf_constants(feconf_tree)

    rows = {key: [] for key in [
        "files", "imports", "registry", "functions", "uses", "routes",
        "mentions", "tests"]}
    rows["constants"] = [{"name": name} for name in sorted(constants)]
    imports_by_file = {}
    failed = []

    for path in paths:
        text = read_file(path)
        rows["files"].append({"path": path, "is_test": path.endswith("_test.py")})
        for issue in sorted({int(n) for n in TODO_PATTERN.findall(text)}):
            rows["mentions"].append({"path": path, "issue": issue})
        try:
            tree = ast.parse(text)
        except SyntaxError:
            failed.append(path)
            continue

        aliases = resolve_imports(tree, known)
        targets = {t for t in aliases.values() if t != path}
        imports_by_file[path] = targets
        rows["imports"] += [{"src": path, "dst": t} for t in sorted(targets)]
        for target, method in sorted(registry_targets(tree, registry, known)):
            if target != path:
                rows["registry"].append(
                    {"src": path, "dst": target, "via": method})
        rows["functions"] += definitions(tree, path)
        rows["uses"] += [{"path": path, "name": n}
                         for n in sorted(feconf_uses(tree, aliases, constants))]
        if path == MAIN_PATH:
            rows["routes"] = [{"name": c, "handler": h}
                              for c, h in routes(tree, aliases, constants)]

    for path in paths:
        if not path.endswith("_test.py"):
            continue
        module = path[:-len("_test.py")] + ".py"
        if module not in known:
            continue
        verified = module in imports_by_file.get(path, set())
        rows["tests"].append({
            "module": module, "test": path,
            "provenance": "test_import" if verified else "naming_convention",
        })

    return rows, failed


def write_rows(graph, rows):
    """Writes all parsed rows to FalkorDB."""
    create_index(graph, "File", "path")
    create_index(graph, "Function", "id")
    create_index(graph, "Constant", "name")
    create_index(graph, "Test", "path")
    create_index(graph, "Issue", "number")

    write_batches(graph, """
        UNWIND $rows AS r
        MERGE (f:File {path: r.path}) SET f.is_test = r.is_test
    """, rows["files"])
    write_batches(graph, """
        UNWIND $rows AS r
        MATCH (a:File {path: r.src}) MATCH (b:File {path: r.dst})
        MERGE (a)-[e:IMPORTS {provenance: 'python_ast'}]->(b)
    """, rows["imports"])
    write_batches(graph, """
        UNWIND $rows AS r
        MATCH (a:File {path: r.src}) MATCH (b:File {path: r.dst})
        MERGE (a)-[e:IMPORTS {provenance: 'registry_mapping'}]->(b)
        SET e.via = r.via
    """, rows["registry"])
    write_batches(graph, """
        UNWIND $rows AS r
        MATCH (f:File {path: r.path})
        MERGE (fn:Function {id: r.id})
        SET fn.name = r.name, fn.path = r.path, fn.line = r.line
        MERGE (f)-[e:CONTAINS {provenance: 'python_ast'}]->(fn)
    """, rows["functions"])
    write_batches(graph, """
        UNWIND $rows AS r
        MATCH (f:File {path: 'core/feconf.py'})
        MERGE (c:Constant {name: r.name})
        MERGE (f)-[e:DEFINES {provenance: 'python_ast'}]->(c)
    """, rows["constants"])
    write_batches(graph, """
        UNWIND $rows AS r
        MATCH (f:File {path: r.path}) MATCH (c:Constant {name: r.name})
        MERGE (f)-[e:USES {provenance: 'python_ast'}]->(c)
    """, rows["uses"])
    write_batches(graph, """
        UNWIND $rows AS r
        MATCH (c:Constant {name: r.name}) MATCH (fn:Function {id: r.handler})
        MERGE (c)-[e:ROUTES_TO {provenance: 'route_table'}]->(fn)
    """, rows["routes"])
    write_batches(graph, """
        UNWIND $rows AS r
        MATCH (f:File {path: r.path})
        MERGE (i:Issue {number: r.issue})
        MERGE (f)-[e:MENTIONS {provenance: 'todo_comment'}]->(i)
    """, rows["mentions"])
    write_batches(graph, """
        UNWIND $rows AS r
        MATCH (f:File {path: r.module})
        MERGE (t:Test {path: r.test})
        MERGE (f)-[e:TESTED_BY]->(t)
        SET e.provenance = r.provenance
    """, rows["tests"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="parse and print counts without writing")
    args = parser.parse_args()

    rows, failed = build_rows()
    for key, value in rows.items():
        print(f"{key}: {len(value)}")
    verified = sum(1 for r in rows["tests"] if r["provenance"] == "test_import")
    print(f"tests verified by import: {verified} of {len(rows['tests'])}")
    if failed:
        print(f"Could not parse {len(failed)} files: {failed[:5]}")

    if not args.dry_run:
        write_rows(get_graph(), rows)
        print("Written to FalkorDB.")


if __name__ == "__main__":
    main()