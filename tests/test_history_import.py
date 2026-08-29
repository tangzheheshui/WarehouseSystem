import csv
import io
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

import db
import service


HEADERS = [
    "库区", "货区", "货位", "货品编号", "货品名称", "生产批号", "生产日期",
    "到期日期", "库存总数", "分拣占用数", "移动占用数", "可用库存",
    "采购单号", "品质标记", "入库日", "禁止出库", "大单位数", "大单位",
    "中单位数", "中单位", "标准单位数", "标准单位", "仓库",
]


class HistoryImportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.directory.name) / "warehouse.db"
        db.init_db(self.db_path)
        self.connection = db.connect(self.db_path)
        self.admin = self.connection.execute(
            "SELECT * FROM users WHERE username = 'admin'"
        ).fetchone()
        self.project = service.create_project(
            self.connection, self.admin, "历史项目"
        )

    def tearDown(self):
        self.connection.close()
        self.directory.cleanup()

    def csv_data(self):
        output = io.StringIO(newline="")
        writer = csv.writer(output, delimiter="\t")
        writer.writerow(HEADERS)
        writer.writerow([
            '="A库区"', "A货区", '="A-01"', '="H100"', "历史商品", "HB001",
            "2026/07/11", "2026/10/09", 12, 2, 3, 7, "PO-HIST", "良品",
            "2026-07-12", "否", 1, "箱", 2, "盒", 12, "个", "历史仓库",
        ])
        return output.getvalue().encode("utf-16")

    def test_import_history_initializes_inventory_and_archives(self):
        result = service.import_history_csv(
            self.connection, self.admin, self.project["id"], self.csv_data()
        )
        self.assertEqual(result["lots"], 1)
        inventory = self.connection.execute(
            """SELECT i.*, w.warehouse_name, l.storage_area, l.cargo_area,
                      l.location_name
               FROM inventory i
               JOIN warehouse_locations l ON l.id = i.location_id
               JOIN warehouses w ON w.id = l.warehouse_id
               WHERE i.project_id = ?""",
            (self.project["id"],),
        ).fetchall()
        self.assertEqual(len(inventory), 1)
        row = inventory[0]
        self.assertEqual(row["product_id"], "H100")
        self.assertEqual(row["production_date"], "2026-07-11")
        self.assertEqual(row["expiry_date"], "2026-10-09")
        self.assertEqual(row["total_quantity"], 12)
        self.assertEqual(row["picking_reserved"], 2)
        self.assertEqual(row["moving_reserved"], 3)
        self.assertEqual(row["warehouse_name"], "历史仓库")
        self.assertEqual(row["storage_area"], "A库区")
        self.assertEqual(row["location_name"], "A-01")
        self.assertEqual(self.connection.execute(
            "SELECT COUNT(*) FROM stock_logs WHERE change_type = 'history_import'"
        ).fetchone()[0], 1)

        with self.assertRaisesRegex(ValueError, "仅可初始化导入一次"):
            service.import_history_csv(
                self.connection, self.admin, self.project["id"], self.csv_data()
            )


if __name__ == "__main__":
    unittest.main()
