"""A larger, automatic evaluation on every recently fixed issue.

The 21 hand-grouped issues in evaluate.py are few. This script uses a rule
fixed before it was run, with no hand picking:

  every issue whose first fix commit is on or after --since (default
  2025-01-01) and changed at least one non-test backend file, except the
  21 held-out issues and the issues used while building the tool.

Everything else matches evaluate.py: keyword search on the code just
before the fix, graph history cut at the fix date, same metrics.

One extra method is defined here, before this script was ever run:

  graph + keyword (gated)  adds the graph score only when the graph has
                           direct evidence (top score >= SEED_THRESHOLD,
                           the existing constant in investigate.py);
                           otherwise it is plain keyword search.

Issues are also split by whether the graph found direct evidence. That
split is decided from the graph result alone, never from the answer.

Results are saved after every issue, so an interrupted run resumes.
Writes results/evaluation_large.md and results/evaluation_large.json.

Usage:
  python -m repograph.evaluate_large --limit 150
"""

import argparse
import json
import os

from repograph.baseline import KeywordIndex
from repograph.db import get_graph
from repograph.evaluate import (HELD_OUT, K, RESULTS_DIR, combine, fix_info,
                                metrics)
from repograph.investigate import SEED_THRESHOLD, investigate, load_issue

# Issues looked at while the scoring was being built.
DEVELOPMENT_ISSUES = {18276, 24159}
METHODS = ["keyword", "graph", "graph + keyword", "graph + keyword (gated)"]
JSON_PATH = os.path.join(RESULTS_DIR, "evaluation_large.json")
MD_PATH = os.path.join(RESULTS_DIR, "evaluation_large.md")


def selected_issues(graph, since):
    excluded = DEVELOPMENT_ISSUES | {n for ns in HELD_OUT.values() for n in ns}
    rows = graph.ro_query("""
        MATCH (c:Commit)-[:FIXES]->(i:Issue) WHERE i.title IS NOT NULL
        WITH i, min(c.date) AS fixed_on
        WHERE fixed_on >= $since
        RETURN i.number ORDER BY i.number
    """, {"since": since}).result_set
    return [n for (n,) in rows if n not in excluded]


def evaluate_issue(graph, number):
    first_sha, fixed = fix_info(graph, number)
    if not fixed:
        return None
    title, body, _ = load_issue(graph, number)
    text = f"{title}\n{body}"
    keyword = KeywordIndex(f"{first_sha}^").search(text, top_k=50)
    graph_result = investigate(graph, number, top_k=50)
    top_score = graph_result["files"][0]["score"] if graph_result["files"] else 0
    has_evidence = top_score >= SEED_THRESHOLD
    keyword_ranked = [p for p, _, _ in keyword]
    combined = combine(keyword, graph_result)
    rankings = {
        "keyword": keyword_ranked,
        "graph": [item["path"] for item in graph_result["files"]],
        "graph + keyword": combined,
        "graph + keyword (gated)": combined if has_evidence else keyword_ranked,
    }
    return {"issue": number, "title": title, "fixed": sorted(fixed),
            "graph_evidence": has_evidence,
            "results": {m: metrics(rankings[m], fixed) for m in METHODS}}


def table(items):
    lines = [f"| Method | hit@{K} | recall@{K} |", "|---|---|---|"]
    for method in METHODS:
        hits = sum(i["results"][method]["hit"] for i in items)
        recall = sum(i["results"][method]["recall"] for i in items)
        lines.append(f"| {method} | {hits}/{len(items)} "
                     f"({hits / max(len(items), 1):.0%}) | "
                     f"{recall / max(len(items), 1):.0%} |")
    return lines


def render(items, since):
    with_ev = [i for i in items if i["graph_evidence"]]
    without = [i for i in items if not i["graph_evidence"]]
    helped = sum(1 for i in items
                 if i["results"]["graph + keyword (gated)"]["hit"]
                 and not i["results"]["keyword"]["hit"])
    hurt = sum(1 for i in items
               if i["results"]["keyword"]["hit"]
               and not i["results"]["graph + keyword (gated)"]["hit"])
    lines = ["# Larger evaluation", "",
             f"{len(items)} issues: every issue first fixed on or after "
             f"{since} whose fix changed a non-test backend file, except the "
             "21 held-out issues and the development issues. No hand "
             "picking. Same rules as the main evaluation: keyword search on "
             "the code before the fix, graph history cut at the fix date.",
             "", "## All issues", ""] + table(items)
    lines += ["", f"## Graph found direct evidence ({len(with_ev)} issues)",
              "", "Decided from the graph result only (top score >= "
              f"{SEED_THRESHOLD}), never from the answer.", ""] + table(with_ev)
    lines += ["", f"## Graph found no direct evidence ({len(without)} issues)",
              ""] + table(without)
    lines += ["", "## Graph on vs off, issue by issue (gated method)", "",
              f"- Graph turned a miss into a hit@{K}: {helped} issues",
              f"- Graph turned a hit@{K} into a miss: {hurt} issues", ""]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="2025-01-01")
    parser.add_argument("--limit", type=int, default=150)
    args = parser.parse_args()

    graph = get_graph()
    done = {}
    if os.path.exists(JSON_PATH):
        with open(JSON_PATH) as f:
            done = {item["issue"]: item for item in json.load(f)}
    numbers = selected_issues(graph, args.since)[:args.limit]
    print(f"{len(numbers)} issues selected, {len(done)} already done",
          flush=True)
    os.makedirs(RESULTS_DIR, exist_ok=True)
    for count, number in enumerate(numbers, start=1):
        if number in done:
            continue
        try:
            item = evaluate_issue(graph, number)
        except Exception as e:  # A dropped connection must not stop the run.
            print(f"#{number}: skipped ({type(e).__name__}), will retry on "
                  "the next run", flush=True)
            graph = get_graph()
            continue
        if item is None:
            continue
        done[number] = item
        print(f"[{count}/{len(numbers)}] #{number}: "
              f"evidence={'yes' if item['graph_evidence'] else 'no '} "
              f"keyword={'hit' if item['results']['keyword']['hit'] else 'miss'} "
              f"gated={'hit' if item['results']['graph + keyword (gated)']['hit'] else 'miss'}",
              flush=True)
        with open(JSON_PATH, "w") as f:
            json.dump(sorted(done.values(), key=lambda i: i["issue"]), f,
                      indent=1)

    items = [done[n] for n in numbers if n in done]
    report = render(items, args.since)
    with open(MD_PATH, "w") as f:
        f.write(report)
    print("\n" + report)


if __name__ == "__main__":
    main()