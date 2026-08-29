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

        self.project_a = service.create_project(
            self.connection, self.admin, "自动化测试项目A", "双角色验收"
        )
        self.project_b = service.create_project(
            self.connection, self.admin, "隔离项目B", "项目隔离"
        )
        self.product = service.create_product(
            self.connection, self.admin, "AUTO-P001", "自动测试商品", "100g", "个"
        )
        self.disabled_product = service.create_product(
            self.connection, self.admin, "AUTO-P002", "停用商品", "200g", "箱"
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
        return {
            "product_code": "AUTO-P001",
            "product_name": "自动测试商品",
            "spec": "100g",
            "unit": "个",
            "batch_no": "AUTO-B001",
            "production_date": "2026-01-01",
            "shelf_life_days": 30,
            "inbound_date": "2026-01-02",
            "quantity": 5,
            "warehouse_name": "自动化测试仓",
            "storage_area": "A库区",
            "cargo_area": "A货区",
            "location_name": "A-01",
            "quality_status": "良品",
            "purchase_order_no": "AUTO-PO-001",
            "note": "",
        }

    def workbook(self, order_type, rows, project_id=None):
        _, columns = db.columns_for(
            self.connection,
            order_type,
            project_id or self.project_a["id"],
        )
        values_by_key = {
            "inbound": self.inbound_defaults(),
            "outbound": {
                "product_code": "AUTO-P001",
                "product_name": "自动测试商品",
                "quantity": 1,
                "note": "",
            },
        }[order_type]
        table = [[column["name"] for column in columns]]
        for row in rows:
            merged = {**values_by_key, **row}
            table.append([merged.get(column["key"], "") for column in columns])
        return excelio.build_workbook(table, f"自动化{order_type}")
