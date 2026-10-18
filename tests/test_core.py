"""Unit tests for the parts that need no database or network.

Run:  python -m pytest -q
"""

from repograph.agent import ground
from repograph.evaluate import metrics
from repograph.investigate import (EXPANSION_CAP, PATH_PATTERN, Evidence,
                                   is_candidate)
from repograph.viz import evidence_dot


def test_is_candidate_keeps_backend_code_only():
    assert is_candidate("core/domain/email_services.py")
    assert is_candidate("main.py")
    assert not is_candidate("core/domain/email_services_test.py")
    assert not is_candidate("core/tests/test_utils.py")
    assert not is_candidate("core/templates/pages/app.component.ts")


def test_path_pattern_finds_paths_in_stack_traces():
    text = 'File "/opt/oppia/core/controllers/tasks.py", line 50, in post'
    assert PATH_PATTERN.findall(text) == ["core/controllers/tasks.py"]


def test_same_kind_of_evidence_counts_once():
    ev = Evidence()
    ev.add("core/a.py", "imports_1_hop", "imported from core/x.py (python_ast)")
    ev.add("core/a.py", "imports_1_hop", "imported from core/y.py (python_ast)")
    assert ev.score("core/a.py") == 1.0


def test_expansion_never_beats_direct_evidence():
    ev = Evidence()
    ev.add("core/direct.py", "todo_comment", "has a TODO(#1) comment")
    for kind in ("imports_1_hop", "co_changed", "route_to_handler"):
        ev.add("core/connected.py", kind, "structural link")
    assert ev.score("core/connected.py") == EXPANSION_CAP
    assert ev.ranked()[0] == "core/direct.py"


def test_test_files_never_get_evidence():
    ev = Evidence()
    ev.add("core/a_test.py", "issue_text_path", "issue text names it")
    assert ev.ranked() == []


def test_ground_removes_files_no_tool_returned():
    final = {"summary": "Look at core/real.py and core/invented.py.",
             "confidence": "high",
             "files": [{"path": "core/real.py", "why": "TODO comment"},
                       {"path": "core/invented.py", "why": "guess"}],
             "tests": ["core/real_test.py", "core/fake_test.py"],
             "next_steps": [], "caveats": []}
    out = ground(final, {"core/real.py", "core/real_test.py"})
    assert [f["path"] for f in out["files"]] == ["core/real.py"]
    assert out["tests"] == ["core/real_test.py"]
    assert "core/invented.py" not in out["summary"]
    assert set(out["unsupported_removed"]) == {"core/invented.py",
                                               "core/fake_test.py"}


def test_ground_lowers_confidence_when_nothing_is_supported():
    final = {"summary": "", "confidence": "high",
             "files": [{"path": "core/invented.py", "why": "guess"}],
             "tests": [], "next_steps": [], "caveats": []}
    assert ground(final, set())["confidence"] == "low"


def test_metrics_hit_and_recall():
    result = metrics(["a", "b", "c", "d", "e", "f"], {"b", "f"})
    assert result["hit"] is True
    assert result["recall"] == 0.5
    assert result["first_rank"] == 2


def test_evidence_graph_draws_the_parent_fix_path():
    result = {"issue": 25307, "files": [{
        "path": "core/controllers/tasks.py",
        "evidence": [
            {"kind": "parent_issue_fix",
             "detail": "changed by 2405253fe (2026-03-21) fixing parent #18276"},
            {"kind": "todo_comment", "detail": "has a TODO(#25307) comment"},
        ]}]}
    dot = evidence_dot(result)
    assert '"issue:25307" -> "issue:18276" [label="CHILD_OF"]' in dot
    assert '"commit:2405253fe" -> "file:core/controllers/tasks.py"' in dot
    assert "MENTIONS (TODO comment)" in dot