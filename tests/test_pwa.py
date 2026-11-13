import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
PUBLIC = ROOT / "public"


class PwaTests(unittest.TestCase):
    def test_manifest_is_installable(self) -> None:
        m = json.loads((PUBLIC / "manifest.webmanifest").read_text(encoding="utf-8"))

        self.assertEqual(m["display"], "standalone")
        self.assertEqual(m["start_url"], "/")
        self.assertTrue(m["name"] and m["short_name"])
        sizes = {i["sizes"] for i in m["icons"]}
        self.assertTrue({"192x192", "512x512"} <= sizes)
        for icon in m["icons"]:
            self.assertTrue((PUBLIC / icon["src"].lstrip("/")).is_file(), icon["src"])

    def test_page_links_manifest_icon_and_worker(self) -> None:
        html = (PUBLIC / "index.html").read_text(encoding="utf-8")

        self.assertIn('rel="manifest"', html)
        self.assertIn('rel="apple-touch-icon"', html)
        self.assertIn('name="theme-color"', html)
        self.assertIn("serviceWorker.register('/sw.js')", html)

    def test_worker_never_caches_live_data(self) -> None:
        sw = (PUBLIC / "sw.js").read_text(encoding="utf-8")

        self.assertIn("/api/", sw)
        self.assertIn("fetch", sw)
        # API requests must bypass the cache so the dashboard is never stale.
        self.assertRegex(sw, r"pathname\.startsWith\(['\"]/api/['\"]\)")

    def test_worker_is_served_uncached(self) -> None:
        config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
        rule = next(h for h in config["headers"] if h["source"] == "/sw.js")

        self.assertIn({"key": "Cache-Control", "value": "no-cache"}, rule["headers"])


if __name__ == "__main__":
    unittest.main()
