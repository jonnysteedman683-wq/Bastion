import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


class VercelConfigTests(unittest.TestCase):
    def test_build_does_not_use_static_source_as_generated_output(self) -> None:
        config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))

        self.assertTrue((ROOT / "public" / "index.html").is_file())
        self.assertNotEqual(config["outputDirectory"], "public")

    def test_install_command_does_not_skip_python_dependencies(self) -> None:
        config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))

        self.assertNotIn("installCommand", config)
        self.assertEqual(
            (ROOT / "api" / "requirements.txt").read_text(encoding="utf-8"),
            (ROOT / "requirements.txt").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
