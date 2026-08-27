import json
from datetime import datetime, timedelta, timezone

import excelio
from auth import public_user, valid_phone
from db import columns_for
from security import hash_password, iso_in, iso_now, valid_password


FIELD_TYPES = {
    "product_id": "text",
    "product_name": "text",
    "spec": "text",
    "batch_no": "text",
    "production_date": "date",
    "shelf_life_days": "integer",
    "quantity": "integer",
    "location": "text",
    "note": "text",
}
REQUIRED_FIELDS = {
    "inbound": {"product_id", "product_name", "batch_no", "quantity", "location"},
    "outbound": {"product_id", "product_name", "quantity"},
}
MAX_IMPORT_ROWS = 500
MAX_FILE_SIZE = 5 * 1024 * 1024


class ImportValidationError(ValueError):
    def __init__(self, message, errors):
        super().__init__(message)
        self.errors = errors


def audit(connection, user, action, detail=""):
    user_name = "系统"
    if user:
        user_name = user["operator_name"] if "operator_name" in user.keys() else user["phone"]
    connection.execute(
        """INSERT INTO audit_logs(user_id, user_name, action, detail, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (user["id"] if user else None, user_name, action, detail, iso_now()),
    )


def validate_template_columns(order_type, columns):
    if not isinstance(columns, list) or not columns or len(columns) > 20:
        raise ValueError("模板列配置必须为1-20列的数组")
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
            raise ValueError(f"字段 {name} 为必填字段")
        keys.append(key)
        names.append(name)
    if len(set(keys)) != len(keys) or len(set(names)) != len(names):
        raise ValueError("字段或列名不能重复")
    if not required_keys.issubset(set(keys)):
        raise ValueError("模板缺少系统必需字段")
    normalized = []
    for index, column in enumerate(columns):
        normalized.append(
            {
                "key": keys[index],
                "name": names[index],
                "required": bool(column["required"]),
                "type": column["type"],
            }
        )
    return normalized


def update_template(connection, order_type, columns):
    normalized = validate_template_columns(order_type, columns)
    current = connection.execute(
        "SELECT version FROM templates WHERE template_type = ?", (order_type,)
    ).fetchone()
    if not current:
        raise ValueError("模板不存在")
    connection.execute(
        """UPDATE templates SET version = version + 1, columns_json = ?,
                       status = 'active',
           updated_at = ? WHERE template_type = ?""",
        (json.dumps(normalized, ensure_ascii=False), iso_now(), order_type),
    )
    connection.commit()
    return get_template(connection, order_type)


def get_template(connection, order_type):
    row = connection.execute(
        "SELECT * FROM templates WHERE template_type = ?", (order_type,)
    ).fetchone()
    if not row:
        raise ValueError("模板不存在")
    return {
        "type": order_type,
        "version": row["version"],
        "status": row["status"],
        "columns": json.loads(row["columns_json"]),
    }


def set_template_status(connection, order_type, status):
    if status not in ("active", "disabled"):
        raise ValueError("模板状态无效")
    row = connection.execute(
        "SELECT template_type FROM templates WHERE template_type = ?", (order_type,)
    ).fetchone()
    if not row:
        raise ValueError("模板不存在")
    connection.execute(
        "UPDATE templates SET status = ?, updated_at = ? WHERE template_type = ?",
        (status, iso_now(), order_type),
    )
    connection.commit()
    return get_template(connection, order_type)


def template_workbook(connection, order_type):
    template = get_template(connection, order_type)
    if template["status"] != "active":
        raise ValueError("模板已停用")
    version = template["version"]
    columns = template["columns"]
    headers = [column["name"] for column in columns]
    examples = {
        "product_id": "示例商品ID",
        "product_name": "示例商品名称",
        "spec": "100g",
        "batch_no": "B20260827-01",
        "production_date": "2026-08-01",
        "shelf_life_days": 180,
        "quantity": 10,
        "location": "A-01-01",
        "note": "导入时本行自动忽略",
    }
    rows = [headers, [examples.get(column["key"], "") for column in columns]]
    return excelio.build_workbook(rows, f"{order_type}-v{version}")


def create_user(connection, operator, phone, role, password):
    if role not in ("admin", "worker"):
        raise ValueError("角色无效")
    if not valid_phone(phone):
        raise ValueError("手机号格式错误")
    if not valid_password(password):
        raise ValueError("密码须为8-20位，且同时包含字母和数字")
    if connection.execute("SELECT 1 FROM users WHERE phone = ?", (phone,)).fetchone():
        raise ValueError("手机号已存在")
    now = iso_now()
    cursor = connection.execute(
        """INSERT INTO users
           (phone, password_hash, role, status, must_change_password,
            created_at, updated_at)
           VALUES (?, ?, ?, 'active', 1, ?, ?)""",
        (phone, hash_password(password), role, now, now),
    )
    audit(connection, operator, "user.create", f"创建用户 {phone}")
    connection.commit()
    row = connection.execute(
        "SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()
    return public_user(row)


def reset_user_password(connection, operator, user_id, password):
    if not valid_password(password):
        raise ValueError("密码须为8-20位，且同时包含字母和数字")
    row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        raise ValueError("用户不存在")
    connection.execute(
        """UPDATE users SET password_hash = ?, must_change_password = 1,
           failed_attempts = 0, locked_until = NULL, updated_at = ?
           WHERE id = ?""",
        (hash_password(password), iso_now(), user_id),
    )
    connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    audit(
        connection, operator, "user.reset_password", f"重置用户 {row['phone']} 密码"
    )
    connection.commit()


def set_user_status(connection, operator, user_id, status):
    if status not in ("active", "disabled"):
        raise ValueError("状态无效")
    row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        raise ValueError("用户不存在")
    if operator["id"] == user_id and status == "disabled":
        raise ValueError("不能禁用当前登录账号")
    connection.execute(
        "UPDATE users SET status = ?, updated_at = ? WHERE id = ?",
        (status, iso_now(), user_id),
    )
    if status == "disabled":
        connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    audit(
        connection,
        operator,
        "user.status",
        f"用户 {row['phone']} 状态变更为 {status}",
    )
    connection.commit()


def _error(errors, row, column, message):
    errors.append({"row": row, "column": column, "message": message})


def parse_import(connection, data, order_type):
    if len(data) > MAX_FILE_SIZE:
        raise ValueError("文件不能超过5MB")
    if get_template(connection, order_type)["status"] != "active":
        raise ValueError("模板已停用")
    version, columns = columns_for(connection, order_type)
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
            _error(
                errors,
                header_number,
                excelio.column_letter(position),
                f"未配置的列：{name}",
            )
    if len(headers) != len(set(headers)):
        _error(errors, header_number, "表头", "列名不能重复")

    index_by_key = {}
    for key_column in columns:
        if key_column["name"] in headers:
            index_by_key[key_column["key"]] = headers.index(key_column["name"])

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
                if not raw.isdigit() or int(raw) <= 0:
                    _error(errors, excel_row, column["name"], f"{column['name']}必须为正整数")
                    item[key] = None
                else:
                    item[key] = int(raw)
            elif column["type"] == "date":
                try:
                    parsed_date = datetime.strptime(raw, "%Y-%m-%d").date()
                    if parsed_date.isoformat() != raw:
                        raise ValueError
                    item[key] = raw
                except ValueError:
                    _error(errors, excel_row, column["name"], "日期格式必须为yyyy-MM-dd")
                    item[key] = None
            else:
                if len(raw) > 100:
                    _error(errors, excel_row, column["name"], f"{column['name']}不能超过100个字符")
                item[key] = raw
        parsed_rows.append(item)

    if not parsed_rows and not errors:
        raise ValueError("导入文件没有数据行")
    if len(parsed_rows) > MAX_IMPORT_ROWS:
        _error(errors, parsed_rows[MAX_IMPORT_ROWS]["_row"], "行数", "单次导入不能超过500行")

    product_rows = {}
    for item in parsed_rows:
        product_id = item.get("product_id")
        product_name = item.get("product_name")
        if not product_id or not product_name:
            continue
        spec = item.get("spec")
        known = product_rows.get(product_id)
        if known and (known["product_name"] != product_name or known.get("spec") != spec):
            _error(
                errors,
                item["_row"],
                "商品ID",
                f"同一商品ID {product_id} 的名称或规格不一致",
            )
        elif not known:
            product_rows[product_id] = {
                "product_name": product_name,
                "spec": spec,
            }

        product = connection.execute(
            "SELECT * FROM products WHERE product_id = ?", (product_id,)
        ).fetchone()
        if product:
            if product["product_name"] != product_name:
                _error(errors, item["_row"], "商品名称", "商品名称与档案不一致")
            if spec and product["spec"] and product["spec"] != spec:
                _error(errors, item["_row"], "规格", "商品规格与档案不一致")
        elif order_type == "outbound":
            _error(errors, item["_row"], "商品ID", f"商品ID不存在：{product_id}")

    if errors:
        raise ImportValidationError("Excel校验失败", errors)
    return {
        "version": version,
        "rows": parsed_rows,
        "products": product_rows,
    }


def _next_number(connection, order_type):
    prefixes = {"inbound": "RK", "outbound": "CK"}
    picks = {"inbound": None, "outbound": "PICK"}
    now = datetime.now(timezone(timedelta(hours=8)))
    day = now.strftime("%Y%m%d")
    prefix = prefixes[order_type]
    like = f"{prefix}-{day}-%"
    rows = connection.execute(
        "SELECT order_no FROM stock_orders WHERE order_no LIKE ?", (like,)
    ).fetchall()
    serial = max((int(row["order_no"].rsplit("-", 1)[1]) for row in rows), default=0) + 1
    number = f"{prefix}-{day}-{serial:04d}"
    if picks[order_type]:
        pick_rows = connection.execute(
            "SELECT pick_no FROM picking_orders WHERE pick_no LIKE ?",
            (f"{picks[order_type]}-{day}-%",),
        ).fetchall()
        pick_serial = max(
            (int(row["pick_no"].rsplit("-", 1)[1]) for row in pick_rows),
            default=0,
        ) + 1
        return number, f"{picks[order_type]}-{day}-{pick_serial:04d}"
    return number, None


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
            "SELECT * FROM picking_items WHERE picking_order_id = ? ORDER BY sequence, id",
            (picking["id"],),
        ).fetchall()
    return {
        "id": order["id"],
        "orderNo": order["order_no"],
        "type": order["order_type"],
        "status": order["status"],
        "operatorName": order["operator_name"],
        "templateVersion": order["template_version"],
        "note": order["note"],
        "createdAt": order["created_at"],
        "completedAt": order["completed_at"],
        "items": [dict(item) for item in items],
        "pickingOrder": dict(picking) if picking else None,
        "pickingItems": [dict(item) for item in picking_items],
    }


def import_inbound(connection, operator, data):
    parsed = parse_import(connection, data, "inbound")
    now = iso_now()
    grouped = {}
    try:
        connection.execute("BEGIN IMMEDIATE")
        for item in parsed["rows"]:
            key = (item["product_id"], item["batch_no"], item["location"])
            if key in grouped:
                previous = grouped[key]
                if (
                    previous.get("production_date") != item.get("production_date")
                    or previous.get("shelf_life_days") != item.get("shelf_life_days")
                ):
                    raise ImportValidationError(
                        "Excel校验失败",
                        [{
                            "row": item["_row"],
                            "column": "批次号",
                            "message": "同一商品、批次、库位的日期信息不一致",
                        }],
                    )
                previous["quantity"] += item["quantity"]
            else:
                grouped[key] = {
                    **item,
                    "quantity": item["quantity"],
                }

        for product_id, product in parsed["products"].items():
            connection.execute(
                """INSERT OR IGNORE INTO products
                   (product_id, product_name, spec, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (product_id, product["product_name"], product["spec"], now, now),
            )

        order_no, _ = _next_number(connection, "inbound")
        note = next((item.get("note") for item in parsed["rows"] if item.get("note")), "")
        cursor = connection.execute(
            """INSERT INTO stock_orders
               (order_no, order_type, status, operator_id, operator_name,
                template_version, note, created_at, completed_at)
               VALUES (?, 'inbound', 'completed', ?, ?, ?, ?, ?, ?)""",
            (order_no, operator["id"], operator["phone"], parsed["version"], note, now, now),
        )
        order_id = cursor.lastrowid
        for item in parsed["rows"]:
            connection.execute(
                """INSERT INTO stock_order_items
                   (order_id, row_no, product_id, product_name, spec, batch_no,
                    location, quantity, note, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order_id, item["_row"], item["product_id"], item["product_name"],
                    item.get("spec"), item["batch_no"], item["location"],
                    item["quantity"], item.get("note"), now,
                ),
            )

        for (product_id, batch_no, location), item in grouped.items():
            existing = connection.execute(
                """SELECT id, quantity FROM inventory
                   WHERE product_id = ? AND batch_no = ? AND location = ?""",
                (product_id, batch_no, location),
            ).fetchone()
            if existing:
                connection.execute(
                    """UPDATE inventory SET quantity = quantity + ?,
                       production_date = ?, shelf_life_days = ?,
                       last_inbound_at = ?, updated_at = ? WHERE id = ?""",
                    (
                        item["quantity"], item.get("production_date"),
                        item.get("shelf_life_days"), now, now, existing["id"],
                    ),
                )
            else:
                connection.execute(
                    """INSERT INTO inventory
                       (product_id, batch_no, location, quantity, production_date,
                        shelf_life_days, last_inbound_at, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        product_id, batch_no, location, item["quantity"],
                        item.get("production_date"), item.get("shelf_life_days"),
                        now, now, now,
                    ),
                )
            connection.execute(
                """INSERT INTO stock_logs
                   (order_no, product_id, batch_no, location, change_type,
                    change_quantity, operator_name, created_at)
                   VALUES (?, ?, ?, ?, 'inbound', ?, ?, ?)""",
                (order_no, product_id, batch_no, location, item["quantity"], operator["phone"], now),
            )
        audit(connection, operator, "stock.inbound", f"创建入库单 {order_no}")
        connection.commit()
        result = _order_json(connection, order_id)
        result["aggregated"] = [
            {
                "productId": key[0],
                "batchNo": key[1],
                "location": key[2],
                "quantity": value["quantity"],
            }
            for key, value in grouped.items()
        ]
        return result
    except Exception:
        connection.rollback()
        raise


def import_outbound(connection, operator, data):
    parsed = parse_import(connection, data, "outbound")
    now = iso_now()
    totals = {}
    first_rows = {}
    for item in parsed["rows"]:
        product_id = item["product_id"]
        totals[product_id] = totals.get(product_id, 0) + item["quantity"]
        first_rows.setdefault(product_id, item)

    try:
        connection.execute("BEGIN IMMEDIATE")
        errors = []
        for product_id, quantity in totals.items():
            available = connection.execute(
                "SELECT COALESCE(SUM(quantity), 0) AS total FROM inventory WHERE product_id = ?",
                (product_id,),
            ).fetchone()["total"]
            if available < quantity:
                errors.append({
                    "row": first_rows[product_id]["_row"],
                    "column": "数量",
                    "message": f"当前商品库存为{available}件，出库数量{quantity}件，库存不足无法出库",
                })
        if errors:
            raise ImportValidationError("Excel校验失败", errors)

        order_no, pick_no = _next_number(connection, "outbound")
        note = next((item.get("note") for item in parsed["rows"] if item.get("note")), "")
        cursor = connection.execute(
            """INSERT INTO stock_orders
               (order_no, order_type, status, operator_id, operator_name,
                template_version, note, created_at)
               VALUES (?, 'outbound', 'pending_picking', ?, ?, ?, ?, ?)""",
            (order_no, operator["id"], operator["phone"], parsed["version"], note, now),
        )
        order_id = cursor.lastrowid
        for item in parsed["rows"]:
            connection.execute(
                """INSERT INTO stock_order_items
                   (order_id, row_no, product_id, product_name, spec, batch_no,
                    location, quantity, note, created_at)
                   VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)""",
                (
                    order_id, item["_row"], item["product_id"], item["product_name"],
                    item.get("spec"), item["quantity"], item.get("note"), now,
                ),
            )

        allocations = []
        for product_id, requested_quantity in totals.items():
            remaining = requested_quantity
            lots = connection.execute(
                """SELECT i.*, p.product_name, p.spec FROM inventory i
                   JOIN products p ON p.product_id = i.product_id
                   WHERE i.product_id = ? AND i.quantity > 0
                   ORDER BY i.last_inbound_at, i.id""",
                (product_id,),
            ).fetchall()
            for lot in lots:
                if remaining <= 0:
                    break
                picked = min(remaining, lot["quantity"])
                connection.execute(
                    """UPDATE inventory SET quantity = quantity - ?, updated_at = ?
                       WHERE id = ?""",
                    (picked, now, lot["id"]),
                )
                connection.execute(
                    """INSERT INTO stock_logs
                       (order_no, product_id, batch_no, location, change_type,
                        change_quantity, operator_name, created_at)
                       VALUES (?, ?, ?, ?, 'outbound', ?, ?, ?)""",
                    (order_no, product_id, lot["batch_no"], lot["location"], -picked, operator["phone"], now),
                )
                allocations.append({
                    "product_id": product_id,
                    "product_name": lot["product_name"],
                    "spec": lot["spec"],
                    "batch_no": lot["batch_no"],
                    "quantity": picked,
                    "location": lot["location"],
                })
                remaining -= picked
            if remaining:
                raise ValueError("库存已发生变化，请重新上传")

        pick_cursor = connection.execute(
            """INSERT INTO picking_orders
               (pick_no, order_id, status, operator_name, generated_at)
               VALUES (?, ?, 'pending', ?, ?)""",
            (pick_no, order_id, operator["phone"], now),
        )
        allocations.sort(key=lambda item: (item["location"], item["product_id"], item["batch_no"]))
        for sequence, allocation in enumerate(allocations, 1):
            connection.execute(
                """INSERT INTO picking_items
                   (picking_order_id, product_id, product_name, spec, batch_no,
                    quantity, location, sequence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pick_cursor.lastrowid, allocation["product_id"],
                    allocation["product_name"], allocation["spec"],
                    allocation["batch_no"], allocation["quantity"],
                    allocation["location"], sequence,
                ),
            )
        audit(connection, operator, "stock.outbound", f"创建出库单 {order_no} 和拣货单 {pick_no}")
        connection.commit()
        result = _order_json(connection, order_id)
        result["aggregated"] = [
            {"productId": product_id, "quantity": quantity}
            for product_id, quantity in totals.items()
        ]
        return result
    except Exception:
        connection.rollback()
        raise


def list_orders(
    connection,
    order_type=None,
    keyword=None,
    operator_name=None,
    start_date=None,
    end_date=None,
):
    sql = "SELECT * FROM stock_orders WHERE 1=1"
    params = []
    if order_type:
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
    return [dict(row) for row in connection.execute(sql, params).fetchall()]


def get_order(connection, order_id):
    row = connection.execute(
        "SELECT id FROM stock_orders WHERE id = ?", (order_id,)
    ).fetchone()
    if not row:
        raise ValueError("单据不存在")
    return _order_json(connection, order_id)


def list_inventory(connection, product_id=None, product_name=None, spec=None,
                   batch_no=None, location=None):
    sql = """SELECT i.*, p.product_name, p.spec FROM inventory i
             JOIN products p ON p.product_id = i.product_id WHERE 1=1"""
    params = []
    if product_id:
        sql += " AND i.product_id = ?"
        params.append(product_id)
    if product_name:
        sql += " AND p.product_name LIKE ?"
        params.append(f"%{product_name}%")
    if spec:
        sql += " AND p.spec LIKE ?"
        params.append(f"%{spec}%")
    if batch_no:
        sql += " AND i.batch_no LIKE ?"
        params.append(f"%{batch_no}%")
    if location:
        sql += " AND i.location LIKE ?"
        params.append(f"%{location}%")
    sql += " ORDER BY i.product_id, i.location, i.batch_no LIMIT 500"
    return [dict(row) for row in connection.execute(sql, params).fetchall()]


def inventory_workbook(rows):
    headers = [
        "商品ID", "商品名称", "规格", "批次号", "库位", "库存数量",
        "生产日期", "保质期（天）", "最近入库时间",
    ]
    values = []
    for row in rows:
        values.append([
            row["product_id"], row["product_name"], row["spec"] or "",
            row["batch_no"], row["location"], row["quantity"],
            row["production_date"] or "", row["shelf_life_days"] or "",
            row["last_inbound_at"],
        ])
    return excelio.build_workbook([headers] + values, "库存明细")


def list_stock_logs(connection, product_id=None):
    sql = "SELECT * FROM stock_logs WHERE 1=1"
    params = []
    if product_id:
        sql += " AND product_id = ?"
        params.append(product_id)
    sql += " ORDER BY id DESC LIMIT 500"
    return [dict(row) for row in connection.execute(sql, params).fetchall()]


def list_picking_orders(connection, status=None):
    sql = """SELECT po.*, so.order_no AS outbound_order_no
             FROM picking_orders po JOIN stock_orders so ON so.id = po.order_id
             WHERE 1=1"""
    params = []
    if status:
        sql += " AND po.status = ?"
        params.append(status)
    sql += " ORDER BY po.id DESC LIMIT 200"
    return [dict(row) for row in connection.execute(sql, params).fetchall()]


def get_picking_order(connection, picking_id):
    order = connection.execute(
        """SELECT po.*, so.order_no AS outbound_order_no
           FROM picking_orders po JOIN stock_orders so ON so.id = po.order_id
           WHERE po.id = ?""",
        (picking_id,),
    ).fetchone()
    if not order:
        raise ValueError("拣货单不存在")
    items = connection.execute(
        "SELECT * FROM picking_items WHERE picking_order_id = ? ORDER BY sequence, id",
        (picking_id,),
    ).fetchall()
    return {"order": dict(order), "items": [dict(item) for item in items]}


def complete_picking(connection, operator, picking_id):
    now = iso_now()
    try:
        connection.execute("BEGIN IMMEDIATE")
        picking = connection.execute(
            "SELECT * FROM picking_orders WHERE id = ?", (picking_id,)
        ).fetchone()
        if not picking:
            raise ValueError("拣货单不存在")
        if picking["status"] != "pending":
            raise ValueError("拣货单已完成")
        connection.execute(
            "UPDATE picking_orders SET status = 'completed', completed_at = ? WHERE id = ?",
            (now, picking_id),
        )
        connection.execute(
            "UPDATE stock_orders SET status = 'completed', completed_at = ? WHERE id = ?",
            (now, picking["order_id"]),
        )
        audit(
            connection,
            operator,
            "picking.complete",
            f"拣货单 {picking['pick_no']} 完成",
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return get_picking_order(connection, picking_id)


def picking_workbook(detail):
    order = detail["order"]
    headers = ["库位", "商品ID", "商品名称", "规格", "批次号", "数量"]
    rows = [[
        item["location"], item["product_id"], item["product_name"],
        item["spec"] or "", item["batch_no"], item["quantity"],
    ] for item in detail["items"]]
    title = f"拣货单-{order['pick_no']}"
    return excelio.build_workbook([headers] + rows, title)


def list_audit_logs(connection, action=None):
    sql = "SELECT * FROM audit_logs WHERE 1=1"
    params = []
    if action:
        sql += " AND action LIKE ?"
        params.append(f"%{action}%")
    sql += " ORDER BY id DESC LIMIT 500"
    return [dict(row) for row in connection.execute(sql, params).fetchall()]


def dashboard(connection):
    products = connection.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    inventory_count = connection.execute(
        "SELECT COUNT(*) FROM inventory WHERE quantity > 0"
    ).fetchone()[0]
    quantity = connection.execute(
        "SELECT COALESCE(SUM(quantity), 0) FROM inventory"
    ).fetchone()[0]
    pending_picking = connection.execute(
        "SELECT COUNT(*) FROM picking_orders WHERE status = 'pending'"
    ).fetchone()[0]
    users = connection.execute(
        "SELECT COUNT(*) FROM users WHERE status = 'active'"
    ).fetchone()[0]
    return {
        "products": products,
        "lots": inventory_count,
        "quantity": quantity,
        "pendingPicking": pending_picking,
        "activeUsers": users,
    }
