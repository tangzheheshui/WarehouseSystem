import csv
import io
import json
import re
from datetime import datetime, timedelta, timezone

import excelio
from auth import valid_phone, valid_username
from db import DEFAULT_TEMPLATES, columns_for
from security import hash_password, iso_now, valid_password


FIELD_TYPES = {
    "order_no": "text",
    "order_date": "date",
    "product_code": "text",
    "product_name": "text",
    "spec": "text",
    "unit": "text",
    "recipient": "text",
    "batch_no": "text",
    "production_date": "date",
    "expiry_date": "date",
    "shelf_life_days": "integer",
    "inbound_date": "date",
    "quantity": "integer",
    "warehouse_name": "text",
    "storage_area": "text",
    "cargo_area": "text",
    "location_name": "text",
    "from_location": "text",
    "to_location": "text",
    "quality_status": "text",
    "purchase_order_no": "text",
    "note": "text",
}
REQUIRED_FIELDS = {
    "inbound": {
        "order_no", "order_date", "product_code", "batch_no",
        "production_date", "quantity", "unit", "location_name",
    },
    "outbound": {"order_no", "order_date", "product_code", "quantity", "unit"},
    "move": {
        "order_no", "order_date", "product_code", "batch_no", "quantity",
        "unit", "from_location", "to_location",
    },
}
MAX_IMPORT_ROWS = 500
MAX_FILE_SIZE = 5 * 1024 * 1024
MAX_HISTORY_ROWS = 5000
MAX_HISTORY_FILE_SIZE = 12 * 1024 * 1024
HISTORY_HEADERS = {
    "库区", "货区", "货位", "货品编号", "货品名称", "生产批号", "生产日期",
    "到期日期", "库存总数", "分拣占用数", "移动占用数", "可用库存",
    "采购单号", "品质标记", "入库日", "禁止出库", "大单位数", "大单位",
    "中单位数", "中单位", "标准单位数", "标准单位", "仓库",
}


class ImportValidationError(ValueError):
    def __init__(self, message, errors):
        super().__init__(message)
        self.errors = errors


class Forbidden(ValueError):
    pass


def audit(connection, user, action, detail="", project_id=None):
    connection.execute(
        """INSERT INTO audit_logs(project_id, user_id, user_name, action,
                                  detail, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            project_id,
            user["id"] if user else None,
            user["display_name"] if user else "系统",
            action,
            detail,
            iso_now(),
        ),
    )


def user_projects(connection, user):
    if user["role"] == "admin":
        sql = """SELECT p.*, NULL AS assigned_at FROM projects p
                 WHERE p.status = 'active' ORDER BY p.id"""
        return [dict(row) for row in connection.execute(sql)]
    sql = """SELECT p.*, up.created_at AS assigned_at
             FROM user_projects up
             JOIN projects p ON p.id = up.project_id
             WHERE up.user_id = ? AND p.status = 'active'
             ORDER BY p.id"""
    return [dict(row) for row in connection.execute(sql, (user["id"],))]


def require_project(connection, user, project_id):
    if user["role"] == "admin":
        # 管理员可代办项目作业（如批量移库），仅要求项目存在且启用
        try:
            project_id = int(project_id)
        except (TypeError, ValueError):
            raise ValueError("项目无效")
        row = connection.execute(
            "SELECT * FROM projects WHERE id = ? AND status = 'active'",
            (project_id,),
        ).fetchone()
        if not row:
            raise Forbidden("项目不存在或已停用")
        return row
    if user["role"] != "project_admin":
        raise Forbidden("仅项目管理员可以执行项目作业")
    try:
        project_id = int(project_id)
    except (TypeError, ValueError):
        raise ValueError("项目无效")
    row = connection.execute(
        """SELECT p.* FROM user_projects up
           JOIN projects p ON p.id = up.project_id
           WHERE up.user_id = ? AND p.id = ? AND p.status = 'active'""",
        (user["id"], project_id),
    ).fetchone()
    if not row:
        raise Forbidden("未授权或项目已停用")
    return row


def _required_text(value, name, max_length=100):
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"{name}不能为空")
    if len(value) > max_length:
        raise ValueError(f"{name}不能超过{max_length}个字符")
    return value


def _optional_number(value, name):
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name}必须为数字")
    if number <= 0:
        raise ValueError(f"{name}必须大于0")
    return number


def _optional_int(value, name, allow_zero=False):
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name}必须为整数")
    if number <= 0 and not (allow_zero and number == 0):
        raise ValueError(f"{name}必须大于0")
    return number


def list_products(connection, keyword=None, status=None, project_id=None):
    sql = "SELECT * FROM products WHERE 1=1"
    params = []
    if project_id is not None:
        sql += " AND project_id = ?"
        params.append(project_id)
    if keyword:
        sql += " AND (product_id LIKE ? OR product_name LIKE ? OR spec LIKE ?)"
        value = f"%{keyword.strip()}%"
        params.extend([value, value, value])
    if status in ("active", "disabled"):
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY product_id LIMIT 1000"
    return [dict(row) for row in connection.execute(sql, params)]


def create_product(connection, operator, product_code, product_name,
                   project_id=None, spec=None, unit=None, category=None,
                   unit_weight=None, volume=None, shelf_life_days=None):
    product_code = _required_text(product_code, "货品编号", 50)
    product_name = _required_text(product_name, "货品名称")
    project = connection.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not project:
        raise ValueError("请选择商品所属项目")
    spec = str(spec or "").strip() or None
    unit = str(unit or "").strip() or None
    category = str(category or "").strip() or None
    if spec and len(spec) > 100:
        raise ValueError("规格不能超过100个字符")
    if unit and len(unit) > 20:
        raise ValueError("标准单位不能超过20个字符")
    if category and len(category) > 50:
        raise ValueError("货品分类不能超过50个字符")
    unit_weight = _optional_number(unit_weight, "单件重量")
    volume = _optional_number(volume, "体积")
    shelf_life_days = _optional_int(shelf_life_days, "保质天数", allow_zero=False)
    if connection.execute(
        "SELECT 1 FROM products WHERE product_id = ?", (product_code,)
    ).fetchone():
        raise ValueError("货品编号已存在")
    now = iso_now()
    connection.execute(
        """INSERT INTO products(product_id, product_name, project_id, spec, unit,
                                category, unit_weight, volume, shelf_life_days,
                                status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
        (product_code, product_name, project["id"], spec, unit, category,
         unit_weight, volume, shelf_life_days, now, now),
    )
    audit(
        connection, operator, "product.create",
        f"在项目 {project['project_name']} 创建商品 {product_code} {product_name}",
        project_id=project["id"],
    )
    connection.commit()
    row = connection.execute(
        "SELECT * FROM products WHERE product_id = ?", (product_code,)
    ).fetchone()
    return dict(row)


def update_product(connection, operator, product_code, product_name=None,
                   spec=None, unit=None, category=None, unit_weight=None,
                   volume=None, shelf_life_days=None, status=None):
    row = connection.execute(
        "SELECT * FROM products WHERE product_id = ?", (product_code,)
    ).fetchone()
    if not row:
        raise ValueError("商品不存在")
    values = {
        "product_name": _required_text(product_name, "货品名称")
        if product_name is not None else row["product_name"],
        "spec": str(spec or "").strip() or None if spec is not None else row["spec"],
        "unit": str(unit or "").strip() or None if unit is not None else row["unit"],
        "category": str(category or "").strip() or None
        if category is not None else row["category"],
        "unit_weight": _optional_number(unit_weight, "单件重量")
        if unit_weight is not None else row["unit_weight"],
        "volume": _optional_number(volume, "体积")
        if volume is not None else row["volume"],
        "shelf_life_days": _optional_int(
            shelf_life_days, "保质天数", allow_zero=False
        ) if shelf_life_days is not None else row["shelf_life_days"],
        "status": status if status is not None else row["status"],
    }
    if values["status"] not in ("active", "disabled"):
        raise ValueError("商品状态无效")
    connection.execute(
        """UPDATE products SET product_name = ?, spec = ?, unit = ?,
           category = ?, unit_weight = ?, volume = ?, shelf_life_days = ?,
           status = ?, updated_at = ? WHERE product_id = ?""",
        (
            values["product_name"], values["spec"], values["unit"],
            values["category"], values["unit_weight"], values["volume"],
            values["shelf_life_days"], values["status"], iso_now(),
            product_code,
        ),
    )
    audit(
        connection, operator, "product.update",
        f"修改商品 {product_code}", project_id=None,
    )
    connection.commit()
    return dict(connection.execute(
        "SELECT * FROM products WHERE product_id = ?", (product_code,)
    ).fetchone())


def list_warehouses(connection, keyword=None):
    warehouses = []
    sql = """SELECT * FROM warehouses WHERE 1=1"""
    params = []
    if keyword:
        sql += " AND warehouse_name LIKE ?"
        params.append(f"%{keyword.strip()}%")
    sql += " ORDER BY warehouse_name"
    for row in connection.execute(sql, params):
        item = dict(row)
        item["locations"] = [dict(location) for location in connection.execute(
            """SELECT l.*,
                      (SELECT COALESCE(SUM(i.total_quantity
                            - i.picking_reserved - i.moving_reserved), 0)
                       FROM inventory i WHERE i.location_id = l.id) AS stock_quantity
               FROM warehouse_locations l
               WHERE l.warehouse_id = ?
               ORDER BY l.storage_area, l.cargo_area, l.location_name""",
            (row["id"],),
        )]
        item["areas"] = [dict(area) for area in connection.execute(
            """SELECT sa.id, sa.name, sa.status,
                      (SELECT COUNT(*) FROM cargo_areas ca
                        WHERE ca.storage_area_id = sa.id) AS cargo_count,
                      (SELECT COUNT(*) FROM warehouse_locations l
                        WHERE l.warehouse_id = sa.warehouse_id
                          AND l.storage_area = sa.name) AS location_count
               FROM storage_areas sa
               WHERE sa.warehouse_id = ?
               ORDER BY sa.name""",
            (row["id"],),
        )]
        item["cargoAreas"] = [dict(cargo) for cargo in connection.execute(
            """SELECT ca.id, ca.storage_area_id, ca.name, ca.status,
                      (SELECT COUNT(*) FROM warehouse_locations l
                        WHERE l.warehouse_id = sa.warehouse_id
                          AND l.storage_area = sa.name
                          AND l.cargo_area = ca.name) AS location_count
               FROM cargo_areas ca
               JOIN storage_areas sa ON sa.id = ca.storage_area_id
               WHERE sa.warehouse_id = ?
               ORDER BY sa.name, ca.name""",
            (row["id"],),
        )]
        warehouses.append(item)
    return warehouses


def create_warehouse(connection, operator, warehouse_name, address=None,
                     contact_name=None, contact_phone=None):
    warehouse_name = _required_text(warehouse_name, "仓库名称")
    if not valid_phone(contact_phone):
        raise ValueError("联系电话格式错误")
    if connection.execute(
        "SELECT 1 FROM warehouses WHERE warehouse_name = ?", (warehouse_name,)
    ).fetchone():
        raise ValueError("仓库名称已存在")
    now = iso_now()
    cursor = connection.execute(
        """INSERT INTO warehouses
           (warehouse_name, address, contact_name, contact_phone, status,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, 'active', ?, ?)""",
        (
            warehouse_name, str(address or "").strip() or None,
            str(contact_name or "").strip() or None,
            str(contact_phone or "").strip() or None, now, now,
        ),
    )
    audit(connection, operator, "warehouse.create", f"创建仓库 {warehouse_name}")
    connection.commit()
    return dict(connection.execute(
        "SELECT * FROM warehouses WHERE id = ?", (cursor.lastrowid,)
    ).fetchone())


# ---- 库区/货区结构档案：实体化管理，支持先建空层后填货位 ----
# warehouse_locations 的 storage_area/cargo_area 仍存名称，档案与货位
# 在写操作时同步：建档位自动登记、改名批量同步、删除校验非空。

def _ensure_area_entities(connection, warehouse_id, storage_area, cargo_area,
                          now):
    """建档位时自动登记库区/货区档案，保证结构树完整。"""
    area = connection.execute(
        "SELECT id FROM storage_areas WHERE warehouse_id = ? AND name = ?",
        (warehouse_id, storage_area),
    ).fetchone()
    if not area:
        connection.execute(
            """INSERT INTO storage_areas(warehouse_id, name, created_at, updated_at)
               VALUES (?, ?, ?, ?)""",
            (warehouse_id, storage_area, now, now),
        )
        area_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
    else:
        area_id = area["id"]
    cargo = connection.execute(
        "SELECT id FROM cargo_areas WHERE storage_area_id = ? AND name = ?",
        (area_id, cargo_area),
    ).fetchone()
    if not cargo:
        connection.execute(
            """INSERT INTO cargo_areas(storage_area_id, name, created_at, updated_at)
               VALUES (?, ?, ?, ?)""",
            (area_id, cargo_area, now, now),
        )


def create_storage_area(connection, operator, warehouse_id, name):
    name = _required_text(name, "库区名", 50)
    warehouse = connection.execute(
        "SELECT * FROM warehouses WHERE id = ?", (warehouse_id,)
    ).fetchone()
    if not warehouse:
        raise ValueError("仓库不存在")
    if connection.execute(
        "SELECT 1 FROM storage_areas WHERE warehouse_id = ? AND name = ?",
        (warehouse_id, name),
    ).fetchone():
        raise ValueError("库区名已存在")
    now = iso_now()
    connection.execute(
        """INSERT INTO storage_areas(warehouse_id, name, created_at, updated_at)
           VALUES (?, ?, ?, ?)""",
        (warehouse_id, name, now, now),
    )
    audit(connection, operator, "warehouse.area.create",
          f"在仓库 {warehouse['warehouse_name']} 创建库区 {name}")
    connection.commit()
    return dict(connection.execute(
        "SELECT * FROM storage_areas WHERE id = ?",
        (connection.execute("SELECT last_insert_rowid()").fetchone()[0],),
    ).fetchone())


def create_cargo_area(connection, operator, storage_area_id, name):
    name = _required_text(name, "货区名", 50)
    area = connection.execute(
        """SELECT sa.*, w.warehouse_name FROM storage_areas sa
           JOIN warehouses w ON w.id = sa.warehouse_id
           WHERE sa.id = ?""",
        (storage_area_id,),
    ).fetchone()
    if not area:
        raise ValueError("库区不存在")
    if connection.execute(
        "SELECT 1 FROM cargo_areas WHERE storage_area_id = ? AND name = ?",
        (storage_area_id, name),
    ).fetchone():
        raise ValueError("货区名已存在")
    now = iso_now()
    connection.execute(
        """INSERT INTO cargo_areas(storage_area_id, name, created_at, updated_at)
           VALUES (?, ?, ?, ?)""",
        (storage_area_id, name, now, now),
    )
    audit(connection, operator, "warehouse.cargo.create",
          f"在 {area['warehouse_name']} / {area['name']} 创建货区 {name}")
    connection.commit()
    return dict(connection.execute(
        "SELECT * FROM cargo_areas WHERE id = ?",
        (connection.execute("SELECT last_insert_rowid()").fetchone()[0],),
    ).fetchone())


def rename_storage_area(connection, operator, storage_area_id, new_name):
    new_name = _required_text(new_name, "库区名", 50)
    area = connection.execute(
        "SELECT * FROM storage_areas WHERE id = ?", (storage_area_id,)
    ).fetchone()
    if not area:
        raise ValueError("库区不存在")
    if new_name != area["name"] and connection.execute(
        "SELECT 1 FROM storage_areas WHERE warehouse_id = ? AND name = ?",
        (area["warehouse_id"], new_name),
    ).fetchone():
        raise ValueError("库区名已存在")
    now = iso_now()
    connection.execute(
        "UPDATE storage_areas SET name = ?, updated_at = ? WHERE id = ?",
        (new_name, now, storage_area_id),
    )
    cursor = connection.execute(
        """UPDATE warehouse_locations SET storage_area = ?, updated_at = ?
           WHERE warehouse_id = ? AND storage_area = ?""",
        (new_name, now, area["warehouse_id"], area["name"]),
    )
    audit(connection, operator, "warehouse.area.rename",
          f"库区 {area['name']} 改名为 {new_name}（同步 {cursor.rowcount} 个货位）")
    connection.commit()
    return dict(connection.execute(
        "SELECT * FROM storage_areas WHERE id = ?", (storage_area_id,)
    ).fetchone())


def rename_cargo_area(connection, operator, cargo_area_id, new_name):
    new_name = _required_text(new_name, "货区名", 50)
    cargo = connection.execute(
        """SELECT ca.*, sa.warehouse_id, sa.name AS area_name
           FROM cargo_areas ca
           JOIN storage_areas sa ON sa.id = ca.storage_area_id
           WHERE ca.id = ?""",
        (cargo_area_id,),
    ).fetchone()
    if not cargo:
        raise ValueError("货区不存在")
    if new_name != cargo["name"] and connection.execute(
        "SELECT 1 FROM cargo_areas WHERE storage_area_id = ? AND name = ?",
        (cargo["storage_area_id"], new_name),
    ).fetchone():
        raise ValueError("货区名已存在")
    now = iso_now()
    connection.execute(
        "UPDATE cargo_areas SET name = ?, updated_at = ? WHERE id = ?",
        (new_name, now, cargo_area_id),
    )
    cursor = connection.execute(
        """UPDATE warehouse_locations SET cargo_area = ?, updated_at = ?
           WHERE warehouse_id = ? AND storage_area = ? AND cargo_area = ?""",
        (new_name, now, cargo["warehouse_id"], cargo["area_name"],
         cargo["name"]),
    )
    audit(connection, operator, "warehouse.cargo.rename",
          f"货区 {cargo['area_name']} / {cargo['name']} 改名为 {new_name}"
          f"（同步 {cursor.rowcount} 个货位）")
    connection.commit()
    return dict(connection.execute(
        "SELECT * FROM cargo_areas WHERE id = ?", (cargo_area_id,)
    ).fetchone())


def delete_storage_area(connection, operator, storage_area_id):
    area = connection.execute(
        """SELECT sa.*, w.warehouse_name FROM storage_areas sa
           JOIN warehouses w ON w.id = sa.warehouse_id
           WHERE sa.id = ?""",
        (storage_area_id,),
    ).fetchone()
    if not area:
        raise ValueError("库区不存在")
    loc_count = connection.execute(
        """SELECT COUNT(*) FROM warehouse_locations
           WHERE warehouse_id = ? AND storage_area = ?""",
        (area["warehouse_id"], area["name"]),
    ).fetchone()[0]
    if loc_count:
        raise ValueError(f"库区内还有 {loc_count} 个货位，请先删除或移出货位")
    connection.execute(
        "DELETE FROM cargo_areas WHERE storage_area_id = ?", (storage_area_id,)
    )
    connection.execute(
        "DELETE FROM storage_areas WHERE id = ?", (storage_area_id,)
    )
    audit(connection, operator, "warehouse.area.delete",
          f"删除库区 {area['warehouse_name']} / {area['name']}")
    connection.commit()
    return list_warehouses(connection)


def delete_cargo_area(connection, operator, cargo_area_id):
    cargo = connection.execute(
        """SELECT ca.*, sa.warehouse_id, sa.name AS area_name
           FROM cargo_areas ca
           JOIN storage_areas sa ON sa.id = ca.storage_area_id
           WHERE ca.id = ?""",
        (cargo_area_id,),
    ).fetchone()
    if not cargo:
        raise ValueError("货区不存在")
    loc_count = connection.execute(
        """SELECT COUNT(*) FROM warehouse_locations
           WHERE warehouse_id = ? AND storage_area = ? AND cargo_area = ?""",
        (cargo["warehouse_id"], cargo["area_name"], cargo["name"]),
    ).fetchone()[0]
    if loc_count:
        raise ValueError(f"货区内还有 {loc_count} 个货位，请先删除或移出货位")
    connection.execute(
        "DELETE FROM cargo_areas WHERE id = ?", (cargo_area_id,)
    )
    audit(connection, operator, "warehouse.cargo.delete",
          f"删除货区 {cargo['area_name']} / {cargo['name']}")
    connection.commit()
    return list_warehouses(connection)


def update_warehouse(connection, operator, warehouse_id, warehouse_name=None,
                     address=None, contact_name=None, contact_phone=None):
    row = connection.execute(
        "SELECT * FROM warehouses WHERE id = ?", (warehouse_id,)
    ).fetchone()
    if not row:
        raise ValueError("仓库不存在")
    name = _required_text(warehouse_name, "仓库名称") \
        if warehouse_name is not None else row["warehouse_name"]
    if name != row["warehouse_name"] and connection.execute(
        "SELECT 1 FROM warehouses WHERE warehouse_name = ?", (name,)
    ).fetchone():
        raise ValueError("仓库名称已存在")
    if contact_phone is not None and not valid_phone(contact_phone):
        raise ValueError("联系电话格式错误")
    connection.execute(
        """UPDATE warehouses SET warehouse_name = ?, address = ?,
           contact_name = ?, contact_phone = ?, updated_at = ? WHERE id = ?""",
        (
            name,
            str(address or "").strip() or None if address is not None
            else row["address"],
            str(contact_name or "").strip() or None if contact_name is not None
            else row["contact_name"],
            contact_phone if contact_phone is not None
            else row["contact_phone"],
            iso_now(), warehouse_id,
        ),
    )
    audit(connection, operator, "warehouse.update", f"修改仓库 {name}")
    connection.commit()
    return dict(connection.execute(
        "SELECT * FROM warehouses WHERE id = ?", (warehouse_id,)
    ).fetchone())


# 删除货位前检查全部引用：库存、流水、单据明细、移库目标、拣货单
_LOCATION_REF_QUERIES = (
    ("库存", "SELECT COUNT(*) FROM inventory WHERE location_id = ?"),
    ("库存流水", "SELECT COUNT(*) FROM stock_logs WHERE location_id = ?"),
    ("单据明细", "SELECT COUNT(*) FROM stock_order_items WHERE location_id = ?"),
    ("移库单", "SELECT COUNT(*) FROM stock_orders WHERE to_location_id = ?"),
    ("拣货记录", "SELECT COUNT(*) FROM picking_items WHERE location_id = ?"),
)


def delete_warehouse_locations(connection, operator, location_ids):
    ids = location_ids or []
    if not isinstance(ids, list) or not ids:
        raise ValueError("请选择要删除的货位")
    ids = [int(value) for value in ids]
    deleted, blocked = [], []
    for location_id in ids:
        row = connection.execute(
            """SELECT l.*, w.warehouse_name FROM warehouse_locations l
               JOIN warehouses w ON w.id = l.warehouse_id
               WHERE l.id = ?""",
            (location_id,),
        ).fetchone()
        if not row:
            blocked.append({"id": location_id, "reason": "货位不存在"})
            continue
        full = (f"{row['storage_area']}/{row['cargo_area']}/"
                f"{row['location_name']}")
        reason = None
        for label, sql in _LOCATION_REF_QUERIES:
            if connection.execute(sql, (location_id,)).fetchone()[0]:
                reason = f"有{label}记录"
                break
        if reason:
            blocked.append({"id": location_id, "name": full,
                            "reason": f"{reason}，请改用停用"})
            continue
        connection.execute(
            "DELETE FROM warehouse_locations WHERE id = ?", (location_id,)
        )
        deleted.append(full)
        audit(connection, operator, "warehouse.location.delete",
              f"删除货位 {row['warehouse_name']} / {full}")
    connection.commit()
    # 注意：空库区/空货区是用户显式搭建的结构（先建层后填位），不自动清理，
    # 需要时由用户在树上显式删除。
    return {"deleted": deleted, "blocked": blocked,
            "warehouses": list_warehouses(connection)}


def create_location(connection, operator, warehouse_id, storage_area,
                    cargo_area, location_name):
    warehouse = connection.execute(
        "SELECT * FROM warehouses WHERE id = ?", (warehouse_id,)
    ).fetchone()
    if not warehouse or warehouse["status"] != "active":
        raise ValueError("仓库不存在或已停用")
    storage_area = _required_text(storage_area, "库区", 50)
    cargo_area = _required_text(cargo_area, "货区", 50)
    location_name = _required_text(location_name, "货位", 50)
    if connection.execute(
        """SELECT 1 FROM warehouse_locations
           WHERE warehouse_id = ? AND storage_area = ?
             AND cargo_area = ? AND location_name = ?""",
        (warehouse_id, storage_area, cargo_area, location_name),
    ).fetchone():
        raise ValueError("货位已存在")
    now = iso_now()
    _ensure_area_entities(connection, warehouse_id, storage_area, cargo_area,
                          now)
    cursor = connection.execute(
        """INSERT INTO warehouse_locations
           (warehouse_id, storage_area, cargo_area, location_name, status,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, 'active', ?, ?)""",
        (warehouse_id, storage_area, cargo_area, location_name, now, now),
    )
    audit(
        connection, operator, "warehouse.location.create",
        f"创建货位 {warehouse['warehouse_name']} / {storage_area} / {cargo_area} / {location_name}",
    )
    connection.commit()
    return dict(connection.execute(
        "SELECT * FROM warehouse_locations WHERE id = ?", (cursor.lastrowid,)
    ).fetchone())


def set_warehouse_status(connection, operator, table, record_id, status):
    if table not in ("warehouses", "warehouse_locations"):
        raise ValueError("仓库对象无效")
    if status not in ("active", "disabled"):
        raise ValueError("状态无效")
    id_column = "id"
    row = connection.execute(
        f"SELECT * FROM {table} WHERE {id_column} = ?", (record_id,)
    ).fetchone()
    if not row:
        raise ValueError("仓库对象不存在")
    name = (
        row["warehouse_name"] if table == "warehouses"
        else row["location_name"]
    )
    connection.execute(
        f"UPDATE {table} SET status = ?, updated_at = ? WHERE id = ?",
        (status, iso_now(), record_id),
    )
    audit(connection, operator, f"{table}.status", f"{name} 状态改为 {status}")
    connection.commit()
    return dict(connection.execute(
        f"SELECT * FROM {table} WHERE id = ?", (record_id,)
    ).fetchone())


def list_project_rows(connection):
    rows = [dict(row) for row in connection.execute(
        """SELECT p.*,
                  w.warehouse_name,
                  (SELECT COUNT(*) FROM user_projects up
                    JOIN users u ON u.id = up.user_id
                    WHERE up.project_id = p.id AND u.role = 'project_admin'
                      AND u.status = 'active') AS user_count,
                  (SELECT COUNT(*) FROM products pd
                    WHERE pd.project_id = p.id AND pd.status = 'active') AS product_count,
                  (SELECT COUNT(*) FROM inventory i WHERE i.project_id = p.id) AS lot_count
           FROM projects p
           LEFT JOIN warehouses w ON w.id = p.warehouse_id
           ORDER BY p.id"""
    )]
    return rows


def _validate_project_warehouse(connection, warehouse_id, storage_area):
    try:
        warehouse_id = int(warehouse_id)
    except (TypeError, ValueError):
        raise ValueError("仓库无效")
    warehouse = connection.execute(
        "SELECT * FROM warehouses WHERE id = ?", (warehouse_id,)
    ).fetchone()
    if not warehouse or warehouse["status"] != "active":
        raise ValueError("仓库不存在或已停用")
    storage_area = _required_text(storage_area, "库区", 50)
    location = connection.execute(
        """SELECT 1 FROM warehouse_locations
           WHERE warehouse_id = ? AND storage_area = ? AND status = 'active'""",
        (warehouse_id, storage_area),
    ).fetchone()
    if not location:
        raise ValueError("库区在仓库中不存在")
    return warehouse_id, storage_area


def create_project(connection, operator, project_name, warehouse_id,
                   storage_area, remark=None):
    project_name = _required_text(project_name, "项目名称")
    if connection.execute(
        "SELECT 1 FROM projects WHERE project_name = ?", (project_name,)
    ).fetchone():
        raise ValueError("项目名称已存在")
    warehouse_id, storage_area = _validate_project_warehouse(
        connection, warehouse_id, storage_area
    )
    now = iso_now()
    cursor = connection.execute(
        """INSERT INTO projects(project_name, status, warehouse_id, storage_area,
                                remark, created_at, updated_at)
           VALUES (?, 'active', ?, ?, ?, ?, ?)""",
        (project_name, warehouse_id, storage_area,
         str(remark or "").strip() or None, now, now),
    )
    for template_type, columns in __import__("db").DEFAULT_TEMPLATES.items():
        connection.execute(
            """INSERT INTO templates
               (project_id, template_type, version, columns_json, status, updated_at)
               VALUES (?, ?, 1, ?, 'active', ?)""",
            (cursor.lastrowid, template_type,
             json.dumps(columns, ensure_ascii=False), now),
        )
    audit(connection, operator, "project.create", f"创建项目 {project_name}")
    connection.commit()
    return dict(connection.execute(
        "SELECT * FROM projects WHERE id = ?", (cursor.lastrowid,)
    ).fetchone())


def update_project(connection, operator, project_id, project_name=None,
                   warehouse_id=None, storage_area=None, remark=None,
                   status=None):
    row = connection.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not row:
        raise ValueError("项目不存在")
    name = _required_text(project_name, "项目名称") if project_name is not None else row["project_name"]
    if connection.execute(
        "SELECT 1 FROM projects WHERE project_name = ? AND id <> ?",
        (name, project_id),
    ).fetchone():
        raise ValueError("项目名称已存在")
    state = status if status is not None else row["status"]
    if state not in ("active", "disabled"):
        raise ValueError("项目状态无效")
    new_warehouse_id = row["warehouse_id"]
    new_storage_area = row["storage_area"]
    if warehouse_id is not None or storage_area is not None:
        bound = _validate_project_warehouse(
            connection,
            row["warehouse_id"] if warehouse_id is None else warehouse_id,
            row["storage_area"] if storage_area is None else storage_area,
        )
        new_warehouse_id, new_storage_area = bound
        if (new_warehouse_id != row["warehouse_id"]
                or new_storage_area != row["storage_area"]):
            has_inventory = connection.execute(
                "SELECT 1 FROM inventory WHERE project_id = ? LIMIT 1",
                (project_id,),
            ).fetchone()
            if has_inventory:
                raise ValueError("项目已有库存，不能修改仓库库区")
    note = str(remark or "").strip() or None if remark is not None else row["remark"]
    connection.execute(
        """UPDATE projects SET project_name = ?, status = ?, warehouse_id = ?,
           storage_area = ?, remark = ?, updated_at = ? WHERE id = ?""",
        (name, state, new_warehouse_id, new_storage_area, note,
         iso_now(), project_id),
    )
    audit(connection, operator, "project.update", f"修改项目 {name}", project_id)
    connection.commit()
    return dict(connection.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone())


# ---- 管理员撤销（V8）：整单撤销，禁止负库存，反向流水留痕 ----

ORDER_TYPE_LABELS = {
    "inbound": "入库单", "outbound": "出库单",
    "move": "移库单", "history_import": "历史库存初始化",
}


def list_admin_orders(connection, project_id=None, order_type=None):
    """管理员单据管理：全部/指定项目的单据（含已撤销），带商品摘要。"""
    sql = """SELECT o.*, p.project_name FROM stock_orders o
             JOIN projects p ON p.id = o.project_id WHERE 1=1"""
    params = []
    if project_id:
        sql += " AND o.project_id = ?"
        params.append(int(project_id))
    if order_type in ("inbound", "outbound", "move", "history_import"):
        sql += " AND o.order_type = ?"
        params.append(order_type)
    sql += " ORDER BY o.id DESC LIMIT 300"
    orders = []
    for row in connection.execute(sql, params):
        item = dict(row)
        item["projectName"] = row["project_name"]
        items = connection.execute(
            """SELECT product_name, SUM(quantity) AS quantity
               FROM stock_order_items WHERE order_id = ?
               GROUP BY product_name""",
            (row["id"],),
        ).fetchall()
        item["summary"] = "；".join(
            f"{one['product_name']}×{one['quantity']}" for one in items
        ) or "-"
        orders.append(item)
    return orders


def _inventory_row(connection, project_id, product_id, location_id, batch_no):
    return connection.execute(
        """SELECT * FROM inventory WHERE project_id = ? AND product_id = ?
           AND location_id = ? AND batch_no = ?""",
        (project_id, product_id, location_id, batch_no),
    ).fetchone()


def _insert_reversal_log(connection, order, log, location_id, change_type,
                         change_quantity, operator_name, now):
    connection.execute(
        """INSERT INTO stock_logs
           (project_id, order_no, product_id, batch_no, location_id,
            change_type, change_quantity, operator_name, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (order["project_id"], order["order_no"], log["product_id"],
         log["batch_no"], location_id, change_type, change_quantity,
         operator_name, now),
    )


def _reverse_inbound(connection, order, operator, now):
    logs = connection.execute(
        """SELECT * FROM stock_logs WHERE project_id = ? AND order_no = ?
           AND change_type = 'inbound'""",
        (order["project_id"], order["order_no"]),
    ).fetchall()
    if not logs:
        raise ValueError("找不到该单据的入库流水，无法撤销")
    for log in logs:
        row = _inventory_row(connection, order["project_id"],
                             log["product_id"], log["location_id"],
                             log["batch_no"])
        qty = log["change_quantity"]
        if not row:
            raise ValueError(f"货品 {log['product_id']} 批次 {log['batch_no']} "
                             f"的库存行不存在（可能已并批），无法撤销")
        available = (row["total_quantity"] - row["picking_reserved"]
                     - row["moving_reserved"])
        if available < qty:
            location = _location_json(connection, log["location_id"])
            raise ValueError(
                f"货品 {log['product_id']} 批次 {log['batch_no']} 在 "
                f"{location['full_name']} 的库存已被消耗（剩 {available}），"
                f"撤销会导致负库存，请先出库或移走"
            )
    for log in logs:
        qty = log["change_quantity"]
        row = _inventory_row(connection, order["project_id"],
                             log["product_id"], log["location_id"],
                             log["batch_no"])
        connection.execute(
            """UPDATE inventory SET total_quantity = total_quantity - ?,
               updated_at = ? WHERE id = ?""",
            (qty, now, row["id"]),
        )
        _insert_reversal_log(connection, order, log, log["location_id"],
                             "inbound_reversed", -qty,
                             operator["display_name"], now)


def _restore_outbound_item(connection, order, item, operator, now):
    location = connection.execute(
        "SELECT * FROM warehouse_locations WHERE id = ?",
        (item["location_id"],),
    ).fetchone()
    if not location or location["status"] != "active":
        raise ValueError(f"货位 {item['location_id']} 已删除或停用，"
                         f"无法恢复出库库存，请先重新启用该货位")
    row = _inventory_row(connection, order["project_id"],
                         item["product_id"], item["location_id"],
                         item["batch_no"])
    if row:
        connection.execute(
            """UPDATE inventory SET total_quantity = total_quantity + ?,
               updated_at = ? WHERE id = ?""",
            (item["quantity"], now, row["id"]),
        )
    else:
        connection.execute(
            """INSERT INTO inventory
               (project_id, product_id, location_id, batch_no,
                production_date, expiry_date, inbound_date, total_quantity,
                picking_reserved, moving_reserved, quality_status,
                prohibit_outbound, purchase_order_no, last_inbound_at,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, NULL, ?, ?, 0, 0, ?, 0, NULL, ?, ?, ?)""",
            (
                order["project_id"], item["product_id"], item["location_id"],
                item["batch_no"], item["production_date"],
                order["order_date"], item["quantity"],
                item["quality_status"] or "良品", now, now, now,
            ),
        )
    _insert_reversal_log(
        connection, order, item, item["location_id"], "outbound_reversed",
        item["quantity"], operator["display_name"], now,
    )


def _reverse_outbound(connection, order, operator, now):
    pick = connection.execute(
        "SELECT * FROM picking_orders WHERE order_id = ?", (order["id"],)
    ).fetchone()
    if not pick:
        raise ValueError("找不到该单据的拣货记录，无法撤销")
    items = connection.execute(
        "SELECT * FROM picking_items WHERE picking_order_id = ?",
        (pick["id"],),
    ).fetchall()
    # 先整单校验，再恢复，避免半撤
    for item in items:
        location = connection.execute(
            "SELECT status FROM warehouse_locations WHERE id = ?",
            (item["location_id"],),
        ).fetchone()
        if not location or location["status"] != "active":
            raise ValueError(f"货位已删除或停用，无法恢复出库库存，"
                             f"请先重新启用相关货位")
    for item in items:
        _restore_outbound_item(connection, order, item, operator, now)
    if pick["status"] == "pending":
        connection.execute(
            "DELETE FROM picking_items WHERE picking_order_id = ?",
            (pick["id"],),
        )
        connection.execute(
            "DELETE FROM picking_orders WHERE id = ?", (pick["id"],)
        )


def _reverse_move(connection, order, operator, now):
    items = connection.execute(
        "SELECT * FROM stock_order_items WHERE order_id = ?",
        (order["id"],),
    ).fetchall()
    if not items:
        raise ValueError("找不到该单据的移库明细，无法撤销")
    for item in items:
        if item["to_location_id"] is None or item["location_id"] is None:
            raise ValueError("移库明细缺少货位信息，无法撤销")
        target = _inventory_row(connection, order["project_id"],
                                item["product_id"], item["to_location_id"],
                                item["batch_no"] or "")
        available = ((target["total_quantity"] - target["picking_reserved"]
                      - target["moving_reserved"]) if target else 0)
        if available < item["quantity"]:
            location = _location_json(connection, item["to_location_id"])
            raise ValueError(
                f"货品 {item['product_id']} 批次 {item['batch_no']} 在 "
                f"{location['full_name']} 的库存不足（剩 {available}），"
                f"撤销会导致负库存"
            )
    for item in items:
        target = _inventory_row(connection, order["project_id"],
                                item["product_id"], item["to_location_id"],
                                item["batch_no"] or "")
        connection.execute(
            """UPDATE inventory SET total_quantity = total_quantity - ?,
               updated_at = ? WHERE id = ?""",
            (item["quantity"], now, target["id"]),
        )
        source = _inventory_row(connection, order["project_id"],
                                item["product_id"], item["location_id"],
                                item["batch_no"] or "")
        if source:
            connection.execute(
                """UPDATE inventory SET total_quantity = total_quantity + ?,
                   updated_at = ? WHERE id = ?""",
                (item["quantity"], now, source["id"]),
            )
        else:
            connection.execute(
                """INSERT INTO inventory
                   (project_id, product_id, location_id, batch_no,
                    production_date, expiry_date, inbound_date, total_quantity,
                    picking_reserved, moving_reserved, quality_status,
                    prohibit_outbound, purchase_order_no, last_inbound_at,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?)""",
                (
                    order["project_id"], item["product_id"],
                    item["location_id"], item["batch_no"],
                    target["production_date"], target["expiry_date"],
                    target["inbound_date"], item["quantity"],
                    target["quality_status"], target["prohibit_outbound"],
                    target["purchase_order_no"], target["last_inbound_at"],
                    now, now,
                ),
            )
        for location_id, quantity in ((item["to_location_id"],
                                       -item["quantity"]),
                                      (item["location_id"], item["quantity"])):
            _insert_reversal_log(
                connection, order, item, location_id, "move_reversed",
                quantity, operator["display_name"], now,
            )


def reverse_stock_order(connection, operator, order_id):
    order = connection.execute(
        "SELECT * FROM stock_orders WHERE id = ?", (order_id,)
    ).fetchone()
    if not order:
        raise ValueError("单据不存在")
    if order["status"] == "reversed":
        raise ValueError("该单据已撤销，不能重复撤销")
    if order["order_type"] == "history_import":
        raise ValueError("历史库存初始化不支持撤销")
    if order["status"] != "completed" and order["order_type"] != "outbound":
        raise ValueError("仅已完成的单据可以撤销")

    now = iso_now()
    type_label = ORDER_TYPE_LABELS[order["order_type"]]
    if order["order_type"] == "inbound":
        _reverse_inbound(connection, order, operator, now)
    elif order["order_type"] == "outbound":
        _reverse_outbound(connection, order, operator, now)
    else:
        _reverse_move(connection, order, operator, now)

    connection.execute(
        """UPDATE stock_orders SET status = 'reversed', reversed_at = ?,
           reversed_by = ? WHERE id = ?""",
        (now, operator["display_name"], order_id),
    )
    audit(connection, operator, "order.reverse",
          f"撤销{type_label} {order['order_no']}", order["project_id"])
    connection.commit()
    return dict(connection.execute(
        "SELECT * FROM stock_orders WHERE id = ?", (order_id,)
    ).fetchone())


def create_bulk_move(connection, operator, project_id, items):
    """筛选式批量移库：库存明细勾选批次 → 一步生成移库单（管理员/项目管理员）。"""
    project = require_project(connection, operator, project_id)
    if not isinstance(items, list) or not items:
        raise ValueError("请选择要移库的库存批次")
    order_no = _next_number(connection, "order", "YK")
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    rows = []
    for index, item in enumerate(items, 1):
        quantity = _optional_int(item.get("quantity"), "移库数量")
        if not quantity or quantity <= 0:
            raise ValueError("移库数量必须为正整数")
        to_location = _required_text(item.get("toLocation"), "目标货位", 100)
        from_location = _required_text(item.get("fromLocation"), "源货位", 100)
        row = _inventory_row(connection, project["id"],
                             item.get("productId"), item.get("fromLocationId"),
                             item.get("batchNo") or "")
        if not row:
            raise ValueError("所选库存不存在或已变化，请刷新后重试")
        available = (row["total_quantity"] - row["picking_reserved"]
                     - row["moving_reserved"])
        if quantity > available:
            raise ValueError(f"货品 {item.get('productId')} 批次 "
                             f"{item.get('batchNo')} 可用库存不足"
                             f"（可用 {available}）")
        rows.append({
            "_row": index,
            "order_no": order_no,
            "order_date": today,
            "product_code": item.get("productId"),
            "batch_no": item.get("batchNo"),
            "quantity": quantity,
            "unit": _required_text(item.get("unit") or "件", "单位", 20),
            "from_location": from_location,
            "to_location": to_location,
            "note": "筛选批量移库",
        })
    parsed = {
        # 批量移库不走模板：模板仅服务于 Excel 导入的列名配置
        "version": 1,
        "rows": rows,
        "products": _validate_items(connection, project["id"], "move", rows),
    }
    return create_move(connection, operator, project["id"], parsed)


# ---- 批次修正 / 品质调整 / 禁出开关（V8）：不改数量，全部留痕 ----

def _adjust_targets(connection, operator, project_id, ids):
    """校验并取回要调整的库存行；ids 无效或越权时报错。"""
    project = require_project(connection, operator, project_id)
    if not isinstance(ids, list) or not ids:
        raise ValueError("请先勾选要调整的库存批次")
    try:
        id_list = [int(value) for value in ids]
    except (TypeError, ValueError):
        raise ValueError("库存批次无效")
    marks = ",".join("?" for _ in id_list)
    rows = connection.execute(
        f"""SELECT i.*, l.location_name FROM inventory i
            JOIN warehouse_locations l ON l.id = i.location_id
            WHERE i.id IN ({marks}) AND i.project_id = ?""",
        (*id_list, project["id"]),
    ).fetchall()
    if len(rows) != len(id_list):
        raise ValueError("部分库存批次不存在或不属于当前项目，请刷新后重试")
    return project, rows


def adjust_inventory_batches(connection, operator, project_id, ids,
                             production_date=None, expiry_date=None,
                             purchase_order_no=None):
    """批次修正：仅允许改生产日期/到期日期/采购单号，字段缺省=不改。"""
    project, rows = _adjust_targets(connection, operator, project_id, ids)
    updates = {}
    if production_date is not None:
        updates["production_date"] = (
            _manual_date(production_date, "生产日期") or None
        )
    if expiry_date is not None:
        updates["expiry_date"] = _manual_date(expiry_date, "到期日期") or None
    if purchase_order_no is not None:
        updates["purchase_order_no"] = (
            str(purchase_order_no).strip() or None
        )
    if not updates:
        raise ValueError("没有要修改的字段")
    now = iso_now()
    for row in rows:
        before = "；".join(
            f"{label}: {row[key] or '空'} → {value or '空'}"
            for key, value, label in (
                ("production_date", updates.get("production_date"), "生产日期"),
                ("expiry_date", updates.get("expiry_date"), "到期日期"),
                ("purchase_order_no", updates.get("purchase_order_no"), "采购单号"),
            ) if key in updates
        )
        sets = ",".join(f"{key} = ?" for key in updates)
        connection.execute(
            f"""UPDATE inventory SET {sets}, updated_at = ? WHERE id = ?""",
            (*updates.values(), now, row["id"]),
        )
        audit(connection, operator, "inventory.adjust",
              f"修正批次 {row['product_id']} / {row['batch_no']} @ "
              f"{row['location_name']}（{before}）",
              project_id=project["id"])
    connection.commit()
    return {"adjusted": len(rows)}


def set_inventory_quality(connection, operator, project_id, ids,
                          quality_status):
    if quality_status not in ("良品", "扣留"):
        raise ValueError("品质标记仅支持 良品 / 扣留")
    project, rows = _adjust_targets(connection, operator, project_id, ids)
    now = iso_now()
    for row in rows:
        if row["quality_status"] == quality_status:
            continue
        connection.execute(
            "UPDATE inventory SET quality_status = ?, updated_at = ? WHERE id = ?",
            (quality_status, now, row["id"]),
        )
        audit(connection, operator, "inventory.quality",
              f"批次 {row['product_id']} / {row['batch_no']} @ "
              f"{row['location_name']} 品质 {row['quality_status']} → {quality_status}",
              project_id=project["id"])
    connection.commit()
    return {"adjusted": len(rows)}


def set_inventory_prohibit(connection, operator, project_id, ids, prohibit):
    prohibit = 1 if prohibit in (1, "1", True, "true") else 0
    project, rows = _adjust_targets(connection, operator, project_id, ids)
    now = iso_now()
    label = "禁止出库" if prohibit else "恢复出库"
    for row in rows:
        if row["prohibit_outbound"] == prohibit:
            continue
        connection.execute(
            "UPDATE inventory SET prohibit_outbound = ?, updated_at = ? WHERE id = ?",
            (prohibit, now, row["id"]),
        )
        audit(connection, operator, "inventory.prohibit",
              f"{label}：批次 {row['product_id']} / {row['batch_no']} @ "
              f"{row['location_name']}",
              project_id=project["id"])
    connection.commit()
    return {"adjusted": len(rows)}


def _user_project_ids(connection, user_id):
    return [
        row["project_id"]
        for row in connection.execute(
            "SELECT project_id FROM user_projects WHERE user_id = ? ORDER BY project_id",
            (user_id,),
        )
    ]


def list_users(connection):
    users = [dict(row) for row in connection.execute(
        """SELECT id, username, display_name, contact_phone, role, status,
                  must_change_password, created_at, updated_at
           FROM users ORDER BY id"""
    )]
    for user in users:
        user["projectIds"] = _user_project_ids(connection, user["id"])
    return users


def _validate_project_ids(connection, role, project_ids):
    if role != "project_admin":
        return []
    ids = project_ids or []
    if not isinstance(ids, list) or not ids:
        raise ValueError("项目管理员必须至少选择一个项目")
    try:
        ids = [int(value) for value in ids]
    except (TypeError, ValueError):
        raise ValueError("项目授权无效")
    if len(set(ids)) != len(ids):
        raise ValueError("项目授权不能重复")
    rows = connection.execute(
        """SELECT id FROM projects WHERE status = 'active'
           AND id IN (%s)""" % ",".join("?" for _ in ids),
        ids,
    ).fetchall()
    if len(rows) != len(ids):
        raise ValueError("包含不存在或已停用的项目")
    return ids


def create_user(connection, operator, username, display_name, contact_phone,
                role, password, project_ids=None):
    username = str(username or "").strip()
    display_name = _required_text(display_name, "姓名")
    if not valid_username(username):
        raise ValueError("姓名全拼须为2-50位小写字母")
    if not valid_phone(contact_phone):
        raise ValueError("手机号格式错误")
    if role not in ("admin", "project_admin"):
        raise ValueError("角色无效")
    if not valid_password(password):
        raise ValueError("密码须为8-20位，且同时包含字母和数字")
    if connection.execute(
        "SELECT 1 FROM users WHERE username = ?", (username,)
    ).fetchone():
        raise ValueError("姓名全拼已存在")
    project_ids = _validate_project_ids(connection, role, project_ids)
    now = iso_now()
    cursor = connection.execute(
        """INSERT INTO users
           (username, display_name, contact_phone, password_hash, role, status,
            must_change_password, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'active', 1, ?, ?)""",
        (username, display_name, str(contact_phone or "").strip() or None,
         hash_password(password), role, now, now),
    )
    for project_id in project_ids:
        connection.execute(
            """INSERT INTO user_projects(user_id, project_id, created_at)
               VALUES (?, ?, ?)""",
            (cursor.lastrowid, project_id, now),
        )
    audit(connection, operator, "user.create", f"创建用户 {display_name}({username})")
    connection.commit()
    return dict(connection.execute(
        "SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)
    ).fetchone())


def update_user(connection, operator, user_id, username=None, display_name=None,
                contact_phone=None, role=None, status=None, project_ids=None):
    row = connection.execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if not row:
        raise ValueError("用户不存在")
    values = {
        "username": str(username or "").strip() if username is not None else row["username"],
        "display_name": _required_text(display_name, "姓名")
        if display_name is not None else row["display_name"],
        "contact_phone": str(contact_phone or "").strip() or None
        if contact_phone is not None else row["contact_phone"],
        "role": role if role is not None else row["role"],
        "status": status if status is not None else row["status"],
    }
    if not valid_username(values["username"]):
        raise ValueError("姓名全拼须为2-50位小写字母")
    if not valid_phone(values["contact_phone"]):
        raise ValueError("手机号格式错误")
    if values["role"] not in ("admin", "project_admin"):
        raise ValueError("角色无效")
    if values["status"] not in ("active", "disabled"):
        raise ValueError("用户状态无效")
    if operator["id"] == user_id and values["status"] == "disabled":
        raise ValueError("不能禁用当前登录账号")
    if connection.execute(
        "SELECT 1 FROM users WHERE username = ? AND id <> ?",
        (values["username"], user_id),
    ).fetchone():
        raise ValueError("姓名全拼已存在")
    if project_ids is None:
        current_ids = _user_project_ids(connection, user_id)
        project_ids = current_ids if values["role"] == "project_admin" else []
    normalized_projects = _validate_project_ids(
        connection, values["role"], project_ids
    )
    connection.execute(
        """UPDATE users SET username = ?, display_name = ?, contact_phone = ?,
           role = ?, status = ?, updated_at = ? WHERE id = ?""",
        (
            values["username"], values["display_name"], values["contact_phone"],
            values["role"], values["status"], iso_now(), user_id,
        ),
    )
    connection.execute("DELETE FROM user_projects WHERE user_id = ?", (user_id,))
    for project_id in normalized_projects:
        connection.execute(
            """INSERT INTO user_projects(user_id, project_id, created_at)
               VALUES (?, ?, ?)""",
            (user_id, project_id, iso_now()),
        )
    if values["status"] == "disabled":
        connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    audit(connection, operator, "user.update", f"修改用户 {values['display_name']}")
    connection.commit()


def reset_user_password(connection, operator, user_id, password):
    row = connection.execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if not row:
        raise ValueError("用户不存在")
    if not valid_password(password):
        raise ValueError("密码须为8-20位，且同时包含字母和数字")
    connection.execute(
        """UPDATE users SET password_hash = ?, must_change_password = 1,
           failed_attempts = 0, locked_until = NULL, updated_at = ?
           WHERE id = ?""",
        (hash_password(password), iso_now(), user_id),
    )
    connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    audit(connection, operator, "user.reset_password", f"重置用户 {row['display_name']}密码")
    connection.commit()


def validate_template_columns(order_type, columns):
    if not isinstance(columns, list) or not columns or len(columns) > 25:
        raise ValueError("模板列配置必须为1-25列的数组")
    keys = []
    names = []
    required_keys = REQUIRED_FIELDS[order_type]
    for column in columns:
        if not isinstance(column, dict):
            raise ValueError("模板列格式错误")
        key = column.get("key")
        name = str(column.get("name", "")).strip()
        field_type = column.get("type")
        required = column.get("required")
        if key not in FIELD_TYPES:
            raise ValueError(f"不支持的字段：{key}")
        if field_type != FIELD_TYPES[key]:
            raise ValueError(f"字段 {name} 类型必须为 {FIELD_TYPES[key]}")
        if not name or len(name) > 30:
            raise ValueError("列名不能为空，且不能超过30个字符")
        if not isinstance(required, bool):
            raise ValueError("required必须为布尔值")
        if key in required_keys and not required:
            raise ValueError(f"字段 {name} 为系统必填字段")
        keys.append(key)
        names.append(name)
    if len(set(keys)) != len(keys) or len(set(names)) != len(names):
        raise ValueError("字段或列名不能重复")
    if not required_keys.issubset(set(keys)):
        raise ValueError("模板缺少系统必需字段")
    return [
        {
            "key": key,
            "name": names[index],
            "required": bool(column["required"]),
            "type": column["type"],
        }
        for index, (key, column) in enumerate(zip(keys, columns))
    ]


def get_template(connection, order_type, project_id):
    row = connection.execute(
        """SELECT * FROM templates
           WHERE project_id = ? AND template_type = ?""",
        (project_id, order_type),
    ).fetchone()
    if not row:
        raise ValueError("模板不存在")
    defaults = {
        column["key"]: column["name"]
        for column in DEFAULT_TEMPLATES.get(order_type, ())
    }
    required_keys = REQUIRED_FIELDS.get(order_type, ())
    return {
        "type": order_type,
        "version": row["version"],
        "status": row["status"],
        "columns": [
            {**column,
             "label": defaults.get(column["key"], column["name"]),
             "locked": column["key"] in required_keys}
            for column in json.loads(row["columns_json"])
        ],
    }


def update_template(connection, operator, project_id, order_type, columns):
    normalized = validate_template_columns(order_type, columns)
    row = connection.execute(
        """SELECT version FROM templates
           WHERE project_id = ? AND template_type = ?""",
        (project_id, order_type),
    ).fetchone()
    if not row:
        raise ValueError("模板不存在")
    connection.execute(
        """UPDATE templates SET version = version + 1, columns_json = ?,
           status = 'active', updated_at = ?
           WHERE project_id = ? AND template_type = ?""",
        (json.dumps(normalized, ensure_ascii=False), iso_now(),
         project_id, order_type),
    )
    audit(
        connection, operator, "template.update",
        f"更新{order_type}模板", project_id,
    )
    connection.commit()
    return get_template(connection, order_type, project_id)


def set_template_status(connection, operator, project_id, order_type, status):
    if status not in ("active", "disabled"):
        raise ValueError("模板状态无效")
    row = connection.execute(
        """SELECT template_type FROM templates
           WHERE project_id = ? AND template_type = ?""",
        (project_id, order_type),
    ).fetchone()
    if not row:
        raise ValueError("模板不存在")
    connection.execute(
        """UPDATE templates SET status = ?, updated_at = ?
           WHERE project_id = ? AND template_type = ?""",
        (status, iso_now(), project_id, order_type),
    )
    audit(
        connection, operator, "template.status",
        f"模板状态改为{status}", project_id,
    )
    connection.commit()
    return get_template(connection, order_type, project_id)


def template_workbook(connection, order_type, project_id):
    template = get_template(connection, order_type, project_id)
    if template["status"] != "active":
        raise ValueError("模板已停用")
    columns = template["columns"]
    examples = {
        "order_no": "RK-20260827-0001",
        "order_date": "2026-08-27",
        "product_code": "H001",
        "product_name": "示例商品",
        "spec": "100g",
        "unit": "个",
        "batch_no": "B20260827-01",
        "production_date": "2026-08-01",
        "expiry_date": "2027-01-27",
        "shelf_life_days": 180,
        "inbound_date": "2026-08-27",
        "quantity": 10,
        "warehouse_name": "主仓库",
        "storage_area": "A库区",
        "cargo_area": "A货区",
        "location_name": "A-01-01",
        "from_location": "A-01-01",
        "to_location": "A-01-02",
        "quality_status": "良品",
        "purchase_order_no": "PO001",
        "recipient": "示例收货方",
        "note": "导入时本行自动忽略",
    }
    rows = [
        [column["name"] for column in columns],
        [examples.get(column["key"], "") for column in columns],
    ]
    return excelio.build_workbook(rows, f"{order_type}-v{template['version']}")


def _error(errors, row, column, message):
    errors.append({"row": row, "column": column, "message": message})


def _parse_date(value):
    """归一化常见日期写法与 Excel 日期序列号，返回 date 对象（YYYY-MM-DD）。"""
    text = str(value or "").strip().lstrip("= \t").strip('"').strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日", "%Y%m%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    if text.isdigit():
        # Excel 日期单元格存的是序列号（1899-12-30 起算的天数），
        # 用户在模板里正常输入日期就会得到它，必须还原
        serial = int(text)
        if 20000 <= serial <= 80000:
            return (datetime(1899, 12, 30) + timedelta(days=serial)).date()
    return None


def parse_import(connection, project_id, data, order_type):
    if len(data) > MAX_FILE_SIZE:
        raise ValueError("文件不能超过5MB")
    template = get_template(connection, order_type, project_id)
    if template["status"] != "active":
        raise ValueError("模板已停用")
    version, columns = columns_for(connection, order_type, project_id)
    rows = excelio.read_workbook(data)
    if not rows:
        raise ValueError("Excel文件为空")

    header_number, header_values = rows[0]
    headers = [str(value).strip() for value in header_values]
    columns_by_name = {column["name"]: column for column in columns}
    errors = []
    for expected in columns:
        if expected["name"] not in headers:
            _error(errors, header_number, expected["name"], "缺少必填列")
    for position, name in enumerate(headers, 1):
        if name and name not in columns_by_name:
            _error(errors, header_number, excelio.column_letter(position),
                   f"未配置的列：{name}")
    if len(headers) != len(set(headers)):
        _error(errors, header_number, "表头", "列名不能重复")

    index_by_key = {}
    for column in columns:
        if column["name"] in headers:
            index_by_key[column["key"]] = headers.index(column["name"])
    parsed_rows = []
    for excel_row, values in rows[1:]:
        normalized = [str(value).strip() if value is not None else "" for value in values]
        if not any(normalized):
            continue
        if normalized and normalized[0].startswith("示例"):
            continue
        if len(normalized) > len(headers):
            _error(errors, excel_row, "多余列", "存在未配置的单元格数据")
        item = {"_row": excel_row}
        for column in columns:
            key = column["key"]
            index = index_by_key.get(key)
            raw = normalized[index] if index is not None and index < len(normalized) else ""
            if raw == "":
                if column["required"]:
                    _error(errors, excel_row, column["name"], f"{column['name']}不能为空")
                item[key] = None
                continue
            if column["type"] == "integer":
                if not re.fullmatch(r"\d+", raw) or int(raw) <= 0:
                    _error(errors, excel_row, column["name"],
                           f"{column['name']}必须为正整数")
                    item[key] = None
                else:
                    item[key] = int(raw)
            elif column["type"] == "date":
                parsed_date = _parse_date(raw)
                if parsed_date is None:
                    _error(errors, excel_row, column["name"],
                           f"日期无法识别：{raw}（可用 2026-01-31、2026/1/31、2026年1月31日）")
                    item[key] = None
                else:
                    item[key] = parsed_date.isoformat()
            else:
                if len(raw) > 100:
                    _error(errors, excel_row, column["name"],
                           f"{column['name']}不能超过100个字符")
                item[key] = raw
        parsed_rows.append(item)

    if not parsed_rows and not errors:
        raise ValueError("导入文件没有数据行")
    if len(parsed_rows) > MAX_IMPORT_ROWS:
        _error(errors, parsed_rows[MAX_IMPORT_ROWS]["_row"], "行数",
               "单次导入不能超过500行")

    products = _validate_items(connection, project_id, order_type, parsed_rows)
    if errors:
        raise ImportValidationError("Excel校验失败", errors)
    return {"version": version, "rows": parsed_rows, "products": products}


def _resolve_location(connection, project, location_name):
    name = str(location_name or "").strip()
    if not name:
        return None
    parts = [part.strip() for part in name.split("/") if part.strip()]
    if len(parts) == 2:
        cargo_area, location = parts
        row = connection.execute(
            """SELECT l.id FROM warehouse_locations l
               WHERE l.warehouse_id = ? AND l.storage_area = ?
                 AND l.cargo_area = ? AND l.location_name = ?
                 AND l.status = 'active'""",
            (project["warehouse_id"], project["storage_area"],
             cargo_area, location),
        ).fetchone()
        return dict(row) if row else None
    rows = connection.execute(
        """SELECT l.id FROM warehouse_locations l
           WHERE l.warehouse_id = ? AND l.storage_area = ?
             AND l.location_name = ? AND l.status = 'active'""",
        (project["warehouse_id"], project["storage_area"], name),
    ).fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        return "ambiguous"
    return dict(rows[0])


def _validate_items(connection, project_id, order_type, items):
    """Shared product/location validation for import and manual entry."""
    errors = []
    project = connection.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    project = dict(project) if project else None
    if not project or not project.get("warehouse_id"):
        raise ValueError("项目未绑定仓库库区，请联系管理员")
    products = {}
    for item in items:
        row_no = item.get("_row", 1)
        code = item.get("product_code")
        if not code:
            _error(errors, row_no, "货品编号", "货品编号不能为空")
            continue
        product = connection.execute(
            "SELECT * FROM products WHERE product_id = ?", (code,)
        ).fetchone()
        if not product:
            _error(errors, row_no, "货品编号", f"货品编号未建档：{code}")
            continue
        if product["status"] != "active":
            _error(errors, row_no, "货品编号", f"货品已停用：{code}")
        linked = product["project_id"] == project_id
        if not linked:
            _error(errors, row_no, "货品编号",
                   f"货品不属于当前项目，请联系管理员：{code}")
        unit = item.get("unit")
        if unit and product["unit"] and product["unit"] != unit:
            _error(errors, row_no, "单位", "单位与商品档案不一致")
        if code not in products:
            products[code] = dict(product)
        item["product_name"] = product["product_name"]
        item["spec"] = product["spec"] or ""
        if not item.get("unit") and product["unit"]:
            item["unit"] = product["unit"]

    for item in items:
        row_no = item.get("_row", 1)
        if order_type == "inbound":
            if item.get("quality_status") in (None, ""):
                item["quality_status"] = "良品"
            if item.get("quality_status") not in ("良品", "扣留"):
                _error(errors, row_no, "品质标记", "品质标记必须为良品或扣留")
        if order_type == "inbound" or item.get("location_name"):
            location = _resolve_location(
                connection, project, item.get("location_name")
            )
            if location is None:
                _error(errors, row_no, "货位", "货位在项目仓库/库区内不存在")
            elif location == "ambiguous":
                _error(errors, row_no, "货位",
                       "货位不唯一，请使用 货区/货位 格式填写")
            else:
                item["location_id"] = location["id"]
        if order_type == "move":
            for field, column_label, attr in (
                ("from_location", "源货位", "from_location_id"),
                ("to_location", "目标货位", "to_location_id"),
            ):
                location = _resolve_location(
                    connection, project, item.get(field)
                )
                if location is None:
                    _error(errors, row_no, column_label,
                           f"{column_label}在项目仓库/库区内不存在")
                elif location == "ambiguous":
                    _error(errors, row_no, column_label,
                           f"{column_label}不唯一，请使用 货区/货位 格式填写")
                else:
                    item[attr] = location["id"]
            if (item.get("from_location_id")
                    and item.get("from_location_id") == item.get("to_location_id")):
                _error(errors, row_no, "目标货位", "源货位与目标货位不能相同")
    if errors:
        raise ImportValidationError("校验失败", errors)
    return products


def _single_order_no(order_nos, parsed, label):
    order_nos.discard(None)
    if len(order_nos) != 1:
        raise ImportValidationError("Excel校验失败", [{
            "row": parsed["rows"][0]["_row"],
            "column": label,
            "message": f"同一文件只能使用一个{label}",
        }])
    return order_nos.pop()


def _row_totals(rows):
    totals = {}
    for item in rows:
        totals[item["product_code"]] = (
            totals.get(item["product_code"], 0) + item["quantity"]
        )
    return totals


def _manual_date(value, name):
    value = str(value or "").strip()
    if not value:
        return None
    parsed = _parse_date(value)
    if parsed is None:
        raise ValueError(f"{name}无法识别：{value}（可用 2026-01-31、2026/1/31）")
    return parsed.isoformat()


def _manual_common(values, order_type):
    label = "入库通知单号" if order_type == "inbound" else "配货单号"
    item = {"_row": 1}
    item["order_no"] = _required_text(values.get("orderNo"), label, 60)
    item["order_date"] = _manual_date(
        values.get("orderDate"),
        "入库日期" if order_type == "inbound" else "出库日期",
    )
    if not item["order_date"]:
        raise ValueError(
            "入库日期不能为空" if order_type == "inbound" else "出库日期不能为空"
        )
    item["product_code"] = _required_text(values.get("productCode"), "货品编号", 50)
    item["quality_status"] = str(values.get("qualityStatus") or "").strip() or "良品"
    item["batch_no"] = str(values.get("batchNo") or "").strip() or None
    if order_type == "inbound" and not item["batch_no"]:
        raise ValueError("生产批号不能为空")
    item["production_date"] = _manual_date(values.get("productionDate"), "生产日期")
    if order_type == "inbound" and not item["production_date"]:
        raise ValueError("生产日期不能为空")
    item["quantity"] = _optional_int(values.get("quantity"), "数量")
    if item["quantity"] is None or item["quantity"] <= 0:
        raise ValueError("数量必须为正整数")
    item["unit"] = _required_text(values.get("unit"), "单位", 20)
    item["location_name"] = str(values.get("locationName") or "").strip()
    if order_type == "inbound" and not item["location_name"]:
        raise ValueError("货位不能为空")
    item["note"] = str(values.get("note") or "").strip() or None
    return item


def create_manual_inbound(connection, operator, project_id, values):
    require_project(connection, operator, project_id)
    item = _manual_common(values, "inbound")
    item["expiry_date"] = _manual_date(values.get("expiryDate"), "到期日期")
    item["purchase_order_no"] = (
        str(values.get("purchaseOrderNo") or "").strip() or None
    )
    return create_inbound(connection, operator, project_id, {
        "version": get_template(connection, "inbound", project_id)["version"],
        "rows": [item],
        "products": _validate_items(
            connection, project_id, "inbound", [item]
        ),
    })


def create_manual_outbound(connection, operator, project_id, values):
    require_project(connection, operator, project_id)
    item = _manual_common(values, "outbound")
    item["recipient"] = str(values.get("recipient") or "").strip() or None
    return create_outbound(connection, operator, project_id, {
        "version": get_template(connection, "outbound", project_id)["version"],
        "rows": [item],
        "products": _validate_items(
            connection, project_id, "outbound", [item]
        ),
    })


def _manual_move(values):
    item = {"_row": 1}
    item["order_no"] = _required_text(values.get("orderNo"), "移库单号", 60)
    item["order_date"] = _manual_date(values.get("orderDate"), "移库日期")
    if not item["order_date"]:
        raise ValueError("移库日期不能为空")
    item["product_code"] = _required_text(values.get("productCode"), "货品编号", 50)
    item["batch_no"] = _required_text(values.get("batchNo"), "生产批号", 100)
    item["quantity"] = _optional_int(values.get("quantity"), "数量")
    if item["quantity"] is None or item["quantity"] <= 0:
        raise ValueError("数量必须为正整数")
    item["unit"] = _required_text(values.get("unit"), "单位", 20)
    item["from_location"] = _required_text(
        values.get("fromLocation"), "源货位", 100
    )
    item["to_location"] = _required_text(
        values.get("toLocation"), "目标货位", 100
    )
    item["note"] = str(values.get("note") or "").strip() or None
    return item


def create_manual_move(connection, operator, project_id, values):
    require_project(connection, operator, project_id)
    item = _manual_move(values)
    return create_move(connection, operator, project_id, {
        "version": get_template(connection, "move", project_id)["version"],
        "rows": [item],
        "products": _validate_items(
            connection, project_id, "move", [item]
        ),
    })


def _next_number(connection, kind, prefix):
    now = datetime.now(timezone(timedelta(hours=8)))
    day = now.strftime("%Y%m%d")
    like = f"{prefix}-{day}-%"
    rows = connection.execute(
        f"SELECT {kind}_no AS number FROM {table(kind)} WHERE {kind}_no LIKE ?",
        (like,),
    ).fetchall()
    serial = max((int(row["number"].rsplit("-", 1)[1]) for row in rows), default=0) + 1
    return f"{prefix}-{day}-{serial:04d}"


def table(kind):
    return "stock_orders" if kind == "order" else "picking_orders"


def _location_json(connection, location_id):
    row = connection.execute(
        """SELECT l.*, w.warehouse_name
           FROM warehouse_locations l
           JOIN warehouses w ON w.id = l.warehouse_id
           WHERE l.id = ?""",
        (location_id,),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["full_name"] = (
        f"{row['warehouse_name']} / {row['storage_area']} / "
        f"{row['cargo_area']} / {row['location_name']}"
    )
    return result


def _order_json(connection, order_id):
    order = connection.execute(
        "SELECT * FROM stock_orders WHERE id = ?", (order_id,)
    ).fetchone()
    items = connection.execute(
        "SELECT * FROM stock_order_items WHERE order_id = ? ORDER BY row_no, id",
        (order_id,),
    ).fetchall()
    picking = connection.execute(
        "SELECT * FROM picking_orders WHERE order_id = ?", (order_id,)
    ).fetchone()
    picking_items = []
    if picking:
        picking_items = connection.execute(
            """SELECT * FROM picking_items
               WHERE picking_order_id = ? ORDER BY sequence, id""",
            (picking["id"],),
        ).fetchall()
    result = {
        "id": order["id"],
        "projectId": order["project_id"],
        "orderNo": order["order_no"],
        "orderDate": order["order_date"],
        "type": order["order_type"],
        "status": order["status"],
        "operatorName": order["operator_name"],
        "templateVersion": order["template_version"],
        "note": order["note"],
        "recipient": order["recipient"],
        "createdAt": order["created_at"],
        "completedAt": order["completed_at"],
        "items": [],
        "pickingOrder": dict(picking) if picking else None,
        "pickingItems": [],
    }
    for item in items:
        value = dict(item)
        value["location"] = _location_json(connection, item["location_id"])
        value["toLocation"] = (
            _location_json(connection, item["to_location_id"])
            if item["to_location_id"] else None
        )
        result["items"].append(value)
    for item in picking_items:
        value = dict(item)
        value["location"] = _location_json(connection, item["location_id"])
        result["pickingItems"].append(value)
    return result


def import_inbound(connection, operator, project_id, data):
    require_project(connection, operator, project_id)
    parsed = parse_import(connection, project_id, data, "inbound")
    return create_inbound(connection, operator, project_id, parsed)


def create_inbound(connection, operator, project_id, parsed):
    now = iso_now()
    for item in parsed["rows"]:
        product = parsed["products"][item["product_code"]]
        if item.get("expiry_date"):
            item["_expiry"] = item["expiry_date"]
        elif item.get("production_date") and product.get("shelf_life_days"):
            item["_expiry"] = (
                datetime.fromisoformat(item["production_date"])
                + timedelta(days=product["shelf_life_days"])
            ).date().isoformat()
        else:
            item["_expiry"] = None
    grouped = {}
    for item in parsed["rows"]:
        key = (item["product_code"], item["batch_no"], item["location_id"])
        stable_fields = (
            "production_date", "_expiry", "order_date",
            "quality_status", "purchase_order_no", "unit",
        )
        if key in grouped and any(
            grouped[key].get(field) != item.get(field)
            for field in stable_fields
        ):
            raise ImportValidationError("Excel校验失败", [{
                "row": item["_row"],
                "column": "生产批号",
                "message": "同一商品、批次、货位的批次信息不一致",
            }])
        if key not in grouped:
            grouped[key] = {**item}
        else:
            grouped[key]["quantity"] += item["quantity"]
    order_no = _single_order_no(
        {item.get("order_no") for item in parsed["rows"]}, parsed, "入库通知单号"
    )
    if connection.execute(
        "SELECT 1 FROM stock_orders WHERE order_no = ?", (order_no,)
    ).fetchone():
        raise ValueError("入库通知单号已存在")
    try:
        connection.execute("BEGIN IMMEDIATE")
        order_date = next(
            (item.get("order_date") for item in parsed["rows"]
             if item.get("order_date")), None
        )
        note = next(
            (item.get("note") for item in parsed["rows"] if item.get("note")), ""
        )
        cursor = connection.execute(
            """INSERT INTO stock_orders
               (project_id, order_no, order_type, status, operator_id,
                operator_name, template_version, note, order_date, created_at,
                completed_at)
               VALUES (?, ?, 'inbound', 'completed', ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id, order_no, operator["id"], operator["display_name"],
                parsed["version"], note, order_date, now, now,
            ),
        )
        order_id = cursor.lastrowid
        for item in parsed["rows"]:
            product = parsed["products"][item["product_code"]]
            connection.execute(
                """INSERT INTO stock_order_items
                   (order_id, row_no, product_id, product_name, spec, batch_no,
                    location_id, quantity, note, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order_id, item["_row"], item["product_code"],
                    item["product_name"], item.get("spec") or product["spec"],
                    item["batch_no"], item["location_id"], item["quantity"],
                    item.get("note"), now,
                ),
            )

        for key, item in grouped.items():
            product = parsed["products"][key[0]]
            existing = connection.execute(
                """SELECT id FROM inventory
                   WHERE project_id = ? AND product_id = ?
                     AND location_id = ? AND batch_no = ?""",
                (project_id, key[0], key[2], key[1]),
            ).fetchone()
            if existing:
                connection.execute(
                    """UPDATE inventory
                       SET total_quantity = total_quantity + ?, production_date = ?,
                           expiry_date = ?, inbound_date = ?, quality_status = ?,
                           purchase_order_no = ?, last_inbound_at = ?,
                           updated_at = ? WHERE id = ?""",
                    (
                        item["quantity"], item.get("production_date"),
                        item["_expiry"], item.get("order_date"),
                        item["quality_status"], item.get("purchase_order_no"),
                        now, now, existing["id"],
                    ),
                )
            else:
                connection.execute(
                    """INSERT INTO inventory
                       (project_id, product_id, location_id, batch_no,
                        production_date, expiry_date, inbound_date,
                        total_quantity, picking_reserved, moving_reserved,
                        quality_status, prohibit_outbound, purchase_order_no,
                        last_inbound_at, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, 0, ?, ?, ?, ?)""",
                    (
                        project_id, key[0], key[2], key[1],
                        item.get("production_date"), item["_expiry"],
                        item.get("order_date"), item["quantity"],
                        item["quality_status"], item.get("purchase_order_no"),
                        now, now, now,
                    ),
                )
            connection.execute(
                """INSERT INTO stock_logs
                   (project_id, order_no, product_id, batch_no, location_id,
                    change_type, change_quantity, operator_name, created_at)
                   VALUES (?, ?, ?, ?, ?, 'inbound', ?, ?, ?)""",
                (
                    project_id, order_no, key[0], key[1], key[2],
                    item["quantity"], operator["display_name"], now,
                ),
            )
        audit(
            connection, operator, "stock.inbound",
            f"创建入库单 {order_no}", project_id,
        )
        connection.commit()
        result = _order_json(connection, order_id)
        result["aggregated"] = [
            {
                "productCode": key[0],
                "batchNo": key[1],
                "location": _location_json(connection, key[2])["full_name"],
                "quantity": value["quantity"],
            }
            for key, value in grouped.items()
        ]
        return result
    except Exception:
        connection.rollback()
        raise


def import_outbound(connection, operator, project_id, data):
    require_project(connection, operator, project_id)
    parsed = parse_import(connection, project_id, data, "outbound")
    return create_outbound(connection, operator, project_id, parsed)


def import_move(connection, operator, project_id, data):
    require_project(connection, operator, project_id)
    parsed = parse_import(connection, project_id, data, "move")
    return create_move(connection, operator, project_id, parsed)


def create_outbound(connection, operator, project_id, parsed):
    now = iso_now()
    rows = parsed["rows"]
    order_no = _single_order_no(
        {item.get("order_no") for item in rows}, parsed, "配货单号"
    )
    if connection.execute(
        "SELECT 1 FROM stock_orders WHERE order_no = ?", (order_no,)
    ).fetchone():
        raise ValueError("配货单号已存在")
    try:
        connection.execute("BEGIN IMMEDIATE")
        order_date = next(
            (item.get("order_date") for item in rows if item.get("order_date")), None
        )
        recipient = next(
            (item.get("recipient") for item in rows if item.get("recipient")), ""
        )
        note = next(
            (item.get("note") for item in rows if item.get("note")), ""
        )
        cursor = connection.execute(
            """INSERT INTO stock_orders
               (project_id, order_no, order_type, status, operator_id,
                operator_name, template_version, note, order_date, recipient,
                created_at)
               VALUES (?, ?, 'outbound', 'pending_picking', ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id, order_no, operator["id"], operator["display_name"],
                parsed["version"], note, order_date, recipient, now,
            ),
        )
        order_id = cursor.lastrowid
        for item in rows:
            product = parsed["products"][item["product_code"]]
            connection.execute(
                """INSERT INTO stock_order_items
                   (order_id, row_no, product_id, product_name, spec, batch_no,
                    location_id, quantity, note, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order_id, item["_row"], item["product_code"],
                    item["product_name"], item.get("spec") or product["spec"],
                    item.get("batch_no"), item.get("location_id"),
                    item["quantity"], item.get("note"), now,
                ),
            )

        lots_by_product = {}
        for item in rows:
            code = item["product_code"]
            if code in lots_by_product:
                continue
            lots_by_product[code] = connection.execute(
                """SELECT i.*, p.product_name, p.spec
                   FROM inventory i
                   JOIN products p ON p.product_id = i.product_id
                   WHERE i.project_id = ? AND i.product_id = ?
                     AND i.quality_status <> '扣留' AND i.prohibit_outbound = 0
                   ORDER BY i.inbound_date, i.last_inbound_at, i.id""",
                (project_id, code),
            ).fetchall()
        allocated = {}

        def lot_available(lot):
            return (
                lot["total_quantity"] - lot["picking_reserved"]
                - lot["moving_reserved"] - allocated.get(lot["id"], 0)
            )

        errors = []
        allocations = []
        groups = {}
        for item in rows:
            quality = item.get("quality_status") or "良品"
            if quality not in ("良品", "扣留"):
                _error(errors, item["_row"], "品质", "品质必须为良品或扣留")
                continue
            key = (
                item["product_code"], item.get("location_id"),
                item.get("batch_no"), item.get("production_date"), quality,
            )
            groups.setdefault(key, []).append(item)
        for (code, location_id, batch_no, production_date, quality), group_rows in groups.items():
            quantity = sum(item["quantity"] for item in group_rows)
            candidates = [
                lot for lot in lots_by_product[code]
                if lot["quality_status"] == quality
                and (not batch_no or lot["batch_no"] == batch_no)
                and (not production_date
                     or (lot["production_date"] or "") == production_date)
                and (not location_id or lot["location_id"] == location_id)
            ]
            available_total = sum(lot_available(lot) for lot in candidates)
            if available_total < quantity:
                errors.append({
                    "row": group_rows[0]["_row"],
                    "column": "数量",
                    "message": (
                        f"当前可用库存为{available_total}件，"
                        f"出库数量{quantity}件，库存不足无法出库"
                    ),
                })
                continue
            remaining = quantity
            for lot in candidates:
                if remaining <= 0:
                    break
                available = lot_available(lot)
                if available <= 0:
                    continue
                picked = min(remaining, available)
                allocated[lot["id"]] = allocated.get(lot["id"], 0) + picked
                allocations.append({
                    "lot_id": lot["id"],
                    "product_id": code,
                    "product_name": lot["product_name"],
                    "spec": lot["spec"] or "",
                    "batch_no": lot["batch_no"],
                    "production_date": lot["production_date"],
                    "quality_status": lot["quality_status"],
                    "quantity": picked,
                    "location_id": lot["location_id"],
                })
                remaining -= picked
        if errors:
            raise ImportValidationError("Excel校验失败", errors)

        for allocation in allocations:
            connection.execute(
                """UPDATE inventory SET total_quantity = total_quantity - ?,
                   updated_at = ? WHERE id = ?""",
                (allocation["quantity"], now, allocation["lot_id"]),
            )
            connection.execute(
                """INSERT INTO stock_logs
                   (project_id, order_no, product_id, batch_no, location_id,
                    change_type, change_quantity, operator_name, created_at)
                   VALUES (?, ?, ?, ?, ?, 'outbound', ?, ?, ?)""",
                (
                    project_id, order_no, allocation["product_id"],
                    allocation["batch_no"], allocation["location_id"],
                    -allocation["quantity"], operator["display_name"], now,
                ),
            )

        pick_no = _next_number(connection, "pick", "PICK")
        pick_cursor = connection.execute(
            """INSERT INTO picking_orders
               (pick_no, project_id, order_id, status, operator_name, generated_at)
               VALUES (?, ?, ?, 'pending', ?, ?)""",
            (pick_no, project_id, order_id, operator["display_name"], now),
        )
        for sequence, allocation in enumerate(allocations, 1):
            connection.execute(
                """INSERT INTO picking_items
                   (picking_order_id, product_id, product_name, spec, batch_no,
                    production_date, quality_status, quantity, location_id, sequence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pick_cursor.lastrowid, allocation["product_id"],
                    allocation["product_name"], allocation["spec"],
                    allocation["batch_no"], allocation["production_date"],
                    allocation["quality_status"], allocation["quantity"],
                    allocation["location_id"], sequence,
                ),
            )
        audit(
            connection, operator, "stock.outbound",
            f"创建出库单 {order_no} 和拣货单 {pick_no}", project_id,
        )
        connection.commit()
        result = _order_json(connection, order_id)
        result["pickNo"] = pick_no
        result["aggregated"] = [
            {"productCode": code, "quantity": quantity}
            for code, quantity in _row_totals(rows).items()
        ]
        return result
    except Exception:
        connection.rollback()
        raise


def create_move(connection, operator, project_id, parsed):
    """移库：一步生效，源货位扣减、目标货位增加，整单原子。

    事务内按分组顺序「校验+落库」交错执行，同文件链式移库（A→B、B→C）
    可见前序分组的写入；任一行校验失败则整单回滚。
    """
    now = iso_now()
    rows = parsed["rows"]
    order_no = _single_order_no(
        {item.get("order_no") for item in rows}, parsed, "移库单号"
    )
    if connection.execute(
        "SELECT 1 FROM stock_orders WHERE order_no = ?", (order_no,)
    ).fetchone():
        raise ValueError("移库单号已存在")
    grouped = {}
    for item in rows:
        key = (item["product_code"], item["batch_no"],
               item["from_location_id"], item["to_location_id"])
        if key not in grouped:
            grouped[key] = {"quantity": 0, "_row": item["_row"]}
        grouped[key]["quantity"] += item["quantity"]
    lot_fields = (
        ("production_date", "生产日期"),
        ("expiry_date", "到期日期"),
        ("inbound_date", "入库日"),
        ("quality_status", "品质标记"),
        ("purchase_order_no", "采购单号"),
    )
    try:
        connection.execute("BEGIN IMMEDIATE")
        order_date = next(
            (item.get("order_date") for item in rows if item.get("order_date")), None
        )
        note = next(
            (item.get("note") for item in rows if item.get("note")), ""
        )
        cursor = connection.execute(
            """INSERT INTO stock_orders
               (project_id, order_no, order_type, status, operator_id,
                operator_name, template_version, note, order_date, created_at,
                completed_at)
               VALUES (?, ?, 'move', 'completed', ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id, order_no, operator["id"], operator["display_name"],
                parsed["version"], note, order_date, now, now,
            ),
        )
        order_id = cursor.lastrowid
        for item in rows:
            product = parsed["products"][item["product_code"]]
            connection.execute(
                """INSERT INTO stock_order_items
                   (order_id, row_no, product_id, product_name, spec, batch_no,
                    location_id, to_location_id, quantity, note, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order_id, item["_row"], item["product_code"],
                    item["product_name"], item.get("spec") or product["spec"],
                    item["batch_no"], item["from_location_id"],
                    item["to_location_id"], item["quantity"],
                    item.get("note"), now,
                ),
            )

        errors = []
        for key, group in grouped.items():
            code, batch_no, from_id, to_id = key
            row_no = group["_row"]
            quantity = group["quantity"]
            source = connection.execute(
                """SELECT * FROM inventory
                   WHERE project_id = ? AND product_id = ?
                     AND location_id = ? AND batch_no = ?""",
                (project_id, code, from_id, batch_no),
            ).fetchone()
            if not source:
                _error(errors, row_no, "源货位", "源货位无该商品批次的库存，无法移库")
                continue
            available = (
                source["total_quantity"] - source["picking_reserved"]
                - source["moving_reserved"]
            )
            if available < quantity:
                _error(errors, row_no, "数量",
                       f"源货位当前可用库存为{available}件，"
                       f"移库数量{quantity}件，库存不足无法移库")
                continue
            target = connection.execute(
                """SELECT * FROM inventory
                   WHERE project_id = ? AND product_id = ?
                     AND location_id = ? AND batch_no = ?""",
                (project_id, code, to_id, batch_no),
            ).fetchone()
            if target is not None:
                mismatched = next(
                    (label for field, label in lot_fields
                     if (target[field] or None) != (source[field] or None)),
                    None,
                )
                if mismatched:
                    _error(errors, row_no, "目标货位",
                           f"目标货位已存在同一商品、批次的库存，"
                           f"但{mismatched}与源批次信息不一致，无法合并")
                    continue
                connection.execute(
                    """UPDATE inventory SET total_quantity = total_quantity + ?,
                       updated_at = ? WHERE id = ?""",
                    (quantity, now, target["id"]),
                )
            else:
                connection.execute(
                    """INSERT INTO inventory
                       (project_id, product_id, location_id, batch_no,
                        production_date, expiry_date, inbound_date,
                        total_quantity, picking_reserved, moving_reserved,
                        quality_status, prohibit_outbound, purchase_order_no,
                        last_inbound_at, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?)""",
                    (
                        project_id, code, to_id, batch_no,
                        source["production_date"], source["expiry_date"],
                        source["inbound_date"], quantity,
                        source["quality_status"], source["prohibit_outbound"],
                        source["purchase_order_no"], source["last_inbound_at"],
                        now, now,
                    ),
                )
            connection.execute(
                """UPDATE inventory SET total_quantity = total_quantity - ?,
                   updated_at = ? WHERE id = ?""",
                (quantity, now, source["id"]),
            )
            for location_id, change_type, change_quantity in (
                (from_id, "move_out", -quantity),
                (to_id, "move_in", quantity),
            ):
                connection.execute(
                    """INSERT INTO stock_logs
                       (project_id, order_no, product_id, batch_no, location_id,
                        change_type, change_quantity, operator_name, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        project_id, order_no, code, batch_no, location_id,
                        change_type, change_quantity,
                        operator["display_name"], now,
                    ),
                )
        if errors:
            raise ImportValidationError("移库校验失败", errors)
        audit(
            connection, operator, "stock.move",
            f"创建移库单 {order_no}", project_id,
        )
        connection.commit()
        result = _order_json(connection, order_id)
        result["aggregated"] = [
            {
                "productCode": key[0],
                "batchNo": key[1],
                "from": _location_json(connection, key[2])["full_name"],
                "to": _location_json(connection, key[3])["full_name"],
                "quantity": value["quantity"],
            }
            for key, value in grouped.items()
        ]
        return result
    except Exception:
        connection.rollback()
        raise


def list_orders(connection, user, project_id, order_type=None, keyword=None,
                operator_name=None, start_date=None, end_date=None):
    require_project(connection, user, project_id)
    sql = "SELECT * FROM stock_orders WHERE project_id = ?"
    params = [project_id]
    if order_type in ("inbound", "outbound", "history_import", "move"):
        sql += " AND order_type = ?"
        params.append(order_type)
    if keyword:
        sql += " AND (order_no LIKE ? OR operator_name LIKE ?)"
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    if operator_name:
        sql += " AND operator_name LIKE ?"
        params.append(f"%{operator_name}%")
    if start_date:
        sql += " AND date(created_at) >= date(?)"
        params.append(start_date)
    if end_date:
        sql += " AND date(created_at) <= date(?)"
        params.append(end_date)
    sql += " ORDER BY id DESC LIMIT 200"
    return [_order_json(connection, row["id"]) for row in connection.execute(sql, params)]


def get_order(connection, user, project_id, order_id):
    require_project(connection, user, project_id)
    row = connection.execute(
        "SELECT id FROM stock_orders WHERE id = ? AND project_id = ?",
        (order_id, project_id),
    ).fetchone()
    if not row:
        raise ValueError("单据不存在")
    return _order_json(connection, order_id)


def list_inventory(connection, user, project_id, warehouse_name=None,
                   storage_area=None, cargo_area=None, location_name=None,
                   product_code=None, product_name=None, batch_no=None,
                   quality_status=None, prohibit_outbound=None,
                   production_start_date=None, production_end_date=None,
                   expiry_start_date=None, expiry_end_date=None,
                   inbound_start_date=None, inbound_end_date=None,
                   summary=None):
    require_project(connection, user, project_id)
    sql = """SELECT i.*, p.product_name, p.spec, p.unit,
                    l.storage_area, l.cargo_area, l.location_name,
                    w.warehouse_name
             FROM inventory i
             JOIN products p ON p.product_id = i.product_id
             JOIN warehouse_locations l ON l.id = i.location_id
             JOIN warehouses w ON w.id = l.warehouse_id
             WHERE i.project_id = ?"""
    params = [project_id]
    filters = (
        ("w.warehouse_name", warehouse_name, "like"),
        ("l.storage_area", storage_area, "like"),
        ("l.cargo_area", cargo_area, "like"),
        ("l.location_name", location_name, "like"),
        ("i.product_id", product_code, "like"),
        ("p.product_name", product_name, "like"),
        ("i.batch_no", batch_no, "like"),
        ("i.quality_status", quality_status, "exact"),
    )
    for column, value, mode in filters:
        if value:
            sql += f" AND {column} {'= ?' if mode == 'exact' else 'LIKE ?'}"
            params.append(value if mode == "exact" else f"%{value}%")
    if prohibit_outbound in ("0", "1", 0, 1):
        sql += " AND i.prohibit_outbound = ?"
        params.append(int(prohibit_outbound))
    date_filters = (
        ("date(i.production_date)", production_start_date, production_end_date),
        ("date(i.expiry_date)", expiry_start_date, expiry_end_date),
        ("date(i.inbound_date)", inbound_start_date, inbound_end_date),
    )
    for column, start_date, end_date in date_filters:
        if start_date:
            sql += f" AND {column} >= date(?)"
            params.append(start_date)
        if end_date:
            sql += f" AND {column} <= date(?)"
            params.append(end_date)
    sql += " ORDER BY w.warehouse_name, l.storage_area, l.cargo_area, l.location_name, i.product_id LIMIT 1000"
    rows = []
    for row in connection.execute(sql, params):
        item = dict(row)
        item["available_quantity"] = (
            item["total_quantity"] - item["picking_reserved"]
            - item["moving_reserved"]
        )
        item["full_location"] = (
            f"{item['warehouse_name']} / {item['storage_area']} / "
            f"{item['cargo_area']} / {item['location_name']}"
        )
        rows.append(item)
    if summary in ("1", 1, True, "true"):
        project = connection.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        project = dict(project) if project else None
        warehouse_name = ""
        if project and project.get("warehouse_id"):
            warehouse = connection.execute(
                "SELECT warehouse_name FROM warehouses WHERE id = ?",
                (project["warehouse_id"],),
            ).fetchone()
            warehouse_name = warehouse["warehouse_name"] if warehouse else ""
        grouped = {}
        for row in rows:
            key = row["product_id"]
            item = grouped.get(key)
            if not item:
                item = {
                    "product_id": key,
                    "product_name": row["product_name"],
                    "unit": row["unit"] or "",
                    "warehouse_name": warehouse_name,
                    "total_quantity": 0,
                    "picking_reserved": 0,
                    "moving_reserved": 0,
                }
                grouped[key] = item
            item["total_quantity"] += row["total_quantity"]
            item["picking_reserved"] += row["picking_reserved"]
            item["moving_reserved"] += row["moving_reserved"]
        for item in grouped.values():
            item["available_quantity"] = (
                item["total_quantity"] - item["picking_reserved"]
                - item["moving_reserved"]
            )
        return list(grouped.values())
    return rows


def inventory_filter_options(connection, user, project_id):
    require_project(connection, user, project_id)
    records = []
    for row in connection.execute(
        """SELECT DISTINCT w.warehouse_name, l.storage_area,
                          l.cargo_area, l.location_name,
                          p.product_id, p.product_name, p.spec, p.unit,
                          i.batch_no, i.quality_status,
                          i.production_date, i.expiry_date, i.inbound_date
           FROM inventory i
           JOIN products p ON p.product_id = i.product_id
           JOIN warehouse_locations l ON l.id = i.location_id
           JOIN warehouses w ON w.id = l.warehouse_id
          WHERE i.project_id = ?
          ORDER BY w.warehouse_name, l.storage_area, l.cargo_area,
                   l.location_name, p.product_id, i.batch_no""",
        (project_id,),
    ):
        records.append(dict(row))

    def date_bounds(column):
        values = [record[column] for record in records if record[column]]
        return {"min": min(values) if values else None, "max": max(values) if values else None}

    return {
        "records": records,
        "dates": {
            "production_date": date_bounds("production_date"),
            "expiry_date": date_bounds("expiry_date"),
            "inbound_date": date_bounds("inbound_date"),
        },
    }


def inventory_workbook(project_name, rows, summary=False):
    if summary:
        headers = ["项目", "货品编号", "货品名称", "库存总数量", "可用库存",
                   "单位", "所属仓库"]
        values = [
            [
                project_name, row["product_id"], row["product_name"],
                row["total_quantity"], row["available_quantity"],
                row["unit"] or "", row["warehouse_name"] or "",
            ]
            for row in rows
        ]
        return excelio.build_workbook([headers] + values, "库存汇总")
    headers = [
        "项目", "仓库", "库区", "货区", "货位", "货品编号", "货品名称", "规格",
        "单位", "生产批号", "生产日期", "到期日期", "入库日", "库存总数",
        "分拣占用数", "移动占用数", "可用库存", "品质标记", "禁止出库", "采购单号",
    ]
    values = []
    for row in rows:
        values.append([
            project_name, row["warehouse_name"], row["storage_area"],
            row["cargo_area"], row["location_name"], row["product_id"],
            row["product_name"], row["spec"] or "", row["unit"] or "",
            row["batch_no"], row["production_date"] or "", row["expiry_date"] or "",
            row["inbound_date"] or "", row["total_quantity"],
            row["picking_reserved"], row["moving_reserved"],
            row["available_quantity"], row["quality_status"],
            "是" if row["prohibit_outbound"] else "否",
            row["purchase_order_no"] or "",
        ])
    return excelio.build_workbook([headers] + values, "库存查询结果")


def list_stock_logs(connection, user, project_id, product_code=None):
    require_project(connection, user, project_id)
    sql = """SELECT s.*, w.warehouse_name, l.storage_area, l.cargo_area,
                    l.location_name
             FROM stock_logs s
             JOIN warehouse_locations l ON l.id = s.location_id
             JOIN warehouses w ON w.id = l.warehouse_id
             WHERE s.project_id = ?"""
    params = [project_id]
    if product_code:
        sql += " AND s.product_id = ?"
        params.append(product_code)
    sql += " ORDER BY s.id DESC LIMIT 1000"
    return [dict(row) for row in connection.execute(sql, params)]


def list_picking_orders(connection, user, project_id, status=None):
    require_project(connection, user, project_id)
    sql = """SELECT po.*, so.order_no AS outbound_order_no
             FROM picking_orders po
             JOIN stock_orders so ON so.id = po.order_id
             WHERE po.project_id = ?"""
    params = [project_id]
    if status in ("pending", "completed"):
        sql += " AND po.status = ?"
        params.append(status)
    sql += " ORDER BY po.id DESC LIMIT 200"
    return [dict(row) for row in connection.execute(sql, params)]


def get_picking_order(connection, user, project_id, picking_id):
    require_project(connection, user, project_id)
    order = connection.execute(
        """SELECT po.*, so.order_no AS outbound_order_no,
                  so.order_date AS outbound_date, so.recipient AS recipient
           FROM picking_orders po
           JOIN stock_orders so ON so.id = po.order_id
           WHERE po.id = ? AND po.project_id = ?""",
        (picking_id, project_id),
    ).fetchone()
    if not order:
        raise ValueError("拣货单不存在")
    items = connection.execute(
        "SELECT * FROM picking_items WHERE picking_order_id = ? ORDER BY sequence, id",
        (picking_id,),
    ).fetchall()
    result_items = []
    for item in items:
        value = dict(item)
        value["location"] = _location_json(connection, item["location_id"])
        unit = connection.execute(
            "SELECT unit FROM products WHERE product_id = ?",
            (item["product_id"],),
        ).fetchone()
        value["unit"] = unit["unit"] if unit and unit["unit"] else ""
        result_items.append(value)
    return {"order": dict(order), "items": result_items}


def complete_picking(connection, operator, project_id, picking_id):
    require_project(connection, operator, project_id)
    now = iso_now()
    try:
        connection.execute("BEGIN IMMEDIATE")
        picking = connection.execute(
            """SELECT * FROM picking_orders
               WHERE id = ? AND project_id = ?""",
            (picking_id, project_id),
        ).fetchone()
        if not picking:
            raise ValueError("拣货单不存在")
        if picking["status"] != "pending":
            raise ValueError("拣货单已完成")
        connection.execute(
            """UPDATE picking_orders SET status = 'completed', completed_at = ?
             WHERE id = ?""",
            (now, picking_id),
        )
        connection.execute(
            """UPDATE stock_orders SET status = 'completed', completed_at = ?
             WHERE id = ?""",
            (now, picking["order_id"]),
        )
        audit(
            connection, operator, "picking.complete",
            f"拣货单 {picking['pick_no']} 完成", project_id,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return get_picking_order(connection, operator, project_id, picking_id)


def picking_workbook(detail, sort="location"):
    order = detail["order"]
    items = sorted(
        detail["items"],
        key=lambda item: (
            item["location"]["full_name"], item["product_id"], item["batch_no"],
        ) if sort == "location" else (
            item["product_id"], item["location"]["full_name"], item["batch_no"],
        ),
    )
    headers = ["序号", "货品编号", "货品名称", "生产日期", "生产批号", "品质",
               "货位", "分拣总数", "单位"]
    rows = [[
        index, item["product_id"], item["product_name"],
        item["production_date"] or "", item["batch_no"],
        item["quality_status"] or "良品", item["location"]["full_name"],
        item["quantity"], item["unit"] or "",
    ] for index, item in enumerate(items, 1)]
    return excelio.build_workbook(
        [headers] + rows, f"拣货单-{order['pick_no']}"
    )


def list_audit_logs(connection, user, project_id, action=None):
    require_project(connection, user, project_id)
    sql = "SELECT * FROM audit_logs WHERE project_id = ?"
    params = [project_id]
    if action:
        sql += " AND action LIKE ?"
        params.append(f"%{action}%")
    sql += " ORDER BY id DESC LIMIT 1000"
    return [dict(row) for row in connection.execute(sql, params)]


def dashboard(connection, user, project_id):
    require_project(connection, user, project_id)
    return {
        "lots": connection.execute(
            "SELECT COUNT(*) FROM inventory WHERE project_id = ?",
            (project_id,),
        ).fetchone()[0],
        "quantity": connection.execute(
            "SELECT COALESCE(SUM(total_quantity), 0) FROM inventory WHERE project_id = ?",
            (project_id,),
        ).fetchone()[0],
        "pendingPicking": connection.execute(
            """SELECT COUNT(*) FROM picking_orders
               WHERE project_id = ? AND status = 'pending'""",
            (project_id,),
        ).fetchone()[0],
        "todayOrders": connection.execute(
            """SELECT COUNT(*) FROM stock_orders
               WHERE project_id = ? AND date(created_at) = date('now')""",
            (project_id,),
        ).fetchone()[0],
    }


def admin_dashboard(connection):
    return {
        "users": connection.execute(
            """SELECT COUNT(*) FROM users
               WHERE role = 'project_admin' AND status = 'active'"""
        ).fetchone()[0],
        "projects": connection.execute(
            "SELECT COUNT(*) FROM projects WHERE status = 'active'"
        ).fetchone()[0],
        "products": connection.execute(
            "SELECT COUNT(*) FROM products WHERE status = 'active'"
        ).fetchone()[0],
        "warehouses": connection.execute(
            "SELECT COUNT(*) FROM warehouses WHERE status = 'active'"
        ).fetchone()[0],
    }


def _history_text(value):
    value = str(value or "").strip()
    if value in ("-", ""):
        return ""
    if value.startswith("="):
        value = value.lstrip("= \t").strip()
    return value.strip('"').strip()


def _history_date(value):
    text = _history_text(value)
    if not text:
        return None
    parsed = _parse_date(text)
    if parsed is None:
        raise ValueError(f"日期格式无效：{text}")
    return parsed.isoformat()


def _history_int(value, name, allow_zero=False):
    value = _history_text(value)
    if not value:
        return 0
    if not re.fullmatch(r"\d+", value):
        raise ValueError(f"{name}必须为非负整数")
    number = int(value)
    if number == 0 and not allow_zero:
        return 0
    return number


def _decode_history(data):
    if len(data) > MAX_HISTORY_FILE_SIZE:
        raise ValueError("历史库存文件不能超过12MB")
    if data.startswith(b"\xff\xfe") or data[:2400].count(b"\x00") > 20:
        text = data.decode("utf-16")
    else:
        text = data.decode("utf-8-sig")
    sample = io.StringIO(text)
    dialect = csv.Sniffer().sniff(sample.readline(), delimiters="\t,")
    return csv.DictReader(io.StringIO(text), dialect=dialect)


def import_history_csv(connection, operator, project_id, data):
    if operator["role"] != "admin":
        raise Forbidden("仅管理员可以导入历史库存")
    project = connection.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not project or project["status"] != "active":
        raise ValueError("项目不存在或已停用")
    if connection.execute(
        "SELECT COUNT(*) FROM inventory WHERE project_id = ?", (project_id,)
    ).fetchone()[0]:
        raise ValueError("该项目已有库存，历史库存仅可初始化导入一次")

    reader = _decode_history(data)
    headers = set(reader.fieldnames or [])
    missing = {
        "货区", "货位", "货品编号", "货品名称", "生产批号", "生产日期",
        "到期日期", "库存总数", "分拣占用数", "移动占用数", "品质标记",
        "入库日", "禁止出库", "标准单位数", "标准单位", "仓库",
    } - headers
    if missing:
        raise ValueError(f"历史库存缺少字段：{'、'.join(sorted(missing))}")
    unknown = headers - HISTORY_HEADERS
    if unknown:
        raise ValueError(f"历史库存包含不支持字段：{'、'.join(sorted(unknown))}")

    grouped = {}
    row_number = 1
    now = iso_now()
    for raw in reader:
        row_number += 1
        code = _history_text(raw.get("货品编号"))
        name = _history_text(raw.get("货品名称"))
        batch = _history_text(raw.get("生产批号"))
        if not code or not name or not batch:
            raise ValueError(f"第{row_number}行货品编号、货品名称、生产批号不能为空")
        total = _history_int(raw.get("库存总数"), "库存总数")
        if total <= 0:
            continue
        picking = _history_int(raw.get("分拣占用数"), "分拣占用数")
        moving = _history_int(raw.get("移动占用数"), "移动占用数")
        if picking + moving > total:
            raise ValueError(f"第{row_number}行占用数量不能超过库存总数")
        prohibit = _history_text(raw.get("禁止出库"))
        prohibit_outbound = 1 if prohibit in ("是", "TRUE", "True", "1", "true") else 0
        key = (
            code, _history_text(raw.get("仓库")), _history_text(raw.get("库区")),
            _history_text(raw.get("货区")), _history_text(raw.get("货位")), batch,
        )
        item = {
            "row": row_number,
            "product_name": name,
            "unit": _history_text(raw.get("标准单位")),
            "production_date": _history_date(raw.get("生产日期")),
            "expiry_date": _history_date(raw.get("到期日期")),
            "inbound_date": _history_date(raw.get("入库日")),
            "total_quantity": total,
            "picking_reserved": picking,
            "moving_reserved": moving,
            "quality_status": _history_text(raw.get("品质标记")) or "良品",
            "prohibit_outbound": prohibit_outbound,
            "purchase_order_no": _history_text(raw.get("采购单号")),
        }
        if key in grouped:
            previous = grouped[key]
            previous["total_quantity"] += total
            previous["picking_reserved"] += picking
            previous["moving_reserved"] += moving
        else:
            grouped[key] = item
    if not grouped:
        raise ValueError("历史库存文件没有有效数据")
    if len(grouped) > MAX_HISTORY_ROWS:
        raise ValueError(f"历史库存不能超过{MAX_HISTORY_ROWS}行")

    try:
        connection.execute("BEGIN IMMEDIATE")
        location_ids = {}
        for key in grouped:
            code, warehouse_name, storage_area, cargo_area, location_name, batch = key
            item = grouped[key]
            product = connection.execute(
                "SELECT * FROM products WHERE product_id = ?", (code,)
            ).fetchone()
            if product and product["product_name"] != item["product_name"]:
                raise ValueError(f"货品编号 {code} 的名称与商品档案不一致")
            if product and product["project_id"] != project_id:
                raise ValueError(f"货品编号 {code} 已属于其他项目")
            if not product:
                connection.execute(
                    """INSERT INTO products
                       (product_id, product_name, project_id, spec, unit, status,
                        created_at, updated_at)
                       VALUES (?, ?, ?, NULL, ?, 'active', ?, ?)""",
                    (code, item["product_name"], project_id,
                     item["unit"] or None, now, now),
                )
            warehouse = connection.execute(
                "SELECT * FROM warehouses WHERE warehouse_name = ?", (warehouse_name,)
            ).fetchone()
            if not warehouse:
                cursor = connection.execute(
                    """INSERT INTO warehouses
                       (warehouse_name, status, created_at, updated_at)
                       VALUES (?, 'active', ?, ?)""",
                    (warehouse_name, now, now),
                )
                warehouse_id = cursor.lastrowid
            else:
                warehouse_id = warehouse["id"]
            location = connection.execute(
                """SELECT id FROM warehouse_locations
                   WHERE warehouse_id = ? AND storage_area = ?
                     AND cargo_area = ? AND location_name = ?""",
                (warehouse_id, storage_area, cargo_area, location_name),
            ).fetchone()
            if not location:
                _ensure_area_entities(connection, warehouse_id, storage_area,
                                      cargo_area, now)
                cursor = connection.execute(
                    """INSERT INTO warehouse_locations
                       (warehouse_id, storage_area, cargo_area, location_name,
                        status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'active', ?, ?)""",
                    (warehouse_id, storage_area, cargo_area, location_name,
                     now, now),
                )
                location_id = cursor.lastrowid
            else:
                location_id = location["id"]
            location_ids[key] = location_id

        order_no = _next_number(connection, "order", "LS")
        cursor = connection.execute(
            """INSERT INTO stock_orders
               (project_id, order_no, order_type, status, operator_id,
                operator_name, template_version, note, created_at, completed_at)
               VALUES (?, ?, 'history_import', 'completed', ?, ?, 0, ?, ?, ?)""",
            (
                project_id, order_no, operator["id"], operator["display_name"],
                "历史库存初始化导入", now, now,
            ),
        )
        for key, item in grouped.items():
            connection.execute(
                """INSERT INTO stock_order_items
                   (order_id, row_no, product_id, product_name, spec, batch_no,
                    location_id, quantity, note, created_at)
                   VALUES (?, ?, ?, ?, NULL, ?, ?, ?, NULL, ?)""",
                (
                    cursor.lastrowid, item["row"], key[0],
                    item["product_name"], key[5], location_ids[key],
                    item["total_quantity"], now,
                ),
            )
            inbound_time = (
                item["inbound_date"] + " 00:00:00"
                if item["inbound_date"] else now
            )
            connection.execute(
                """INSERT INTO inventory
                   (project_id, product_id, location_id, batch_no,
                    production_date, expiry_date, inbound_date, total_quantity,
                    picking_reserved, moving_reserved, quality_status,
                    prohibit_outbound, purchase_order_no, last_inbound_at,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id, key[0], location_ids[key], key[5],
                    item["production_date"], item["expiry_date"],
                    item["inbound_date"], item["total_quantity"],
                    item["picking_reserved"], item["moving_reserved"],
                    item["quality_status"], item["prohibit_outbound"],
                    item["purchase_order_no"] or None, inbound_time, now, now,
                ),
            )
            connection.execute(
                """INSERT INTO stock_logs
                   (project_id, order_no, product_id, batch_no, location_id,
                    change_type, change_quantity, operator_name, created_at)
                   VALUES (?, ?, ?, ?, ?, 'history_import', ?, ?, ?)""",
                (
                    project_id, order_no, key[0], key[5], location_ids[key],
                    item["total_quantity"], operator["display_name"], now,
                ),
            )
        audit(
            connection, operator, "inventory.history_import",
            f"导入历史库存 {order_no}，共{len(grouped)}个批次", project_id,
        )
        connection.commit()
        return {"orderNo": order_no, "lots": len(grouped)}
    except Exception:
        connection.rollback()
        raise
