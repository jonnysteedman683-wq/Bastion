"""Run with: python -m unittest discover -s tests -t ."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from tools import local


class AuthError(Exception):
    pass


class EnvFileTest(unittest.TestCase):
    def write(self, text: str) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / ".env.local"
        path.write_text(text, encoding="utf-8")
        return path

    def test_reads_name_value_lines(self) -> None:
        path = self.write('# comment\n\nPORT = 9000\nINGEST_AGENT_KEYS={"Hermes":"a=b"}\nnot a setting\n')
        self.assertEqual(local.read_env_file(path), {"PORT": "9000", "INGEST_AGENT_KEYS": '{"Hermes":"a=b"}'})

    def test_missing_file_is_no_settings(self) -> None:
        self.assertEqual(local.read_env_file(Path("no-such-dir") / ".env.local"), {})

    def test_the_environment_wins_over_the_file(self) -> None:
        path = self.write("PORT=9000\nINGEST_AGENT_KEYS={}\n")
        env = {"PORT": "8081"}
        self.assertEqual(local.apply_env_file(env, path), ["INGEST_AGENT_KEYS"])
        self.assertEqual(env, {"PORT": "8081", "INGEST_AGENT_KEYS": "{}"})


class LocalAuthTest(unittest.TestCase):
    def link(self, auth: local.LocalAuth) -> tuple[str, str]:
        query = parse_qs(urlsplit(auth.sign_in_path()).query)
        return query["token_hash"][0], query["type"][0]

    def test_the_printed_link_signs_the_owner_in(self) -> None:
        auth = local.LocalAuth(AuthError)
        self.assertTrue(auth.sign_in_path().startswith("/auth/callback?"))
        session = auth.verify_magic_link(*self.link(auth))
        self.assertEqual(auth.current_user(session)["id"], "local-owner")

    def test_anything_else_is_refused(self) -> None:
        auth = local.LocalAuth(AuthError)
        secret, kind = self.link(auth)
        for token, token_type in (("", kind), ("guess", kind), (secret, "email"), (secret + "x", kind)):
            with self.subTest(token_type=token_type), self.assertRaises(AuthError):
                auth.verify_magic_link(token, token_type)
        for session in ("", "guess", secret):  # the link secret is not itself a session
            with self.subTest(session=session), self.assertRaises(AuthError):
                auth.current_user(session)
        with self.assertRaises(AuthError):
            auth.send_magic_link("someone@example.com", "http://localhost/auth/callback")

    def test_every_run_has_its_own_secrets(self) -> None:
        first, second = local.LocalAuth(AuthError), local.LocalAuth(AuthError)
        self.assertNotEqual(first.sign_in_path(), second.sign_in_path())
        with self.assertRaises(AuthError):
            second.verify_magic_link(*self.link(first))

    def test_it_only_ever_listens_on_this_machine(self) -> None:
        self.assertEqual(local.HOST, "127.0.0.1")

    def test_supabase_settings_select_the_normal_sign_in(self) -> None:
        self.assertEqual(local.describe({}), "local-link")
        self.assertEqual(local.describe({"SUPABASE_URL": "https://x.supabase.co"}), "local-link")
        self.assertEqual(
            local.describe({"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_PUBLISHABLE_KEY": "k"}), "supabase"
        )


if __name__ == "__main__":
    unittest.main()
