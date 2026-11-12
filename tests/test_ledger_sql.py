"""The ledger SQL keeps API roles off the views that run with their owner's rights (backlog G5).

A view without ``security_invoker`` reads and writes as its owner, so row-level security on the tables
under it does not apply. These checks read the SQL files only; ``ledger/audit_grants.sql`` is the same
check against a live database.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

LEDGER = Path(__file__).parents[1] / "services" / "command-centre" / "ledger"
API_ROLES = ("public", "anon", "authenticated")
# The only grants to an API role a ledger file may make, as (file, object, privileges).
ALLOWED_GRANTS = {
    ("bastion_core.sql", "schema bastion", "usage"),
    ("bastion_core.sql", "bastion.public_agent_status", "select"),
    # Reaches every view too; bastion_lockdown.sql takes the views back (checked below).
    ("bastion_core.sql", "all tables in schema bastion", "select, insert, update, delete"),
}
GRANT = re.compile(r"^\s*grant\s+(.+?)\s+on\s+(.+?)\s+to\s+(.+?);", re.IGNORECASE | re.MULTILINE | re.DOTALL)
VIEW = re.compile(
    r"create\s+(?:or\s+replace\s+)?view\s+(bastion\.\w+)(\s+with\s*\(\s*security_invoker\s*=\s*true\s*\))?",
    re.IGNORECASE,
)


def statements(name: str) -> str:
    """A ledger file without its comments."""
    text = (LEDGER / name).read_text(encoding="utf-8")
    return re.sub(r"--[^\n]*", "", text)


def squash(text: str) -> str:
    return " ".join(text.lower().split())


class LedgerGrantsTest(unittest.TestCase):
    def files(self) -> list[str]:
        return sorted(p.name for p in LEDGER.glob("*.sql"))

    def test_no_ledger_file_grants_to_an_api_role_outside_the_allowlist(self) -> None:
        found = set()
        for name in self.files():
            for privileges, target, roles in GRANT.findall(statements(name)):
                if any(role.strip().lower() in API_ROLES for role in roles.split(",")):
                    found.add((name, squash(target), squash(privileges)))
        self.assertEqual(found - ALLOWED_GRANTS, set())

    def test_every_definer_view_is_revoked_from_api_roles(self) -> None:
        """Each view that runs as its owner is revoked from every API role somewhere in the ledger SQL."""
        revoked: dict[str, str] = {}
        for name in self.files():
            for privileges, targets, roles in re.findall(
                r"revoke\s+(.+?)\s+on\s+(.+?)\s+from\s+(.+?);", statements(name), re.IGNORECASE | re.DOTALL
            ):
                if {r.strip().lower() for r in roles.split(",")} >= set(API_ROLES):
                    for target in targets.split(","):
                        revoked[squash(target)] = squash(privileges)
        views = [
            view.lower() for name in self.files() for view, invoker in VIEW.findall(statements(name)) if not invoker
        ]
        self.assertIn("bastion.feed", views)
        for view in views:
            self.assertIn(view, revoked, f"{view} runs as its owner but is never revoked from {API_ROLES}")
            if view == "bastion.public_agent_status":
                self.assertIn("insert, update, delete", revoked[view])
            else:
                self.assertEqual(revoked[view], "all", view)

    def test_lockdown_covers_the_views_core_grants_reach(self) -> None:
        lockdown = squash(statements("bastion_lockdown.sql"))
        self.assertIn("revoke all on bastion.feed, bastion.roster from public, anon, authenticated;", lockdown)
        self.assertIn("on bastion.public_agent_status from public, anon, authenticated;", lockdown)

    def test_reader_file_does_not_strip_other_files_grants(self) -> None:
        self.assertNotIn("all tables in schema", squash(statements("bastion_reader.sql")))

    def test_audit_query_is_read_only(self) -> None:
        audit = squash(statements("audit_grants.sql"))
        self.assertTrue(audit.startswith("select "))
        for word in ("insert ", "update ", "delete ", "drop ", "alter ", "grant ", "revoke "):
            self.assertNotIn(word, audit)


if __name__ == "__main__":
    unittest.main()
