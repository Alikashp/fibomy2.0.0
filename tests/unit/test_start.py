"""src/start.py: процесс образа выбирается переменной SERVICE_ROLE (D-062), а не файлами
railway*.toml — Config as Code Railway отключает."""
import sys
import unittest
from pathlib import Path

import _helpers  # noqa: F401
from _helpers import ROOT

import start


class Roles(unittest.TestCase):

    def test_default_is_bot(self):
        for role in ("", None, "bot", " BOT "):
            self.assertEqual(start.command(role), [sys.executable, str(ROOT / "src" / "main.py")])

    def test_worker(self):
        self.assertEqual(start.command("worker")[1:], ["-m", "arq", "worker.WorkerSettings"])

    def test_api_listens_on_railway_port(self):
        argv = start.command("api", "4321")
        self.assertEqual(argv[1:4], ["-m", "uvicorn", "api.app:app"])
        self.assertEqual(argv[argv.index("--port") + 1], "4321")
        self.assertEqual(argv[argv.index("--host") + 1], "0.0.0.0")
        self.assertIn("--proxy-headers", argv)
        self.assertEqual(start.command("api")[start.command("api").index("--port") + 1], "8000")

    def test_unknown_role(self):
        with self.assertRaises(ValueError):
            start.command("apі")   # кириллическая «і» — опечатка не должна запустить бота

    def test_dockerfile_uses_start(self):
        self.assertIn('CMD ["python", "src/start.py"]', (ROOT / "Dockerfile").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
