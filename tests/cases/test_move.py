import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "server"))

import db
import excelio
import service


ADMIN_USERNAME = "admin"
ADMIN_INITIAL_PASSWORD = "Admin123456"
PROJECT_USER_PASSWORD = "Project123456"


class MoveTests(unittest.TestCase):
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
        self.location_b = service.create_location(
            self.connection, self.admin, self.warehouse["id"],
            "A库区", "A货区", "A-01-02",
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
            project_id=self.project_a["id"],
            spec="100g", unit="个", category="测试",
            shelf_life_days=10,
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

    def move_row(self, **overrides):
        values = [
            f"AUTO-MV-{self.serial:04d}", "2026-08-28", "H001", "B1",
            2, "个", "A-01-01", "A-01-02", "",
        ]
        for key, value in overrides.items():
            values[{
                "order_no": 0, "move": 1, "code": 2, "batch": 3,
                "quantity": 4, "unit": 5, "from_loc": 6, "to": 7, "note": 8,
            }[key]] = value
        return values

    def stock_in(self, **kwargs):
        return service.import_inbound(
            self.connection, self.user, self.project_a["id"],
            self.workbook("inbound", [self.inbound_row(**kwargs)]),
        )

    def inventory_row(self, location_id, batch="B1", code="H001"):
        return self.connection.execute(
            """SELECT * FROM inventory
               WHERE project_id = ? AND product_id = ?
                 AND location_id = ? AND batch_no = ?""",
            (self.project_a["id"], code, location_id, batch),
        ).fetchone()

    def test_import_move_updates_inventory_and_writes_logs(self):
        self.stock_in(quantity=5)
        result = service.import_move(
            self.connection, self.user, self.project_a["id"],
            self.workbook("move", [self.move_row(quantity=2)]),
        )
        self.assertEqual(result["type"], "move")
        self.assertEqual(result["status"], "completed")
        source = self.inventory_row(self.location["id"])
        target = self.inventory_row(self.location_b["id"])
        self.assertEqual(source["total_quantity"], 3)
        self.assertEqual(target["total_quantity"], 2)
        for field in ("production_date", "expiry_date", "inbound_date",
                      "quality_status", "purchase_order_no"):
            self.assertEqual(target[field], source[field])
        self.assertEqual(target["picking_reserved"], 0)
        self.assertEqual(target["moving_reserved"], 0)
        item = result["items"][0]
        self.assertEqual(item["location"]["id"], self.location["id"])
        self.assertEqual(item["toLocation"]["id"], self.location_b["id"])
        aggregated = result["aggregated"]
        self.assertEqual(aggregated[0]["productCode"], "H001")
        self.assertEqual(aggregated[0]["batchNo"], "B1")
        self.assertEqual(aggregated[0]["quantity"], 2)
        self.assertIn("A-01-01", aggregated[0]["from"])
        self.assertIn("A-01-02", aggregated[0]["to"])
        logs = self.connection.execute(
            """SELECT change_type, change_quantity, location_id FROM stock_logs
               WHERE order_no = ? ORDER BY change_type""",
            (result["orderNo"],),
        ).fetchall()
        self.assertEqual(
            [(row["change_type"], row["change_quantity"]) for row in logs],
            [("move_in", 2), ("move_out", -2)],
        )
        self.assertEqual(
            {row["location_id"] for row in logs},
            {self.location["id"], self.location_b["id"]},
        )
        audit = self.connection.execute(
            "SELECT action FROM audit_logs WHERE action = 'stock.move'"
        ).fetchone()
        self.assertIsNotNone(audit)

    def test_manual_move_one_step(self):
        self.stock_in(quantity=5)
        result = service.create_manual_move(
            self.connection, self.user, self.project_a["id"],
            {
                "orderNo": "MV-MANUAL-001",
                "orderDate": "2026-08-28",
                "productCode": "H001",
                "batchNo": "B1",
                "quantity": 2,
                "unit": "个",
                "fromLocation": "A-01-01",
                "toLocation": "A-01-02",
            },
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            self.inventory_row(self.location["id"])["total_quantity"], 3
        )
        self.assertEqual(
            self.inventory_row(self.location_b["id"])["total_quantity"], 2
        )

    def test_manual_move_validates_required_fields(self):
        self.stock_in(quantity=5)
        with self.assertRaisesRegex(ValueError, "源货位不能为空"):
            service.create_manual_move(
                self.connection, self.user, self.project_a["id"],
                {
                    "orderNo": "MV-MANUAL-002",
                    "orderDate": "2026-08-28",
                    "productCode": "H001",
                    "batchNo": "B1",
                    "quantity": 2,
                    "unit": "个",
                    "fromLocation": "",
                    "toLocation": "A-01-02",
                },
            )

    def test_move_rejects_when_available_insufficient(self):
        self.stock_in(quantity=5)
        self.connection.execute(
            "UPDATE inventory SET picking_reserved = 4"
        )
        self.connection.commit()
        with self.assertRaises(service.ImportValidationError) as caught:
            service.import_move(
                self.connection, self.user, self.project_a["id"],
                self.workbook("move", [self.move_row(quantity=2)]),
            )
        self.assertIn("可用库存为1件", caught.exception.errors[0]["message"])
        self.assertEqual(self.inventory_row(self.location["id"])["total_quantity"], 5)
        self.assertIsNone(self.inventory_row(self.location_b["id"]))
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM stock_orders WHERE order_type = 'move'"
            ).fetchone()[0],
            0,
        )

    def test_move_rejects_same_location(self):
        with self.assertRaises(service.ImportValidationError) as caught:
            service.import_move(
                self.connection, self.user, self.project_a["id"],
                self.workbook("move", [self.move_row(to="A-01-01")]),
            )
        messages = [error["message"] for error in caught.exception.errors]
        self.assertIn("源货位与目标货位不能相同", messages)

    def test_move_rejects_invalid_or_foreign_location(self):
        location_other_area = service.create_location(
            self.connection, self.admin, self.warehouse["id"],
            "B库区", "B货区", "B-01-01",
        )
        self.stock_in(quantity=5)
        with self.assertRaises(service.ImportValidationError) as caught:
            service.import_move(
                self.connection, self.user, self.project_a["id"],
                self.workbook("move", [
                    self.move_row(to="B-01-01"),
                    self.move_row(to="X-99-99"),
                ]),
            )
        messages = [error["message"] for error in caught.exception.errors]
        self.assertIn("目标货位在项目仓库/库区内不存在", messages)
        self.assertEqual(len(messages), 2)

    def test_move_rejects_mismatched_target_lot(self):
        self.stock_in(quantity=5)
        service.import_inbound(
            self.connection, self.user, self.project_a["id"],
            self.workbook("inbound", [
                self.inbound_row(order_no=f"AUTO-RK-{self.serial:04d}",
                                 batch="B1", production="2026-08-02",
                                 location="A-01-02"),
            ]),
        )
        with self.assertRaises(service.ImportValidationError) as caught:
            service.import_move(
                self.connection, self.user, self.project_a["id"],
                self.workbook("move", [self.move_row(quantity=2)]),
            )
        message = caught.exception.errors[0]["message"]
        self.assertIn("生产日期与源批次信息不一致", message)
        self.assertEqual(self.inventory_row(self.location["id"])["total_quantity"], 5)
        self.assertEqual(self.inventory_row(self.location_b["id"])["total_quantity"], 3)

    def test_move_merges_matching_target_lot(self):
        self.stock_in(quantity=5)
        service.import_inbound(
            self.connection, self.user, self.project_a["id"],
            self.workbook("inbound", [
                self.inbound_row(order_no=f"AUTO-RK-{self.serial:04d}",
                                 location="A-01-02"),
            ]),
        )
        service.import_move(
            self.connection, self.user, self.project_a["id"],
            self.workbook("move", [self.move_row(quantity=2)]),
        )
        self.assertEqual(self.inventory_row(self.location_b["id"])["total_quantity"], 5)

    def test_move_to_zero_keeps_source_row(self):
        self.stock_in(quantity=3)
        service.import_move(
            self.connection, self.user, self.project_a["id"],
            self.workbook("move", [self.move_row(quantity=3)]),
        )
        source = self.inventory_row(self.location["id"])
        self.assertEqual(source["total_quantity"], 0)
        self.assertEqual(
            self.inventory_row(self.location_b["id"])["total_quantity"], 3
        )

    def test_move_allows_held_and_prohibited_lots(self):
        self.stock_in(quantity=5, quality="扣留")
        self.connection.execute(
            "UPDATE inventory SET prohibit_outbound = 1"
        )
        self.connection.commit()
        service.import_move(
            self.connection, self.user, self.project_a["id"],
            self.workbook("move", [self.move_row(quantity=2)]),
        )
        target = self.inventory_row(self.location_b["id"])
        self.assertEqual(target["total_quantity"], 2)
        self.assertEqual(target["quality_status"], "扣留")
        self.assertEqual(target["prohibit_outbound"], 1)

    def test_move_order_no_unique_and_single_per_file(self):
        self.stock_in(quantity=5)
        service.import_move(
            self.connection, self.user, self.project_a["id"],
            self.workbook("move", [self.move_row(order_no="DUP-001")]),
        )
        with self.assertRaisesRegex(ValueError, "移库单号已存在"):
            service.import_move(
                self.connection, self.user, self.project_a["id"],
                self.workbook("move", [self.move_row(order_no="DUP-001")]),
            )
        with self.assertRaises(service.ImportValidationError) as caught:
            service.import_move(
                self.connection, self.user, self.project_a["id"],
                self.workbook("move", [
                    self.move_row(order_no="MIX-001"),
                    self.move_row(order_no="MIX-002"),
                ]),
            )
        self.assertIn("同一文件只能使用一个移库单号",
                      caught.exception.errors[0]["message"])

    def test_move_chain_within_one_file(self):
        self.stock_in(quantity=2)
        service.import_move(
            self.connection, self.user, self.project_a["id"],
            self.workbook("move", [
                self.move_row(order_no="CHAIN-001", quantity=2),
                self.move_row(order_no="CHAIN-001", from_loc="A-01-02",
                              to="A-01-01", quantity=2),
            ]),
        )
        self.assertEqual(self.inventory_row(self.location["id"])["total_quantity"], 2)
        self.assertEqual(
            self.inventory_row(self.location_b["id"])["total_quantity"], 0
        )

    def test_move_row_level_errors_aggregated(self):
        self.stock_in(quantity=1)
        with self.assertRaises(service.ImportValidationError) as caught:
            service.import_move(
                self.connection, self.user, self.project_a["id"],
                self.workbook("move", [
                    self.move_row(quantity=5, batch="B1"),
                    self.move_row(quantity=1, batch="B2"),
                ]),
            )
        messages = [error["message"] for error in caught.exception.errors]
        self.assertEqual(len(messages), 2)
        self.assertTrue(any("库存不足" in message for message in messages))
        self.assertTrue(any("无该商品批次的库存" in message for message in messages))
        self.assertEqual(
            self.inventory_row(self.location["id"])["total_quantity"], 1
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM stock_logs WHERE change_type LIKE 'move_%'"
            ).fetchone()[0],
            0,
        )

    def test_move_requires_project_admin(self):
        self.stock_in(quantity=5)
        # V8：管理员可代办项目作业（批量移库等），不再拦截
        service.import_move(
            self.connection, self.admin, self.project_a["id"],
            self.workbook("move", [self.move_row()]),
        )
        with self.assertRaisesRegex(service.Forbidden, "未授权"):
            service.import_move(
                self.connection, self.user, self.project_b["id"],
                self.workbook("move", [self.move_row()],
                              project_id=self.project_b["id"]),
            )

    def test_move_template_isolated_per_project(self):
        columns = service.get_template(
            self.connection, "move", self.project_a["id"]
        )["columns"]
        columns[0]["name"] = "移库单号（新）"
        updated = service.update_template(
            self.connection, self.user, self.project_a["id"], "move", columns
        )
        self.assertEqual(updated["version"], 2)
        other = service.get_template(
            self.connection, "move", self.project_b["id"]
        )
        self.assertEqual(other["version"], 1)
        self.assertEqual(other["columns"][0]["name"], "移库单号")
        orders = service.list_orders(
            self.connection, self.user, self.project_a["id"], order_type="move"
        )
        self.assertEqual(orders, [])

    def test_new_project_gets_move_template(self):
        template = service.get_template(
            self.connection, "move", self.project_b["id"]
        )
        self.assertEqual(template["type"], "move")
        self.assertEqual(
            [column["key"] for column in template["columns"]],
            [column["key"] for column in db.DEFAULT_TEMPLATES["move"]],
        )
        self.assertEqual(template["columns"][6]["name"], "源货位")
        self.assertEqual(template["columns"][7]["name"], "目标货位")


if __name__ == "__main__":
    unittest.main()
