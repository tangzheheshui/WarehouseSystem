import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "server"))

import auth
import excelio
import service
from app import Handler, WarehouseServer
from tests.data.warehouse_data import (
    ADMIN_PASSWORD,
    PROJECT_USER_PASSWORD,
    WarehouseDataFactory,
)


class WorkflowAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.fixture = WarehouseDataFactory(
            Path(self.directory.name) / "acceptance.db"
        )
        self.admin = self.fixture.admin
        self.project_user = self.fixture.project_user
        self.project_a = self.fixture.project_a
        self.project_b = self.fixture.project_b
        self.product = self.fixture.product
        self.disabled_product = self.fixture.disabled_product
        self.warehouse = self.fixture.warehouse
        self.location = self.fixture.location
        self.connection = self.fixture.connection

    def tearDown(self):
        self.fixture.close()
        self.directory.cleanup()

    def test_admin_initializes_data_and_project_authorization(self):
        self.assertEqual(self.admin["username"], "zhangsan")
        self.assertEqual(self.admin["role"], "admin")
        self.assertEqual(self.project_user["username"], "liuhaifeng")
        self.assertEqual(self.project_user["role"], "project_admin")
        self.assertEqual(self.product["status"], "active")
        self.assertEqual(self.disabled_product["status"], "disabled")
        self.assertEqual(self.warehouse["status"], "active")
        self.assertEqual(self.location["status"], "active")

        projects = service.user_projects(self.connection, self.project_user)
        self.assertEqual([item["project_name"] for item in projects], ["自动化测试项目A"])
        self.assertEqual(
            service.require_project(
                self.connection, self.project_user, self.project_a["id"]
            )["project_name"],
            "自动化测试项目A",
        )
        with self.assertRaisesRegex(service.Forbidden, "未授权"):
            service.require_project(
                self.connection, self.project_user, self.project_b["id"]
            )

        with self.assertRaisesRegex(ValueError, "货品编号已存在"):
            service.create_product(
                self.connection,
                self.admin,
                "AUTO-P001",
                "重复商品",
                project_id=self.project_a["id"],
            )
        with self.assertRaisesRegex(ValueError, "仓库名称已存在"):
            service.create_warehouse(
                self.connection, self.admin, "自动化测试仓"
            )
        with self.assertRaisesRegex(ValueError, "项目名称已存在"):
            service.create_project(
                self.connection, self.admin, "自动化测试项目A",
                self.warehouse["id"], "A库区",
            )

    def test_template_is_project_scoped_and_disabled_import_rejected(self):
        template_a = service.get_template(
            self.connection, "inbound", self.project_a["id"]
        )
        template_b = service.get_template(
            self.connection, "inbound", self.project_b["id"]
        )
        self.assertEqual(template_a["version"], 1)
        self.assertEqual(template_b["version"], 1)

        for order_type in ("inbound", "outbound"):
            workbook = service.template_workbook(
                self.connection, order_type, self.project_a["id"]
            )
            parsed = excelio.read_workbook(workbook)
            expected = service.get_template(
                self.connection, order_type, self.project_a["id"]
            )
            self.assertEqual(
                [item["name"] for item in expected["columns"]],
                parsed[0][1],
            )
            self.assertEqual(len(parsed), 2)
            self.assertTrue(all(
                parsed[1][1][index] != ""
                for index, column in enumerate(expected["columns"])
                if column["required"]
            ))

        columns = template_a["columns"]
        note_column = next(item for item in columns if item["key"] == "note")
        note_column["name"] = "备注（新）"
        updated = service.update_template(
            self.connection,
            self.project_user,
            self.project_a["id"],
            "inbound",
            columns,
        )
        self.assertEqual(updated["version"], 2)
        self.assertIn("备注（新）", [item["name"] for item in updated["columns"]])
        unchanged_b = service.get_template(
            self.connection, "inbound", self.project_b["id"]
        )
        self.assertEqual(unchanged_b["version"], 1)
        self.assertIn("备注", [item["name"] for item in unchanged_b["columns"]])

        disabled = service.set_template_status(
            self.connection,
            self.project_user,
            self.project_a["id"],
            "inbound",
            "disabled",
        )
        self.assertEqual(disabled["status"], "disabled")
        with self.assertRaisesRegex(ValueError, "模板已停用"):
            service.template_workbook(
                self.connection, "inbound", self.project_a["id"]
            )
        with self.assertRaisesRegex(ValueError, "模板已停用"):
            service.import_inbound(
                self.connection,
                self.project_user,
                self.project_a["id"],
                self.fixture.workbook("inbound", [{}]),
            )
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM stock_orders").fetchone()[0],
            0,
        )

    def test_successful_inbound_accumulates_inventory_and_writes_trace(self):
        first = service.import_inbound(
            self.connection,
            self.project_user,
            self.project_a["id"],
            self.fixture.workbook("inbound", [{"quantity": 3}]),
        )
        second = service.import_inbound(
            self.connection,
            self.project_user,
            self.project_a["id"],
            self.fixture.workbook("inbound", [{"quantity": 2}]),
        )
        self.assertEqual(first["status"], "completed")
        self.assertEqual(second["status"], "completed")
        self.assertTrue(first["orderNo"].startswith("AUTO-RK"))

        inventory = service.list_inventory(
            self.connection, self.project_user, self.project_a["id"]
        )
        self.assertEqual(len(inventory), 1)
        row = inventory[0]
        self.assertEqual(row["product_id"], "AUTO-P001")
        self.assertEqual(row["batch_no"], "AUTO-B001")
        self.assertEqual(row["total_quantity"], 5)
        self.assertEqual(row["available_quantity"], 5)
        self.assertEqual(row["production_date"], "2026-01-01")
        self.assertEqual(row["expiry_date"], "2026-01-31")

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM stock_logs WHERE change_type = 'inbound'"
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM audit_logs WHERE action = 'stock.inbound'"
            ).fetchone()[0],
            2,
        )

    def test_inbound_archive_and_field_errors_are_atomic(self):
        scenarios = [
            {"unit": "错误单位"},
            {"quantity": 0},
            {"location_name": "不存在货位"},
            {"production_date": "2026-13-01"},
        ]
        for scenario in scenarios:
            with self.assertRaises(service.ImportValidationError) as caught:
                service.import_inbound(
                    self.connection,
                    self.project_user,
                    self.project_a["id"],
                    self.fixture.workbook(
                        "inbound", [{"quantity": 2}, scenario]
                    ),
                )
            self.assertTrue(caught.exception.errors)
            self.assertEqual(
                self.connection.execute(
                    "SELECT COUNT(*) FROM stock_orders"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                self.connection.execute("SELECT COUNT(*) FROM inventory").fetchone()[0],
                0,
            )

    def test_disabled_product_cannot_be_imported(self):
        with self.assertRaises(service.ImportValidationError) as caught:
            service.import_inbound(
                self.connection,
                self.project_user,
                self.project_a["id"],
                self.fixture.workbook(
                    "inbound",
                    [{
                        "product_code": "AUTO-P002",
                        "product_name": "停用商品",
                        "spec": "200g",
                        "unit": "箱",
                    }],
                ),
            )
        messages = [item["message"] for item in caught.exception.errors]
        self.assertIn("货品已停用：AUTO-P002", messages)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM inventory").fetchone()[0],
            0,
        )

    def test_outbound_fifo_available_stock_and_picking(self):
        service.import_inbound(
            self.connection,
            self.project_user,
            self.project_a["id"],
            self.fixture.workbook("inbound", [
                {"batch_no": "AUTO-B001", "order_date": "2026-01-01", "quantity": 5},
                {"batch_no": "AUTO-B002", "order_date": "2026-01-02", "quantity": 5},
            ]),
        )
        self.connection.execute(
            """UPDATE inventory SET picking_reserved = 2, moving_reserved = 1
               WHERE batch_no = 'AUTO-B002'"""
        )
        self.connection.commit()

        with self.assertRaises(service.ImportValidationError) as caught:
            service.import_outbound(
                self.connection,
                self.project_user,
                self.project_a["id"],
                self.fixture.workbook("outbound", [
                    {"quantity": 5},
                    {"quantity": 5},
                ]),
            )
        self.assertIn("当前可用库存为7件", caught.exception.errors[0]["message"])
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM stock_orders WHERE order_type = 'outbound'"
            ).fetchone()[0],
            0,
        )

        outbound = service.import_outbound(
            self.connection,
            self.project_user,
            self.project_a["id"],
            self.fixture.workbook("outbound", [{"quantity": 7}]),
        )
        self.assertEqual(outbound["status"], "pending_picking")
        self.assertEqual(
            [(item["batch_no"], item["quantity"]) for item in outbound["pickingItems"]],
            [("AUTO-B001", 5), ("AUTO-B002", 2)],
        )

        inventory = {
            row["batch_no"]: row
            for row in service.list_inventory(
                self.connection, self.project_user, self.project_a["id"]
            )
        }
        self.assertEqual(inventory["AUTO-B001"]["total_quantity"], 0)
        self.assertEqual(inventory["AUTO-B002"]["total_quantity"], 3)

        completed = service.complete_picking(
            self.connection,
            self.project_user,
            self.project_a["id"],
            outbound["pickingOrder"]["id"],
        )
        self.assertEqual(completed["order"]["status"], "completed")
        after = {
            row["batch_no"]: row
            for row in service.list_inventory(
                self.connection, self.project_user, self.project_a["id"]
            )
        }
        self.assertEqual(after["AUTO-B001"]["total_quantity"], 0)
        self.assertEqual(after["AUTO-B002"]["total_quantity"], 3)

        changes = self.connection.execute(
            """SELECT change_quantity FROM stock_logs
               WHERE change_type = 'outbound' ORDER BY id"""
        ).fetchall()
        self.assertEqual([row["change_quantity"] for row in changes], [-5, -2])

    def test_hold_and_prohibited_batches_are_not_available(self):
        service.import_inbound(
            self.connection,
            self.project_user,
            self.project_a["id"],
            self.fixture.workbook("inbound", [
                {
                    "batch_no": "AUTO-HOLD",
                    "quality_status": "扣留",
                    "quantity": 2,
                },
                {"batch_no": "AUTO-BLOCK", "quantity": 3},
            ]),
        )
        self.connection.execute(
            "UPDATE inventory SET prohibit_outbound = 1 WHERE batch_no = 'AUTO-BLOCK'"
        )
        self.connection.commit()

        with self.assertRaises(service.ImportValidationError) as caught:
            service.import_outbound(
                self.connection,
                self.project_user,
                self.project_a["id"],
                self.fixture.workbook("outbound", [{"quantity": 5}]),
            )
        self.assertIn("当前可用库存为0件", caught.exception.errors[0]["message"])
        quantities = {
            row["batch_no"]: row["total_quantity"]
            for row in self.connection.execute("SELECT * FROM inventory")
        }
        self.assertEqual(quantities["AUTO-HOLD"], 2)
        self.assertEqual(quantities["AUTO-BLOCK"], 3)

    def test_project_inventory_isolation_after_reauthorization(self):
        service.update_user(
            self.connection,
            self.admin,
            self.project_user["id"],
            project_ids=[self.project_a["id"], self.project_b["id"]],
        )
        # 商品归属唯一项目：B 项目用自己名下的商品入库
        service.create_product(
            self.connection, self.admin, "AUTO-B001", "B项目商品",
            project_id=self.project_b["id"], unit="个",
        )
        service.import_inbound(
            self.connection,
            self.project_user,
            self.project_b["id"],
            self.fixture.workbook(
                "inbound",
                [{"product_code": "AUTO-B001", "quantity": 6}],
                self.project_b["id"],
            ),
        )
        rows_b = service.list_inventory(
            self.connection, self.project_user, self.project_b["id"]
        )
        self.assertEqual(rows_b[0]["total_quantity"], 6)

        service.update_user(
            self.connection,
            self.admin,
            self.project_user["id"],
            project_ids=[self.project_a["id"]],
        )
        rows_a = service.list_inventory(
            self.connection, self.project_user, self.project_a["id"]
        )
        self.assertEqual(rows_a, [])
        with self.assertRaisesRegex(service.Forbidden, "未授权"):
            service.list_inventory(
                self.connection, self.project_user, self.project_b["id"]
            )

    def test_user_disable_and_reset_invalidate_sessions(self):
        token, _ = auth.login(
            self.connection, "liuhaifeng", PROJECT_USER_PASSWORD
        )
        self.assertTrue(auth.user_for_token(self.connection, token))

        service.update_user(
            self.connection,
            self.admin,
            self.project_user["id"],
            status="disabled",
        )
        self.assertFalse(auth.user_for_token(self.connection, token))

        service.update_user(
            self.connection,
            self.admin,
            self.project_user["id"],
            status="active",
            project_ids=[self.project_a["id"]],
        )
        service.reset_user_password(
            self.connection, self.admin, self.project_user["id"], "Reset123456"
        )
        with self.assertRaisesRegex(ValueError, "用户名或密码错误"):
            auth.login(self.connection, "liuhaifeng", PROJECT_USER_PASSWORD)
        _, public = auth.login(self.connection, "liuhaifeng", "Reset123456")
        self.assertTrue(public["mustChangePassword"])

    def test_manual_entry_creates_orders_and_inventory(self):
        inbound = service.create_manual_inbound(
            self.connection,
            self.project_user,
            self.project_a["id"],
            {
                "orderNo": "AUTO-RK-MANUAL-001",
                "orderDate": "2026-02-01",
                "productCode": "AUTO-P001",
                "qualityStatus": "良品",
                "batchNo": "AUTO-MANUAL-B001",
                "productionDate": "2026-01-15",
                "expiryDate": "",
                "quantity": 4,
                "unit": "个",
                "locationName": "A-01",
                "purchaseOrderNo": "",
                "note": "",
            },
        )
        self.assertEqual(inbound["orderNo"], "AUTO-RK-MANUAL-001")
        self.assertEqual(inbound["status"], "completed")

        outbound = service.create_manual_outbound(
            self.connection,
            self.project_user,
            self.project_a["id"],
            {
                "orderNo": "AUTO-CK-MANUAL-001",
                "orderDate": "2026-02-02",
                "productCode": "AUTO-P001",
                "qualityStatus": "良品",
                "batchNo": "",
                "productionDate": "",
                "quantity": 2,
                "unit": "个",
                "locationName": "",
                "recipient": "自动化收货方",
                "note": "",
            },
        )
        self.assertEqual(outbound["orderNo"], "AUTO-CK-MANUAL-001")
        self.assertEqual(outbound["status"], "pending_picking")
        inventory = service.list_inventory(
            self.connection, self.project_user, self.project_a["id"]
        )
        self.assertEqual(inventory[0]["total_quantity"], 2)

    def test_order_numbers_must_be_unique(self):
        workbook = self.fixture.workbook("inbound", [{"quantity": 1}])
        service.import_inbound(
            self.connection, self.project_user, self.project_a["id"], workbook
        )
        with self.assertRaisesRegex(ValueError, "入库通知单号已存在"):
            service.import_inbound(
                self.connection, self.project_user, self.project_a["id"], workbook
            )

    def test_product_must_be_linked_to_project(self):
        unlinked = service.create_product(
            self.connection, self.admin, "AUTO-P003", "未关联商品",
            project_id=self.project_b["id"], spec="50g", unit="个",
        )
        with self.assertRaises(service.ImportValidationError) as caught:
            service.import_inbound(
                self.connection,
                self.project_user,
                self.project_a["id"],
                self.fixture.workbook("inbound", [{
                    "product_code": "AUTO-P003",
                    "unit": "个",
                }]),
            )
        messages = [item["message"] for item in caught.exception.errors]
        self.assertTrue(any("不属于当前项目" in message for message in messages))

    def test_summary_inventory_aggregates_by_product(self):
        service.import_inbound(
            self.connection,
            self.project_user,
            self.project_a["id"],
            self.fixture.workbook("inbound", [
                {"batch_no": "AUTO-B001", "quantity": 3},
                {"batch_no": "AUTO-B002", "location_name": "A-01", "quantity": 4},
            ]),
        )
        summary = service.list_inventory(
            self.connection, self.project_user, self.project_a["id"], summary="1"
        )
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["product_id"], "AUTO-P001")
        self.assertEqual(summary[0]["total_quantity"], 7)
        self.assertEqual(summary[0]["available_quantity"], 7)
        self.assertEqual(summary[0]["warehouse_name"], "自动化测试仓")

    def test_outbound_specified_location_and_batch_constraints(self):
        service.import_inbound(
            self.connection,
            self.project_user,
            self.project_a["id"],
            self.fixture.workbook("inbound", [
                {"batch_no": "AUTO-B001", "production_date": "2026-01-01", "quantity": 2},
                {"batch_no": "AUTO-B002", "production_date": "2026-01-02", "quantity": 3},
            ]),
        )
        with self.assertRaises(service.ImportValidationError) as caught:
            service.import_outbound(
                self.connection,
                self.project_user,
                self.project_a["id"],
                self.fixture.workbook("outbound", [{
                    "quantity": 4,
                    "batch_no": "AUTO-B001",
                }]),
            )
        self.assertIn("当前可用库存为2件", caught.exception.errors[0]["message"])

        outbound = service.import_outbound(
            self.connection,
            self.project_user,
            self.project_a["id"],
            self.fixture.workbook("outbound", [{
                "quantity": 2,
                "batch_no": "AUTO-B001",
            }]),
        )
        picking = service.get_picking_order(
            self.connection,
            self.project_user,
            self.project_a["id"],
            outbound["pickingOrder"]["id"],
        )
        self.assertEqual(
            [(item["batch_no"], item["quantity"]) for item in picking["items"]],
            [("AUTO-B001", 2)],
        )
        self.assertEqual(picking["order"]["recipient"], "自动化收货方")


class HttpAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.fixture = WarehouseDataFactory(
            Path(self.directory.name) / "http-acceptance.db"
        )
        self.server = WarehouseServer(
            ("127.0.0.1", 0), Handler, self.fixture.db_path, "/acme"
        )
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/acme"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.fixture.close()
        self.directory.cleanup()

    def request(self, method, path, body=None, headers=None):
        payload = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=payload,
            method=method,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                data = response.read()
                content_type = response.headers.get("Content-Type", "")
                parsed = json.loads(data) if "json" in content_type else data
                return response.status, parsed
        except urllib.error.HTTPError as error:
            data = error.read()
            try:
                return error.code, json.loads(data)
            except json.JSONDecodeError:
                return error.code, data

    def login(self, username, password):
        status, data = self.request(
            "POST", "/api/login", {"username": username, "password": password}
        )
        self.assertEqual(status, 200)
        return data["token"]

    def test_role_boundaries_and_company_prefix(self):
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"\xe5\xa7\x93\xe5\x90\x8d\xe5\x85\xa8\xe6\x8b\xbc", page)

        admin_token = self.login("zhangsan", ADMIN_PASSWORD)
        status, me = self.request(
            "GET", "/api/me", headers={"Authorization": f"Bearer {admin_token}"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(me["username"], "zhangsan")
        self.assertEqual(me["role"], "admin")

        status, projects = self.request(
            "GET", "/api/projects",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        self.assertEqual(status, 200)
        status, created = self.request(
            "POST",
            "/api/products",
            {
                "productCode": "HTTP-P001",
                "productName": "HTTP测试商品",
                "projectId": projects[0]["id"],
                "spec": "10g",
                "unit": "个",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(created["product_id"], "HTTP-P001")

        project_token = self.login("liuhaifeng", PROJECT_USER_PASSWORD)
        status, forbidden = self.request(
            "GET", "/api/users",
            headers={"Authorization": f"Bearer {project_token}"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(forbidden["error"], "无权限访问")

        status, forbidden = self.request(
            "GET", "/api/templates",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        self.assertEqual(status, 403)

        status, inventory = self.request(
            "GET",
            "/api/inventory",
            headers={
                "Authorization": f"Bearer {project_token}",
                "X-Project-Id": str(self.fixture.project_a["id"]),
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(inventory, [])

        status, forbidden = self.request(
            "GET",
            "/api/inventory",
            headers={
                "Authorization": f"Bearer {project_token}",
                "X-Project-Id": str(self.fixture.project_b["id"]),
            },
        )
        self.assertEqual(status, 403)
        self.assertEqual(forbidden["error"], "未授权或项目已停用")


if __name__ == "__main__":
    unittest.main()
