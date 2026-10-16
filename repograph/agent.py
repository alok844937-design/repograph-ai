"""An investigation agent that uses the graph as its only source of facts.

The agent (Gemini) decides which graph tool to call next, reads the
results, and writes an investigation plan. Every file it recommends is
checked against what the tools actually returned; anything else is
removed and counted as an unsupported claim. The plan is saved as a
Markdown report ready to post on the issue, with a trace of every tool
call.

Usage:
  python -m repograph.agent 24156
"""

import argparse
import json
import os
import re

from repograph import config
from repograph.db import get_graph
from repograph.investigate import investigate, is_candidate, load_issue
from repograph.llm import Gemini, LLMError

MAX_STEPS = 6
MAX_BODY_CHARS = 4000
REPORTS_DIR = "reports"
PATH_IN_TEXT = re.compile(r"(core/[\w/]+\.py|main\.py)")

SYSTEM_PROMPT = """You investigate bug reports for the Oppia codebase.
Your only source of facts is the tool results you are given. Never name a
file, test, commit or issue that did not appear in a tool result.

Each turn, reply with exactly one JSON object, either a tool call:
  {"thought": "<one sentence>", "action": "<tool>", "args": {...}}
or the final answer:
  {"thought": "<one sentence>", "final": {
     "summary": "<2-3 sentences: what is probably wrong and where>",
     "confidence": "high" | "medium" | "low",
     "files": [{"path": "<file>", "why": "<cite the evidence>"}],
     "tests": ["<test file path>"],
     "next_steps": ["<concrete step>"],
     "caveats": ["<what the evidence does not show>"]}}

Tools:
  related_issues {}            parent, sibling and child issues, with the
                               files their fixes changed
  file_context {"path": str}   what a file defines, what imports it, its
                               tests and the past issues its changes fixed
  keyword_search {"query": str}
                               plain keyword search over the code, with no
                               graph; "query" adds your own search words to
                               the issue text; use it when evidence is weak

Rules:
  - The graph investigation is already given. Start from it.
  - If its strength is "weak", call keyword_search before answering, and
    say in the caveats that graph evidence was thin.
  - Use at most 4 tool calls, then answer.
  - Recommend at most 5 files, best first. Prefer files with direct
    evidence (issue text, TODO comment, parent or sibling fix) over files
    reached only through imports or co-changes.
  - Confidence is high only when a file has direct evidence."""


class Tools:
    """Graph tools the agent can call. Each returns a small JSON-able dict."""

    def __init__(self, graph, number, cutoff, text):
        self.graph = graph
        self.number = number
        self.cutoff = cutoff
        self.text = text
        self.seen_paths = set()

    def _rows(self, query, params):
        return self.graph.ro_query(query, params).result_set

    def _note(self, paths):
        self.seen_paths.update(p for p in paths if p)

    def graph_investigation(self):
        result = investigate(self.graph, self.number, top_k=10)
        files = [{
            "path": item["path"],
            "score": item["score"],
            "evidence": [f"[{r['kind']}] {r['detail']}"
                         for r in item["evidence"][:3]],
            "tests": [t["test"] for t in item["tests"]],
        } for item in result["files"]]
        self._note(f["path"] for f in files)
        self._note(t for f in files for t in f["tests"])
        top = files[0]["score"] if files else 0
        strength = "strong" if top >= 3 else "medium" if top >= 2 else "weak"
        return {"strength": strength, "files": files}

    def related_issues(self):
        found = []
        for relation, query in [
            ("parent", "MATCH (:Issue {number: $n})-[:CHILD_OF]->(r:Issue)"),
            ("sibling", "MATCH (:Issue {number: $n})-[:CHILD_OF]->(:Issue)"
                        "<-[:CHILD_OF]-(r:Issue) WHERE r.number <> $n"),
            ("child", "MATCH (r:Issue)-[:CHILD_OF]->(:Issue {number: $n})"),
        ]:
            for number, title, files in self._rows(query + """
                OPTIONAL MATCH (r)<-[:FIXES]-(c:Commit)-[:TOUCHES]->(f:File)
                WHERE c.date < $cutoff
                RETURN r.number, r.title, collect(DISTINCT f.path)
            """, {"n": self.number, "cutoff": self.cutoff}):
                files = sorted(p for p in files if is_candidate(p))
                self._note(files)
                found.append({"relation": relation, "issue": number,
                              "title": title, "fixed_files": files})
        return {"related": found} if found else {
            "related": [], "note": "This issue has no parent or child links."}

    def file_context(self, path):
        exists = self._rows("MATCH (f:File {path: $p}) RETURN f.path",
                            {"p": path})
        if not exists:
            return {"error": f"{path} is not in the graph."}
        defines = [r[0] for r in self._rows("""
            MATCH (:File {path: $p})-[:CONTAINS]->(fn:Function)
            RETURN fn.name ORDER BY fn.line LIMIT 15
        """, {"p": path})]
        importers = self._rows("""
            MATCH (:File {path: $p})<-[:IMPORTS]-(x:File)
            RETURN count(DISTINCT x)
        """, {"p": path})[0][0]
        tests = [r[0] for r in self._rows("""
            MATCH (:File {path: $p})-[:TESTED_BY]->(t:Test) RETURN t.path
        """, {"p": path})]
        past = [{"issue": r[0], "title": r[1], "date": r[2]}
                for r in self._rows("""
            MATCH (:File {path: $p})<-[:TOUCHES]-(c:Commit)-[:FIXES]->(i:Issue)
            WHERE c.date < $cutoff AND i.number <> $n
            RETURN i.number, i.title, c.date ORDER BY c.date DESC LIMIT 5
        """, {"p": path, "cutoff": self.cutoff, "n": self.number})]
        self._note([path] + tests)
        return {"path": path, "defines": defines, "imported_by": importers,
                "tests": tests, "recent_fixes": past}

    def keyword_search(self, query=""):
        if not os.path.isdir(config.OPPIA_REPO):
            return {"error": "Keyword search needs a local Oppia clone, "
                             "which this deployment does not have."}
        from repograph.baseline import KeywordIndex
        fix = self._rows("""
            MATCH (c:Commit)-[:FIXES]->(:Issue {number: $n})
            RETURN c.sha ORDER BY c.date LIMIT 1
        """, {"n": self.number})
        # For an already fixed issue, search the code from before the fix.
        revision = f"{fix[0][0]}^" if fix else None
        # Agent-chosen words like "learner_dashboard" are split the same
        # way identifiers in the code are.
        extra = " ".join(query.replace("_", " ").split())
        hits = KeywordIndex(revision).search(f"{self.text}\n{extra}", top_k=8)
        self._note(p for p, _, _ in hits)
        return {"searched": revision or config.OPPIA_BRANCH,
                "files": [{"path": p, "score": s, "reason": r}
                          for p, s, r in hits]}

    def call(self, action, args):
        if action == "related_issues":
            return self.related_issues()
        if action == "file_context":
            return self.file_context(str(args.get("path", "")))
        if action == "keyword_search":
            return self.keyword_search(str(args.get("query", "")))
        return {"error": f"Unknown tool {action!r}."}


def ground(final, seen_paths):
    """Removes any file or test the tools never returned."""
    removed = []
    files = []
    for item in final.get("files", [])[:5]:
        path = str(item.get("path", ""))
        if path in seen_paths:
            files.append(item)
        else:
            removed.append(path)
    tests = []
    for test in final.get("tests", []):
        (tests if test in seen_paths else removed).append(test)
    # File paths inside free text must be grounded too; unsupported ones
    # are replaced so the report never names a file the graph did not.
    for field in ("summary", "next_steps", "caveats"):
        value = final.get(field, "")
        texts = value if isinstance(value, list) else [value]
        cleaned = []
        for text in texts:
            text = str(text)
            for path in set(PATH_IN_TEXT.findall(text)):
                if path not in seen_paths:
                    removed.append(path)
                    text = text.replace(path, "[unsupported file removed]")
            cleaned.append(text)
        final[field] = cleaned if isinstance(value, list) else cleaned[0]
    final["files"], final["tests"] = files, tests
    final["unsupported_removed"] = sorted(set(removed))
    if not files:
        final["confidence"] = "low"
    return final


def fallback_plan(first, reason):
    """A plan built from graph evidence alone, used if the LLM fails."""
    files = [{"path": f["path"], "why": "; ".join(f["evidence"][:2])}
             for f in first["files"][:5]]
    tests = sorted({t for f in first["files"][:3] for t in f["tests"]})
    return {
        "summary": "Gemini was unavailable, so this plan lists the graph "
                   "investigation as is, without a written analysis.",
        "confidence": {"strong": "high", "medium": "medium"}.get(
            first["strength"], "low"),
        "files": files, "tests": tests,
        "next_steps": ["Open the top file and check the evidence listed."],
        "caveats": [f"LLM step skipped: {reason}"],
    }


def say(message):
    print(message, flush=True)


def run_agent(graph, number, llm=None, log=say):
    title, body, cutoff = load_issue(graph, number)
    text = f"{title}\n{body}"
    tools = Tools(graph, number, cutoff, text)
    trace = []

    first = tools.graph_investigation()
    trace.append({"action": "graph_investigation", "result": first})
    log(f"graph_investigation: strength {first['strength']}, "
        f"{len(first['files'])} files")

    messages = [("user", json.dumps({
        "issue": number, "title": title, "body": body[:MAX_BODY_CHARS],
        "history_cutoff": cutoff, "graph_investigation": first}))]
    final = None
    try:
        llm = llm or Gemini()
        for step in range(MAX_STEPS):
            reply = llm.complete_json(SYSTEM_PROMPT, messages)
            messages.append(("model", json.dumps(reply)))
            if "final" in reply:
                final = reply["final"]
                log(f"final answer after {step} tool calls")
                break
            action, args = reply.get("action", ""), reply.get("args") or {}
            log(f"{action} {json.dumps(args)}  ({reply.get('thought', '')})")
            result = tools.call(action, args)
            trace.append({"action": action, "args": args,
                          "thought": reply.get("thought", ""),
                          "result": result})
            messages.append(("user", json.dumps({"tool_result": result})))
        if final is None:
            messages.append(("user", "Tool budget used up. Give the final "
                                     "answer now as {\"final\": {...}}."))
            final = llm.complete_json(SYSTEM_PROMPT, messages).get("final", {})
    except LLMError as e:
        log(f"Gemini failed, using the graph-only plan.\n{e}")
        final = fallback_plan(first, " ".join(str(e).split())[:200])

    final = ground(final, tools.seen_paths)
    return {"issue": number, "title": title, "history_cutoff": cutoff,
            "model": getattr(llm, "model", None), "plan": final,
            "trace": trace}


def render_report(result):
    """Markdown report, ready to paste as an issue comment."""
    plan = result["plan"]
    lines = [f"### Investigation plan for #{result['issue']}", "",
             plan.get("summary", ""), "",
             f"**Confidence:** {plan.get('confidence', 'low')}", ""]
    if plan.get("files"):
        lines += ["**Files to look at**", ""]
        for i, item in enumerate(plan["files"], start=1):
            lines.append(f"{i}. `{item['path']}`: {item.get('why', '')}")
        lines.append("")
    if plan.get("tests"):
        lines += ["**Tests to run first**", "", "```"]
        for test in plan["tests"]:
            target = test[:-len(".py")].replace("/", ".")
            lines.append("python -m scripts.run_backend_tests "
                         f"--test_target={target}")
        lines += ["```", ""]
    for heading, key in [("Next steps", "next_steps"), ("Caveats", "caveats")]:
        if plan.get(key):
            lines += [f"**{heading}**", ""]
            lines += [f"- {entry}" for entry in plan[key]]
            lines.append("")
    tools_used = ", ".join(step["action"] for step in result["trace"])
    lines += ["---",
              f"Generated by RepoGraph AI from the FalkorDB graph. Tools "
              f"used: {tools_used}. History before {result['history_cutoff']}. "
              f"Unsupported claims removed: "
              f"{len(plan.get('unsupported_removed', []))}."]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("issue", type=int)
    args = parser.parse_args()

    result = run_agent(get_graph(), args.issue)
    os.makedirs(REPORTS_DIR, exist_ok=True)
    base = os.path.join(REPORTS_DIR, f"issue_{args.issue}")
    with open(base + ".md", "w") as f:
        f.write(render_report(result))
    with open(base + ".trace.json", "w") as f:
        json.dump(result, f, indent=2)
    print("\n" + render_report(result))
    print(f"Saved {base}.md and {base}.trace.json")


if __name__ == "__main__":
    main()