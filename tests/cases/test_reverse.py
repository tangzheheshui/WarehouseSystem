"""管理员撤销：整单撤销、禁止负库存、重复撤销拒绝、反向流水留痕。"""
import shutil
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "server"))
sys.path.insert(0, str(PROJECT_ROOT / "tests" / "data"))

from warehouse_data import WarehouseDataFactory  # noqa: E402
import service  # noqa: E402


class ReverseOrderTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__import__("tempfile").mkdtemp())
        self.fixture = WarehouseDataFactory(self.directory / "reverse.db")
        self.admin = self.fixture.admin
        self.project_user = self.fixture.project_user
        self.project_a = self.fixture.project_a
        self.connection = self.fixture.connection

    def tearDown(self):
        self.connection.close()
        shutil.rmtree(self.directory, ignore_errors=True)

    def stock(self, product_id, location_name):
        row = self.connection.execute(
            """SELECT i.total_quantity FROM inventory i
               JOIN warehouse_locations l ON l.id = i.location_id
               WHERE i.project_id = ? AND i.product_id = ?
                 AND l.location_name = ? AND i.batch_no = 'AUTO-B001'""",
            (self.project_a["id"], product_id, location_name),
        ).fetchone()
        return row[0] if row else 0

    def inbound(self, order_no, quantity):
        return service.create_manual_inbound(
            self.connection, self.project_user, self.project_a["id"],
            {
                "orderNo": order_no, "orderDate": "2026-10-17",
                "productCode": "AUTO-P001", "qualityStatus": "良品",
                "batchNo": "AUTO-B001", "productionDate": "2026-01-01",
                "expiryDate": "", "quantity": quantity, "unit": "个",
                "locationName": "A-01", "purchaseOrderNo": "", "note": "",
            },
        )

    def test_reverse_inbound_restores_stock_and_blocks_double(self):
        before = self.stock("AUTO-P001", "A-01")
        order = self.inbound("AUTO-RK-REV1", 10)
        self.assertEqual(self.stock("AUTO-P001", "A-01"), before + 10)
        result = service.reverse_stock_order(self.connection, self.admin, order["id"])
        self.assertEqual(result["status"], "reversed")
        self.assertEqual(self.stock("AUTO-P001", "A-01"), before)
        logs = self.connection.execute(
            """SELECT change_type, change_quantity FROM stock_logs
               WHERE order_no = 'AUTO-RK-REV1' AND change_type = 'inbound_reversed'"""
        ).fetchall()
        self.assertEqual([(r["change_type"], r["change_quantity"]) for r in logs],
                         [("inbound_reversed", -10)])
        with self.assertRaisesRegex(ValueError, "不能重复撤销"):
            service.reverse_stock_order(self.connection, self.admin, order["id"])

    def test_reverse_outbound_after_picking_restores_batches(self):
        order = self.inbound("AUTO-RK-REV2", 10)
        self.assertEqual(self.stock("AUTO-P001", "A-01"), 10)
        outbound = service.create_manual_outbound(
            self.connection, self.project_user, self.project_a["id"],
            {
                "orderNo": "AUTO-CK-REV2", "orderDate": "2026-10-17",
                "productCode": "AUTO-P001", "qualityStatus": "良品",
                "batchNo": "AUTO-B001", "productionDate": "2026-01-01",
                "quantity": 6, "unit": "个", "locationName": "A-01",
                "recipient": "撤销测试", "note": "",
            },
        )
        self.assertEqual(self.stock("AUTO-P001", "A-01"), 4)
        service.reverse_stock_order(self.connection, self.admin, outbound["id"])
        self.assertEqual(self.stock("AUTO-P001", "A-01"), 10)
        pick = self.connection.execute(
            "SELECT COUNT(*) FROM picking_orders WHERE order_id = ?",
            (outbound["id"],),
        ).fetchone()[0]
        self.assertEqual(pick, 0)
        self.assertEqual(order["status"], "completed")

    def test_reverse_move_moves_stock_back(self):
        self.inbound("AUTO-RK-REV3", 10)
        move = service.create_manual_move(
            self.connection, self.project_user, self.project_a["id"],
            {
                "orderNo": "AUTO-YK-REV3", "orderDate": "2026-10-17",
                "productCode": "AUTO-P001", "batchNo": "AUTO-B001",
                "quantity": 4, "unit": "个", "fromLocation": "A-01",
                "toLocation": "A-02", "note": "",
            },
        )
        self.assertEqual(self.stock("AUTO-P001", "A-01"), 6)
        self.assertEqual(self.stock("AUTO-P001", "A-02"), 4)
        service.reverse_stock_order(self.connection, self.admin, move["id"])
        self.assertEqual(self.stock("AUTO-P001", "A-01"), 10)
        self.assertEqual(self.stock("AUTO-P001", "A-02"), 0)

    def test_reverse_inbound_blocked_when_stock_consumed(self):
        self.inbound("AUTO-RK-REV4", 10)
        service.create_manual_outbound(
            self.connection, self.project_user, self.project_a["id"],
            {
                "orderNo": "AUTO-CK-REV4", "orderDate": "2026-10-17",
                "productCode": "AUTO-P001", "qualityStatus": "良品",
                "batchNo": "AUTO-B001", "productionDate": "2026-01-01",
                "quantity": 8, "unit": "个", "locationName": "A-01",
                "recipient": "撤销测试", "note": "",
            },
        )
        inbound_id = self.connection.execute(
            "SELECT id FROM stock_orders WHERE order_no = 'AUTO-RK-REV4'"
        ).fetchone()[0]
        with self.assertRaisesRegex(ValueError, "负库存"):
            service.reverse_stock_order(self.connection, self.admin, inbound_id)



    def test_inventory_adjust_quality_and_prohibit(self):
        self.inbound("AUTO-RK-ADJ1", 10)
        inv = self.connection.execute(
            """SELECT i.id, i.production_date FROM inventory i
               JOIN warehouse_locations l ON l.id = i.location_id
               WHERE i.project_id = ? AND i.batch_no = 'AUTO-B001'""",
            (self.project_a["id"],),
        ).fetchone()
        result = service.adjust_inventory_batches(
            self.connection, self.admin, self.project_a["id"],
            [inv["id"]], production_date="2026-02-02",
            expiry_date="2027-02-02", purchase_order_no="PO-ADJ",
        )
        self.assertEqual(result["adjusted"], 1)
        row = self.connection.execute(
            "SELECT * FROM inventory WHERE id = ?", (inv["id"],)
        ).fetchone()
        self.assertEqual(row["production_date"], "2026-02-02")
        self.assertEqual(row["expiry_date"], "2027-02-02")
        self.assertEqual(row["purchase_order_no"], "PO-ADJ")
        self.assertEqual(self.stock("AUTO-P001", "A-01"), 10)

        service.set_inventory_quality(
            self.connection, self.admin, self.project_a["id"],
            [inv["id"]], "扣留",
        )
        service.set_inventory_prohibit(
            self.connection, self.admin, self.project_a["id"],
            [inv["id"]], 1,
        )
        row = self.connection.execute(
            "SELECT * FROM inventory WHERE id = ?", (inv["id"],)
        ).fetchone()
        self.assertEqual(row["quality_status"], "扣留")
        self.assertEqual(row["prohibit_outbound"], 1)
        audits = self.connection.execute(
            """SELECT COUNT(*) FROM audit_logs
               WHERE action LIKE 'inventory.%'"""
        ).fetchone()[0]
        self.assertGreaterEqual(audits, 3)

if __name__ == "__main__":
    unittest.main()
