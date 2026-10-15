"""Build isolated warehouse data and in-memory Excel workbooks."""

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "server"))

import auth
import db
import excelio
import service


ADMIN_INITIAL_PASSWORD = "Zhangsan123456"
ADMIN_PASSWORD = "Zhangsan456789"
PROJECT_USER_INITIAL_PASSWORD = "Liuhaifeng123456"
PROJECT_USER_PASSWORD = "Liuhaifeng456789"


class WarehouseDataFactory:
    def __init__(self, db_path):
        self.db_path = db_path
        self.order_serial = 0
        db.init_db(db_path)
        self.connection = db.connect(db_path)

        bootstrap = self.connection.execute(
            "SELECT * FROM users WHERE username = 'admin'"
        ).fetchone()
        created_admin = service.create_user(
            self.connection,
            bootstrap,
            "zhangsan",
            "张三",
            "13800000001",
            "admin",
            ADMIN_INITIAL_PASSWORD,
        )
        auth.change_password(
            self.connection,
            created_admin,
            ADMIN_INITIAL_PASSWORD,
            ADMIN_PASSWORD,
        )
        self.admin = self.connection.execute(
            "SELECT * FROM users WHERE username = 'zhangsan'"
        ).fetchone()

        self.warehouse = service.create_warehouse(
            self.connection, self.admin, "自动化测试仓", "测试地址"
        )
        self.location = service.create_location(
            self.connection,
            self.admin,
            self.warehouse["id"],
            "A库区",
            "A货区",
            "A-01",
        )
        self.location_b = service.create_location(
            self.connection,
            self.admin,
            self.warehouse["id"],
            "A库区",
            "A货区",
            "A-02",
        )
        self.project_a = service.create_project(
            self.connection, self.admin, "自动化测试项目A",
            self.warehouse["id"], "A库区", "双角色验收",
        )
        self.project_b = service.create_project(
            self.connection, self.admin, "隔离项目B",
            self.warehouse["id"], "A库区", "项目隔离",
        )
        self.product = service.create_product(
            self.connection, self.admin, "AUTO-P001", "自动测试商品",
            "100g", "个", "测试分类", 0.1, 0.01, 30,
        )
        self.disabled_product = service.create_product(
            self.connection, self.admin, "AUTO-P002", "停用商品", "200g", "箱"
        )
        service.set_project_products(
            self.connection, self.admin, self.project_a["id"],
            ["AUTO-P001"],
        )
        service.set_project_products(
            self.connection, self.admin, self.project_b["id"],
            ["AUTO-P001"],
        )
        service.update_product(
            self.connection,
            self.admin,
            "AUTO-P002",
            status="disabled",
        )
        self.disabled_product = self.connection.execute(
            "SELECT * FROM products WHERE product_id = 'AUTO-P002'"
        ).fetchone()
        created_project_user = service.create_user(
            self.connection,
            self.admin,
            "liuhaifeng",
            "刘海峰",
            "13900000001",
            "project_admin",
            PROJECT_USER_INITIAL_PASSWORD,
            [self.project_a["id"]],
        )
        auth.change_password(
            self.connection,
            created_project_user,
            PROJECT_USER_INITIAL_PASSWORD,
            PROJECT_USER_PASSWORD,
        )
        self.project_user = self.connection.execute(
            "SELECT * FROM users WHERE username = 'liuhaifeng'"
        ).fetchone()

    def close(self):
        self.connection.close()

    def inbound_defaults(self):
        self.order_serial += 1
        return {
            "order_no": f"AUTO-RK-{self.order_serial:04d}",
            "order_date": "2026-01-02",
            "product_code": "AUTO-P001",
            "quality_status": "良品",
            "batch_no": "AUTO-B001",
            "production_date": "2026-01-01",
            "expiry_date": "",
            "quantity": 5,
            "unit": "个",
            "location_name": "A-01",
            "purchase_order_no": "AUTO-PO-001",
            "note": "",
        }

    def workbook(self, order_type, rows, project_id=None):
        self.order_serial += 1
        _, columns = db.columns_for(
            self.connection,
            order_type,
            project_id or self.project_a["id"],
        )
        values_by_key = {
            "inbound": self.inbound_defaults(),
            "outbound": {
                "order_no": f"AUTO-CK-{self.order_serial:04d}",
                "order_date": "2026-01-03",
                "product_code": "AUTO-P001",
                "quality_status": "良品",
                "batch_no": "",
                "production_date": "",
                "quantity": 1,
                "unit": "个",
                "location_name": "",
                "recipient": "自动化收货方",
                "note": "",
            },
            "move": {
                "order_no": f"AUTO-MV-{self.order_serial:04d}",
                "order_date": "2026-01-04",
                "product_code": "AUTO-P001",
                "batch_no": "AUTO-B001",
                "quantity": 1,
                "unit": "个",
                "from_location": "A-01",
                "to_location": "A-02",
                "note": "",
            },
        }[order_type]
        table = [[column["name"] for column in columns]]
        for row in rows:
            merged = {**values_by_key, **row}
            table.append([merged.get(column["key"], "") for column in columns])
        return excelio.build_workbook(table, f"自动化{order_type}")
