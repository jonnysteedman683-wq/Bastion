"""Quartermaster command line.

    python -m tools.quartermaster validate [MANIFEST ...]   # default: every services/*/deploy.json
    python -m tools.quartermaster outputs MANIFEST           # key=value lines for $GITHUB_OUTPUT

Exit code 0 when everything passes, 1 when any manifest or the policy is invalid.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tools.quartermaster.manifest import ManifestError, load_manifest, load_policy

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = ROOT / "deploy" / "policy.json"


def _discover() -> list[Path]:
    return sorted((ROOT / "services").glob("*/deploy.json"))


def cmd_validate(paths: list[Path], policy_path: Path) -> int:
    try:
        policy = load_policy(policy_path)
    except ManifestError as exc:
        print(f"FAIL policy {policy_path}:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    paths = paths or _discover()
    if not paths:
        print("FAIL no manifests found under services/*/deploy.json", file=sys.stderr)
        return 1
    failed = 0
    for path in paths:
        try:
            manifest = load_manifest(path, policy)
        except ManifestError as exc:
            failed += 1
            print(f"FAIL {path}:", file=sys.stderr)
            for problem in exc.problems:
                print(f"  - {problem}", file=sys.stderr)
        else:
            print(f"ok   {path} -> {manifest.service} ({manifest.access}, max {manifest.max_instances})")
    return 1 if failed else 0


def cmd_outputs(path: Path, policy_path: Path) -> int:
    try:
        manifest = load_manifest(path, load_policy(policy_path))
    except ManifestError as exc:
        for problem in exc.problems:
            print(f"::error file={path}::{problem}", file=sys.stderr)
        return 1
    lines = {
        "service": manifest.service,
        "port": str(manifest.port),
        "health": manifest.health,
        "max_instances": str(manifest.max_instances),
        "access": manifest.access,
        "memory": manifest.memory,
        "set_secrets": manifest.gcloud_secrets(),
    }
    for key, value in lines.items():
        print(f"{key}={value}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quartermaster")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="validate manifests against the policy")
    validate.add_argument("manifests", nargs="*", type=Path)
    outputs = sub.add_parser("outputs", help="print one manifest as GitHub Actions outputs")
    outputs.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    if args.command == "validate":
        return cmd_validate(args.manifests, args.policy)
    return cmd_outputs(args.manifest, args.policy)


if __name__ == "__main__":
    sys.exit(main())
