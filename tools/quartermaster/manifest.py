"""Manifest and policy validation for Quartermaster deploys.

A manifest (``services/<name>/deploy.json``) says what a service needs. The policy
(``deploy/policy.json``) says what any service may have. Validation collects every
problem before failing, so one run shows the whole list.

Pure standard library and side-effect free apart from reading the two JSON files.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeIs, cast

# Absolute ceiling enforced in code. deploy/policy.json may lower it, never raise it.
HARD_MAX_INSTANCES = 2

# Cloud Run tag URLs need tag + service name <= 46 characters. Deploy tags are
# "qm-" + 7 hex characters (10 total), so service names stop at 32.
SERVICE_RE = re.compile(r"^[a-z](?:[a-z0-9-]{0,30}[a-z0-9])?$")
SECRET_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,62}$")
HEALTH_RE = re.compile(r"^/[A-Za-z0-9._~/-]{0,63}$")

MANIFEST_KEYS = frozenset({"service", "port", "health", "secrets", "max_instances", "access", "memory"})
REQUIRED_KEYS = frozenset({"service", "port", "health", "max_instances", "access"})
ACCESS_VALUES = ("private", "public")
POLICY_KEYS = frozenset({"version", "max_instances_cap", "allowed_services", "public_allowlist", "allowed_memory"})


class ManifestError(ValueError):
    """Raised with every problem found, not just the first."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


@dataclass(frozen=True)
class Policy:
    max_instances_cap: int
    allowed_services: frozenset[str]
    public_allowlist: frozenset[str]
    allowed_memory: tuple[str, ...]


@dataclass(frozen=True)
class Manifest:
    service: str
    port: int
    health: str
    secrets: tuple[str, ...]
    max_instances: int
    access: str
    memory: str

    def gcloud_secrets(self) -> str:
        """Value for ``gcloud run deploy --set-secrets``: env var name = secret name."""
        return ",".join(f"{name}={name}:latest" for name in self.secrets)


def _is_int(value: object) -> TypeIs[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def parse_policy(raw: Any) -> Policy:
    problems: list[str] = []
    if not isinstance(raw, Mapping):
        raise ManifestError(["policy must be a JSON object"])
    unknown = sorted(set(raw) - POLICY_KEYS)
    if unknown:
        problems.append(f"policy has unknown keys: {', '.join(unknown)}")
    if raw.get("version") != 1:
        problems.append("policy version must be 1")
    cap = raw.get("max_instances_cap")
    if not _is_int(cap) or not 1 <= cap <= HARD_MAX_INSTANCES:
        problems.append(f"policy max_instances_cap must be an integer from 1 to {HARD_MAX_INSTANCES}")
    for key in ("allowed_services", "public_allowlist", "allowed_memory"):
        if not _string_list(raw.get(key)):
            problems.append(f"policy {key} must be a list of strings")
    if problems:
        raise ManifestError(problems)
    allowed = frozenset(raw["allowed_services"])
    public = frozenset(raw["public_allowlist"])
    bad_names = sorted(name for name in allowed | public if not SERVICE_RE.match(name))
    if bad_names:
        problems.append(f"policy names invalid services: {', '.join(bad_names)}")
    if not public <= allowed:
        problems.append("policy public_allowlist must be a subset of allowed_services")
    if not raw["allowed_memory"]:
        problems.append("policy allowed_memory must not be empty")
    if problems:
        raise ManifestError(problems)
    return Policy(
        max_instances_cap=cast(int, cap),  # checked by _is_int above
        allowed_services=allowed,
        public_allowlist=public,
        allowed_memory=tuple(raw["allowed_memory"]),
    )


def load_policy(path: Path) -> Policy:
    return parse_policy(_read_json(path))


def validate_manifest(raw: Any, policy: Policy, *, expected_service: str | None = None) -> Manifest:
    if not isinstance(raw, Mapping):
        raise ManifestError(["manifest must be a JSON object"])
    problems: list[str] = []

    unknown = sorted(set(raw) - MANIFEST_KEYS)
    if unknown:
        problems.append(f"unknown keys: {', '.join(unknown)}")
    missing = sorted(REQUIRED_KEYS - set(raw))
    if missing:
        problems.append(f"missing keys: {', '.join(missing)}")

    service = raw.get("service")
    if "service" in raw:
        if not isinstance(service, str) or not SERVICE_RE.match(service):
            problems.append(
                "service must be 1-32 lowercase letters, digits or hyphens, "
                "starting with a letter and not ending with a hyphen"
            )
        else:
            if expected_service is not None and service != expected_service:
                problems.append(f"service {service!r} must match its folder name {expected_service!r}")
            if service not in policy.allowed_services:
                problems.append(f"service {service!r} is not in the policy allowed_services list")

    port = raw.get("port")
    if "port" in raw and (not _is_int(port) or not 1024 <= port <= 65535):
        problems.append("port must be an integer from 1024 to 65535")

    health = raw.get("health")
    if "health" in raw:
        if not isinstance(health, str) or not HEALTH_RE.match(health) or ".." in health or "//" in health:
            problems.append("health must be an absolute path such as /health (no '..', no '//', max 64 chars)")
        elif health.rstrip("/").endswith("z"):
            # Cloud Run reserves some URL paths ending in "z" (e.g. /healthz) at its front end.
            problems.append("health must not end in 'z': Cloud Run reserves such paths (use /health)")

    secrets = raw.get("secrets", [])
    if not _string_list(secrets):
        problems.append("secrets must be a list of secret names")
        secrets = []
    else:
        bad = [name for name in secrets if not SECRET_RE.match(name)]
        if bad:
            problems.append(f"secret names must be UPPER_SNAKE_CASE: {', '.join(bad)}")
        if len(set(secrets)) != len(secrets):
            problems.append("secrets must not repeat")

    max_instances = raw.get("max_instances")
    if "max_instances" in raw:
        if not _is_int(max_instances) or max_instances < 1:
            problems.append("max_instances must be a positive integer")
        elif max_instances > policy.max_instances_cap:
            problems.append(f"max_instances {max_instances} exceeds the policy cap of {policy.max_instances_cap}")

    access = raw.get("access")
    if "access" in raw:
        if access not in ACCESS_VALUES:
            problems.append("access must be 'private' or 'public'")
        elif access == "public" and service not in policy.public_allowlist:
            problems.append(f"service {service!r} may not be public: it is not in public_allowlist")

    memory = raw.get("memory", policy.allowed_memory[0])
    if memory not in policy.allowed_memory:
        problems.append(f"memory must be one of: {', '.join(policy.allowed_memory)}")

    if problems:
        raise ManifestError(problems)
    # Every field below was checked above; the casts only tell the type checker.
    return Manifest(
        service=cast(str, service),
        port=cast(int, port),
        health=cast(str, health),
        secrets=tuple(secrets),
        max_instances=cast(int, max_instances),
        access=cast(str, access),
        memory=cast(str, memory),
    )


def load_manifest(path: Path, policy: Policy) -> Manifest:
    """Validate ``services/<name>/deploy.json``; the folder name must equal the service."""
    return validate_manifest(_read_json(path), policy, expected_service=path.parent.name)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ManifestError([f"{path}: file not found"]) from None
    except json.JSONDecodeError as exc:
        raise ManifestError([f"{path}: invalid JSON ({exc.msg} at line {exc.lineno})"]) from None
