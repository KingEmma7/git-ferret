"""Tests for markdown report rendering."""
from datetime import datetime
from pathlib import Path

from git_wombat.models import Finding, RepoReport, Status
from git_wombat.report import render_report


def _repo(**kwargs):
    return RepoReport(path=Path("/tmp/api"), base_ref="origin/main", **kwargs)


def test_render_includes_repo_heading_and_findings():
    rep = _repo()
    rep.findings.append(
        Finding(branch="feat/x", status=Status.NOT_PUSHED, detail="3 commits ahead")
    )
    out = render_report([rep], stale_count=0)
    assert "## /tmp/api" in out
    assert "[NOT PUSHED]" in out
    assert "feat/x" in out


def test_render_uses_absolute_fetch_timestamp():
    rep = _repo(fetched_at=datetime(2026, 10, 16, 9, 41, 2))
    out = render_report([rep], stale_count=0)
    assert "2026-10-16 09:41:02" in out


def test_render_contains_no_ansi_escapes():
    rep = _repo()
    rep.findings.append(Finding(branch="b", status=Status.OK))
    out = render_report([rep], stale_count=0)
    assert "\x1b[" not in out


def test_render_emits_stale_warning_once():
    out = render_report([_repo()], stale_count=2)
    assert "stale" in out.lower()
    assert out.lower().count("stale") == 1


def test_render_omits_stale_warning_when_none():
    out = render_report([_repo()], stale_count=0)
    assert "stale" not in out.lower()


def test_render_has_no_markdown_tables():
    rep = _repo()
    rep.findings.append(Finding(branch="b", status=Status.OK))
    out = render_report([rep], stale_count=0)
    assert "|" not in out


def test_marker_survives_without_emoji():
    # Emoji are decorative; the marker alone must carry the meaning.
    rep = _repo()
    rep.findings.append(Finding(branch="b", status=Status.CONFLICTS, emoji="❌"))
    out = render_report([rep], stale_count=0)
    assert "[CONFLICTS]" in out


def test_render_survives_missing_emoji_entirely():
    rep = _repo()
    rep.findings.append(Finding(branch="b", status=Status.DIRTY, emoji=""))
    out = render_report([rep], stale_count=0)
    assert "[DIRTY]" in out


def test_render_shows_base_ref():
    out = render_report([_repo()], stale_count=0)
    assert "origin/main" in out


def test_render_shows_skipped_reason():
    rep = RepoReport(path=Path("/tmp/web"), base_ref=None,
                     skipped_reason="no base branch")
    out = render_report([rep], stale_count=0)
    assert "no base branch" in out


def test_every_repo_gets_a_heading_even_when_clean():
    out = render_report([_repo(), RepoReport(path=Path("/tmp/clean"),
                                             base_ref="origin/main")],
                        stale_count=0)
    assert "/tmp/api" in out and "/tmp/clean" in out


def test_starts_with_top_level_heading():
    out = render_report([_repo()], stale_count=0)
    assert out.startswith("# git-wombat")


def test_safe_to_delete_reason_is_shown():
    rep = _repo()
    rep.findings.append(
        Finding(branch="origin/done", status=Status.OK,
                detail="safe to delete: merged into origin/main", safe_to_delete=True)
    )
    out = render_report([rep], stale_count=0)
    assert "safe to delete" in out


def test_empty_report_list_still_renders_header():
    out = render_report([], stale_count=0)
    assert out.startswith("# git-wombat")


def test_fetch_failure_is_flagged():
    rep = _repo(fetch_failed=True)
    out = render_report([rep], stale_count=0)
    assert "stale" in out.lower()
