import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "server"))

from app import Handler, normalize_base_path
import db


class DirectHandler(Handler):
    def __init__(self, database_path, path, body=None, headers=None):
        self.server = SimpleNamespace(
            db_path=str(database_path), base_path="/acme"
        )
        self.connection = db.connect(database_path)
        normalized = self._normalize_path(path)
        if normalized is None:
            raise ValueError("invalid prefixed path")
        self.path = normalized
        self.body = body or {}
        self.headers = headers or {}
        self.json_response = None
        self.json_status = None

    def read_json(self):
        return self.body

    def send_json(self, value, status=200):
        self.json_response = value
        self.json_status = status


class BasePathTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.directory.name) / "http.db"
        db.init_db(self.db_path)

    def tearDown(self):
        self.directory.cleanup()

    def request(self, method, path, body=None, headers=None):
        handler = DirectHandler(self.db_path, path, body, headers)
        handler.handle_api(method)
        return handler.json_status, handler.json_response

    def test_normalize_company_prefix(self):
        self.assertEqual(normalize_base_path(None), "/")
        self.assertEqual(normalize_base_path("acme"), "/acme")
        self.assertEqual(normalize_base_path("/acme/"), "/acme")
        with self.assertRaises(ValueError):
            normalize_base_path("/bad prefix/")

    def test_prefixed_api_health_and_login(self):
        status, data = self.request("GET", "/acme/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(data["basePath"], "/acme")

        status, login = self.request("POST", "/acme/api/login", {
            "username": "admin",
            "password": "Admin123456",
        })
        self.assertEqual(status, 200)
        self.assertEqual(login["user"]["username"], "admin")

        status, me = self.request(
            "GET", "/acme/api/me",
            headers={"Authorization": f"Bearer {login['token']}"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(me["displayName"], "管理员")
        self.assertIsInstance(me["projects"], list)

        self.assertIsNone(Handler._normalize_path(
            SimpleNamespace(server=SimpleNamespace(base_path="/acme")),
            "/api/health"
        ))

    def test_static_assets_use_relative_paths(self):
        index = (PROJECT_ROOT / "static" / "index.html").read_text()
        script = (PROJECT_ROOT / "static" / "app.js").read_text()
        self.assertIn('href="styles.css"', index)
        self.assertIn('src="app.js"', index)
        self.assertIn('<label>姓名全拼<input name="username"', index)
        self.assertNotIn('<input name="phone"', index)
        self.assertIn("BASE_PATH", script)
        self.assertIn("wmsToken:${BASE_PATH}", script)
        self.assertIn('.replace(/\\/index\\.html$/, "")', script)
        self.assertIn('.replace(/\\/?$/, "/")', script)


if __name__ == "__main__":
    unittest.main()
