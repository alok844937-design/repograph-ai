"""Investigates an Oppia issue through the graph.

Given an issue number, returns the backend files most likely to need a
change, each with the graph evidence that led to it, plus the tests to
run. No LLM is involved: every suggestion comes from a stored edge.

Time rule: only commits merged before the issue's own fix are used, and
the issue's own fix commits are never used. Code structure (imports,
functions, TODO comments) is read from the current develop branch.

Usage:
  python -m repograph.investigate 25307
  python -m repograph.investigate 25307 --json
"""

import argparse
import json
import math
import re
from collections import defaultdict

from repograph.db import get_graph

TOP_K = 10
NO_CUTOFF = "9999-12-31"

# Evidence weights. Direct evidence names a file; expansion follows edges
# from a strongly supported file.
WEIGHTS = {
    "issue_text_path": 3.0,
    "todo_comment": 3.0,
    "issue_text_symbol": 2.0,
    "parent_issue_fix": 2.0,
    "sibling_issue_fix": 1.0,
    "similar_issue_fix": 1.0,
    "route_to_handler": 1.0,
    "imports_1_hop": 1.0,
    "imports_2_hops": 0.5,
    "co_changed": 1.0,
}
# Only these kinds can make a file a starting point for expansion.
SEED_KINDS = {"issue_text_path", "todo_comment", "issue_text_symbol",
              "parent_issue_fix", "sibling_issue_fix"}
EXPANSION_KINDS = {"route_to_handler", "imports_1_hop", "imports_2_hops",
                   "co_changed"}
# Expansion can reorder files with similar direct evidence, but never
# lift a file above one with stronger direct evidence.
EXPANSION_CAP = 1.5
SEED_THRESHOLD = 2.0
MAX_SEEDS = 5
# Files imported by more than this many files are hubs (feconf.py,
# utils.py). They are never used as seeds, because they connect to
# almost everything.
HUB_IN_DEGREE = 100
HUB_FILES = {"main.py"}
MAX_SYMBOL_FILES = 3
MAX_SIMILAR_ISSUES = 3

PATH_PATTERN = re.compile(r"(core/[\w/]+\.py|main\.py)")
SYMBOL_PATTERN = re.compile(
    r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+|[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+)\b")
WORD_PATTERN = re.compile(r"[a-z][a-z0-9]{2,}")
STOPWORDS = set("""
the and for are but not you all any can had her was one our out has have
this that with from they will would there their what about which when make
like just into than then them these some could other more also should
does being been were while where after before such only over very bug
feature request issue error errors describe expected behavior behaviour
steps reproduce screenshots additional context developers please oppia
unable able add adding added page fix fixed fails failing shown show
showing working work correct correctly incorrect currently instead button
click clicking new using used get getting see seen
""".split())


def is_candidate(path):
    """True for non-test backend Python files.
    
    Test helpers under core/tests/ are excluded too: a fix is never
    expected to live there.
    """
    return (path.endswith(".py") and not path.endswith("_test.py")
            and not path.startswith("core/tests/"))


def rows(graph, query, params=None):
    """Runs a read-only query and returns its rows."""
    return graph.ro_query(query, params or {}).result_set


def load_issue(graph, number):
    """Returns the issue's title, body and cutoff date for history."""
    found = rows(graph, """
        MATCH (i:Issue {number: $n})
        OPTIONAL MATCH (c:Commit)-[:FIXES]->(i)
        RETURN i.title, i.body, min(c.date)
    """, {"n": number})
    if not found or found[0][0] is None:
        raise SystemExit(f"Issue #{number} is not in the graph with text.")
    title, body, fixed_on = found[0]
    return title or "", body or "", fixed_on or NO_CUTOFF


def words(text):
    """Returns lowercase content words for similarity scoring."""
    return [w for w in WORD_PATTERN.findall(text.lower()) if w not in STOPWORDS]


class Evidence:
    """Collects evidence per file.

    Each kind of evidence counts once per file, at its strongest value,
    so a file reached by many paths of the same kind is not inflated.
    """

    def __init__(self):
        self.best = defaultdict(dict)
        self.reasons = defaultdict(list)

    def add(self, path, kind, detail, multiplier=1.0):
        if not is_candidate(path):
            return
        value = WEIGHTS[kind] * multiplier
        if value <= 0:
            return
        self.best[path][kind] = max(self.best[path].get(kind, 0.0), value)
        reason = {"kind": kind, "detail": detail, "value": round(value, 2)}
        if reason not in self.reasons[path]:
            self.reasons[path].append(reason)

    def direct(self, path):
        return sum(v for k, v in self.best[path].items()
                   if k not in EXPANSION_KINDS)

    def seed_strength(self, path):
        return sum(v for k, v in self.best[path].items() if k in SEED_KINDS)

    def expansion(self, path):
        return sum(v for k, v in self.best[path].items()
                   if k in EXPANSION_KINDS)

    def score(self, path):
        return self.direct(path) + min(self.expansion(path), EXPANSION_CAP)

    def ranked(self):
        # Ties at the expansion cap are broken by the uncapped expansion,
        # so a file with stronger connections ranks first.
        return sorted(self.best, key=lambda p: (
            -self.score(p), p))

    def sorted_reasons(self, path):
        return sorted(self.reasons[path], key=lambda r: -r["value"])


def add_text_paths(graph, ev, text):
    """Files named directly in the issue text, e.g. in a stack trace."""
    named = sorted(set(PATH_PATTERN.findall(text)))
    if not named:
        return
    for (path,) in rows(graph, """
        MATCH (f:File) WHERE f.path IN $paths RETURN f.path
    """, {"paths": named}):
        ev.add(path, "issue_text_path", f"issue text names {path}")


def add_text_symbols(graph, ev, text):
    """Files defining functions or classes named in the issue text."""
    symbols = sorted(set(SYMBOL_PATTERN.findall(text)))
    if not symbols:
        return
    by_symbol = defaultdict(set)
    for name, path in rows(graph, """
        MATCH (fn:Function)
        WHERE fn.name IN $names
           OR any(s IN $names WHERE fn.name ENDS WITH ('.' + s))
        RETURN fn.name, fn.path
    """, {"names": symbols}):
        short = name.split(".")[-1]
        by_symbol[short if short in symbols else name].add(path)
    for symbol, paths in sorted(by_symbol.items()):
        if len(paths) > MAX_SYMBOL_FILES:
            continue  # Too common to point anywhere specific.
        for path in sorted(paths):
            ev.add(path, "issue_text_symbol", f"defines `{symbol}` named in issue")


def add_todo_mentions(graph, ev, number):
    """Files with a TODO(#N) comment for this issue."""
    for (path,) in rows(graph, """
        MATCH (f:File)-[:MENTIONS]->(:Issue {number: $n}) RETURN f.path
    """, {"n": number}):
        ev.add(path, "todo_comment", f"has a TODO(#{number}) comment")


def add_related_issue_fixes(graph, ev, number, cutoff):
    """Files changed by fixes of the parent issue and sibling issues."""
    for parent, sha, date, path in rows(graph, """
        MATCH (:Issue {number: $n})-[:CHILD_OF]->(p:Issue)
              <-[:FIXES]-(c:Commit)-[:TOUCHES]->(f:File)
        WHERE c.date < $cutoff
        RETURN p.number, c.sha, c.date, f.path
    """, {"n": number, "cutoff": cutoff}):
        ev.add(path, "parent_issue_fix",
               f"changed by {sha[:9]} ({date}) fixing parent #{parent}")
    for sibling, sha, date, path in rows(graph, """
        MATCH (:Issue {number: $n})-[:CHILD_OF]->(:Issue)<-[:CHILD_OF]-(s:Issue)
              <-[:FIXES]-(c:Commit)-[:TOUCHES]->(f:File)
        WHERE c.date < $cutoff AND s.number <> $n
        RETURN s.number, c.sha, c.date, f.path
    """, {"n": number, "cutoff": cutoff}):
        ev.add(path, "sibling_issue_fix",
               f"changed by {sha[:9]} ({date}) fixing sibling #{sibling}")


def add_similar_issue_fixes(graph, ev, number, cutoff, text):
    """Files changed by fixes of past issues with similar titles."""
    query_words = set(words(text))
    if not query_words:
        return
    past = rows(graph, """
        MATCH (i:Issue)<-[:FIXES]-(c:Commit)
        WHERE c.date < $cutoff AND i.number <> $n AND i.title IS NOT NULL
        RETURN DISTINCT i.number, i.title
    """, {"n": number, "cutoff": cutoff})
    doc_freq = defaultdict(int)
    titles = {}
    for other, title in past:
        titles[other] = set(words(title))
        for w in titles[other]:
            doc_freq[w] += 1
    total = max(len(titles), 1)

    def idf(w):
        return math.log((total + 1) / (doc_freq[w] + 1))

    scored = []
    for other, title_words in titles.items():
        shared = query_words & title_words
        if len(shared) < 2:
            continue
        similarity = sum(idf(w) for w in shared) / (
            sum(idf(w) for w in title_words) or 1)
        scored.append((similarity, other, sorted(shared)))
    scored.sort(reverse=True)

    for similarity, other, shared in scored[:MAX_SIMILAR_ISSUES]:
        for sha, date, path in rows(graph, """
            MATCH (:Issue {number: $o})<-[:FIXES]-(c:Commit)-[:TOUCHES]->(f:File)
            WHERE c.date < $cutoff
            RETURN c.sha, c.date, f.path
        """, {"o": other, "cutoff": cutoff}):
            ev.add(path, "similar_issue_fix",
                   f"changed by {sha[:9]} ({date}) fixing similar #{other} "
                   f"(shared words: {', '.join(shared[:4])})",
                   multiplier=min(1.0, similarity * 2))


def import_in_degree(graph, path):
    """Returns how many files import the given file."""
    found = rows(graph, """
        MATCH (:File {path: $p})<-[:IMPORTS]-(x:File) RETURN count(DISTINCT x)
    """, {"p": path})
    return found[0][0] if found else 0


def expand_from_seeds(graph, ev, cutoff):
    """Follows import, route and co-change edges from strong files."""
    seeds = []
    for path in sorted(ev.best, key=lambda p: (-ev.seed_strength(p), p)):
        if ev.seed_strength(path) < SEED_THRESHOLD or len(seeds) >= MAX_SEEDS:
            break
        if path in HUB_FILES or import_in_degree(graph, path) > HUB_IN_DEGREE:
            continue
        seeds.append(path)

    for seed in seeds:
        for path, provenance, hops, in_degree in rows(graph, """
            MATCH p = (:File {path: $s})-[:IMPORTS*1..2]->(f:File)
            WITH f, length(p) AS hops,
                 [e IN relationships(p) | e.provenance] AS provenance
            MATCH (f)<-[:IMPORTS]-(importer:File)
            RETURN f.path, provenance, hops, count(DISTINCT importer)
        """, {"s": seed}):
            # Files imported almost everywhere say little about this issue.
            hub = 1 + math.log10(max(in_degree, 1))
            kind = "imports_1_hop" if hops == 1 else "imports_2_hops"
            ev.add(path, kind,
                   f"imported from {seed} ({' -> '.join(provenance)})",
                   multiplier=1 / hub)
        for constant, handler_path in rows(graph, """
            MATCH (:File {path: $s})-[:USES]->(c:Constant)
                  -[:ROUTES_TO]->(fn:Function)
            RETURN c.name, fn.path
        """, {"s": seed}):
            ev.add(handler_path, "route_to_handler",
                   f"{seed} uses {constant}, routed to a handler here")
        seed_commits = rows(graph, """
            MATCH (:File {path: $s})<-[:TOUCHES]-(c:Commit)
            WHERE c.date < $cutoff RETURN count(DISTINCT c)
        """, {"s": seed, "cutoff": cutoff})[0][0]
        if not seed_commits:
            continue
        for path, together in rows(graph, """
            MATCH (:File {path: $s})<-[:TOUCHES]-(c:Commit)-[:TOUCHES]->(f:File)
            WHERE c.date < $cutoff AND f.path <> $s
            RETURN f.path, count(DISTINCT c)
        """, {"s": seed, "cutoff": cutoff}):
            if together >= 2:
                # Share of the seed's history that also touched this file.
                share = together / seed_commits
                ev.add(path, "co_changed",
                       f"changed in {together} of {seed}'s {seed_commits} "
                       f"commits", multiplier=min(1.0, share * 10))


def tests_for(graph, paths):
    """Returns the tests linked to each file, with their provenance."""
    found = defaultdict(list)
    for path, test, provenance in rows(graph, """
        MATCH (f:File)-[e:TESTED_BY]->(t:Test)
        WHERE f.path IN $paths
        RETURN f.path, t.path, e.provenance
    """, {"paths": paths}):
        found[path].append({"test": test, "provenance": provenance})
    return found


def test_command(test_path):
    """Returns Oppia's command for running one backend test file."""
    target = test_path[:-len(".py")].replace("/", ".")
    return f"python -m scripts.run_backend_tests --test_target={target}"


def investigate(graph, number, use_history=True, top_k=TOP_K):
    """Returns the ranked investigation for an issue as a dict."""
    title, body, cutoff = load_issue(graph, number)
    text = f"{title}\n{body}"
    ev = Evidence()
    add_text_paths(graph, ev, text)
    add_text_symbols(graph, ev, text)
    add_todo_mentions(graph, ev, number)
    if use_history:
        add_related_issue_fixes(graph, ev, number, cutoff)
        add_similar_issue_fixes(graph, ev, number, cutoff, text)
    expand_from_seeds(graph, ev, cutoff if use_history else "0000-00-00")

    top = ev.ranked()[:top_k]
    tests = tests_for(graph, top)
    return {
        "issue": number,
        "title": title,
        "history_cutoff": cutoff,
        "files": [{
            "path": path,
            "score": round(ev.score(path), 2),
            "evidence": ev.sorted_reasons(path),
            "tests": tests.get(path, []),
        } for path in top],
    }


def print_report(result):
    """Prints a readable investigation plan."""
    print(f"Issue #{result['issue']}: {result['title']}")
    print(f"History used: commits before {result['history_cutoff']}\n")
    if not result["files"]:
        print("No graph evidence found for this issue.")
        return
    commands = []
    for rank, item in enumerate(result["files"], start=1):
        print(f"{rank}. {item['path']}  (score {item['score']})")
        for reason in item["evidence"][:4]:
            print(f"     - [{reason['kind']}] {reason['detail']}")
        if len(item["evidence"]) > 4:
            print(f"     - ... {len(item['evidence']) - 4} more")
        for test in item["tests"]:
            print(f"     test: {test['test']} ({test['provenance']})")
            if rank <= 3:
                commands.append(test_command(test["test"]))
    if commands:
        print("\nRun these tests first:")
        for command in dict.fromkeys(commands):
            print(f"  {command}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("issue", type=int)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = investigate(get_graph(), args.issue)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_report(result)


if __name__ == "__main__":
    main()