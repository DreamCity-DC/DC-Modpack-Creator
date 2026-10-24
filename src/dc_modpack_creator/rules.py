from __future__ import annotations

import re
from dataclasses import dataclass

from pathspec.patterns.gitignore.basic import GitIgnoreBasicPattern

from .errors import ExportError


@dataclass(frozen=True)
class Rule:
    line: int
    text: str
    include: bool
    pattern: GitIgnoreBasicPattern


class RuleSet:
    """Gitignore path syntax with ordered export inclusion/exclusion rules.

    Use pathspec's documented Git pattern semantics rather than a custom glob
    translator. Export selection still starts empty and the last match wins;
    excluded directories are scanned so later rules can select their children.
    """

    def __init__(self, text: str):
        self.rules: list[Rule] = []
        for number, raw in enumerate(text.splitlines(), 1):
            # Git preserves leading spaces and uses backslashes for escaping.
            if not raw.strip() or raw.startswith("#"):
                continue
            pattern = raw[1:] if raw.startswith("!") else raw
            # A leading slash anchors to instance_dir; it is not an OS path.
            if pattern.rstrip() == "/":
                continue
            # Patterns only match enumerated instance files; they are not OS paths.
            # Let pathspec validate the syntax without extra path restrictions.
            try:
                matcher = GitIgnoreBasicPattern(raw.casefold())
            except (ValueError, re.error) as exc:
                raise ExportError(f"规则第 {number} 行：{exc}") from exc
            if matcher.include is not None:
                self.rules.append(Rule(number, raw, matcher.include, matcher))
        self.hits = [0] * len(self.rules)

    def select(self, path: str) -> bool:
        selected = False
        normalized = path.replace("\\", "/").casefold()
        for i, rule in enumerate(self.rules):
            if rule.pattern.match_file(normalized) is not None:
                self.hits[i] += 1
                selected = rule.include
        return selected

    @property
    def warnings(self) -> list[str]:
        return [
            f"规则第 {r.line} 行未匹配文件：{r.text}"
            for r, hits in zip(self.rules, self.hits, strict=True)
            if hits == 0
        ]
