"""Compares graph investigation with keyword search on held-out issues.

For each held-out issue:
  - The answer is the set of non-test backend files changed by the
    commits that fixed it.
  - Keyword search ("graph off") runs on the code just before the fix.
  - Graph investigation uses only history from before the fix.
  - "Graph + keyword" adds the graph's evidence score to keyword search,
    so the same system can be measured with the graph on and off.

Metrics, per issue and per group:
  hit@5     at least one fixed file is in the top 5
  recall@5  share of fixed files that are in the top 5

Writes results/evaluation.md and results/evaluation.json.

Usage:
  python -m repograph.evaluate
"""

import json
import os

from repograph.baseline import KeywordIndex
from repograph.db import get_graph
from repograph.investigate import investigate, is_candidate, load_issue

HELD_OUT = {
    "A1: stack trace names the fixed file": [25008, 23033, 22277, 21878],
    "A2: stack trace names a different file": [21883, 20148, 18371],
    "B: no file named": [23330, 25307, 24588, 24315, 22999, 24470, 24156,
                         23440, 22479, 23492, 22109, 21841],
    "C: file named without a trace": [22726, 25518],
}
METHODS = ["keyword", "graph", "graph + keyword"]
K = 5
RESULTS_DIR = "results"


def fix_info(graph, number):
    """Returns (first fix sha, fixed candidate files) for an issue."""
    found = graph.ro_query("""
        MATCH (c:Commit)-[:FIXES]->(:Issue {number: $n})
        OPTIONAL MATCH (c)-[:TOUCHES]->(f:File)
        RETURN c.sha, c.date, collect(f.path)
        ORDER BY c.date
    """, {"n": number}).result_set
    if not found:
        return None, set()
    first_sha = found[0][0]
    fixed = {p for _, _, paths in found for p in paths if is_candidate(p)}
    return first_sha, fixed


def combine(keyword_results, graph_result):
    """Adds graph evidence to keyword ranking.

    Keyword scores are scaled so the top file gets 2.0, the
    same size as one strong piece of graph evidence. Files named in the
    issue text get 3.0, matching the graph's weight for the same signal.
    The scale uses the best keyword match only, never a named file's
    boosted score, which would flatten every other keyword score to zero.
    """
    keyword_only = [s for _, s, _ in keyword_results if s < 1000]
    top = max(keyword_only) if keyword_only else 0
    scores = {}
    for path, score, _ in keyword_results:
        if score >= 1000:
            scores[path] = 3.0 + 2.0 * (score - 1000) / (top or 1)
        else:
            scores[path] = 2.0 * score / (top or 1)
    for item in graph_result["files"]:
        scores[item["path"]] = scores.get(item["path"], 0) + item["score"]
    return sorted(scores, key=lambda p: (-scores[p], p))


def metrics(ranked, fixed):
    top = ranked[:K]
    found = [p for p in fixed if p in top]
    first = next((i + 1 for i, p in enumerate(ranked) if p in fixed), None)
    return {
        "hit": bool(found),
        "recall": len(found) / len(fixed),
        "first_rank": first,
        "top": top,
    }


def main():
    graph = get_graph()
    per_issue = []
    for group, numbers in HELD_OUT.items():
        for number in numbers:
            first_sha, fixed = fix_info(graph, number)
            if not fixed:
                print(f"#{number}: skipped, fix changed no backend files")
                continue
            title, body, _ = load_issue(graph, number)
            text = f"{title}\n{body}"
            print(f"#{number}: evaluating ({len(fixed)} fixed files)")

            keyword = KeywordIndex(f"{first_sha}^").search(text, top_k=50)
            graph_result = investigate(graph, number, top_k=50)
            rankings = {
                "keyword": [p for p, _, _ in keyword],
                "graph": [item["path"] for item in graph_result["files"]],
                "graph + keyword": combine(keyword, graph_result),
            }
            per_issue.append({
                "issue": number,
                "group": group,
                "title": title,
                "fixed": sorted(fixed),
                "results": {m: metrics(rankings[m], fixed) for m in METHODS},
            })

    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(os.path.join(RESULTS_DIR, "evaluation.json"), "w") as f:
        json.dump(per_issue, f, indent=2)
    report = render(per_issue)
    with open(os.path.join(RESULTS_DIR, "evaluation.md"), "w") as f:
        f.write(report)
    print("\n" + report)


def summary_rows(items):
    rows = []
    for method in METHODS:
        hits = sum(i["results"][method]["hit"] for i in items)
        recall = sum(i["results"][method]["recall"] for i in items)
        rows.append(f"| {method} | {hits}/{len(items)} | "
                    f"{recall / max(len(items), 1):.0%} |")
    return rows


def render(per_issue):
    lines = ["# Evaluation", "",
             f"{len(per_issue)} held-out issues. hit@{K}: at least one fixed "
             f"file in the top {K}. recall@{K}: share of fixed files in the "
             f"top {K}.", "", "## All issues", "",
             f"| Method | hit@{K} | recall@{K} |", "|---|---|---|"]
    lines += summary_rows(per_issue)
    for group in HELD_OUT:
        items = [i for i in per_issue if i["group"] == group]
        if not items:
            continue
        lines += ["", f"## {group} ({len(items)} issues)", "",
                  f"| Method | hit@{K} | recall@{K} |", "|---|---|---|"]
        lines += summary_rows(items)
    lines += ["", "## Per issue", "",
              "First rank of a fixed file (- means not in the list).", "",
              "| Issue | Group | Fixed files | keyword | graph | graph + keyword |",
              "|---|---|---|---|---|---|"]
    for item in per_issue:
        ranks = [str(item["results"][m]["first_rank"] or "-") for m in METHODS]
        lines.append(f"| #{item['issue']} | {item['group'].split(':')[0]} | "
                     f"{len(item['fixed'])} | " + " | ".join(ranks) + " |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()