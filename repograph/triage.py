"""Triage agent: investigates open issues and writes its plans into the graph.

For each open issue it runs the investigation agent and stores the plan:

  (:Investigation)-[:FOR]->(:Issue)
  (:Investigation)-[:SUGGESTS {rank, why, provenance: 'agent'}]->(:File)

These nodes are kept apart from the evidence: investigate.py never reads
Investigation or SUGGESTS, so an agent's output can never become evidence
for a later investigation. Nothing is posted to GitHub; a human reads the
plans on the triage board and decides.

Usage:
  python -m repograph.triage --limit 3          # with Gemini
  python -m repograph.triage --limit 30 --no-llm
"""

import argparse
import datetime
import time

from repograph.agent import run_agent
from repograph.db import get_graph
from repograph.llm import LLMError


class NoLLM:
    """Stands in for Gemini when --no-llm is given."""
    model = None

    def complete_json(self, system, messages):
        raise LLMError("LLM turned off with --no-llm")


def save_plan(graph, result):
    """Replaces any earlier plan for the issue with this one."""
    plan = result["plan"]
    number = result["issue"]
    graph.query("""
        MATCH (r:Investigation)-[:FOR]->(:Issue {number: $n})
        DETACH DELETE r
    """, {"n": number})
    graph.query("""
        MATCH (i:Issue {number: $n})
        CREATE (r:Investigation {issue: $n, created: $created, model: $model,
                confidence: $confidence, summary: $summary,
                unsupported_removed: $removed, tools: $tools})-[:FOR]->(i)
        WITH r
        UNWIND $files AS f
        MATCH (file:File {path: f.path})
        CREATE (r)-[:SUGGESTS {rank: f.rank, why: f.why,
                               provenance: 'agent'}]->(file)
    """, {
        "n": number,
        "created": datetime.datetime.now().isoformat(timespec="minutes"),
        "model": result.get("model") or "graph only",
        "confidence": plan.get("confidence", "low"),
        "summary": str(plan.get("summary", "")),
        "removed": len(plan.get("unsupported_removed", [])),
        "tools": [step["action"] for step in result["trace"]],
        "files": [{"path": item["path"], "rank": rank,
                   "why": str(item.get("why", ""))}
                  for rank, item in enumerate(plan.get("files", []), 1)],
    })


def open_issues(graph, limit, redo):
    """Open issues with text, newest first; skips triaged ones unless redo."""
    return [row[0] for row in graph.ro_query("""
        MATCH (i:Issue {state: 'open'}) WHERE i.title IS NOT NULL
        OPTIONAL MATCH (r:Investigation)-[:FOR]->(i)
        WITH i, count(r) AS done
        WHERE $redo OR done = 0
        RETURN i.number ORDER BY i.number DESC LIMIT $limit
    """, {"limit": limit, "redo": redo}).result_set]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--no-llm", action="store_true",
                        help="graph-only plans, no Gemini calls")
    parser.add_argument("--redo", action="store_true",
                        help="also re-triage issues that already have a plan")
    parser.add_argument("--pause", type=float, default=5.0,
                        help="seconds between issues, to respect Gemini limits")
    args = parser.parse_args()

    graph = get_graph()
    llm = NoLLM() if args.no_llm else None
    numbers = open_issues(graph, args.limit, args.redo)
    print(f"Triaging {len(numbers)} open issues\n", flush=True)
    for done, number in enumerate(numbers, start=1):
        print(f"--- #{number} ({done}/{len(numbers)})", flush=True)
        try:
            result = run_agent(graph, number, llm=llm,
                           log=lambda m: print(f"    {m}", flush=True))
            save_plan(graph, result)
        except Exception as e: # A dropped connection must not stop the run.
            print(f"    skipped: {type(e).__name__}: {str(e)[:120]}",
                  flush=True)
            graph = get_graph() # Reconnect for the next issue.
            continue
        plan = result["plan"]
        top = plan["files"][0]["path"] if plan.get("files") else "no files"
        print(f"    saved: {plan.get('confidence')} confidence, top: {top}",
              flush=True)
        if not args.no_llm and done < len(numbers):
            time.sleep(args.pause)


if __name__ == "__main__":
    main()