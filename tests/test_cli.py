"""Quartermaster CLI behaviour, as the deploy workflow uses it."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tools.quartermaster.__main__ import main

ROOT = Path(__file__).resolve().parents[1]
HELLO = ROOT / "services" / "hello" / "deploy.json"


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    def test_validate_discovers_repo_manifests(self) -> None:
        code, out, _ = run("validate")
        self.assertEqual(code, 0)
        self.assertIn("-> hello (private, max 1)", out)

    def test_outputs_are_github_output_lines(self) -> None:
        code, out, _ = run("outputs", str(HELLO))
        self.assertEqual(code, 0)
        pairs = dict(line.split("=", 1) for line in out.splitlines())
        self.assertEqual(pairs["service"], "hello")
        self.assertEqual(pairs["health"], "/health")
        self.assertEqual(pairs["max_instances"], "1")
        self.assertEqual(pairs["set_secrets"], "")

    def test_bad_manifest_fails_with_annotations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "hello"
            folder.mkdir()
            bad = folder / "deploy.json"
            bad.write_text(json.dumps({"service": "hello", "max_instances": 50}), encoding="utf-8")
            code, _, err = run("outputs", str(bad))
            self.assertEqual(code, 1)
            self.assertIn("::error file=", err)
            code, _, err = run("validate", str(bad))
            self.assertEqual(code, 1)
            self.assertIn("exceeds the policy cap", err)

    def test_bad_policy_fails_validate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = Path(tmp) / "policy.json"
            policy.write_text(json.dumps({"version": 1, "max_instances_cap": 99}), encoding="utf-8")
            code, _, err = run("--policy", str(policy), "validate")
            self.assertEqual(code, 1)
            self.assertIn("FAIL policy", err)


if __name__ == "__main__":
    unittest.main()
