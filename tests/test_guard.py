"""The Guard workflow, CODEOWNERS and AGENTS.md must name the same guarded paths.

Run with: python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / ".github" / "workflows" / "guard.yml"


def guard_pattern() -> re.Pattern[str]:
    match = re.search(r"^\s*guarded='([^']+)'$", GUARD.read_text(encoding="utf-8"), re.MULTILINE)
    if match is None:
        raise AssertionError("guard.yml has no guarded='...' line")
    return re.compile(match.group(1))


def codeowners_paths() -> list[str]:
    lines = (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8").splitlines()
    return [line.split()[0].lstrip("/") for line in lines if line.strip() and not line.startswith("#")]


def agents_paths() -> list[str]:
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    never = text.split("Touch guarded paths:", 1)[1].split("Items that need them", 1)[0]
    return re.findall(r"`([^`]+)`", never)


def example_file(path: str) -> str:
    """A file inside a guarded directory, or the guarded file itself."""
    return path + "x.yml" if path.endswith("/") else path


class GuardSyncTest(unittest.TestCase):
    def test_every_codeowners_path_is_guarded(self) -> None:
        pattern = guard_pattern()
        for path in codeowners_paths():
            with self.subTest(path=path):
                self.assertRegex(example_file(path), pattern)

    def test_codeowners_and_agents_md_agree(self) -> None:
        self.assertEqual(sorted(codeowners_paths()), sorted(agents_paths()))

    def test_ordinary_paths_are_not_guarded(self) -> None:
        pattern = guard_pattern()
        for path in (
            "tools/summon/__init__.py",
            "services/command-centre/README.md",
            "docs/BACKLOG.md",
            "deploy/healthchecks.json",
            "tools/quartermaster_notes.md",
            "infra.md",
            "docs/AGENTS.md",
        ):
            with self.subTest(path=path):
                self.assertIsNone(pattern.search(path))


if __name__ == "__main__":
    unittest.main()
