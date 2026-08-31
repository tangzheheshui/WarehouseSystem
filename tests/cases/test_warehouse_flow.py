import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "server"))

import auth
import db
import excelio
import service


ADMIN_USERNAME = "admin"
ADMIN_INITIAL_PASSWORD = "Admin123456"
PROJECT_USER_PASSWORD = "Project123456"


class WarehouseFlowTests(unittest.TestCase):
    def setUp(self):
        self.serial = 0
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.directory.name) / "warehouse.db"
        db.init_db(self.db_path)
        self.connection = db.connect(self.db_path)
        self.admin = self.connection.execute(
            "SELECT * FROM users WHERE username = ?", (ADMIN_USERNAME,)
        ).fetchone()
        self.warehouse = service.create_warehouse(
            self.connection, self.admin, "主仓库"
        )
        self.location = service.create_location(
            self.connection, self.admin, self.warehouse["id"],
            "A库区", "A货区", "A-01-01",
        )
        self.project_a = service.create_project(
            self.connection, self.admin, "A项目",
            self.warehouse["id"], "A库区",
        )
        self.project_b = service.create_project(
            self.connection, self.admin, "B项目",
            self.warehouse["id"], "A库区",
        )
        service.create_product(
            self.connection, self.admin, "H001", "测试商品",
            "100g", "个", "测试", None, None, 10,
        )
        service.set_project_products(
            self.connection, self.admin, self.project_a["id"], ["H001"]
        )
        created = service.create_user(
            self.connection, self.admin, "zhangsan", "张三",
            "13812345678", "project_admin", PROJECT_USER_PASSWORD,
            [self.project_a["id"]],
        )
        self.user = self.connection.execute(
            "SELECT * FROM users WHERE id = ?", (created["id"],)
        ).fetchone()

    def tearDown(self):
        self.connection.close()
        self.directory.cleanup()

    def workbook(self, order_type, rows, project_id=None):
        self.serial += 1
        columns = db.columns_for(
            self.connection, order_type, project_id or self.project_a["id"]
        )[1]
        return excelio.build_workbook(
            [[column["name"] for column in columns]] + rows, "测试"
        )

    def inbound_row(self, **overrides):
        values = [
            f"AUTO-RK-{self.serial:04d}", "2026-08-27", "H001", "良品",
            "B1", "2026-08-01", "", 3, "个", "A-01-01", "", "",
        ]
        for key, value in overrides.items():
            values[{
                "order_no": 0, "inbound": 1, "code": 2, "quality": 3,
                "batch": 4, "production": 5, "expiry": 6, "quantity": 7,
                "unit": 8, "location": 9, "purchase": 10, "note": 11,
            }[key]] = value
        return values

    def outbound_row(self, **overrides):
        values = [
            f"AUTO-CK-{self.serial:04d}", "2026-08-27", "H001", "良品",
            "", "", 1, "个", "", "", "",
        ]
        for key, value in overrides.items():
            values[{
                "order_no": 0, "outbound": 1, "code": 2, "quality": 3,
                "batch": 4, "production": 5, "quantity": 6, "unit": 7,
                "location": 8, "recipient": 9, "note": 10,
            }[key]] = value
        return values

    def test_username_login_and_project_isolation(self):
        token, public = auth.login(
            self.connection, "zhangsan", PROJECT_USER_PASSWORD
        )
        self.assertEqual(auth.user_for_token(self.connection, token)["username"], "zhangsan")
        self.assertEqual(public["displayName"], "张三")

        inbound = service.import_inbound(
            self.connection,
            self.user,
            self.project_a["id"],
            self.workbook("inbound", [self.inbound_row()]),
        )
        self.assertEqual(inbound["status"], "completed")
        rows = service.list_inventory(
            self.connection, self.user, self.project_a["id"]
        )
        self.assertEqual(rows[0]["total_quantity"], 3)
        self.assertEqual(rows[0]["available_quantity"], 3)
        self.assertEqual(rows[0]["expiry_date"], "2026-08-11")

        with self.assertRaisesRegex(service.Forbidden, "未授权"):
            service.list_inventory(
                self.connection, self.user, self.project_b["id"]
            )

    def test_outbound_uses_available_inventory_and_fifo(self):
        service.import_inbound(
            self.connection,
            self.user,
            self.project_a["id"],
            self.workbook("inbound", [
                self.inbound_row(batch="B1", inbound="2026-08-25", quantity=2),
                self.inbound_row(batch="B2", inbound="2026-08-26", quantity=3),
            ]),
        )
        self.connection.execute(
            """UPDATE inventory SET picking_reserved = 1, moving_reserved = 1
               WHERE batch_no = 'B2'"""
        )
        self.connection.commit()

        with self.assertRaises(service.ImportValidationError) as caught:
            service.import_outbound(
                self.connection,
                self.user,
                self.project_a["id"],
                self.workbook("outbound", [self.outbound_row(quantity=4)]),
            )
        self.assertIn("可用库存为3件", caught.exception.errors[0]["message"])
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM stock_orders").fetchone()[0], 1)

        outbound = service.import_outbound(
            self.connection,
            self.user,
            self.project_a["id"],
            self.workbook("outbound", [self.outbound_row(quantity=3)]),
        )
        self.assertEqual(outbound["status"], "pending_picking")
        self.assertEqual(
            [(item["batch_no"], item["quantity"]) for item in outbound["pickingItems"]],
            [("B1", 2), ("B2", 1)],
        )
        completed = service.complete_picking(
            self.connection,
            self.user,
            self.project_a["id"],
            outbound["pickingOrder"]["id"],
        )
        self.assertEqual(completed["order"]["status"], "completed")

    def test_blocked_inventory_cannot_outbound(self):
        service.import_inbound(
            self.connection,
            self.user,
            self.project_a["id"],
            self.workbook("inbound", [self.inbound_row(quality="扣留")]),
        )
        with self.assertRaises(service.ImportValidationError):
            service.import_outbound(
                self.connection,
                self.user,
                self.project_a["id"],
                self.workbook("outbound", [self.outbound_row()]),
            )

    def test_import_reports_row_and_column_errors(self):
        with self.assertRaises(service.ImportValidationError) as caught:
            service.import_inbound(
                self.connection,
                self.user,
                self.project_a["id"],
                self.workbook("inbound", [
                    self.inbound_row(batch="", production="2026-8-1", quantity=0),
                ]),
            )
        messages = [error["message"] for error in caught.exception.errors]
        self.assertIn("生产批号不能为空", messages)
        self.assertIn("日期格式必须为yyyy-MM-dd", messages)
        self.assertIn("数量必须为正整数", messages)

    def test_templates_are_project_scoped(self):
        columns = service.get_template(
            self.connection, "inbound", self.project_a["id"]
        )["columns"]
        columns[0]["name"] = "入库通知单号（新）"
        updated = service.update_template(
            self.connection, self.user, self.project_a["id"], "inbound", columns
        )
        self.assertEqual(updated["version"], 2)
        other = service.get_template(
            self.connection, "inbound", self.project_b["id"]
        )
        self.assertEqual(other["version"], 1)
        self.assertEqual(other["columns"][0]["name"], "入库通知单号")

    def test_login_lockout(self):
        for _ in range(4):
            with self.assertRaisesRegex(ValueError, "用户名或密码错误"):
                auth.login(self.connection, "zhangsan", "Bad123456")
        with self.assertRaisesRegex(ValueError, "账号已锁定"):
            auth.login(self.connection, "zhangsan", "Bad123456")
        with self.assertRaisesRegex(ValueError, "账号已锁定"):
            auth.login(self.connection, "zhangsan", PROJECT_USER_PASSWORD)


if __name__ == "__main__":
    unittest.main()
