#!/usr/bin/env python3
"""本地演示数据种子脚本。

新电脑上 clone 仓库后运行一次：

    python3 seed_demo.py
    ./run.sh --platform --domain localhost --port 8000

即可得到一家「ACME物流」演示公司，含仓库、货位、项目、商品、用户、
出入库单与库存。数据全部落在 data/ 目录（已在 .gitignore 中，不入 git），
换电脑只需重新执行本脚本即可复原同一套演示数据。

脚本幂等：公司已存在则跳过创建；业务数据只在空库（无商品）时写入。
"""

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "server"))

import auth
import db
import excelio
import platform_registry
import service

DEMO_PREFIX = "acme"
DEMO_COMPANY_NAME = "ACME物流"
CONSOLE_USERNAME = "wanshun"
CONSOLE_PASSWORD = "Local123456"      # 万顺控制台账号，仅首次初始化时生效
COMPANY_PASSWORD = "Admin123456"      # 公司管理员初始密码，首次登录强制改密

WAREHOUSE = ("上海青浦一号仓", "上海市青浦区华新镇示范路88号")

LOCATIONS = [
    # (库区, 货区, 货位)
    ("A库区", "食品区", "A-01"),
    ("A库区", "食品区", "A-02"),
    ("A库区", "食品区", "A-03"),
    ("B库区", "日分区", "B-01"),
    ("B库区", "日分区", "B-02"),
]

PROJECTS = [
    # (项目名称, 绑定库区, 备注)
    ("味佳食品-2026秋季订单", "A库区", "味佳食品秋季订单入出库与库存管理"),
    ("洁雅日用-常备库存", "B库区", "洁雅日用百货常备库存"),
]

PRODUCTS = [
    # (编号, 名称, 规格, 单位, 分类, 单件重量kg, 体积m³, 保质天数, 所属项目)
    ("SP-F001", "原味坚果礼盒", "1kg/盒", "盒", "食品", 1.0, 0.012, 180,
     ["味佳食品-2026秋季订单"]),
    ("SP-F002", "手工曲奇饼干", "500g/盒", "盒", "食品", 0.5, 0.006, 120,
     ["味佳食品-2026秋季订单"]),
    ("SP-F003", "冷榨山茶油", "1L/瓶", "瓶", "食品", 1.1, 0.003, 540,
     ["味佳食品-2026秋季订单"]),
    ("RJ-D001", "竹纤维抽纸", "100抽*3层/提", "提", "日用百货", 0.3, 0.004, None,
     ["洁雅日用-常备库存"]),
    ("RJ-D002", "洗衣凝珠", "30颗/袋", "袋", "日用百货", 0.6, 0.005, None,
     ["洁雅日用-常备库存"]),
]

USERS = [
    # (用户名, 姓名, 手机号, 角色, 初始密码, 使用密码, 授权项目)
    ("wangli", "王丽", "13811112222", "project_admin",
     "Wangli123456", "Wangli456789", ["味佳食品-2026秋季订单"]),
    ("zhangchen", "张晨", "13933334444", "project_admin",
     "Zhangchen123456", "Zhangchen456789", ["洁雅日用-常备库存"]),
]

INBOUND_ORDERS = [
    # (项目, 入库单号, 入库日期, 商品, 批号, 生产日期, 数量, 单位, 货位, 采购单号, 备注)
    ("味佳食品-2026秋季订单", "RK20260901001", "2026-09-01", "SP-F001",
     "JG20260901", "2026-09-01", 100, "盒", "A-01", "PO2026081501", "月度补货"),
    ("味佳食品-2026秋季订单", "RK20261008001", "2026-10-08", "SP-F001",
     "JG20261008", "2026-10-08", 200, "盒", "A-02", "PO2026092003", "双节备货"),
    ("味佳食品-2026秋季订单", "RK20260915001", "2026-09-15", "SP-F002",
     "CK20260915", "2026-09-15", 150, "盒", "A-01", "PO2026090102", None),
    ("味佳食品-2026秋季订单", "RK20261012001", "2026-10-12", "SP-F003",
     "CY20261012", "2026-10-12", 80, "瓶", "A-03", "PO2026092801", None),
    ("洁雅日用-常备库存", "RK20260920001", "2026-09-20", "RJ-D001",
     "ZH20260920", "2026-09-20", 500, "提", "B-01", "PO2026091002", None),
    ("洁雅日用-常备库存", "RK20261010001", "2026-10-10", "RJ-D002",
     "XN20261010", "2026-10-10", 300, "袋", "B-02", "PO2026093001", None),
]

OUTBOUND_ORDERS = [
    # (项目, 出库单号, 出库日期, 商品, 数量, 单位, 收货方, 备注)
    ("味佳食品-2026秋季订单", "CK20261014001", "2026-10-14", "SP-F001",
     30, "盒", "联华超市", "秋季门店调拨"),
]


def build_order_workbook(connection, order_type, project_id, rows):
    """按项目模板列顺序构造导入工作簿。"""
    _, columns = db.columns_for(connection, order_type, project_id)
    table = [[column["name"] for column in columns]]
    table.extend(
        [row.get(column["key"], "") for column in columns] for row in rows
    )
    return excelio.build_workbook(table, f"演示{order_type}")


def ensure_company(registry):
    record = registry.get_company(DEMO_PREFIX)
    if record:
        print(f"公司 {DEMO_PREFIX}（{record['name']}）已存在，跳过创建")
        return
    registry.create_company(
        DEMO_PREFIX, DEMO_COMPANY_NAME, years=1, remark="演示开通",
        operator=CONSOLE_USERNAME,
    )
    registry.reset_company_admin_password(DEMO_PREFIX, COMPANY_PASSWORD)
    print(
        f"已创建公司「{DEMO_COMPANY_NAME}」，管理员 admin / {COMPANY_PASSWORD}"
        "（首次登录会要求改密）"
    )


def seed_business_data(connection):
    admin = connection.execute(
        "SELECT * FROM users WHERE username = 'admin' AND role = 'admin'"
    ).fetchone()

    def fetch_one(sql, params):
        return connection.execute(sql, params).fetchone()

    # ---- 主数据：逐项判存，重跑或中断后重跑都安全 ----
    warehouse = fetch_one(
        "SELECT * FROM warehouses WHERE warehouse_name = ?", (WAREHOUSE[0],)
    )
    if warehouse:
        warehouse = dict(warehouse)
    else:
        warehouse = service.create_warehouse(
            connection, admin, WAREHOUSE[0], WAREHOUSE[1]
        )
    for storage_area, cargo_area, location_name in LOCATIONS:
        if not fetch_one(
            """SELECT 1 FROM warehouse_locations
               WHERE warehouse_id = ? AND location_name = ?""",
            (warehouse["id"], location_name),
        ):
            service.create_location(
                connection, admin, warehouse["id"],
                storage_area, cargo_area, location_name,
            )

    projects = {}
    for name, storage_area, remark in PROJECTS:
        row = fetch_one(
            "SELECT * FROM projects WHERE project_name = ?", (name,)
        )
        projects[name] = dict(row) if row else service.create_project(
            connection, admin, name, warehouse["id"], storage_area, remark
        )

    # 商品直接归属项目：演示数据里每个商品只属于一个项目
    for code, name, spec, unit, category, weight, volume, shelf, owners in PRODUCTS:
        if fetch_one("SELECT 1 FROM products WHERE product_id = ?", (code,)):
            continue
        owner = owners[0]
        service.create_product(
            connection, admin, code, name,
            project_id=projects[owner]["id"],
            spec=spec, unit=unit, category=category,
            unit_weight=weight, volume=volume, shelf_life_days=shelf,
        )

    for username, display_name, phone, role, initial, final, owners in USERS:
        if fetch_one("SELECT 1 FROM users WHERE username = ?", (username,)):
            continue
        created = service.create_user(
            connection, admin, username, display_name, phone, role, initial,
            [projects[name]["id"] for name in owners],
        )
        auth.change_password(connection, created, initial, final)

    # ---- 出入库：有库存即视为已导入，避免重复入账 ----
    if fetch_one("SELECT 1 FROM inventory LIMIT 1", ()):
        print("出入库演示数据已存在，跳过导入")
        return

    # 出入库属项目作业，须由该项目授权的项目管理员执行
    users = {
        row["username"]: row
        for row in connection.execute("SELECT * FROM users").fetchall()
    }
    project_operator = {}
    for username, _, _, _, _, _, owners in USERS:
        for name in owners:
            project_operator.setdefault(name, users[username])

    project_ids = {name: row["id"] for name, row in projects.items()}
    for (project_name, order_no, order_date, product_code, batch_no,
         production_date, quantity, unit, location_name, purchase_no,
         note) in INBOUND_ORDERS:
        project_id = project_ids[project_name]
        service.import_inbound(
            connection, project_operator[project_name], project_id,
            build_order_workbook(connection, "inbound", project_id, [{
                "order_no": order_no,
                "order_date": order_date,
                "product_code": product_code,
                "quality_status": "良品",
                "batch_no": batch_no,
                "production_date": production_date,
                "expiry_date": "",
                "quantity": quantity,
                "unit": unit,
                "location_name": location_name,
                "purchase_order_no": purchase_no,
                "note": note or "",
            }]),
        )

    for (project_name, order_no, order_date, product_code, quantity,
         unit, recipient, note) in OUTBOUND_ORDERS:
        project_id = project_ids[project_name]
        service.import_outbound(
            connection, project_operator[project_name], project_id,
            build_order_workbook(connection, "outbound", project_id, [{
                "order_no": order_no,
                "order_date": order_date,
                "product_code": product_code,
                "quality_status": "良品",
                "batch_no": "",
                "production_date": "",
                "quantity": quantity,
                "unit": unit,
                "location_name": "",
                "recipient": recipient,
                "note": note,
            }]),
        )

    inventory = connection.execute(
        """SELECT COUNT(*) FROM inventory WHERE total_quantity > 0"""
    ).fetchone()[0]
    print(
        f"演示数据写入完成：仓库 {WAREHOUSE[0]}，货位 {len(LOCATIONS)} 个，"
        f"项目 {len(PROJECTS)} 个，商品 {len(PRODUCTS)} 个，"
        f"入库单 {len(INBOUND_ORDERS)} 张，出库单 {len(OUTBOUND_ORDERS)} 张，"
        f"库存批次 {inventory} 条"
    )


def main():
    parser = argparse.ArgumentParser(description="构造本地演示数据")
    parser.add_argument(
        "--platform-data", default=None,
        help="平台数据目录（默认 <项目>/data）",
    )
    args = parser.parse_args()
    data_dir = Path(
        args.platform_data
        or os.environ.get("WMS_PLATFORM_DATA")
        or PROJECT_ROOT / "data"
    )
    os.environ.setdefault("WMS_PLATFORM_ADMIN_PASSWORD", CONSOLE_PASSWORD)

    registry = platform_registry.PlatformRegistry(data_dir / "platform.json")
    if registry.ensure_platform_admin():
        print(
            f"控制台账号已初始化：{CONSOLE_USERNAME} / {CONSOLE_PASSWORD}"
        )
    ensure_company(registry)

    record = registry.get_company(DEMO_PREFIX)
    connection = db.connect(record["db_path"])
    try:
        seed_business_data(connection)
    finally:
        connection.close()

    print(f"完成。启动服务：./run.sh --platform --domain localhost --port 8000")


if __name__ == "__main__":
    main()
