"""Quartermaster policy and manifest rules. Run: python -m unittest discover -s tests -t ."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, ClassVar

from tools.quartermaster.manifest import (
    HARD_MAX_INSTANCES,
    ManifestError,
    Policy,
    load_manifest,
    load_policy,
    parse_policy,
    validate_manifest,
)

ROOT = Path(__file__).resolve().parents[1]

POLICY = Policy(
    max_instances_cap=2,
    allowed_services=frozenset({"hello", "showcase"}),
    public_allowlist=frozenset({"showcase"}),
    allowed_memory=("256Mi", "512Mi"),
)

GOOD = {
    "service": "hello",
    "port": 8080,
    "health": "/health",
    "secrets": ["SUPABASE_READ_URL"],
    "max_instances": 1,
    "access": "private",
    "memory": "512Mi",
}


def with_(**changes: object) -> dict[str, object]:
    raw = dict(GOOD)
    for key, value in changes.items():
        if value is ...:
            raw.pop(key, None)
        else:
            raw[key] = value
    return raw


class ManifestRulesTest(unittest.TestCase):
    def assert_rejected(self, raw: object, fragment: str, **kwargs: object) -> None:
        with self.assertRaises(ManifestError) as ctx:
            validate_manifest(raw, POLICY, **kwargs)
        self.assertTrue(
            any(fragment in problem for problem in ctx.exception.problems),
            f"{fragment!r} not in {ctx.exception.problems}",
        )

    def test_good_manifest_round_trips(self) -> None:
        m = validate_manifest(GOOD, POLICY, expected_service="hello")
        self.assertEqual((m.service, m.port, m.health, m.max_instances), ("hello", 8080, "/health", 1))
        self.assertEqual(m.secrets, ("SUPABASE_READ_URL",))
        self.assertEqual(m.gcloud_secrets(), "SUPABASE_READ_URL=SUPABASE_READ_URL:latest")

    def test_optional_fields_default(self) -> None:
        m = validate_manifest(with_(secrets=..., memory=...), POLICY)
        self.assertEqual(m.secrets, ())
        self.assertEqual(m.memory, "256Mi")
        self.assertEqual(m.gcloud_secrets(), "")

    def test_reports_every_problem_at_once(self) -> None:
        with self.assertRaises(ManifestError) as ctx:
            validate_manifest(with_(port=80, max_instances=9, access="open"), POLICY)
        self.assertEqual(len(ctx.exception.problems), 3)

    def test_not_an_object(self) -> None:
        self.assert_rejected(["hello"], "JSON object")

    def test_unknown_and_missing_keys(self) -> None:
        self.assert_rejected(with_(image="evil:latest"), "unknown keys: image")
        self.assert_rejected(with_(health=...), "missing keys: health")

    def test_service_name_rules(self) -> None:
        for bad in ("Hello", "1hello", "hello-", "a" * 33, "hel_lo", ""):
            with self.subTest(bad=bad):
                self.assert_rejected(with_(service=bad), "service must be")

    def test_service_must_match_folder(self) -> None:
        self.assert_rejected(GOOD, "must match its folder", expected_service="other")

    def test_service_must_be_allowlisted(self) -> None:
        raw = with_(service="rogue")
        self.assert_rejected(raw, "not in the policy allowed_services")

    def test_port_range_and_type(self) -> None:
        for bad in (80, 70000, "8080", True, 8080.0):
            with self.subTest(bad=bad):
                self.assert_rejected(with_(port=bad), "port must be")

    def test_health_path_rules(self) -> None:
        for bad in ("health", "/../etc", "//x", "/" + "a" * 70, "/heal th", 5):
            with self.subTest(bad=bad):
                self.assert_rejected(with_(health=bad), "health must be")

    def test_health_rejects_cloud_run_reserved_z_suffix(self) -> None:
        self.assert_rejected(with_(health="/healthz"), "must not end in 'z'")
        self.assert_rejected(with_(health="/livez/"), "must not end in 'z'")

    def test_secret_names(self) -> None:
        self.assert_rejected(with_(secrets=["lower"]), "UPPER_SNAKE_CASE")
        self.assert_rejected(with_(secrets=["A", "A"]), "must not repeat")
        self.assert_rejected(with_(secrets="A"), "list of secret names")
        self.assert_rejected(with_(secrets=[1]), "list of secret names")

    def test_max_instances_cap(self) -> None:
        self.assert_rejected(with_(max_instances=3), "exceeds the policy cap of 2")
        self.assert_rejected(with_(max_instances=0), "positive integer")
        self.assert_rejected(with_(max_instances=True), "positive integer")

    def test_public_needs_allowlist(self) -> None:
        self.assert_rejected(with_(access="public"), "may not be public")
        m = validate_manifest(with_(service="showcase", access="public"), POLICY)
        self.assertEqual(m.access, "public")

    def test_memory_must_be_allowed(self) -> None:
        self.assert_rejected(with_(memory="8Gi"), "memory must be one of")


class PolicyRulesTest(unittest.TestCase):
    BASE: ClassVar[dict[str, Any]] = {
        "version": 1,
        "max_instances_cap": 2,
        "allowed_services": ["hello"],
        "public_allowlist": [],
        "allowed_memory": ["256Mi"],
    }

    def test_repo_policy_is_valid(self) -> None:
        policy = load_policy(ROOT / "deploy" / "policy.json")
        self.assertLessEqual(policy.max_instances_cap, HARD_MAX_INSTANCES)

    def test_cap_cannot_exceed_hard_limit(self) -> None:
        with self.assertRaises(ManifestError):
            parse_policy({**self.BASE, "max_instances_cap": HARD_MAX_INSTANCES + 1})

    def test_public_must_be_subset_of_allowed(self) -> None:
        with self.assertRaises(ManifestError):
            parse_policy({**self.BASE, "public_allowlist": ["other"]})

    def test_rejects_unknown_keys_and_bad_types(self) -> None:
        for bad in (
            {**self.BASE, "extra": 1},
            {**self.BASE, "version": 2},
            {**self.BASE, "allowed_services": "hello"},
            {**self.BASE, "allowed_memory": []},
            {**self.BASE, "allowed_services": ["Bad_Name"]},
            [],
        ):
            with self.subTest(bad=bad), self.assertRaises(ManifestError):
                parse_policy(bad)


class FileLoadingTest(unittest.TestCase):
    def test_every_service_manifest_in_repo_is_valid(self) -> None:
        policy = load_policy(ROOT / "deploy" / "policy.json")
        manifests = sorted((ROOT / "services").glob("*/deploy.json"))
        self.assertTrue(manifests, "no services/*/deploy.json found")
        for path in manifests:
            with self.subTest(path=str(path)):
                load_manifest(path, policy)

    def test_missing_and_invalid_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "hello"
            folder.mkdir()
            path = folder / "deploy.json"
            with self.assertRaises(ManifestError) as ctx:
                load_manifest(path, POLICY)
            self.assertIn("file not found", ctx.exception.problems[0])
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ManifestError) as ctx:
                load_manifest(path, POLICY)
            self.assertIn("invalid JSON", ctx.exception.problems[0])
            path.write_text(json.dumps(GOOD), encoding="utf-8")
            self.assertEqual(load_manifest(path, POLICY).service, "hello")


if __name__ == "__main__":
    unittest.main()
