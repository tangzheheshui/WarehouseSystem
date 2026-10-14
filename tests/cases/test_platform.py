"""V5 多公司与万顺控制台自动化验收（requirements §12.6、§13.12-14）。

真实起一个平台模式进程，用不同 Host 头模拟 console / 公司子域名访问：
- 控制台接口仅平台管理员可用；公司创建、停用启用、管理员重置全流程；
- 按 Host 分发数据库，A 公司会话在 B 公司子域名无效；
- 品牌信息（系统名 + 公司名称）与公司管理员改公司名。
"""

import http.client
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "server"))

import app
import db
import platform_registry

DOMAIN = "wms.test"
SYSTEM_NAME = "万顺WMS仓库管理系统"
INITIAL_ADMIN_PASSWORD = "Wanshun123456"
PASSWORD_PATTERN = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).{8,20}$")


class PlatformServerTestCase(unittest.TestCase):
    admin_password = INITIAL_ADMIN_PASSWORD

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.data_dir = Path(cls.tmp.name)
        cls._env = {
            "WMS_PLATFORM_ADMIN_USERNAME": "wanshun",
            "WMS_PLATFORM_ADMIN_NAME": "万顺平台管理员",
            "WMS_PLATFORM_ADMIN_PASSWORD": INITIAL_ADMIN_PASSWORD,
        }
        cls._saved = {key: os.environ.get(key) for key in cls._env}
        os.environ.update(cls._env)
        cls.registry = platform_registry.PlatformRegistry(
            cls.data_dir / "platform.json"
        )
        cls.server = app.create_server(
            "127.0.0.1", 0, None, registry=cls.registry, domain=DOMAIN
        )
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        for key, value in cls._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        cls.tmp.cleanup()

    def request(self, method, host, path, body=None, token=None, raw=False):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.port, timeout=10
        )
        headers = {"Host": host}
        payload = None
        if body is not None:
            payload = json.dumps(body)
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        if raw:
            return response.status, data, response.getheader("Content-Type", "")
        return response.status, (json.loads(data) if data else {})

    # ---- 控制台 ----

    def console_login(self):
        status, data = self.request(
            "POST", f"console.{DOMAIN}", "/api/console/login",
            {"username": "wanshun", "password": self.admin_password},
        )
        self.assertEqual(status, 200, data)
        return data["token"], data["user"]

    def admin_token(self):
        """登录控制台；若首登强制改密则先完成改密，返回可用 token。"""
        token, user = self.console_login()
        if user["mustChangePassword"]:
            new_password = (
                "Wanshun123456"
                if self.admin_password != "Wanshun123456"
                else "Wanshun654321"
            )
            status, data = self.request(
                "POST", f"console.{DOMAIN}", "/api/console/change-password",
                {"oldPassword": self.admin_password,
                 "newPassword": new_password},
                token=token,
            )
            self.assertEqual(status, 200, data)
            type(self).admin_password = new_password
        return token

    def create_company(self, prefix, name=None, admin_username="zhangsan"):
        token = self.admin_token()
        return self.request(
            "POST", f"console.{DOMAIN}", "/api/console/companies",
            {
                "prefix": prefix,
                "name": name or f"{prefix}公司",
                "adminUsername": admin_username,
            },
            token=token,
        )

    def test_console_health_and_login(self):
        status, data = self.request(
            "GET", f"console.{DOMAIN}", "/api/console/health"
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["mode"], "console")

        status, data = self.request(
            "POST", f"console.{DOMAIN}", "/api/console/login",
            {"username": "wanshun", "password": "Wrong000000"},
        )
        self.assertEqual(status, 400)
        self.assertIn("用户名或密码错误", data["error"])

        token, user = self.console_login()
        self.assertTrue(token)
        self.assertEqual(user["displayName"], "万顺平台管理员")

    def test_console_requires_platform_admin(self):
        status, data = self.request(
            "GET", f"console.{DOMAIN}", "/api/console/companies"
        )
        self.assertEqual(status, 401)
        self.assertIn("请先登录", data["error"])

        token = "forged-token"
        status, data = self.request(
            "GET", f"console.{DOMAIN}", "/api/console/companies", token=token
        )
        self.assertEqual(status, 401)

    def test_console_must_change_password_gate(self):
        with self.registry.lock:
            self.registry._admin()["must_change_password"] = 1
            self.registry._save()

        token, user = self.console_login()
        self.assertTrue(user["mustChangePassword"])
        status, data = self.request(
            "GET", f"console.{DOMAIN}", "/api/console/companies", token=token
        )
        self.assertEqual(status, 403)
        self.assertTrue(data["mustChangePassword"])

        new_password = "Wanshun888999"
        status, data = self.request(
            "POST", f"console.{DOMAIN}", "/api/console/change-password",
            {"oldPassword": self.admin_password,
             "newPassword": new_password},
            token=token,
        )
        self.assertEqual(status, 200, data)
        type(self).admin_password = new_password

        status, data = self.request(
            "GET", f"console.{DOMAIN}", "/api/console/companies", token=token
        )
        self.assertEqual(status, 200, data)

    def test_create_company_flow(self):
        status, company = self.create_company(
            "acme", name="ACME贸易", admin_username="zhangsan"
        )
        self.assertEqual(status, 201, company)
        self.assertEqual(company["prefix"], "acme")
        self.assertEqual(company["name"], "ACME贸易")
        self.assertEqual(company["status"], "active")
        self.assertEqual(company["adminUsername"], "zhangsan")
        self.assertEqual(company["subdomain"], f"acme.{DOMAIN}")
        self.assertTrue(PASSWORD_PATTERN.fullmatch(company["initialPassword"]))
        initial_password = company["initialPassword"]

        # 新公司子域名立即可访问，登录页品牌就绪
        status, data = self.request("GET", "acme.wms.test", "/api/health")
        self.assertEqual(status, 200)
        status, data = self.request("GET", "acme.wms.test", "/api/branding")
        self.assertEqual(status, 200)
        self.assertEqual(data["systemName"], SYSTEM_NAME)
        self.assertEqual(data["companyName"], "ACME贸易")

        # 公司管理员可用初始密码登录，且强制改密
        status, login = self.request(
            "POST", "acme.wms.test", "/api/login",
            {"username": "zhangsan", "password": initial_password},
        )
        self.assertEqual(status, 200, login)
        self.assertEqual(login["user"]["role"], "admin")
        self.assertTrue(login["user"]["mustChangePassword"])

        # 注册表与数据库文件落地
        record = self.registry.get_company("acme")
        self.assertEqual(record["name"], "ACME贸易")
        self.assertTrue(Path(record["db_path"]).exists())

    def test_create_company_validation(self):
        cases = [
            ("console", "保留字"),
            ("www", "保留字"),
            ("Acme", "必须小写"),
            ("-acme", "不能以连字符开头"),
            ("a", "至少2位"),
            ("x" * 33, "最多32位"),
        ]
        for prefix, reason in cases:
            status, data = self.create_company(prefix)
            self.assertEqual(status, 400, f"{prefix}({reason}) 应被拒绝")
            self.assertIn("公司前缀", data["error"])

        status, data = self.create_company("acme", admin_username="ZhangSan")
        self.assertEqual(status, 400)
        self.assertIn("姓名全拼", data["error"])

        status, _ = self.create_company("dupco")
        self.assertEqual(status, 201)
        status, data = self.create_company("dupco")
        self.assertEqual(status, 400)
        self.assertIn("已存在", data["error"])

    def test_console_has_no_business_api_and_reverse(self):
        status, data = self.request(
            "POST", f"console.{DOMAIN}", "/api/login",
            {"username": "zhangsan", "password": "Admin123456"},
        )
        self.assertEqual(status, 404)

        status, data = self.request(
            "GET", "acme.wms.test", "/api/console/companies"
        )
        self.assertEqual(status, 404)

    # ---- 公司侧 ----

    def company_session(self, prefix, username, password):
        status, login = self.request(
            "POST", f"{prefix}.{DOMAIN}", "/api/login",
            {"username": username, "password": password},
        )
        self.assertEqual(status, 200, login)
        token = login["token"]
        if login["user"]["mustChangePassword"]:
            status, data = self.request(
                "POST", f"{prefix}.{DOMAIN}", "/api/change-password",
                {"oldPassword": password, "newPassword": "Company123456"},
                token=token,
            )
            self.assertEqual(status, 200, data)
            password = "Company123456"
        return token, password

    def test_cross_company_isolation(self):
        _, alpha = self.create_company("alpha", admin_username="alice")
        _, beta = self.create_company("beta", admin_username="bob")
        alpha_token, alpha_password = self.company_session(
            "alpha", "alice", alpha["initialPassword"]
        )
        beta_token, _ = self.company_session(
            "beta", "bob", beta["initialPassword"]
        )

        # A 公司会话在 B 公司子域名无效（按 Host 选库，会话查不到）
        status, data = self.request(
            "GET", "beta.wms.test", "/api/me", token=alpha_token
        )
        self.assertEqual(status, 401)
        # 反向同理
        status, data = self.request(
            "GET", "alpha.wms.test", "/api/me", token=beta_token
        )
        self.assertEqual(status, 401)
        # 本公司访问正常
        status, data = self.request(
            "GET", "alpha.wms.test", "/api/me", token=alpha_token
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["username"], "alice")

        # 数据也隔离：alpha 建的商品在 beta 不可见
        status, _ = self.request(
            "POST", "alpha.wms.test", "/api/products",
            {
                "productCode": "ISO-P001", "productName": "隔离商品",
                "unit": "个",
            },
            token=alpha_token,
        )
        self.assertEqual(status, 200)
        status, beta_products = self.request(
            "GET", "beta.wms.test", "/api/products", token=beta_token
        )
        self.assertEqual(status, 200)
        self.assertFalse(any(
            item["product_id"] == "ISO-P001" for item in beta_products
        ))
        self.assertEqual(alpha_password, "Company123456")

    def test_disable_and_enable_company(self):
        _, created = self.create_company("toggleco", admin_username="tadmin")
        self.assertEqual(created["status"], "active")

        token = self.admin_token()
        status, data = self.request(
            "POST", f"console.{DOMAIN}",
            "/api/console/companies/toggleco/status",
            {"status": "disabled"}, token=token,
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["status"], "disabled")

        # 停用后 API 与页面均提示停用，数据完整保留
        status, data = self.request(
            "GET", "toggleco.wms.test", "/api/health"
        )
        self.assertEqual(status, 403)
        self.assertIn("已停用", data["error"])
        status, body, content_type = self.request(
            "GET", "toggleco.wms.test", "/", raw=True
        )
        self.assertEqual(status, 403)
        self.assertIn("text/html", content_type)
        self.assertIn("已停用", body.decode("utf-8"))
        self.assertTrue(Path(self.registry.get_company("toggleco")["db_path"]).exists())

        status, data = self.request(
            "POST", f"console.{DOMAIN}",
            "/api/console/companies/toggleco/status",
            {"status": "active"}, token=token,
        )
        self.assertEqual(status, 200, data)
        status, data = self.request("GET", "toggleco.wms.test", "/api/health")
        self.assertEqual(status, 200)

    def test_reset_company_admin_password(self):
        _, created = self.create_company("resco", admin_username="radmin")
        token, password = self.company_session(
            "resco", "radmin", created["initialPassword"]
        )

        console = self.admin_token()
        status, data = self.request(
            "POST", f"console.{DOMAIN}",
            "/api/console/companies/resco/reset-admin-password",
            body={}, token=console,
        )
        self.assertEqual(status, 200, data)
        new_password = data["password"]
        self.assertTrue(PASSWORD_PATTERN.fullmatch(new_password))

        # 旧 token 立即失效；旧密码不能再登录；新密码可登录且强制改密
        status, _ = self.request(
            "GET", "resco.wms.test", "/api/me", token=token
        )
        self.assertEqual(status, 401)
        status, data = self.request(
            "POST", "resco.wms.test", "/api/login",
            {"username": "radmin", "password": password},
        )
        self.assertEqual(status, 400)
        status, login = self.request(
            "POST", "resco.wms.test", "/api/login",
            {"username": "radmin", "password": new_password},
        )
        self.assertEqual(status, 200)
        self.assertTrue(login["user"]["mustChangePassword"])

    def test_unknown_company_host(self):
        status, data = self.request(
            "GET", "ghost.wms.test", "/api/health"
        )
        self.assertEqual(status, 404)
        self.assertIn("公司不存在", data["error"])

    def test_company_rename_by_company_admin(self):
        _, created = self.create_company("renameco", admin_username="nadmin")
        token, _ = self.company_session(
            "renameco", "nadmin", created["initialPassword"]
        )

        status, data = self.request(
            "GET", "renameco.wms.test", "/api/branding"
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["companyName"], "renameco公司")

        status, data = self.request(
            "PUT", "renameco.wms.test", "/api/company-name",
            {"companyName": "新名称贸易"}, token=token,
        )
        self.assertEqual(status, 200, data)

        status, data = self.request(
            "GET", "renameco.wms.test", "/api/branding"
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["companyName"], "新名称贸易")
        self.assertEqual(
            self.registry.get_company("renameco")["name"], "新名称贸易"
        )

        # 未登录不可修改
        status, data = self.request(
            "PUT", "renameco.wms.test", "/api/company-name",
            {"companyName": "越权名称"},
        )
        self.assertEqual(status, 401)

    def test_registry_external_edit_visible(self):
        """应急恢复场景：进程外直接改 platform.json，运行中的服务立即生效。"""
        token = self.admin_token()
        external = platform_registry.PlatformRegistry(self.registry.path)
        with external.lock:
            external._admin()["must_change_password"] = 1
            external._save()
        status, data = self.request(
            "GET", f"console.{DOMAIN}", "/api/console/me", token=token
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["mustChangePassword"])
        with external.lock:
            external._admin()["must_change_password"] = 0
            external._save()
        status, data = self.request(
            "GET", f"console.{DOMAIN}", "/api/console/me", token=token
        )
        self.assertEqual(status, 200)
        self.assertFalse(data["mustChangePassword"])

    def test_platform_admin_lockout(self):
        try:
            for _ in range(5):
                status, data = self.request(
                    "POST", f"console.{DOMAIN}", "/api/console/login",
                    {"username": "wanshun", "password": "Bad000000"},
                )
                self.assertEqual(status, 400)
            status, data = self.request(
                "POST", f"console.{DOMAIN}", "/api/console/login",
                {"username": "wanshun", "password": self.admin_password},
            )
            self.assertEqual(status, 400)
            self.assertIn("锁定", data["error"])
        finally:
            with self.registry.lock:
                admin = self.registry._admin()
                admin["failed_attempts"] = 0
                admin["locked_until"] = None
                self.registry._save()


class SingleCompanyBrandingTests(unittest.TestCase):
    """非平台模式（独立部署）下品牌接口走 meta 表。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.tmp.name) / "warehouse.db"
        cls.server = app.create_server("127.0.0.1", 0, cls.db_path)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.tmp.cleanup()

    def request(self, method, path, body=None, token=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.port, timeout=10
        )
        headers = {"Host": "127.0.0.1"}
        payload = None
        if body is not None:
            payload = json.dumps(body)
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read() or b"{}")
        connection.close()
        return response.status, data

    def test_branding_default_and_rename(self):
        status, data = self.request("GET", "/api/branding")
        self.assertEqual(status, 200)
        self.assertEqual(data["systemName"], SYSTEM_NAME)
        self.assertEqual(data["companyName"], "")

        status, login = self.request(
            "POST", "/api/login",
            {"username": "admin", "password": "Admin123456"},
        )
        self.assertEqual(status, 200)
        token = login["token"]

        # 默认管理员须先完成强制改密，才能改公司名称
        status, data = self.request(
            "POST", "/api/change-password",
            {"oldPassword": "Admin123456", "newPassword": "Admin654321"},
            token=token,
        )
        self.assertEqual(status, 200, data)

        status, data = self.request(
            "PUT", "/api/company-name", {"companyName": "独立部署公司"},
            token=token,
        )
        self.assertEqual(status, 200, data)
        status, data = self.request("GET", "/api/branding")
        self.assertEqual(data["companyName"], "独立部署公司")

        connection = db.connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT value FROM meta WHERE key = 'company_name'"
            ).fetchone()
            self.assertEqual(row["value"], "独立部署公司")
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
