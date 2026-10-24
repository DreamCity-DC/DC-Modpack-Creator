import shutil
import subprocess

import pytest
from conftest import write_task

from dc_modpack_creator.planner import preflight_batch
from dc_modpack_creator.rules import RuleSet


@pytest.mark.parametrize(
    ("pattern", "path", "selected"),
    [
        ("mods/*.jar", "mods/a.jar", True),
        ("mods/*.jar", "mods/.jar", True),
        ("mods/*.jar", "mods/sub/a.jar", False),
        ("mods/*.jar", "mods/a/b/c.jar", False),
        ("mods/?.jar", "mods/a.jar", True),
        ("mods/?.jar", "mods/ab.jar", False),
        ("mods/?.jar", "mods/x/a.jar", False),
        ("mods/*/a.jar", "mods/x/a.jar", True),
        ("mods/*/a.jar", "mods/x/y/a.jar", False),
        ("mods/**/a.jar", "mods/a.jar", True),
        ("mods/**/a.jar", "mods/x/a.jar", True),
        ("mods/**/a.jar", "mods/x/y/a.jar", True),
        ("mods/**", "mods/a/b/c.jar", True),
        ("mods/**", "mods", False),
        ("**/a.jar", "a.jar", True),
        ("**/a.jar", "mods/x/a.jar", True),
        ("mods/a**b.jar", "mods/ab.jar", True),
        ("mods/a**b.jar", "mods/a/b.jar", False),
        ("*.log", "logs/sub/latest.log", True),
        ("/latest.log", "latest.log", True),
        ("/latest.log", "logs/latest.log", False),
        ("mods/a.jar", "other/mods/a.jar", False),
        ("/mods/a.jar", "mods/a.jar", True),
        ("cache/", "nested/cache/data/x", True),
        ("cache/", "cache", False),
        ("/cache/", "cache/data/x", True),
        ("/cache/", "nested/cache/data/x", False),
        ("mods/", "mods/a/b/c.jar", True),
        ("mods/", "mods-old/a.jar", False),
        ("mods/*", "mods/a/b.jar", False),
        ("mods/[a-c].jar", "mods/b.jar", True),
        ("mods/[a-c].jar", "mods/d.jar", False),
        ("mods/[!a-c].jar", "mods/d.jar", True),
        ("mods/a#?.jar", "mods/a#x.jar", True),
        ("mods/a#?.jar", "mods/a4x.jar", False),
        (r"\#notes.txt", "#notes.txt", True),
        (r"\!notes.txt", "!notes.txt", True),
        (r"mods/\[abc\].jar", "mods/[abc].jar", True),
        ("mods/[", "mods/[", True),
        (" leading.txt", " leading.txt", True),
        (" leading.txt", "leading.txt", False),
        ("notes.txt   ", "notes.txt", True),
        (r"notes.txt\ ", "notes.txt ", True),
        ("./mods/a.jar", "mods/a.jar", False),
        ("../mods/a.jar", "mods/a.jar", False),
        ("mods/./a.jar", "mods/a.jar", False),
        ("mods/../a.jar", "a.jar", False),
        ("mods//a.jar", "mods/a.jar", False),
        ("C:/mods/a.jar", "mods/a.jar", False),
        ("//server/share", "server/share", False),
        ("a:b", "a:b", True),
        ("\x00", "mods/a.jar", False),
        ("/", "anything.txt", False),
    ],
)
def test_git_path_patterns(pattern, path, selected):
    assert RuleSet(pattern).select(path) is selected


def test_rule_order_hit_counts_and_reinclude():
    rules = RuleSet("# comment\nmods/\n!mods/**/private*.jar\nmods/keep/private.jar")
    assert rules.select("mods/a.jar")
    assert not rules.select("mods/x/y/private.jar")
    assert rules.select("mods/keep/private.jar")
    assert rules.hits == [3, 2, 1]
    assert [(rule.line, rule.include) for rule in rules.rules] == [(2, True), (3, False), (4, True)]
    assert not rules.warnings
    assert not RuleSet("# comment\n\n").rules


@pytest.mark.parametrize("recursive", [False, True])
def test_preflight_uses_git_path_patterns(workspace, recursive):
    app, instance, settings = workspace
    (instance / "mods/nested/deep").mkdir(parents=True)
    (instance / "mods/nested/deep/child.jar").write_bytes(b"child")
    pattern = "mods/**/*.jar" if recursive else "mods/*.jar"
    task = write_task(app, rules=pattern)
    result = preflight_batch([task], app, settings)[0]
    assert not result.error
    assert result.plan is not None
    paths = {f.destination for f in result.plan.files}
    assert "overrides/mods/模组.jar" in paths
    assert ("overrides/mods/nested/deep/child.jar" in paths) is recursive
    assert result.plan.rule_hits == [(1, pattern, 2 if recursive else 1)]


@pytest.mark.parametrize(
    "pattern",
    ["./mods/", "../mods/", "mods/./", "mods/../", "mods//", "C:/mods/", "//server/share", "a:b"],
)
def test_preflight_warns_for_unmatched_path_patterns(workspace, pattern):
    app, _, settings = workspace
    task = write_task(app, rules=f"mods/*.jar\n{pattern}")
    result = preflight_batch([task], app, settings)[0]
    assert not result.error
    assert result.plan is not None
    assert [f.destination for f in result.plan.files] == ["overrides/mods/模组.jar"]
    assert result.plan.rule_hits == [(1, "mods/*.jar", 1), (2, pattern, 0)]
    assert result.plan.warnings == [f"规则第 2 行未匹配文件：{pattern}"]


@pytest.mark.skipif(shutil.which("git") is None, reason="Git is not installed")
def test_patterns_against_git(tmp_path):
    """Compare representative path patterns with Git, independent of pathspec."""
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    paths = [
        "a.jar",
        "mods/a.jar",
        "mods/sub/a.jar",
        "mods/sub/deep/a.jar",
        "mods/.jar",
        "mods/ab.jar",
        "other/mods/a.jar",
        "logs/latest.log",
        "latest.log",
        "#notes.txt",
        "!notes.txt",
        "mods/a#x.jar",
        "mods/a4x.jar",
    ]
    for path in paths:
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"fixture")
    for pattern in [
        "mods/*.jar",
        "mods/?.jar",
        "mods/*/a.jar",
        "mods/**/a.jar",
        "**/a.jar",
        "mods/**",
        "mods/",
        "*.log",
        "/latest.log",
        "mods/[a-c].jar",
        "mods/[!a-c].jar",
        "mods/a**b.jar",
        r"\#notes.txt",
        r"\!notes.txt",
        "mods/a#?.jar",
    ]:
        (tmp_path / ".gitignore").write_text(pattern + "\n", encoding="utf-8")
        result = subprocess.run(
            [
                "git",
                "-c",
                "core.ignoreCase=true",
                "-c",
                "core.excludesFile=",
                "check-ignore",
                "--no-index",
                "--stdin",
                "-z",
            ],
            cwd=tmp_path,
            input="\x00".join(paths) + "\x00",
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode in {0, 1}, result.stderr
        matched = set(result.stdout.rstrip("\x00").split("\x00")) if result.stdout else set()
        rules = RuleSet(pattern)
        assert {p for p in paths if rules.select(p)} == matched, pattern
