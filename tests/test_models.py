"""Tests for the shared finding and report models."""
from pathlib import Path

from git_wombat.models import Finding, RepoReport, Status


def test_finding_defaults():
    f = Finding(branch="feat/x", status=Status.NOT_PUSHED)
    assert f.detail == "" and f.emoji == "" and f.safe_to_delete is False


def test_repo_report_is_empty_by_default():
    r = RepoReport(path=Path("/tmp/r"))
    assert r.findings == [] and r.worktrees == [] and r.skipped_reason is None


def test_status_values_are_exactly_the_documented_set():
    assert {s.name for s in Status} == {
        "OK",
        "NOT_PUSHED",
        "NOT_MERGED",
        "DIVERGED",
        "UPSTREAM_DELETED",
        "CONFLICTS",
        "UNKNOWN",
        "DIRTY",
        "DETACHED",
        "STASH",
        "UNREACHABLE",
        "NO_BASE",
    }


def test_status_is_a_str_enum():
    # StrEnum so markers and statuses compare as strings without conversion.
    assert Status.OK == "OK"
    assert f"{Status.NOT_PUSHED}" == "NOT_PUSHED"


def test_status_marker_texts():
    assert Status.OK.MARKER == "[OK]"
    assert Status.NOT_PUSHED.MARKER == "[NOT PUSHED]"
    assert Status.UPSTREAM_DELETED.MARKER == "[UPSTREAM DELETED]"
    assert Status.NO_BASE.MARKER == "[NO BASE]"


def test_repo_report_needs_base_only_without_base_ref():
    assert RepoReport(path=Path("/tmp/r"), base_ref=None).needs_base is True
    assert RepoReport(path=Path("/tmp/r"), base_ref="origin/main").needs_base is False
