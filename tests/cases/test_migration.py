import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "server"))

import db


OLD_SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'worker')),
    status TEXT NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 1,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE products (
    product_id TEXT PRIMARY KEY,
    product_name TEXT NOT NULL,
    spec TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL,
    batch_no TEXT NOT NULL,
    location TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    production_date TEXT,
    shelf_life_days INTEGER,
    last_inbound_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE stock_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT NOT NULL UNIQUE,
    order_type TEXT NOT NULL,
    status TEXT NOT NULL,
    operator_id INTEGER NOT NULL,
    operator_name TEXT NOT NULL,
    template_version INTEGER NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE stock_order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    row_no INTEGER NOT NULL,
    product_id TEXT NOT NULL,
    product_name TEXT NOT NULL,
    spec TEXT,
    batch_no TEXT,
    location TEXT,
    quantity INTEGER NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE stock_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT NOT NULL,
    product_id TEXT NOT NULL,
    batch_no TEXT NOT NULL,
    location TEXT NOT NULL,
    change_type TEXT NOT NULL,
    change_quantity INTEGER NOT NULL,
    operator_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE picking_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pick_no TEXT NOT NULL UNIQUE,
    order_id INTEGER NOT NULL UNIQUE,
    status TEXT NOT NULL,
    operator_name TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE picking_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    picking_order_id INTEGER NOT NULL,
    product_id TEXT NOT NULL,
    product_name TEXT NOT NULL,
    spec TEXT,
    batch_no TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    location TEXT NOT NULL,
    sequence INTEGER NOT NULL
);
CREATE TABLE templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_type TEXT NOT NULL UNIQUE,
    version INTEGER NOT NULL,
    columns_json TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    user_name TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);
"""


class MigrationTests(unittest.TestCase):
    def test_v1_database_moves_to_default_project(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.db"
            connection = sqlite3.connect(path)
            connection.executescript(OLD_SCHEMA)
            connection.executemany(
                """INSERT INTO users
                   (id, phone, password_hash, role, status, must_change_password,
                    failed_attempts, locked_until, created_at, updated_at)
                   VALUES (?, ?, 'hash', ?, 'active', 1, 0, NULL, '2026-01-01', '2026-01-01')""",
                [
                    (1, "13800000000", "admin"),
                    (2, "13900000000", "worker"),
                ],
            )
            connection.execute(
                """INSERT INTO products
                   (product_id, product_name, spec, created_at, updated_at)
                   VALUES ('H001', '旧商品', '10g', '2026-01-01', '2026-01-01')"""
            )
            connection.execute(
                """INSERT INTO inventory
                   (product_id, batch_no, location, quantity, production_date,
                    shelf_life_days, last_inbound_at, created_at, updated_at)
                   VALUES ('H001', 'OLD', 'A-01', 8, '2026-01-01', 30,
                           '2026-01-02 10:00:00', '2026-01-02', '2026-01-02')"""
            )
            connection.execute(
                """INSERT INTO stock_orders
                   (id, order_no, order_type, status, operator_id, operator_name,
                    template_version, note, created_at, completed_at)
                   VALUES (1, 'RK-OLD', 'inbound', 'completed', 2, '13900000000',
                           1, NULL, '2026-01-02', '2026-01-02')"""
            )
            connection.execute(
                """INSERT INTO stock_order_items
                   (order_id, row_no, product_id, product_name, spec, batch_no,
                    location, quantity, note, created_at)
                   VALUES (1, 2, 'H001', '旧商品', '10g', 'OLD', 'A-01', 8,
                           NULL, '2026-01-02')"""
            )
            connection.execute(
                """INSERT INTO stock_logs
                   (order_no, product_id, batch_no, location, change_type,
                    change_quantity, operator_name, created_at)
                   VALUES ('RK-OLD', 'H001', 'OLD', 'A-01', 'inbound', 8,
                           '13900000000', '2026-01-02')"""
            )
            connection.execute(
                """INSERT INTO picking_orders
                   (id, pick_no, order_id, status, operator_name, generated_at)
                   VALUES (1, 'PICK-OLD', 1, 'completed', '13900000000', '2026-01-02')"""
            )
            connection.execute(
                """INSERT INTO picking_items
                   (picking_order_id, product_id, product_name, spec, batch_no,
                    quantity, location, sequence)
                   VALUES (1, 'H001', '旧商品', '10g', 'OLD', 8, 'A-01', 1)"""
            )
            connection.execute(
                """INSERT INTO templates
                   (template_type, version, columns_json, status, updated_at)
                   VALUES ('inbound', 3, '[]', 'active', '2026-01-01')"""
            )
            connection.execute(
                """INSERT INTO audit_logs
                   (user_id, user_name, action, detail, created_at)
                   VALUES (1, '13800000000', 'test', NULL, '2026-01-01')"""
            )
            connection.commit()
            connection.close()

            db.init_db(path)
            migrated = db.connect(path)
            admin = migrated.execute(
                "SELECT username, role FROM users WHERE id = 1"
            ).fetchone()
            worker = migrated.execute(
                "SELECT username, role FROM users WHERE id = 2"
            ).fetchone()
            self.assertEqual(tuple(admin), ("admin", "admin"))
            self.assertEqual(tuple(worker), ("user2", "project_admin"))
            self.assertEqual(migrated.execute(
                "SELECT COUNT(*) FROM user_projects WHERE user_id = 2"
            ).fetchone()[0], 1)
            inventory = migrated.execute(
                """SELECT project_id, total_quantity, expiry_date, inbound_date
                   FROM inventory"""
            ).fetchone()
            self.assertEqual(tuple(inventory), (1, 8, "2026-01-31", "2026-01-02"))
            self.assertEqual(migrated.execute(
                "SELECT project_id FROM stock_orders WHERE id = 1"
            ).fetchone()[0], 1)
            self.assertEqual(migrated.execute(
                "SELECT project_id FROM picking_orders WHERE id = 1"
            ).fetchone()[0], 1)
            self.assertEqual(migrated.execute(
                "SELECT project_id FROM audit_logs"
            ).fetchone()[0], 1)
            self.assertEqual(migrated.execute("PRAGMA foreign_key_check").fetchall(), [])
            migrated.close()


if __name__ == "__main__":
    unittest.main()
