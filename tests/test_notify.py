"""notify: phone push through ntfy. Run with: python -m unittest discover -s tests -t ."""

from __future__ import annotations

import io
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from tools import notify

ENV = {"NTFY_TOPIC": "bastion-secret-topic-123"}


class Recorder:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.calls: list[tuple[str, bytes, dict[str, str]]] = []

    def __call__(self, url: str, body: bytes, headers: dict[str, str]) -> int:
        self.calls.append((url, body, headers))
        return self.status


def run(argv: list[str], env: dict[str, str] | None = None, post: Any = None) -> tuple[int, str, Recorder]:
    rec = post or Recorder()
    out = io.StringIO()
    code = notify.main(argv, env=ENV if env is None else env, post=rec, out=out)
    return code, out.getvalue(), rec


class SendTests(unittest.TestCase):
    def test_posts_message_to_topic_on_default_server(self) -> None:
        code, _, rec = run(["send", "--message", "Sebastian is over its cap", "--title", "Cap breach"])

        self.assertEqual(code, 0)
        url, body, headers = rec.calls[0]
        self.assertEqual(url, "https://ntfy.sh/bastion-secret-topic-123")
        self.assertEqual(body, b"Sebastian is over its cap")
        self.assertEqual(headers["Title"], "Cap breach")
        self.assertEqual(headers["Priority"], "default")

    def test_token_and_server_come_from_the_environment(self) -> None:
        env = {**ENV, "NTFY_URL": "https://ntfy.example.org/", "NTFY_TOKEN": "tk_abc"}
        _, _, rec = run(["send", "--message", "hi"], env=env)

        url, _, headers = rec.calls[0]
        self.assertEqual(url, "https://ntfy.example.org/bastion-secret-topic-123")
        self.assertEqual(headers["Authorization"], "Bearer tk_abc")

    def test_missing_topic_is_an_error_and_sends_nothing(self) -> None:
        code, text, rec = run(["send", "--message", "hi"], env={})

        self.assertEqual(code, 2)
        self.assertIn("NTFY_TOPIC", text)
        self.assertEqual(rec.calls, [])

    def test_topic_is_never_printed(self) -> None:
        for post in (Recorder(200), Recorder(500)):
            _, text, _ = run(["send", "--message", "hi"], post=post)
            self.assertNotIn("bastion-secret-topic-123", text)

    def test_rejects_a_plain_http_server(self) -> None:
        code, text, rec = run(["send", "--message", "hi"], env={**ENV, "NTFY_URL": "http://ntfy.example.org"})

        self.assertEqual(code, 2)
        self.assertIn("https", text)
        self.assertEqual(rec.calls, [])

    def test_loopback_http_is_allowed_for_a_local_server(self) -> None:
        code, _, rec = run(["send", "--message", "hi"], env={**ENV, "NTFY_URL": "http://127.0.0.1:8081"})

        self.assertEqual(code, 0)
        self.assertEqual(rec.calls[0][0], "http://127.0.0.1:8081/bastion-secret-topic-123")

    def test_a_bad_topic_is_rejected(self) -> None:
        for topic in ("has space", "a/b", "x" * 65, ""):
            code, _, rec = run(["send", "--message", "hi"], env={"NTFY_TOPIC": topic})
            self.assertEqual(code, 2, topic)
            self.assertEqual(rec.calls, [])

    def test_priority_must_be_known(self) -> None:
        code, _, rec = run(["send", "--message", "hi", "--priority", "panic"])

        self.assertEqual(code, 2)
        self.assertEqual(rec.calls, [])

    def test_urgent_priority_and_tags(self) -> None:
        _, _, rec = run(["send", "--message", "hi", "--priority", "urgent", "--tags", "warning,rotating_light"])

        headers = rec.calls[0][2]
        self.assertEqual(headers["Priority"], "urgent")
        self.assertEqual(headers["Tags"], "warning,rotating_light")

    def test_header_values_cannot_carry_newlines_or_non_latin1(self) -> None:
        _, _, rec = run(["send", "--message", "hi", "--title", "line1\r\nX-Evil: 1 — done"])

        title = rec.calls[0][2]["Title"]
        self.assertNotIn("\n", title)
        self.assertNotIn("\r", title)
        title.encode("latin-1")

    def test_long_messages_keep_the_first_bytes_up_to_the_ntfy_limit(self) -> None:
        message = "".join(f"{i:04d}" for i in range(2000))
        _, _, rec = run(["send", "--message", message])

        self.assertEqual(rec.calls[0][1], message.encode()[: notify.MAX_MESSAGE_BYTES])

    def test_truncation_never_splits_a_multibyte_character(self) -> None:
        _, _, rec = run(["send", "--message", "a" + "\u20ac" * 2000])  # euro sign: 3 bytes each

        body = rec.calls[0][1]
        body.decode("utf-8")  # must not raise
        self.assertLessEqual(len(body), notify.MAX_MESSAGE_BYTES)
        self.assertGreater(len(body), notify.MAX_MESSAGE_BYTES - 3)

    def test_click_link_must_be_https(self) -> None:
        code, _, rec = run(["send", "--message", "hi", "--click", "javascript:alert(1)"])
        self.assertEqual(code, 2)
        self.assertEqual(rec.calls, [])

        _, _, ok = run(["send", "--message", "hi", "--click", "https://bastion.example/"])
        self.assertEqual(ok.calls[0][2]["Click"], "https://bastion.example/")

    def test_server_error_fails_the_command_without_the_topic(self) -> None:
        code, text, _ = run(["send", "--message", "hi"], post=Recorder(429))

        self.assertEqual(code, 1)
        self.assertIn("429", text)

    def test_network_failure_is_a_clean_error(self) -> None:
        def boom(url: str, body: bytes, headers: dict[str, str]) -> int:
            raise OSError("connection refused: " + url)

        code, text, _ = run(["send", "--message", "hi"], post=boom)

        self.assertEqual(code, 1)
        self.assertNotIn("bastion-secret-topic-123", text)


class HardeningTests(unittest.TestCase):
    def test_click_with_newline_or_non_ascii_is_rejected(self) -> None:
        for click in ("https://a.org/\nX-Evil: 1", "https://a.org/\r\n", "https://a.org/caf\u00e9"):
            code, _, rec = run(["send", "--message", "hi", "--click", click])
            self.assertEqual(code, 2, repr(click))
            self.assertEqual(rec.calls, [])

    def test_malformed_urls_are_config_errors_not_tracebacks(self) -> None:
        code, _, rec = run(["send", "--message", "hi"], env={**ENV, "NTFY_URL": "https://[::1"})
        self.assertEqual(code, 2)
        code, _, _ = run(["send", "--message", "hi", "--click", "https://[::1"])
        self.assertEqual(code, 2)
        self.assertEqual(rec.calls, [])

    def test_a_bare_query_or_fragment_delimiter_is_rejected(self) -> None:
        for url in ("https://ntfy.example.org?", "https://ntfy.example.org/#"):
            code, _, rec = run(["send", "--message", "hi"], env={**ENV, "NTFY_URL": url})
            self.assertEqual(code, 2, url)
            self.assertEqual(rec.calls, [])

    def test_any_loopback_address_may_use_plain_http(self) -> None:
        for host in ("127.0.0.2:8081", "[::1]:8081", "localhost"):
            code, _, _ = run(["send", "--message", "hi"], env={**ENV, "NTFY_URL": "http://" + host})
            self.assertEqual(code, 0, host)

    def test_a_non_loopback_http_address_is_still_rejected(self) -> None:
        code, _, _ = run(["send", "--message", "hi"], env={**ENV, "NTFY_URL": "http://10.0.0.5"})
        self.assertEqual(code, 2)

    def test_a_network_failure_says_delivery_is_unknown(self) -> None:
        def boom(url: str, body: bytes, headers: dict[str, str]) -> int:
            raise OSError("timed out")

        _, text, _ = run(["send", "--message", "hi"], post=boom)
        self.assertIn("may or may not have been delivered", text)


class RedirectTests(unittest.TestCase):
    def test_redirects_are_not_followed(self) -> None:
        hits: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                hits.append("origin")
                self.send_response(307)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/elsewhere")
                self.end_headers()

            def do_GET(self) -> None:
                hits.append("followed")
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args: Any) -> None:
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status = notify.http_post(
                f"http://127.0.0.1:{server.server_port}/topic", b"hi", {"Authorization": "Bearer t"}
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertEqual(status, 307)
        self.assertEqual(hits, ["origin"])


if __name__ == "__main__":
    unittest.main()
