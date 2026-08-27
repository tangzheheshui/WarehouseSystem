import json
import os
import sqlite3
from pathlib import Path

from security import hash_password


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "warehouse.db"


DEFAULT_TEMPLATES = {
    "inbound": [
        {"key": "product_id", "name": "商品ID", "required": True, "type": "text"},
        {"key": "product_name", "name": "商品名称", "required": True, "type": "text"},
        {"key": "spec", "name": "规格", "required": False, "type": "text"},
        {"key": "batch_no", "name": "批次号", "required": True, "type": "text"},
        {"key": "production_date", "name": "生产日期", "required": False, "type": "date"},
        {"key": "shelf_life_days", "name": "保质期（天）", "required": False, "type": "integer"},
        {"key": "quantity", "name": "数量", "required": True, "type": "integer"},
        {"key": "location", "name": "库位", "required": True, "type": "text"},
        {"key": "note", "name": "备注", "required": False, "type": "text"},
    ],
    "outbound": [
        {"key": "product_id", "name": "商品ID", "required": True, "type": "text"},
        {"key": "product_name", "name": "商品名称", "required": True, "type": "text"},
        {"key": "spec", "name": "规格", "required": False, "type": "text"},
        {"key": "quantity", "name": "数量", "required": True, "type": "integer"},
        {"key": "note", "name": "备注", "required": False, "type": "text"},
    ],
}


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'worker')),
    status TEXT NOT NULL CHECK(status IN ('active', 'disabled')),
    must_change_password INTEGER NOT NULL DEFAULT 1,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
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
    spec TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL REFERENCES products(product_id),
    batch_no TEXT NOT NULL,
    location TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK(quantity >= 0),
    production_date TEXT,
    shelf_life_days INTEGER,
    last_inbound_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(product_id, batch_no, location)
);

CREATE TABLE IF NOT EXISTS stock_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT NOT NULL UNIQUE,
    order_type TEXT NOT NULL CHECK(order_type IN ('inbound', 'outbound')),
    status TEXT NOT NULL CHECK(status IN ('pending_picking', 'completed')),
    operator_id INTEGER NOT NULL REFERENCES users(id),
    operator_name TEXT NOT NULL,
    template_version INTEGER NOT NULL,
    note TEXT,
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
    location TEXT,
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT NOT NULL,
    product_id TEXT NOT NULL,
    batch_no TEXT NOT NULL,
    location TEXT NOT NULL,
    change_type TEXT NOT NULL CHECK(change_type IN ('inbound', 'outbound')),
    change_quantity INTEGER NOT NULL,
    operator_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS picking_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pick_no TEXT NOT NULL UNIQUE,
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
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    location TEXT NOT NULL,
    sequence INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_type TEXT NOT NULL UNIQUE CHECK(template_type IN ('inbound', 'outbound')),
    version INTEGER NOT NULL,
    columns_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'disabled')),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    user_name TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_inventory_product ON inventory(product_id);
CREATE INDEX IF NOT EXISTS idx_orders_type_time ON stock_orders(order_type, created_at);
CREATE INDEX IF NOT EXISTS idx_order_items_order ON stock_order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_stock_logs_product ON stock_logs(product_id, created_at);
CREATE INDEX IF NOT EXISTS idx_picking_orders_order ON picking_orders(order_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_audit_logs_time ON audit_logs(created_at);
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


def init_db(path=None):
    connection = connect(path)
    try:
        with connection:
            connection.executescript(SCHEMA)
            template_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(templates)")
            }
            if "status" not in template_columns:
                connection.execute(
                    "ALTER TABLE templates ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
                )
            for template_type, columns in DEFAULT_TEMPLATES.items():
                connection.execute(
                    """INSERT OR IGNORE INTO templates
                       (template_type, version, columns_json, updated_at)
                       VALUES (?, 1, ?, datetime('now'))""",
                    (template_type, json.dumps(columns, ensure_ascii=False)),
                )
            if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
                phone = os.environ.get("WMS_ADMIN_PHONE", "13800000000")
                password = os.environ.get("WMS_ADMIN_PASSWORD", "Admin123456")
                connection.execute(
                    """INSERT INTO users
                       (phone, password_hash, role, status, must_change_password,
                        created_at, updated_at)
                       VALUES (?, ?, 'admin', 'active', 1,
                               datetime('now'), datetime('now'))""",
                    (phone, hash_password(password)),
                )
    finally:
        connection.close()


def columns_for(connection, template_type):
    row = connection.execute(
        "SELECT version, columns_json FROM templates WHERE template_type = ?",
        (template_type,),
    ).fetchone()
    if not row:
        raise ValueError("模板不存在")
    return row["version"], json.loads(row["columns_json"])
