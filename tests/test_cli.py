"""Tests for the CLI surface."""
from typer.testing import CliRunner

from git_wombat.cli import build_parser

runner = CliRunner()


def test_has_three_subcommands():
    res = runner.invoke(build_parser(), ["--help"])
    assert res.exit_code == 0
    for cmd in ("local", "remote", "merged"):
        assert cmd in res.stdout


def test_local_help_lists_no_fetch():
    res = runner.invoke(build_parser(), ["local", "--help"])
    assert res.exit_code == 0
    assert "--no-fetch" in res.stdout


def test_remote_help_lists_branch_filter():
    res = runner.invoke(build_parser(), ["remote", "--help"])
    assert "--branch" in res.stdout
    assert "--author" in res.stdout


def test_merged_help_lists_filters():
    res = runner.invoke(build_parser(), ["merged", "--help"])
    assert "--author" in res.stdout
    assert "--branch" in res.stdout


def test_local_help_lists_update_flag():
    res = runner.invoke(build_parser(), ["local", "--help"])
    assert "--update" in res.stdout


def test_update_is_not_on_remote():
    res = runner.invoke(build_parser(), ["remote", "--help"])
    assert "--update" not in res.stdout


def test_local_help_documents_it_does_not_write_by_default():
    res = runner.invoke(build_parser(), ["local", "--help"])
    assert "--update" in res.stdout
