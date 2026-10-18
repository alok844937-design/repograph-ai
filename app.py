"""RepoGraph AI web app.

Run locally:  streamlit run app.py
On Streamlit Community Cloud the settings come from the app's secrets
instead of .env.
"""

import json
import os

import streamlit as st

SETTINGS = ["FALKOR_HOST", "FALKOR_PORT", "FALKOR_USERNAME", "FALKOR_PASSWORD",
            "GEMINI_API_KEY", "GEMINI_MODEL", "OPPIA_REPO", "ALLOW_WRITES"]
try:
    for key in SETTINGS:
        if key in st.secrets and key not in os.environ:
            os.environ[key] = str(st.secrets[key])
except Exception:  # No secrets file when running locally with .env.
    pass

from repograph.agent import render_report, run_agent  # noqa: E402
from repograph.db import get_graph  # noqa: E402
from repograph.investigate import investigate  # noqa: E402
from repograph.triage import save_plan  # noqa: E402
from repograph.viz import evidence_dot  # noqa: E402

EXAMPLES = {
    24156: "sibling issue fix (graph wins)",
    25307: "parent fix + TODO comment",
    22726: "file named in the issue",
    23330: "no graph evidence (graph loses)",
}
EVALUATION_FILE = os.path.join("results", "evaluation.json")
ISSUE_URL = "https://github.com/oppia/oppia/issues/{}"

st.set_page_config(page_title="RepoGraph AI", layout="wide")


@st.cache_resource
def graph():
    return get_graph()


@st.cache_data(ttl=3600, show_spinner="Walking the graph...")
def investigation(number):
    return investigate(graph(), number, top_k=10)


@st.cache_data(ttl=3600, show_spinner=False)
def issue_list():
    """Every issue in the graph that has text, newest first."""
    rows = graph().ro_query("""
        MATCH (i:Issue) WHERE i.title IS NOT NULL
        RETURN i.number, i.title ORDER BY i.number DESC
    """).result_set
    return {number: title for number, title in rows}


@st.cache_data
def evaluation():
    if not os.path.exists(EVALUATION_FILE):
        return {}
    with open(EVALUATION_FILE) as f:
        return {item["issue"]: item for item in json.load(f)}


TRIAGE_QUERY = """
MATCH (r:Investigation)-[:FOR]->(i:Issue)
OPTIONAL MATCH (r)-[s:SUGGESTS]->(f:File)
WITH r, i, s, f ORDER BY s.rank
RETURN i.number, i.title, i.state, r.confidence, r.model, r.created,
       r.summary, r.unsupported_removed, collect(f.path), collect(s.why)
ORDER BY r.created DESC
"""
BADGE = {"high": "🟢", "medium": "🟡", "low": "⚪"}


@st.cache_data(ttl=60, show_spinner=False)
def triage_rows():
    return graph().ro_query(TRIAGE_QUERY).result_set


def show_triage_board():
    st.subheader("Triage board")
    st.caption("The agent investigated these issues and wrote each plan "
               "back into FalkorDB as an Investigation node. Open issues "
               "have no fix yet, so this is the tool working on today's "
               "backlog. Plans are suggestions for a human to check; "
               "nothing is posted to GitHub.")
    rows = triage_rows()
    if not rows:
        st.info("No plans yet. Run `python -m repograph.triage --limit 3`.")
        return
    counts = {level: sum(1 for row in rows if row[3] == level)
              for level in BADGE}
    cols = st.columns(4)
    cols[0].metric("Issues triaged", len(rows))
    for col, level in zip(cols[1:], BADGE):
        col.metric(f"{level.title()} confidence", counts[level])
    for (number, title, state, confidence, model, created, summary,
         removed, paths, whys) in rows:
        badge = BADGE.get(confidence, "⚪")
        status = "open" if state == "open" else "closed (evaluation issue)"
        with st.expander(f"{badge} #{number}: {title} · {confidence} "
                         "confidence"):
            st.markdown(f"[Open on GitHub]({ISSUE_URL.format(number)}) · "
                        f"{status} · planned by {model} on {created}")
            st.write(summary)
            for rank, (path, why) in enumerate(zip(paths, whys), start=1):
                st.markdown(f"{rank}. `{path}`: {why}")
            st.caption(f"Unsupported claims removed: {removed}. Search "
                       f"#{number} in the sidebar for its evidence graph.")
    with st.expander("Cypher behind this board"):
        st.code(TRIAGE_QUERY.strip(), language="cypher")


# How much each kind of evidence can be trusted, and where it comes from.
# An edge records a link; it does not prove two files are related in meaning.
EVIDENCE_INFO = {
    "issue_text_path": ("direct", "issue text",
                        "The issue names this file, e.g. in a stack trace."),
    "todo_comment": ("direct", "code comment",
                     "A developer left TODO(#issue) in this file."),
    "issue_text_symbol": ("direct", "issue text + parsed code",
                          "A name in the issue matches a function here; "
                          "common names can match by chance."),
    "parent_issue_fix": ("related", "git history + GitHub parent link",
                         "The parent issue's fix changed this file. Same "
                         "area, not necessarily the same bug."),
    "sibling_issue_fix": ("related", "git history + GitHub parent link",
                          "A sibling issue's fix changed this file. Same "
                          "area, not necessarily the same bug."),
    "similar_issue_fix": ("weak", "title similarity + git history",
                          "Only shared title words link the two issues."),
    "route_to_handler": ("structural", "route table in main.py",
                         "A URL constant used by a strong file routes here."),
    "imports_1_hop": ("structural", "imports (parsed code or registry map)",
                      "Linked by an import, which shows a dependency, not "
                      "that this file is involved."),
    "imports_2_hops": ("structural", "imports (parsed code or registry map)",
                       "Two imports away; a loose link."),
    "co_changed": ("weak", "git co-change statistics",
                   "Often changed in the same commits; a correlation."),
}
TIER_ICON = {"direct": "🟢", "related": "🟡", "structural": "🔵",
             "weak": "⚪"}


def mark(path, fixed):
    return " ✅ changed by the real fix" if path in fixed else ""


st.title("RepoGraph AI")
st.caption("Give it an Oppia issue. It walks a FalkorDB graph of Oppia's "
           "history and code, and returns the files to investigate with the "
           "evidence path for each one.")

with st.sidebar:
    view = st.radio("View", ["Investigate an issue", "Triage board"])
    st.divider()
    st.header("Issue")
    choice = st.radio("Examples", list(EXAMPLES),
                      format_func=lambda n: f"#{n}: {EXAMPLES[n]}")
    issues = issue_list()
    picked = st.selectbox(
        f"Or search all {len(issues)} issues in the graph",
        [None] + list(issues),
        format_func=lambda n: "Type a number or title..." if n is None
        else f"#{n}: {issues[n][:70]}")
    number = picked or choice
    st.divider()
    st.markdown("History is cut at the issue's own fix date, so the graph "
                "never sees the answer. Issue comments are never loaded.")

if view == "Triage board":
    show_triage_board()
    st.stop()

try:
    result = investigation(number)
except SystemExit as e:
    st.error(str(e))
    st.stop()

evaluated = evaluation().get(number)
fixed = set(evaluated["fixed"]) if evaluated else set()

st.subheader(f"#{number}: {result['title']}")
st.markdown(f"[Open on GitHub]({ISSUE_URL.format(number)}) · history used: "
            f"commits before **{result['history_cutoff']}**")

tabs = st.tabs(["Investigation", "Evidence graph", "Graph on vs off",
                "Agent", "Evaluation"])

with tabs[0]:
    if not result["files"]:
        st.warning("The graph found no evidence for this issue. The agent "
                   "tab can fall back to keyword search.")
    if fixed:
        st.caption("✅ marks files the real fix changed. The investigation "
                   "did not see that fix.")
    st.caption("Evidence: 🟢 direct · 🟡 related issue · 🔵 structural "
               "(imports, routes) · ⚪ weak (co-change, title words). An "
               "edge records a link; it is not proof the files are related "
               "in meaning.")
    for rank, item in enumerate(result["files"], start=1):
        with st.expander(f"{rank}. {item['path']} · score {item['score']}"
                         f"{mark(item['path'], fixed)}", expanded=rank <= 3):
            for reason in item["evidence"][:6]:
                tier, source, caveat = EVIDENCE_INFO.get(
                    reason["kind"], ("weak", "graph", ""))
                st.markdown(f"- {TIER_ICON[tier]} **{tier}** . {source}: "
                            f"{reason['detail']}")
                st.caption(f"{reason['kind']} . {caveat}")
            for test in item["tests"]:
                target = test["test"][:-len(".py")].replace("/", ".")
                st.code("python -m scripts.run_backend_tests "
                        f"--test_target={target}", language="bash")
                st.caption(f"Test link: {test['provenance']}")

with tabs[1]:
    if result["files"]:
        options = ["Top 5 files"] + [item["path"] for item in result["files"]]
        left, right = st.columns([3, 2])
        focus = left.selectbox("Show the evidence path for", options)
        weak = right.checkbox("Also show co-changes and 2-hop imports")
        paths = None if focus == options[0] else {focus}
        st.caption("Every edge here is a stored relationship in FalkorDB. "
                   "Read it left to right: from the issue to the files.")
        st.graphviz_chart(evidence_dot(result, paths=paths, show_weak=weak))
    else:
        st.info("No evidence paths to draw for this issue.")

with tabs[2]:
    if not evaluated:
        st.info("The side-by-side comparison is available for the 21 "
                "evaluated issues, such as the examples in the sidebar.")
    else:
        st.markdown(f"Real fix changed: "
                    + ", ".join(f"`{p}`" for p in evaluated["fixed"]))
        left, right = st.columns(2)
        for column, method, label in [
                (left, "keyword", "Graph off: keyword search"),
                (right, "graph + keyword", "Graph on: keyword + graph")]:
            res = evaluated["results"][method]
            with column:
                st.markdown(f"**{label}**")
                st.metric("First fixed file at rank",
                          res["first_rank"] or "not found")
                for rank, path in enumerate(res["top"], start=1):
                    st.markdown(f"{rank}. `{path}`{mark(path, fixed)}")

with tabs[3]:
    st.markdown("The agent (Gemini) reads the graph investigation, decides "
                "which graph tools to call next, and writes a plan. Any "
                "file it names that no tool returned is removed.")
    key = f"agent_{number}"
    if st.button("Run agent", type="primary"):
        with st.status("Agent working...", expanded=True) as status:
            st.session_state[key] = run_agent(graph(), number, log=st.write)
            status.update(label="Agent finished", state="complete")
    if key in st.session_state:
        agent_result = st.session_state[key]
        report = render_report(agent_result)
        st.markdown(report)
        st.download_button("Download report (Markdown)", report,
                           file_name=f"issue_{number}_plan.md")
        if os.environ.get("ALLOW_WRITES") != "1":
            st.caption("Saving to the graph is turned off on the public "
                       "demo. The triage agent writes from the command line.")
        elif st.button("Save this plan to the graph"):
            save_plan(graph(), agent_result)
            triage_rows.clear()
            st.success("Saved as an Investigation node. It now shows on "
                       "the triage board.")
        with st.expander("Tool calls (trace)"):
            st.json(agent_result["trace"])

with tabs[4]:
    path = os.path.join("results", "evaluation.md")
    if os.path.exists(path):
        with open(path) as f:
            st.markdown(f.read())