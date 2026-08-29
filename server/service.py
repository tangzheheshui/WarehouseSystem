import csv
import io
import json
import re
from datetime import datetime, timedelta, timezone

import excelio
from auth import valid_phone, valid_username
from db import columns_for
from security import hash_password, iso_now, valid_password


FIELD_TYPES = {
    "product_code": "text",
    "product_name": "text",
    "spec": "text",
    "unit": "text",
    "batch_no": "text",
    "production_date": "date",
    "shelf_life_days": "integer",
    "inbound_date": "date",
    "quantity": "integer",
    "warehouse_name": "text",
    "storage_area": "text",
    "cargo_area": "text",
    "location_name": "text",
    "quality_status": "text",
    "purchase_order_no": "text",
    "note": "text",
}
REQUIRED_FIELDS = {
    "inbound": {
        "product_code", "product_name", "batch_no", "production_date",
        "shelf_life_days", "inbound_date", "quantity", "warehouse_name",
        "storage_area", "cargo_area", "location_name", "quality_status",
    },
    "outbound": {"product_code", "product_name", "quantity"},
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


def list_products(connection, keyword=None, status=None):
    sql = "SELECT * FROM products WHERE 1=1"
    params = []
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
                   spec=None, unit=None):
    product_code = _required_text(product_code, "货品编号", 50)
    product_name = _required_text(product_name, "货品名称")
    spec = str(spec or "").strip() or None
    unit = str(unit or "").strip() or None
    if spec and len(spec) > 100:
        raise ValueError("规格不能超过100个字符")
    if unit and len(unit) > 20:
        raise ValueError("标准单位不能超过20个字符")
    if connection.execute(
        "SELECT 1 FROM products WHERE product_id = ?", (product_code,)
    ).fetchone():
        raise ValueError("货品编号已存在")
    now = iso_now()
    connection.execute(
        """INSERT INTO products(product_id, product_name, spec, unit, status,
                                created_at, updated_at)
           VALUES (?, ?, ?, ?, 'active', ?, ?)""",
        (product_code, product_name, spec, unit, now, now),
    )
    audit(connection, operator, "product.create", f"创建商品 {product_code} {product_name}")
    connection.commit()
    row = connection.execute(
        "SELECT * FROM products WHERE product_id = ?", (product_code,)
    ).fetchone()
    return dict(row)


def update_product(connection, operator, product_code, product_name=None,
                   spec=None, unit=None, status=None):
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
        "status": status if status is not None else row["status"],
    }
    if values["status"] not in ("active", "disabled"):
        raise ValueError("商品状态无效")
    connection.execute(
        """UPDATE products SET product_name = ?, spec = ?, unit = ?, status = ?,
           updated_at = ? WHERE product_id = ?""",
        (
            values["product_name"], values["spec"], values["unit"],
            values["status"], iso_now(), product_code,
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
            """SELECT * FROM warehouse_locations
               WHERE warehouse_id = ?
               ORDER BY storage_area, cargo_area, location_name""",
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
    return [dict(row) for row in connection.execute(
        """SELECT p.*,
                  (SELECT COUNT(*) FROM user_projects up
                    JOIN users u ON u.id = up.user_id
                    WHERE up.project_id = p.id AND u.role = 'project_admin'
                      AND u.status = 'active') AS user_count,
                  (SELECT COUNT(*) FROM inventory i WHERE i.project_id = p.id) AS lot_count
           FROM projects p ORDER BY p.id"""
    )]


def create_project(connection, operator, project_name, remark=None):
    project_name = _required_text(project_name, "项目名称")
    if connection.execute(
        "SELECT 1 FROM projects WHERE project_name = ?", (project_name,)
    ).fetchone():
        raise ValueError("项目名称已存在")
    now = iso_now()
    cursor = connection.execute(
        """INSERT INTO projects(project_name, status, remark, created_at, updated_at)
           VALUES (?, 'active', ?, ?, ?)""",
        (project_name, str(remark or "").strip() or None, now, now),
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
                   remark=None, status=None):
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
    note = str(remark or "").strip() or None if remark is not None else row["remark"]
    connection.execute(
        """UPDATE projects SET project_name = ?, status = ?, remark = ?,
           updated_at = ? WHERE id = ?""",
        (name, state, note, iso_now(), project_id),
    )
    audit(connection, operator, "project.update", f"修改项目 {name}", project_id)
    connection.commit()
    return dict(connection.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone())


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
    return {
        "type": order_type,
        "version": row["version"],
        "status": row["status"],
        "columns": json.loads(row["columns_json"]),
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
        "product_code": "H001",
        "product_name": "示例商品",
        "spec": "100g",
        "unit": "个",
        "batch_no": "B20260827-01",
        "production_date": "2026-08-01",
        "shelf_life_days": 180,
        "inbound_date": "2026-08-27",
        "quantity": 10,
        "warehouse_name": "主仓库",
        "storage_area": "A库区",
        "cargo_area": "A货区",
        "location_name": "A-01-01",
        "quality_status": "良品",
        "purchase_order_no": "PO001",
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
    try:
        parsed = datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None
    if parsed.isoformat() != str(value).strip():
        return None
    return parsed


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
                           "日期格式必须为yyyy-MM-dd")
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

    products = {}
    for item in parsed_rows:
        code = item.get("product_code")
        name = item.get("product_name")
        if not code or not name:
            continue
        product = connection.execute(
            "SELECT * FROM products WHERE product_id = ?", (code,)
        ).fetchone()
        if not product:
            _error(errors, item["_row"], "货品编号", f"货品编号未建档：{code}")
            continue
        if product["status"] != "active":
            _error(errors, item["_row"], "货品编号", f"货品已停用：{code}")
        if product["product_name"] != name:
            _error(errors, item["_row"], "货品名称", "货品名称与档案不一致")
        for field, archive_field in (("spec", "spec"), ("unit", "unit")):
            value = item.get(field)
            if value and product[archive_field] and product[archive_field] != value:
                _error(errors, item["_row"], columns_by_name[
                    next(column["name"] for column in columns if column["key"] == field)
                ], "与商品档案不一致")
        if code not in products:
            products[code] = dict(product)

    if order_type == "inbound":
        for item in parsed_rows:
            location = connection.execute(
                """SELECT l.id, l.status, w.status AS warehouse_status
                   FROM warehouse_locations l
                   JOIN warehouses w ON w.id = l.warehouse_id
                   WHERE w.warehouse_name = ? AND l.storage_area = ?
                     AND l.cargo_area = ? AND l.location_name = ?""",
                (
                    item.get("warehouse_name"), item.get("storage_area"),
                    item.get("cargo_area"), item.get("location_name"),
                ),
            ).fetchone()
            if not location:
                _error(errors, item["_row"], "货位", "仓库/库区/货区/货位不存在")
            elif location["status"] != "active" or location["warehouse_status"] != "active":
                _error(errors, item["_row"], "货位", "仓库或货位已停用")
            if item.get("quality_status") not in (None, "良品", "扣留"):
                _error(errors, item["_row"], "品质标记", "品质标记必须为良品或扣留")

    if errors:
        raise ImportValidationError("Excel校验失败", errors)
    return {"version": version, "rows": parsed_rows, "products": products}


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
        "type": order["order_type"],
        "status": order["status"],
        "operatorName": order["operator_name"],
        "templateVersion": order["template_version"],
        "note": order["note"],
        "createdAt": order["created_at"],
        "completedAt": order["completed_at"],
        "items": [],
        "pickingOrder": dict(picking) if picking else None,
        "pickingItems": [],
    }
    for item in items:
        value = dict(item)
        value["location"] = _location_json(connection, item["location_id"])
        result["items"].append(value)
    for item in picking_items:
        value = dict(item)
        value["location"] = _location_json(connection, item["location_id"])
        result["pickingItems"].append(value)
    return result


def import_inbound(connection, operator, project_id, data):
    require_project(connection, operator, project_id)
    parsed = parse_import(connection, project_id, data, "inbound")
    now = iso_now()
    grouped = {}
    try:
        connection.execute("BEGIN IMMEDIATE")
        for item in parsed["rows"]:
            key = (item["product_code"], item["batch_no"],
                   item["warehouse_name"], item["storage_area"],
                   item["cargo_area"], item["location_name"])
            stable_fields = (
                "production_date", "shelf_life_days", "inbound_date",
                "quality_status", "purchase_order_no",
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

        order_no = _next_number(connection, "order", "RK")
        note = next(
            (item.get("note") for item in parsed["rows"] if item.get("note")), ""
        )
        cursor = connection.execute(
            """INSERT INTO stock_orders
               (project_id, order_no, order_type, status, operator_id,
                operator_name, template_version, note, created_at, completed_at)
               VALUES (?, ?, 'inbound', 'completed', ?, ?, ?, ?, ?, ?)""",
            (
                project_id, order_no, operator["id"], operator["display_name"],
                parsed["version"], note, now, now,
            ),
        )
        order_id = cursor.lastrowid
        for item in parsed["rows"]:
            location = connection.execute(
                """SELECT l.id FROM warehouse_locations l
                   JOIN warehouses w ON w.id = l.warehouse_id
                   WHERE w.warehouse_name = ? AND l.storage_area = ?
                     AND l.cargo_area = ? AND l.location_name = ?""",
                (
                    item["warehouse_name"], item["storage_area"],
                    item["cargo_area"], item["location_name"],
                ),
            ).fetchone()
            product = parsed["products"][item["product_code"]]
            connection.execute(
                """INSERT INTO stock_order_items
                   (order_id, row_no, product_id, product_name, spec, batch_no,
                    location_id, quantity, note, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order_id, item["_row"], item["product_code"],
                    item["product_name"], item.get("spec") or product["spec"],
                    item["batch_no"], location["id"], item["quantity"],
                    item.get("note"), now,
                ),
            )

        for key, item in grouped.items():
            location = connection.execute(
                """SELECT l.id FROM warehouse_locations l
                   JOIN warehouses w ON w.id = l.warehouse_id
                   WHERE w.warehouse_name = ? AND l.storage_area = ?
                     AND l.cargo_area = ? AND l.location_name = ?""",
                key[2:],
            ).fetchone()
            expiry_date = None
            if item.get("production_date") and item.get("shelf_life_days"):
                expiry_date = (
                    datetime.fromisoformat(item["production_date"])
                    + timedelta(days=item["shelf_life_days"])
                ).date().isoformat()
            existing = connection.execute(
                """SELECT id FROM inventory
                   WHERE project_id = ? AND product_id = ?
                     AND location_id = ? AND batch_no = ?""",
                (project_id, key[0], location["id"], key[1]),
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
                        expiry_date, item.get("inbound_date"),
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
                        project_id, key[0], location["id"], key[1],
                        item.get("production_date"), expiry_date,
                        item.get("inbound_date"), item["quantity"],
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
                    project_id, order_no, key[0], key[1], location["id"],
                    item["quantity"], operator["display_name"], now,
                ),
            )
        audit(connection, operator, "stock.inbound", f"创建入库单 {order_no}", project_id)
        connection.commit()
        result = _order_json(connection, order_id)
        result["aggregated"] = [
            {
                "productCode": key[0],
                "batchNo": key[1],
                "location": f"{key[2]} / {key[3]} / {key[4]} / {key[5]}",
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
    now = iso_now()
    totals = {}
    first_rows = {}
    for item in parsed["rows"]:
        code = item["product_code"]
        totals[code] = totals.get(code, 0) + item["quantity"]
        first_rows.setdefault(code, item)
    try:
        connection.execute("BEGIN IMMEDIATE")
        errors = []
        for code, quantity in totals.items():
            row = first_rows[code]
            available = connection.execute(
                """SELECT COALESCE(SUM(total_quantity - picking_reserved
                                          - moving_reserved), 0) AS available
                   FROM inventory
                   WHERE project_id = ? AND product_id = ?
                     AND quality_status <> '扣留' AND prohibit_outbound = 0""",
                (project_id, code),
            ).fetchone()["available"]
            if available < quantity:
                errors.append({
                    "row": row["_row"],
                    "column": "标准单位数",
                    "message": f"当前可用库存为{available}件，出库数量{quantity}件，库存不足无法出库",
                })
        if errors:
            raise ImportValidationError("Excel校验失败", errors)

        order_no = _next_number(connection, "order", "CK")
        pick_no = _next_number(connection, "pick", "PICK")
        note = next(
            (item.get("note") for item in parsed["rows"] if item.get("note")), ""
        )
        cursor = connection.execute(
            """INSERT INTO stock_orders
               (project_id, order_no, order_type, status, operator_id,
                operator_name, template_version, note, created_at)
               VALUES (?, ?, 'outbound', 'pending_picking', ?, ?, ?, ?, ?)""",
            (
                project_id, order_no, operator["id"], operator["display_name"],
                parsed["version"], note, now,
            ),
        )
        order_id = cursor.lastrowid
        for item in parsed["rows"]:
            product = parsed["products"][item["product_code"]]
            connection.execute(
                """INSERT INTO stock_order_items
                   (order_id, row_no, product_id, product_name, spec, batch_no,
                    location_id, quantity, note, created_at)
                   VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)""",
                (
                    order_id, item["_row"], item["product_code"],
                    item["product_name"], item.get("spec") or product["spec"],
                    item["quantity"], item.get("note"), now,
                ),
            )

        allocations = []
        for code, requested_quantity in totals.items():
            remaining = requested_quantity
            lots = connection.execute(
                """SELECT i.*, p.product_name, p.spec
                   FROM inventory i
                   JOIN products p ON p.product_id = i.product_id
                   WHERE i.project_id = ? AND i.product_id = ?
                     AND i.total_quantity > i.picking_reserved + i.moving_reserved
                     AND i.quality_status <> '扣留' AND i.prohibit_outbound = 0
                   ORDER BY i.inbound_date, i.last_inbound_at, i.id""",
                (project_id, code),
            ).fetchall()
            for lot in lots:
                if remaining <= 0:
                    break
                available = (
                    lot["total_quantity"] - lot["picking_reserved"]
                    - lot["moving_reserved"]
                )
                picked = min(remaining, available)
                connection.execute(
                    """UPDATE inventory SET total_quantity = total_quantity - ?,
                       updated_at = ? WHERE id = ?""",
                    (picked, now, lot["id"]),
                )
                connection.execute(
                    """INSERT INTO stock_logs
                       (project_id, order_no, product_id, batch_no, location_id,
                        change_type, change_quantity, operator_name, created_at)
                       VALUES (?, ?, ?, ?, ?, 'outbound', ?, ?, ?)""",
                    (
                        project_id, order_no, code, lot["batch_no"],
                        lot["location_id"], -picked, operator["display_name"], now,
                    ),
                )
                allocations.append({
                    "product_id": code,
                    "product_name": lot["product_name"],
                    "spec": lot["spec"],
                    "batch_no": lot["batch_no"],
                    "quantity": picked,
                    "location_id": lot["location_id"],
                })
                remaining -= picked
            if remaining:
                raise ValueError("库存已发生变化，请重新上传")

        pick_cursor = connection.execute(
            """INSERT INTO picking_orders
               (pick_no, project_id, order_id, status, operator_name, generated_at)
               VALUES (?, ?, ?, 'pending', ?, ?)""",
            (pick_no, project_id, order_id, operator["display_name"], now),
        )
        allocations.sort(key=lambda item: (
            item["location_id"], item["product_id"], item["batch_no"]
        ))
        for sequence, allocation in enumerate(allocations, 1):
            connection.execute(
                """INSERT INTO picking_items
                   (picking_order_id, product_id, product_name, spec, batch_no,
                    quantity, location_id, sequence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pick_cursor.lastrowid, allocation["product_id"],
                    allocation["product_name"], allocation["spec"],
                    allocation["batch_no"], allocation["quantity"],
                    allocation["location_id"], sequence,
                ),
            )
        audit(
            connection, operator, "stock.outbound",
            f"创建出库单 {order_no} 和拣货单 {pick_no}", project_id,
        )
        connection.commit()
        result = _order_json(connection, order_id)
        result["aggregated"] = [
            {"productCode": code, "quantity": quantity}
            for code, quantity in totals.items()
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
    if order_type in ("inbound", "outbound", "history_import"):
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
                   quality_status=None, prohibit_outbound=None):
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
    return rows


def inventory_workbook(project_name, rows):
    headers = [
        "项目", "仓库", "库区", "货区", "货位", "货品编号", "货品名称", "规格",
        "标准单位", "生产批号", "生产日期", "到期日期", "入库日", "库存总数",
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
        """SELECT po.*, so.order_no AS outbound_order_no
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


def picking_workbook(detail):
    order = detail["order"]
    headers = ["库位", "货品编号", "货品名称", "规格", "生产批号", "标准单位数"]
    rows = [[
        item["location"]["full_name"], item["product_id"], item["product_name"],
        item["spec"] or "", item["batch_no"], item["quantity"],
    ] for item in detail["items"]]
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
    value = _history_text(value)
    if not value:
        return None
    for pattern in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value[:10], pattern).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"日期格式无效：{value}")


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
            if not product:
                connection.execute(
                    """INSERT INTO products
                       (product_id, product_name, spec, unit, status,
                        created_at, updated_at)
                       VALUES (?, ?, NULL, ?, 'active', ?, ?)""",
                    (code, item["product_name"], item["unit"] or None, now, now),
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
