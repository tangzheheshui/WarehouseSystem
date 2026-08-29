import json
import mimetypes
import re
from email.parser import BytesParser
from email.policy import default as default_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import auth
import db
import service
from security import iso_now


STATIC_ROOT = Path(__file__).resolve().parent.parent / "static"
TOKEN_PATTERN = re.compile(r"^Bearer\s+(.+)$", re.I)


def normalize_base_path(value):
    value = (value or "/").strip()
    if not value.startswith("/"):
        value = "/" + value
    if len(value) > 1:
        value = value.rstrip("/")
    if value in ("", "/"):
        return "/"
    if not re.fullmatch(r"/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", value):
        raise ValueError("访问前缀只能包含字母、数字、下划线、连字符和斜杠")
    return value


class WarehouseServer(ThreadingHTTPServer):
    def __init__(self, address, handler, database_path, base_path="/"):
        self.db_path = str(database_path)
        self.base_path = normalize_base_path(base_path)
        super().__init__(address, handler)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Allow", "GET, POST, PUT, OPTIONS")
        self.end_headers()

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def _normalize_path(self, path):
        base = self.server.base_path
        if base == "/":
            return path
        if path == base:
            return "/"
        if path.startswith(base + "/"):
            return path[len(base):] or "/"
        return None

    def dispatch(self, method):
        parsed = urlparse(self.path)
        self.path = self._normalize_path(parsed.path)
        self.query = parse_qs(parsed.query)
        if self.path is None:
            self.send_json({"error": "路径无效"}, 404)
            return
        try:
            if self.path.startswith("/api/"):
                self.handle_api(method)
            elif method == "GET":
                self.handle_static()
            else:
                self.send_json({"error": "方法不允许"}, 405)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            try:
                self.send_json({"error": str(exc)}, 500)
            except Exception:
                pass

    def read_body(self, limit=1024 * 1024):
        length = int(self.headers.get("Content-Length", "0"))
        if length > limit:
            raise ValueError("请求体过大")
        return self.rfile.read(length)

    def read_json(self):
        try:
            value = json.loads(self.read_body().decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("JSON格式错误")
            return value
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("JSON格式错误") from exc

    def multipart_file(self, limit):
        content_type = self.headers.get("Content-Type", "")
        body = self.read_body(limit + 1024)
        raw = f"Content-Type: {content_type}\r\n\r\n".encode("utf-8") + body
        message = BytesParser(policy=default_policy).parsebytes(raw)
        for part in message.iter_parts():
            filename = part.get_filename()
            if filename:
                return unquote(filename), part.get_payload(decode=True) or b""
        raise ValueError("请选择上传文件")

    def token(self):
        authorization = self.headers.get("Authorization", "")
        match = TOKEN_PATTERN.match(authorization)
        return match.group(1) if match else ""

    def user(self, connection):
        return auth.user_for_token(connection, self.token())

    def require_user(self, connection, role=None):
        user = self.user(connection)
        if not user:
            self.send_json({"error": "请先登录"}, 401)
            return None
        if user["must_change_password"] and self.path not in (
            "/api/me", "/api/change-password", "/api/logout"
        ):
            self.send_json(
                {"error": "请先修改初始密码", "mustChangePassword": True}, 403
            )
            return None
        if role and user["role"] != role:
            self.send_json({"error": "无权限访问"}, 403)
            return None
        return user

    def require_admin(self, connection):
        return self.require_user(connection, role="admin")

    def project_id(self, user):
        if user["role"] != "project_admin":
            raise service.Forbidden("仅项目管理员可以执行项目作业")
        raw = self.headers.get("X-Project-Id", "")
        if raw:
            return raw
        projects = service.user_projects(self.connection, user)
        if len(projects) == 1:
            return projects[0]["id"]
        raise ValueError("请选择项目")

    def query_value(self, name):
        values = self.query.get(name)
        return values[0] if values else None

    def handle_api(self, method):
        connection = db.connect(self.server.db_path)
        self.connection = connection
        try:
            if method == "POST" and self.path == "/api/login":
                body = self.read_json()
                token, user = auth.login(
                    connection, body.get("username"), body.get("password")
                )
                self.send_json({"token": token, "user": user})
                return

            if method == "GET" and self.path == "/api/health":
                self.send_json({
                    "status": "ok",
                    "time": iso_now(),
                    "basePath": self.server.base_path,
                })
                return

            if method == "GET" and self.path == "/api/me":
                user = self.require_user(connection)
                if user:
                    result = auth.public_user(user)
                    result["projects"] = service.user_projects(connection, user)
                    self.send_json(result)
                return

            if method == "POST" and self.path == "/api/logout":
                user = self.user(connection)
                if user:
                    auth.logout(connection, self.token())
                self.send_json({"ok": True})
                return

            if method == "POST" and self.path == "/api/change-password":
                user = self.require_user(connection)
                if not user:
                    return
                body = self.read_json()
                auth.change_password(
                    connection,
                    user,
                    body.get("oldPassword"),
                    body.get("newPassword"),
                    self.token(),
                )
                self.send_json({"ok": True})
                return

            self.handle_protected_api(connection, method)
        except service.ImportValidationError as exc:
            self.send_json({"error": exc.args[0], "details": exc.errors}, 422)
        except service.Forbidden as exc:
            self.send_json({"error": str(exc)}, 403)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
        finally:
            connection.close()

    def handle_protected_api(self, connection, method):
        if method == "GET" and self.path == "/api/dashboard":
            user = self.require_user(connection)
            if not user:
                return
            if user["role"] == "admin":
                self.send_json(service.admin_dashboard(connection))
            else:
                self.send_json(
                    service.dashboard(connection, user, self.project_id(user))
                )
            return

        if method == "GET" and self.path == "/api/templates":
            user = self.require_user(connection, role="project_admin")
            if not user:
                return
            project_id = self.project_id(user)
            self.send_json([
                service.get_template(connection, "inbound", project_id),
                service.get_template(connection, "outbound", project_id),
            ])
            return

        match = re.fullmatch(r"/api/templates/(inbound|outbound)", self.path)
        if match and method == "PUT":
            user = self.require_user(connection, role="project_admin")
            if not user:
                return
            body = self.read_json()
            self.send_json(service.update_template(
                connection, user, self.project_id(user), match.group(1),
                body.get("columns"),
            ))
            return

        match = re.fullmatch(r"/api/templates/(inbound|outbound)/status", self.path)
        if match and method == "POST":
            user = self.require_user(connection, role="project_admin")
            if not user:
                return
            body = self.read_json()
            self.send_json(service.set_template_status(
                connection, user, self.project_id(user), match.group(1),
                body.get("status"),
            ))
            return

        match = re.fullmatch(r"/api/templates/(inbound|outbound)/download", self.path)
        if match and method == "GET":
            user = self.require_user(connection, role="project_admin")
            if not user:
                return
            project_id = self.project_id(user)
            template_type = match.group(1)
            data = service.template_workbook(
                connection, template_type, project_id
            )
            version = service.get_template(
                connection, template_type, project_id
            )["version"]
            self.send_bytes(
                data,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                f"{template_type}-template-v{version}.xlsx",
            )
            return

        if method == "POST" and self.path == "/api/import-errors/export":
            user = self.require_user(connection)
            if not user:
                return
            body = self.read_json()
            rows = [["行号", "列名", "错误信息"]]
            rows.extend([
                [item.get("row", ""), item.get("column", ""),
                 item.get("message", "")]
                for item in body.get("details", [])
            ])
            data = service.excelio.build_workbook(rows, "导入错误")
            self.send_bytes(
                data,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "import-errors.xlsx",
            )
            return

        match = re.fullmatch(r"/api/(inbound|outbound)/upload", self.path)
        if match and method == "POST":
            user = self.require_user(connection, role="project_admin")
            if not user:
                return
            filename, data = self.multipart_file(service.MAX_FILE_SIZE)
            if not filename.lower().endswith(".xlsx"):
                raise ValueError("仅支持xlsx格式文件")
            importer = (
                service.import_inbound
                if match.group(1) == "inbound"
                else service.import_outbound
            )
            self.send_json(
                importer(connection, user, self.project_id(user), data)
            )
            return

        if method == "GET" and self.path == "/api/orders":
            user = self.require_user(connection, role="project_admin")
            if not user:
                return
            self.send_json(service.list_orders(
                connection, user, self.project_id(user),
                order_type=self.query_value("type"),
                keyword=self.query_value("keyword"),
                operator_name=self.query_value("operator"),
                start_date=self.query_value("startDate"),
                end_date=self.query_value("endDate"),
            ))
            return

        match = re.fullmatch(r"/api/orders/(\d+)", self.path)
        if match and method == "GET":
            user = self.require_user(connection, role="project_admin")
            if user:
                self.send_json(service.get_order(
                    connection, user, self.project_id(user), int(match.group(1))
                ))
            return

        if method == "GET" and self.path == "/api/inventory":
            user = self.require_user(connection, role="project_admin")
            if not user:
                return
            self.send_json(service.list_inventory(
                connection, user, self.project_id(user),
                warehouse_name=self.query_value("warehouseName"),
                storage_area=self.query_value("storageArea"),
                cargo_area=self.query_value("cargoArea"),
                location_name=self.query_value("locationName"),
                product_code=self.query_value("productCode"),
                product_name=self.query_value("productName"),
                batch_no=self.query_value("batchNo"),
                quality_status=self.query_value("qualityStatus"),
                prohibit_outbound=self.query_value("prohibitOutbound"),
            ))
            return

        if method == "GET" and self.path == "/api/inventory/export":
            user = self.require_user(connection, role="project_admin")
            if not user:
                return
            project_id = self.project_id(user)
            project = connection.execute(
                "SELECT project_name FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            rows = service.list_inventory(
                connection, user, project_id,
                warehouse_name=self.query_value("warehouseName"),
                storage_area=self.query_value("storageArea"),
                cargo_area=self.query_value("cargoArea"),
                location_name=self.query_value("locationName"),
                product_code=self.query_value("productCode"),
                product_name=self.query_value("productName"),
                batch_no=self.query_value("batchNo"),
                quality_status=self.query_value("qualityStatus"),
                prohibit_outbound=self.query_value("prohibitOutbound"),
            )
            self.send_bytes(
                service.inventory_workbook(project["project_name"], rows),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "inventory.xlsx",
            )
            return

        if method == "GET" and self.path == "/api/stock-logs":
            user = self.require_user(connection, role="project_admin")
            if user:
                self.send_json(service.list_stock_logs(
                    connection, user, self.project_id(user),
                    self.query_value("productCode"),
                ))
            return

        if method == "GET" and self.path == "/api/picking-orders":
            user = self.require_user(connection, role="project_admin")
            if not user:
                return
            self.send_json(service.list_picking_orders(
                connection, user, self.project_id(user),
                self.query_value("status"),
            ))
            return

        match = re.fullmatch(r"/api/picking-orders/(\d+)", self.path)
        if match and method == "GET":
            user = self.require_user(connection, role="project_admin")
            if user:
                self.send_json(service.get_picking_order(
                    connection, user, self.project_id(user),
                    int(match.group(1)),
                ))
            return

        match = re.fullmatch(r"/api/picking-orders/(\d+)/complete", self.path)
        if match and method == "POST":
            user = self.require_user(connection, role="project_admin")
            if user:
                self.send_json(service.complete_picking(
                    connection, user, self.project_id(user),
                    int(match.group(1)),
                ))
            return

        match = re.fullmatch(r"/api/picking-orders/(\d+)/export", self.path)
        if match and method == "GET":
            user = self.require_user(connection, role="project_admin")
            if not user:
                return
            detail = service.get_picking_order(
                connection, user, self.project_id(user), int(match.group(1))
            )
            self.send_bytes(
                service.picking_workbook(detail),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                f"picking-{detail['order']['pick_no']}.xlsx",
            )
            return

        if method == "GET" and self.path == "/api/products":
            user = self.require_user(connection, role="admin")
            if user:
                self.send_json(service.list_products(
                    connection, self.query_value("keyword"),
                    self.query_value("status"),
                ))
            return

        if method == "POST" and self.path == "/api/products":
            user = self.require_admin(connection)
            if not user:
                return
            body = self.read_json()
            self.send_json(service.create_product(
                connection, user, body.get("productCode"),
                body.get("productName"), body.get("spec"), body.get("unit"),
            ))
            return

        match = re.fullmatch(r"/api/products/([^/]+)", self.path)
        if match and method == "PUT":
            user = self.require_admin(connection)
            if not user:
                return
            body = self.read_json()
            self.send_json(service.update_product(
                connection, user, unquote(match.group(1)),
                body.get("productName"), body.get("spec"), body.get("unit"),
                body.get("status"),
            ))
            return

        if method == "GET" and self.path == "/api/warehouses":
            user = self.require_user(connection, role="admin")
            if user:
                self.send_json(service.list_warehouses(
                    connection, self.query_value("keyword")
                ))
            return

        if method == "POST" and self.path == "/api/warehouses":
            user = self.require_admin(connection)
            if not user:
                return
            body = self.read_json()
            self.send_json(service.create_warehouse(
                connection, user, body.get("warehouseName"), body.get("address"),
                body.get("contactName"), body.get("contactPhone"),
            ))
            return

        match = re.fullmatch(r"/api/warehouses/(\d+)/status", self.path)
        if match and method == "POST":
            user = self.require_admin(connection)
            if not user:
                return
            body = self.read_json()
            self.send_json(service.set_warehouse_status(
                connection, user, "warehouses", int(match.group(1)),
                body.get("status"),
            ))
            return

        if method == "POST" and self.path == "/api/warehouse-locations":
            user = self.require_admin(connection)
            if not user:
                return
            body = self.read_json()
            self.send_json(service.create_location(
                connection, user, body.get("warehouseId"),
                body.get("storageArea"), body.get("cargoArea"),
                body.get("locationName"),
            ))
            return

        match = re.fullmatch(r"/api/warehouse-locations/(\d+)/status", self.path)
        if match and method == "POST":
            user = self.require_admin(connection)
            if not user:
                return
            body = self.read_json()
            self.send_json(service.set_warehouse_status(
                connection, user, "warehouse_locations",
                int(match.group(1)), body.get("status"),
            ))
            return

        if method == "GET" and self.path == "/api/projects":
            user = self.require_admin(connection)
            if user:
                self.send_json(service.list_project_rows(connection))
            return

        if method == "POST" and self.path == "/api/projects":
            user = self.require_admin(connection)
            if not user:
                return
            body = self.read_json()
            self.send_json(service.create_project(
                connection, user, body.get("projectName"), body.get("remark")
            ))
            return

        match = re.fullmatch(r"/api/projects/(\d+)", self.path)
        if match and method == "PUT":
            user = self.require_admin(connection)
            if not user:
                return
            body = self.read_json()
            self.send_json(service.update_project(
                connection, user, int(match.group(1)),
                body.get("projectName"), body.get("remark"),
                body.get("status"),
            ))
            return

        match = re.fullmatch(r"/api/projects/(\d+)/history-import", self.path)
        if match and method == "POST":
            user = self.require_admin(connection)
            if not user:
                return
            filename, data = self.multipart_file(service.MAX_HISTORY_FILE_SIZE)
            if not filename.lower().endswith(".csv"):
                raise ValueError("历史库存仅支持csv格式文件")
            self.send_json(service.import_history_csv(
                connection, user, int(match.group(1)), data
            ))
            return

        if method == "GET" and self.path == "/api/users":
            user = self.require_admin(connection)
            if user:
                self.send_json(service.list_users(connection))
            return

        if method == "POST" and self.path == "/api/users":
            operator = self.require_admin(connection)
            if not operator:
                return
            body = self.read_json()
            created = service.create_user(
                connection, operator, body.get("username"),
                body.get("displayName"), body.get("contactPhone"),
                body.get("role"), body.get("password"),
                body.get("projectIds"),
            )
            created.pop("password_hash", None)
            self.send_json(created)
            return

        match = re.fullmatch(r"/api/users/(\d+)", self.path)
        if match and method == "PUT":
            operator = self.require_admin(connection)
            if not operator:
                return
            body = self.read_json()
            service.update_user(
                connection, operator, int(match.group(1)),
                body.get("username"), body.get("displayName"),
                body.get("contactPhone"), body.get("role"),
                body.get("status"), body.get("projectIds"),
            )
            self.send_json({"ok": True})
            return

        match = re.fullmatch(r"/api/users/(\d+)/reset-password", self.path)
        if match and method == "POST":
            operator = self.require_admin(connection)
            if not operator:
                return
            body = self.read_json()
            service.reset_user_password(
                connection, operator, int(match.group(1)),
                body.get("password"),
            )
            self.send_json({"ok": True})
            return

        if method == "GET" and self.path == "/api/audit-logs":
            user = self.require_user(connection, role="project_admin")
            if user:
                self.send_json(service.list_audit_logs(
                    connection, user, self.project_id(user),
                    self.query_value("action"),
                ))
            return

        self.send_json({"error": "接口不存在"}, 404)

    def send_json(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_bytes(self, data, content_type, filename=None):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if filename:
            self.send_header(
                "Content-Disposition", f'attachment; filename="{filename}"'
            )
        self.end_headers()
        self.wfile.write(data)

    def handle_static(self):
        if self.path == "/":
            path = STATIC_ROOT / "index.html"
        else:
            path = (STATIC_ROOT / self.path.lstrip("/")).resolve()
            if not str(path).startswith(str(STATIC_ROOT.resolve())):
                self.send_json({"error": "路径无效"}, 403)
                return
            if not path.exists() and path.suffix not in (".css", ".js", ".ico"):
                path = STATIC_ROOT / "index.html"
        if not path.exists() or not path.is_file():
            self.send_json({"error": "文件不存在"}, 404)
            return
        data = path.read_bytes()
        content_type = (
            mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        )
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


def create_server(host, port, database_path, base_path="/"):
    db.init_db(database_path)
    return WarehouseServer(
        (host, port), Handler, database_path, base_path=base_path
    )
