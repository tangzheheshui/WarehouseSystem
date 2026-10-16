import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from security import hash_password


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "warehouse.db"

DEFAULT_TEMPLATES = {
    "inbound": [
        {"key": "order_no", "name": "入库通知单号", "required": True, "type": "text"},
        {"key": "order_date", "name": "入库日期", "required": True, "type": "date"},
        {"key": "product_code", "name": "货品编号", "required": True, "type": "text"},
        {"key": "quality_status", "name": "品质标记", "required": False, "type": "text"},
        {"key": "batch_no", "name": "生产批号", "required": True, "type": "text"},
        {"key": "production_date", "name": "生产日期", "required": True, "type": "date"},
        {"key": "expiry_date", "name": "到期日期", "required": False, "type": "date"},
        {"key": "quantity", "name": "数量", "required": True, "type": "integer"},
        {"key": "unit", "name": "单位", "required": True, "type": "text"},
        {"key": "location_name", "name": "货位", "required": True, "type": "text"},
        {"key": "purchase_order_no", "name": "采购单号", "required": False, "type": "text"},
        {"key": "note", "name": "备注", "required": False, "type": "text"},
    ],
    "outbound": [
        {"key": "order_no", "name": "配货单号", "required": True, "type": "text"},
        {"key": "order_date", "name": "出库日期", "required": True, "type": "date"},
        {"key": "product_code", "name": "货品编号", "required": True, "type": "text"},
        {"key": "quality_status", "name": "品质", "required": False, "type": "text"},
        {"key": "batch_no", "name": "生产批号", "required": False, "type": "text"},
        {"key": "production_date", "name": "生产日期", "required": False, "type": "date"},
        {"key": "quantity", "name": "数量", "required": True, "type": "integer"},
        {"key": "unit", "name": "单位", "required": True, "type": "text"},
        {"key": "location_name", "name": "指定货位", "required": False, "type": "text"},
        {"key": "recipient", "name": "收货方", "required": False, "type": "text"},
        {"key": "note", "name": "备注", "required": False, "type": "text"},
    ],
    "move": [
        {"key": "order_no", "name": "移库单号", "required": True, "type": "text"},
        {"key": "order_date", "name": "移库日期", "required": True, "type": "date"},
        {"key": "product_code", "name": "货品编号", "required": True, "type": "text"},
        {"key": "batch_no", "name": "生产批号", "required": True, "type": "text"},
        {"key": "quantity", "name": "数量", "required": True, "type": "integer"},
        {"key": "unit", "name": "单位", "required": True, "type": "text"},
        {"key": "from_location", "name": "源货位", "required": True, "type": "text"},
        {"key": "to_location", "name": "目标货位", "required": True, "type": "text"},
        {"key": "note", "name": "备注", "required": False, "type": "text"},
    ],
}

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('active', 'disabled')),
    warehouse_id INTEGER REFERENCES warehouses(id),
    storage_area TEXT,
    remark TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS warehouses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    warehouse_name TEXT NOT NULL UNIQUE,
    address TEXT,
    contact_name TEXT,
    contact_phone TEXT,
    status TEXT NOT NULL CHECK(status IN ('active', 'disabled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS storage_areas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'disabled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(warehouse_id, name)
);

CREATE TABLE IF NOT EXISTS cargo_areas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    storage_area_id INTEGER NOT NULL REFERENCES storage_areas(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'disabled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(storage_area_id, name)
);

CREATE TABLE IF NOT EXISTS warehouse_locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
    storage_area TEXT NOT NULL,
    cargo_area TEXT NOT NULL,
    location_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active', 'disabled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(warehouse_id, storage_area, cargo_area, location_name)
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    contact_phone TEXT,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'project_admin')),
    status TEXT NOT NULL CHECK(status IN ('active', 'disabled')),
    must_change_password INTEGER NOT NULL DEFAULT 1,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_projects (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY(user_id, project_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    product_id TEXT PRIMARY KEY,
    product_name TEXT NOT NULL,
    project_id INTEGER REFERENCES projects(id),
    spec TEXT,
    unit TEXT,
    category TEXT,
    unit_weight REAL,
    volume REAL,
    shelf_life_days INTEGER,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'disabled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    product_id TEXT NOT NULL REFERENCES products(product_id),
    location_id INTEGER NOT NULL REFERENCES warehouse_locations(id),
    batch_no TEXT NOT NULL,
    production_date TEXT,
    expiry_date TEXT,
    inbound_date TEXT,
    total_quantity INTEGER NOT NULL CHECK(total_quantity >= 0),
    picking_reserved INTEGER NOT NULL DEFAULT 0 CHECK(picking_reserved >= 0),
    moving_reserved INTEGER NOT NULL DEFAULT 0 CHECK(moving_reserved >= 0),
    quality_status TEXT NOT NULL DEFAULT '良品',
    prohibit_outbound INTEGER NOT NULL DEFAULT 0 CHECK(prohibit_outbound IN (0, 1)),
    purchase_order_no TEXT,
    last_inbound_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, product_id, location_id, batch_no)
);

CREATE TABLE IF NOT EXISTS stock_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    order_no TEXT NOT NULL UNIQUE,
    order_type TEXT NOT NULL CHECK(order_type IN ('inbound', 'outbound', 'history_import', 'move')),
    status TEXT NOT NULL CHECK(status IN ('pending_picking', 'completed')),
    operator_id INTEGER NOT NULL REFERENCES users(id),
    operator_name TEXT NOT NULL,
    template_version INTEGER NOT NULL,
    note TEXT,
    order_date TEXT,
    recipient TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS stock_order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES stock_orders(id) ON DELETE CASCADE,
    row_no INTEGER NOT NULL,
    product_id TEXT NOT NULL,
    product_name TEXT NOT NULL,
    spec TEXT,
    batch_no TEXT,
    location_id INTEGER,
    to_location_id INTEGER REFERENCES warehouse_locations(id),
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    order_no TEXT NOT NULL,
    product_id TEXT NOT NULL,
    batch_no TEXT NOT NULL,
    location_id INTEGER NOT NULL REFERENCES warehouse_locations(id),
    change_type TEXT NOT NULL CHECK(change_type IN ('inbound', 'outbound', 'history_import', 'move_out', 'move_in')),
    change_quantity INTEGER NOT NULL,
    operator_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS picking_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pick_no TEXT NOT NULL UNIQUE,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    order_id INTEGER NOT NULL UNIQUE REFERENCES stock_orders(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK(status IN ('pending', 'completed')),
    operator_name TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS picking_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    picking_order_id INTEGER NOT NULL REFERENCES picking_orders(id) ON DELETE CASCADE,
    product_id TEXT NOT NULL,
    product_name TEXT NOT NULL,
    spec TEXT,
    batch_no TEXT NOT NULL,
    production_date TEXT,
    quality_status TEXT,
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    location_id INTEGER NOT NULL REFERENCES warehouse_locations(id),
    sequence INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    template_type TEXT NOT NULL CHECK(template_type IN ('inbound', 'outbound', 'move')),
    version INTEGER NOT NULL,
    columns_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'disabled')),
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, template_type)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES projects(id),
    user_id INTEGER,
    user_name TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_locations_warehouse
    ON warehouse_locations(warehouse_id, storage_area, cargo_area, location_name);
CREATE INDEX IF NOT EXISTS idx_user_projects_project ON user_projects(project_id);
CREATE INDEX IF NOT EXISTS idx_products_project ON products(project_id);
CREATE INDEX IF NOT EXISTS idx_inventory_project_product
    ON inventory(project_id, product_id);
CREATE INDEX IF NOT EXISTS idx_orders_project_time ON stock_orders(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_order_items_order ON stock_order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_stock_logs_project_product
    ON stock_logs(project_id, product_id, created_at);
CREATE INDEX IF NOT EXISTS idx_picking_orders_order ON picking_orders(order_id);
CREATE INDEX IF NOT EXISTS idx_picking_orders_project ON picking_orders(project_id);
CREATE INDEX IF NOT EXISTS idx_templates_project ON templates(project_id, template_type);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_audit_logs_project_time
    ON audit_logs(project_id, created_at);
"""

# 以下三条 DDL 与 SCHEMA 中对应表保持同步（含移库所需的 CHECK 扩展），
# 仅供 _migrate_v5 重建旧表使用。
STOCK_ORDERS_DDL = """
CREATE TABLE stock_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    order_no TEXT NOT NULL UNIQUE,
    order_type TEXT NOT NULL CHECK(order_type IN ('inbound', 'outbound', 'history_import', 'move')),
    status TEXT NOT NULL CHECK(status IN ('pending_picking', 'completed')),
    operator_id INTEGER NOT NULL REFERENCES users(id),
    operator_name TEXT NOT NULL,
    template_version INTEGER NOT NULL,
    note TEXT,
    order_date TEXT,
    recipient TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
)
"""

STOCK_LOGS_DDL = """
CREATE TABLE stock_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    order_no TEXT NOT NULL,
    product_id TEXT NOT NULL,
    batch_no TEXT NOT NULL,
    location_id INTEGER NOT NULL REFERENCES warehouse_locations(id),
    change_type TEXT NOT NULL CHECK(change_type IN ('inbound', 'outbound', 'history_import', 'move_out', 'move_in')),
    change_quantity INTEGER NOT NULL,
    operator_name TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

TEMPLATES_DDL = """
CREATE TABLE templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    template_type TEXT NOT NULL CHECK(template_type IN ('inbound', 'outbound', 'move')),
    version INTEGER NOT NULL,
    columns_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'disabled')),
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, template_type)
)
"""


def db_path():
    return Path(os.environ.get("WMS_DB_PATH", str(DEFAULT_DB_PATH)))


def connect(path=None):
    target = Path(path) if path else db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(target), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def _columns(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _rows(connection, table):
    return [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]


def _create_sql(connection, table):
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row[0] if row else ""


def _parse_date(value):
    if not value:
        return None
    for pattern in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(str(value)[:10], pattern).date()
        except ValueError:
            pass
    return None


def _ensure_location(connection, warehouse_id, location_name, created_at):
    location = str(location_name or "").strip() or "默认货位"
    row = connection.execute(
        """SELECT id FROM warehouse_locations
           WHERE warehouse_id = ? AND storage_area = '默认库区'
             AND cargo_area = '默认货区' AND location_name = ?""",
        (warehouse_id, location),
    ).fetchone()
    if row:
        return row["id"]
    cursor = connection.execute(
        """INSERT INTO warehouse_locations
           (warehouse_id, storage_area, cargo_area, location_name, status,
            created_at, updated_at)
           VALUES (?, '默认库区', '默认货区', ?, 'active', ?, ?)""",
        (warehouse_id, location, created_at, created_at),
    )
    return cursor.lastrowid


def _migrate_legacy(connection):
    """Rebuild V1 tables and move all historical data into the default project."""
    table_names = {
        "sessions", "inventory", "stock_order_items", "picking_items",
        "picking_orders", "stock_orders", "stock_logs", "templates",
        "audit_logs", "user_projects", "users", "products",
        "warehouse_locations", "warehouses", "projects",
    }
    existing = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    data = {name: _rows(connection, name) for name in table_names & existing}

    connection.execute("PRAGMA foreign_keys = OFF")
    connection.commit()
    connection.execute("BEGIN IMMEDIATE")
    for name in table_names & existing:
        connection.execute(f"DROP TABLE {name}")
    connection.executescript(SCHEMA)
    sql_now = "datetime('now')"
    migration_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    connection.execute(
        f"""INSERT INTO projects(project_name, status, remark, created_at, updated_at)
            VALUES ('默认项目', 'active', 'V1数据迁移项目', {sql_now}, {sql_now})"""
    )
    project_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
    connection.execute(
        f"""INSERT INTO warehouses(warehouse_name, status, created_at, updated_at)
            VALUES ('默认仓库', 'active', {sql_now}, {sql_now})"""
    )
    warehouse_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
    connection.execute(
        """UPDATE projects SET warehouse_id = ?, storage_area = '默认库区'
           WHERE id = ?""",
        (warehouse_id, project_id),
    )

    admin_configured = False
    for row in data.get("users", []):
        role = "admin" if row["role"] == "admin" else "project_admin"
        if role == "admin" and not admin_configured:
            username = os.environ.get("WMS_ADMIN_USERNAME", "admin")
            display_name = os.environ.get("WMS_ADMIN_NAME", "管理员")
            admin_configured = True
        else:
            username = f"user{row['id']}"
            display_name = f"用户{row['id']}"
        connection.execute(
            """INSERT INTO users
               (id, username, display_name, contact_phone, password_hash, role,
                status, must_change_password, failed_attempts, locked_until,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["id"], username, display_name, row.get("phone"),
                row["password_hash"], role, row["status"],
                row["must_change_password"], row["failed_attempts"],
                row["locked_until"], row["created_at"], row["updated_at"],
            ),
        )
        if role == "project_admin":
            connection.execute(
                f"""INSERT INTO user_projects(user_id, project_id, created_at)
                    VALUES (?, ?, {sql_now})""",
                (row["id"], project_id),
            )

    for row in data.get("sessions", []):
        connection.execute(
            """INSERT INTO sessions(token_hash, user_id, expires_at, created_at)
               VALUES (?, ?, ?, ?)""",
            (row["token_hash"], row["user_id"], row["expires_at"], row["created_at"]),
        )

    for row in data.get("products", []):
        connection.execute(
            """INSERT INTO products
               (product_id, product_name, project_id, spec, unit, status,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, NULL, 'active', ?, ?)""",
            (row["product_id"], row["product_name"], project_id,
             row["spec"], row["created_at"], row["updated_at"]),
        )

    location_ids = {}
    for row in data.get("inventory", []):
        location_ids[row["location"]] = _ensure_location(
            connection, warehouse_id, row["location"], row["created_at"]
        )
        production = _parse_date(row.get("production_date"))
        expiry = None
        if production and row.get("shelf_life_days") is not None:
            try:
                expiry = (production + timedelta(days=int(row["shelf_life_days"]))).isoformat()
            except (TypeError, ValueError):
                expiry = None
        connection.execute(
            """INSERT INTO inventory
               (project_id, product_id, location_id, batch_no, production_date,
                expiry_date, inbound_date, total_quantity, picking_reserved,
                moving_reserved, quality_status, prohibit_outbound,
                purchase_order_no, last_inbound_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, '良品', 0, NULL, ?, ?, ?)""",
            (
                project_id, row["product_id"], location_ids[row["location"]],
                row["batch_no"], production.isoformat() if production else None,
                expiry, row["last_inbound_at"][:10], row["quantity"],
                row["last_inbound_at"], row["created_at"], row["updated_at"],
            ),
        )

    for row in data.get("stock_orders", []):
        connection.execute(
            """INSERT INTO stock_orders
               (id, project_id, order_no, order_type, status, operator_id,
                operator_name, template_version, note, created_at, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["id"], project_id, row["order_no"], row["order_type"],
                row["status"], row["operator_id"], row["operator_name"],
                row["template_version"], row["note"], row["created_at"],
                row["completed_at"],
            ),
        )
    for row in data.get("stock_order_items", []):
        location_id = None
        if row.get("location"):
            location_id = location_ids.setdefault(
                row["location"],
                _ensure_location(connection, warehouse_id, row["location"], row["created_at"]),
            )
        connection.execute(
            """INSERT INTO stock_order_items
               (order_id, row_no, product_id, product_name, spec, batch_no,
                location_id, quantity, note, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["order_id"], row["row_no"], row["product_id"],
                row["product_name"], row["spec"], row["batch_no"], location_id,
                row["quantity"], row["note"], row["created_at"],
            ),
        )
    for row in data.get("stock_logs", []):
        location_id = location_ids.setdefault(
            row["location"],
            _ensure_location(connection, warehouse_id, row["location"], row["created_at"]),
        )
        connection.execute(
            """INSERT INTO stock_logs
               (project_id, order_no, product_id, batch_no, location_id,
                change_type, change_quantity, operator_name, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id, row["order_no"], row["product_id"], row["batch_no"],
                location_id, row["change_type"], row["change_quantity"],
                row["operator_name"], row["created_at"],
            ),
        )
    for row in data.get("picking_orders", []):
        connection.execute(
            """INSERT INTO picking_orders
               (id, pick_no, project_id, order_id, status, operator_name,
                generated_at, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["id"], row["pick_no"], project_id, row["order_id"],
                row["status"], row["operator_name"], row["generated_at"],
                row["completed_at"],
            ),
        )
    for row in data.get("picking_items", []):
        location_id = location_ids.setdefault(
            row["location"],
            _ensure_location(connection, warehouse_id, row["location"], migration_now),
        )
        connection.execute(
            """INSERT INTO picking_items
               (picking_order_id, product_id, product_name, spec, batch_no,
                quantity, location_id, sequence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["picking_order_id"], row["product_id"], row["product_name"],
                row["spec"], row["batch_no"], row["quantity"], location_id,
                row["sequence"],
            ),
        )

    for row in data.get("templates", []):
        connection.execute(
            """INSERT INTO templates
               (project_id, template_type, version, columns_json, status, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                project_id, row["template_type"], row["version"],
                row["columns_json"], row["status"], row["updated_at"],
            ),
        )
    for row in data.get("audit_logs", []):
        connection.execute(
            """INSERT INTO audit_logs
               (project_id, user_id, user_name, action, detail, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                project_id, row["user_id"], row["user_name"], row["action"],
                row["detail"], row["created_at"],
            ),
        )

    for template_type, columns in DEFAULT_TEMPLATES.items():
        connection.execute(
            """INSERT INTO templates
               (project_id, template_type, version, columns_json, status, updated_at)
               VALUES (?, ?, 1, ?, 'active', datetime('now'))
               ON CONFLICT(project_id, template_type) DO UPDATE SET
                   columns_json = excluded.columns_json""",
            (project_id, template_type, json.dumps(columns, ensure_ascii=False)),
        )
    connection.commit()
    connection.execute("PRAGMA foreign_keys = ON")


def init_db(path=None, admin_username=None, admin_display_name=None,
            admin_password=None):
    connection = connect(path)
    try:
        user_columns = _columns(connection, "users")
        if user_columns and "username" not in user_columns:
            _migrate_legacy(connection)
            connection.executescript(INDEXES)
            return

        with connection:
            connection.executescript(SCHEMA)
            _migrate_v4_columns(connection)
            _migrate_v5(connection)
            _migrate_products_project(connection)
            _migrate_area_entities(connection)
            _reset_templates_v4(connection)
            if connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0:
                connection.execute(
                    """INSERT INTO projects
                       (project_name, status, remark, created_at, updated_at)
                       VALUES ('默认项目', 'active', '系统初始化项目',
                               datetime('now'), datetime('now'))"""
                )
            if connection.execute("SELECT COUNT(*) FROM warehouses").fetchone()[0] == 0:
                connection.execute(
                    """INSERT INTO warehouses
                       (warehouse_name, status, created_at, updated_at)
                       VALUES ('默认仓库', 'active',
                               datetime('now'), datetime('now'))"""
                )
            _bind_default_project_warehouse(connection)
            for project in connection.execute("SELECT id FROM projects").fetchall():
                for template_type, columns in DEFAULT_TEMPLATES.items():
                    connection.execute(
                        """INSERT INTO templates
                           (project_id, template_type, version, columns_json,
                            status, updated_at)
                           VALUES (?, ?, 1, ?, 'active', datetime('now'))
                           ON CONFLICT(project_id, template_type) DO NOTHING""",
                        (project["id"], template_type,
                         json.dumps(columns, ensure_ascii=False)),
                    )
            if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
                username = (
                    admin_username or os.environ.get("WMS_ADMIN_USERNAME", "admin")
                )
                display_name = (
                    admin_display_name
                    or os.environ.get("WMS_ADMIN_NAME", "管理员")
                )
                contact_phone = os.environ.get("WMS_ADMIN_CONTACT_PHONE")
                password = (
                    admin_password
                    or os.environ.get("WMS_ADMIN_PASSWORD", "Admin123456")
                )
                connection.execute(
                    """INSERT INTO users
                       (username, display_name, contact_phone, password_hash, role,
                        status, must_change_password, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'admin', 'active', 1,
                               datetime('now'), datetime('now'))""",
                    (username, display_name, contact_phone, hash_password(password)),
                )
            connection.executescript(INDEXES)
    finally:
        connection.close()


def _migrate_v4_columns(connection):
    """Add V4 columns to databases created before V4."""
    product_columns = _columns(connection, "products")
    additions = {
        "category": "TEXT",
        "unit_weight": "REAL",
        "volume": "REAL",
        "shelf_life_days": "INTEGER",
    }
    for name, column_type in additions.items():
        if name not in product_columns:
            connection.execute(
                f"ALTER TABLE products ADD COLUMN {name} {column_type}"
            )
    project_columns = _columns(connection, "projects")
    if "warehouse_id" not in project_columns:
        connection.execute(
            "ALTER TABLE projects ADD COLUMN warehouse_id INTEGER REFERENCES warehouses(id)"
        )
    if "storage_area" not in project_columns:
        connection.execute("ALTER TABLE projects ADD COLUMN storage_area TEXT")
    order_columns = _columns(connection, "stock_orders")
    if "order_date" not in order_columns:
        connection.execute("ALTER TABLE stock_orders ADD COLUMN order_date TEXT")
    if "recipient" not in order_columns:
        connection.execute("ALTER TABLE stock_orders ADD COLUMN recipient TEXT")
    picking_columns = _columns(connection, "picking_items")
    if "production_date" not in picking_columns:
        connection.execute(
            "ALTER TABLE picking_items ADD COLUMN production_date TEXT"
        )
    if "quality_status" not in picking_columns:
        connection.execute("ALTER TABLE picking_items ADD COLUMN quality_status TEXT")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS project_products (
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            product_id TEXT NOT NULL REFERENCES products(product_id),
            created_at TEXT NOT NULL,
            PRIMARY KEY(project_id, product_id)
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )"""
    )


def _migrate_products_project(connection):
    """商品归属唯一项目：老库补 project_id 列，从授权表回填后退役该表。

    授权了多个项目的商品归最早授权的项目；从未授权的商品归第一个项目，
    保证迁移后每个商品都能在新版「项目内商品管理」里找到并维护。
    """
    if "project_id" in _columns(connection, "products"):
        return
    connection.execute(
        "ALTER TABLE products ADD COLUMN project_id INTEGER REFERENCES projects(id)"
    )
    if _table_exists(connection, "project_products"):
        connection.execute(
            """UPDATE products SET project_id = (
                   SELECT pp.project_id FROM project_products pp
                   WHERE pp.product_id = products.product_id
                   ORDER BY pp.created_at, pp.rowid LIMIT 1
               )"""
        )
        connection.execute("DROP TABLE project_products")
    connection.execute(
        """UPDATE products SET project_id = (
               SELECT MIN(id) FROM projects
           ) WHERE project_id IS NULL"""
    )


def _table_exists(connection, name):
    return bool(connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone())


def _migrate_area_entities(connection):
    """库区/货区实体化：从货位表回填结构档案，支持先建空层后填货位。

    warehouse_locations 的 storage_area/cargo_area 仍存名称（单据、导入、
    解析全部按名称工作），结构档案与货位名称在写操作时双向同步。
    幂等：DISTINCT 回填，跑多少次结果一致。
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    connection.execute(
        """INSERT OR IGNORE INTO storage_areas(warehouse_id, name, status,
                                            created_at, updated_at)
           SELECT DISTINCT warehouse_id, storage_area, 'active', ?, ?
           FROM warehouse_locations""",
        (now, now),
    )
    connection.execute(
        """INSERT OR IGNORE INTO cargo_areas(storage_area_id, name, status,
                                             created_at, updated_at)
           SELECT DISTINCT sa.id, l.cargo_area, 'active', ?, ?
           FROM warehouse_locations l
           JOIN storage_areas sa
             ON sa.warehouse_id = l.warehouse_id AND sa.name = l.storage_area""",
        (now, now),
    )


def _migrate_v5(connection):
    """V5 移库：扩展 stock_orders/stock_logs/templates 的 CHECK 约束（重建三表搬数据），
    并为 stock_order_items 补 to_location_id 列。新库或已迁移库直接返回。"""
    if ("move" in _create_sql(connection, "stock_orders")
            and "to_location_id" in _columns(connection, "stock_order_items")):
        return
    if "move" not in _create_sql(connection, "stock_orders"):
        orders = _rows(connection, "stock_orders")
        logs = _rows(connection, "stock_logs")
        templates = _rows(connection, "templates")
        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("DROP TABLE stock_orders")
            connection.execute(STOCK_ORDERS_DDL)
            connection.execute("DROP TABLE stock_logs")
            connection.execute(STOCK_LOGS_DDL)
            connection.execute("DROP TABLE templates")
            connection.execute(TEMPLATES_DDL)
            for row in orders:
                connection.execute(
                    """INSERT INTO stock_orders
                       (id, project_id, order_no, order_type, status, operator_id,
                        operator_name, template_version, note, order_date, recipient,
                        created_at, completed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    tuple(row[name] for name in (
                        "id", "project_id", "order_no", "order_type", "status",
                        "operator_id", "operator_name", "template_version",
                        "note", "order_date", "recipient", "created_at",
                        "completed_at")),
                )
            for row in logs:
                connection.execute(
                    """INSERT INTO stock_logs
                       (id, project_id, order_no, product_id, batch_no, location_id,
                        change_type, change_quantity, operator_name, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    tuple(row[name] for name in (
                        "id", "project_id", "order_no", "product_id", "batch_no",
                        "location_id", "change_type", "change_quantity",
                        "operator_name", "created_at")),
                )
            for row in templates:
                connection.execute(
                    """INSERT INTO templates
                       (id, project_id, template_type, version, columns_json,
                        status, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    tuple(row[name] for name in (
                        "id", "project_id", "template_type", "version",
                        "columns_json", "status", "updated_at")),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys = ON")
    if "to_location_id" not in _columns(connection, "stock_order_items"):
        connection.execute(
            """ALTER TABLE stock_order_items ADD COLUMN to_location_id
               INTEGER REFERENCES warehouse_locations(id)"""
        )
        connection.commit()
    connection.executescript(INDEXES)


def _reset_templates_v4(connection):
    """Reset project templates to V4 defaults once."""
    done = connection.execute(
        "SELECT 1 FROM meta WHERE key = 'template_v4'"
    ).fetchone()
    if done:
        return
    now = "datetime('now')"
    for project in connection.execute("SELECT id FROM projects").fetchall():
        for template_type, columns in DEFAULT_TEMPLATES.items():
            connection.execute(
                """INSERT INTO templates
                   (project_id, template_type, version, columns_json, status, updated_at)
                   VALUES (?, ?, 1, ?, 'active', datetime('now'))
                   ON CONFLICT(project_id, template_type) DO UPDATE SET
                       version = 1, columns_json = excluded.columns_json,
                       status = 'active', updated_at = excluded.updated_at""",
                (project["id"], template_type,
                 json.dumps(columns, ensure_ascii=False)),
            )
    connection.execute(
        "INSERT INTO meta(key, value) VALUES ('template_v4', '1')"
    )


def _bind_default_project_warehouse(connection):
    """Bind projects without a warehouse to the first active warehouse."""
    row = connection.execute(
        """SELECT id, warehouse_name FROM warehouses
           WHERE status = 'active' ORDER BY id LIMIT 1"""
    ).fetchone()
    if not row:
        return
    location = connection.execute(
        """SELECT storage_area FROM warehouse_locations
           WHERE warehouse_id = ? AND status = 'active'
           ORDER BY id LIMIT 1""",
        (row["id"],),
    ).fetchone()
    if not location:
        connection.execute(
            """INSERT INTO warehouse_locations
               (warehouse_id, storage_area, cargo_area, location_name, status,
                created_at, updated_at)
               VALUES (?, '默认库区', '默认货区', '默认货位', 'active',
                       datetime('now'), datetime('now'))""",
            (row["id"],),
        )
        storage_area = "默认库区"
    else:
        storage_area = location["storage_area"]
    connection.execute(
        """UPDATE projects SET warehouse_id = ?, storage_area = ?
           WHERE warehouse_id IS NULL""",
        (row["id"], storage_area),
    )


def columns_for(connection, template_type, project_id):
    row = connection.execute(
        """SELECT version, columns_json FROM templates
           WHERE project_id = ? AND template_type = ?""",
        (project_id, template_type),
    ).fetchone()
    if not row:
        raise ValueError("模板不存在")
    return row["version"], json.loads(row["columns_json"])
