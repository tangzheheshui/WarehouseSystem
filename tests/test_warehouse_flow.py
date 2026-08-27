import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "server"))

import auth
import db
import excelio
import service


ADMIN_PHONE = "13800000000"
ADMIN_INITIAL_PASSWORD = "Admin123456"
ADMIN_NEW_PASSWORD = "Admin1234567"
WORKER_PHONE = "13912345678"
WORKER_PASSWORD = "Work123456"


class WarehouseFlowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.directory.name) / "warehouse.db"
        db.init_db(self.db_path)
        self.connection = db.connect(self.db_path)

    def tearDown(self):
        self.connection.close()
        self.directory.cleanup()

    def login(self, phone, password):
        token, user = auth.login(self.connection, phone, password)
        return token, auth.user_for_token(self.connection, token)

    def workbook(self, order_type, rows):
        columns = db.columns_for(self.connection, order_type)[1]
        headers = [column["name"] for column in columns]
        return excelio.build_workbook([headers] + rows)

    def test_full_flow_and_transaction_rollback(self):
        _, admin = self.login(ADMIN_PHONE, ADMIN_INITIAL_PASSWORD)
        auth.change_password(
            self.connection,
            admin,
            ADMIN_INITIAL_PASSWORD,
            ADMIN_NEW_PASSWORD,
        )
        worker_created = service.create_user(
            self.connection, admin, WORKER_PHONE, "worker", WORKER_PASSWORD
        )
        self.assertEqual(worker_created["role"], "worker")

        _, worker = self.login(WORKER_PHONE, WORKER_PASSWORD)
        auth.change_password(
            self.connection,
            worker,
            WORKER_PASSWORD,
            WORKER_PASSWORD + "a",
        )
        _, worker = self.login(WORKER_PHONE, WORKER_PASSWORD + "a")

        first_rows = [
            ["P001", "测试商品", "100g", "B1", "2026-08-01", 180, 3, "B-02", ""],
            ["P002", "测试商品二", "200g", "B2", "2026-08-02", 90, 5, "A-03", ""],
        ]
        inbound = service.import_inbound(
            self.connection, worker, self.workbook("inbound", first_rows)
        )
        self.assertEqual(inbound["status"], "completed")
        self.assertEqual(len(inbound["items"]), 2)
        inventory = {
            row["product_id"]: row["quantity"]
            for row in service.list_inventory(self.connection)
        }
        self.assertEqual(inventory, {"P001": 3, "P002": 5})

        outbound_rows = [
            ["P001", "测试商品", "100g", 8, ""],
            ["P002", "测试商品二", "200g", 5, ""],
            ["P002", "测试商品二", "200g", 1, ""],
        ]
        before_orders = service.list_orders(self.connection)
        with self.assertRaises(service.ImportValidationError) as caught:
            service.import_outbound(
                self.connection,
                worker,
                self.workbook("outbound", outbound_rows),
            )
        self.assertEqual(
            caught.exception.errors[0]["message"],
            "当前商品库存为3件，出库数量8件，库存不足无法出库",
        )
        self.assertEqual(service.list_orders(self.connection), before_orders)
        self.assertEqual(
            {
                row["product_id"]: row["quantity"]
                for row in service.list_inventory(self.connection)
            },
            {"P001": 3, "P002": 5},
        )

        second_rows = [
            ["P001", "测试商品", "100g", "B0", "2026-08-03", 180, 7, "A-01", ""],
        ]
        service.import_inbound(
            self.connection, worker, self.workbook("inbound", second_rows)
        )
        outbound = service.import_outbound(
            self.connection,
            worker,
            self.workbook("outbound", [["P001", "测试商品", "100g", 8, ""]]),
        )
        self.assertEqual(outbound["status"], "pending_picking")
        self.assertEqual(outbound["pickingOrder"]["status"], "pending")
        self.assertEqual(
            [(item["location"], item["batch_no"], item["quantity"]) for item in outbound["pickingItems"]],
            [("A-01", "B0", 5), ("B-02", "B1", 3)],
        )

        logs = service.list_stock_logs(self.connection, "P001")
        self.assertEqual(sum(row["change_quantity"] for row in logs), 2)
        completed = service.complete_picking(
            self.connection, worker, outbound["pickingOrder"]["id"]
        )
        self.assertEqual(completed["order"]["status"], "completed")
        self.assertGreater(len(service.list_audit_logs(self.connection)), 3)

    def test_import_reports_all_row_errors(self):
        _, admin = self.login(ADMIN_PHONE, ADMIN_INITIAL_PASSWORD)
        rows = [
            ["P001", "测试商品", "100g", "", "2026-8-1", "abc", 0, "A-01", ""],
            ["P001", "名称不同", "100g", "B1", "2026-08-01", 10, 2, "A-01", ""],
        ]
        with self.assertRaises(service.ImportValidationError) as caught:
            service.import_inbound(
                self.connection, admin, self.workbook("inbound", rows)
            )
        messages = [error["message"] for error in caught.exception.errors]
        self.assertIn("批次号不能为空", messages)
        self.assertIn("日期格式必须为yyyy-MM-dd", messages)
        self.assertIn("保质期（天）必须为正整数", messages)
        self.assertIn("数量必须为正整数", messages)
        self.assertIn("同一商品ID P001 的名称或规格不一致", messages)

    def test_login_lockout(self):
        _, admin = self.login(ADMIN_PHONE, ADMIN_INITIAL_PASSWORD)
        service.create_user(
            self.connection, admin, WORKER_PHONE, "worker", WORKER_PASSWORD
        )
        for _ in range(4):
            with self.assertRaisesRegex(ValueError, "手机号或密码错误"):
                auth.login(self.connection, WORKER_PHONE, "Bad123456")
        with self.assertRaisesRegex(ValueError, "账号已锁定"):
            auth.login(self.connection, WORKER_PHONE, "Bad123456")
        with self.assertRaisesRegex(ValueError, "账号已锁定"):
            auth.login(self.connection, WORKER_PHONE, WORKER_PASSWORD)

    def test_template_disable_blocks_download_and_import(self):
        _, admin = self.login(ADMIN_PHONE, ADMIN_INITIAL_PASSWORD)
        disabled = service.set_template_status(
            self.connection, "inbound", "disabled"
        )
        self.assertEqual(disabled["status"], "disabled")
        with self.assertRaisesRegex(ValueError, "模板已停用"):
            service.template_workbook(self.connection, "inbound")
        rows = [["P001", "测试商品", "100g", "B1", "2026-08-01", 10, 1, "A-01", ""]]
        with self.assertRaisesRegex(ValueError, "模板已停用"):
            service.import_inbound(
                self.connection, admin, self.workbook("inbound", rows)
            )
        active = service.set_template_status(
            self.connection, "inbound", "active"
        )
        self.assertEqual(active["status"], "active")


if __name__ == "__main__":
    unittest.main()
