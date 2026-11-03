"""Quartermaster: Bastion's deploy policy.

Every Cloud Run service declares a ``deploy.json`` manifest. Quartermaster checks it
against ``deploy/policy.json`` before anything is built or deployed. The policy file
holds the hard caps; a manifest can ask for less, never for more.
"""

from tools.quartermaster.manifest import (
    Manifest,
    ManifestError,
    Policy,
    load_manifest,
    load_policy,
    validate_manifest,
)

__all__ = ["Manifest", "ManifestError", "Policy", "load_manifest", "load_policy", "validate_manifest"]
