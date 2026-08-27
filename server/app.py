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


class WarehouseServer(ThreadingHTTPServer):
    def __init__(self, address, handler, database_path):
        self.db_path = str(database_path)
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

    def dispatch(self, method):
        parsed = urlparse(self.path)
        self.path = parsed.path
        self.query = parse_qs(parsed.query)
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

    def multipart_file(self):
        content_type = self.headers.get("Content-Type", "")
        body = self.read_body(service.MAX_FILE_SIZE + 1024)
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

    def require_user(self, connection, admin=False):
        user = self.user(connection)
        if not user:
            self.send_json({"error": "请先登录"}, 401)
            return None
        if user["must_change_password"] and self.path not in (
            "/api/me", "/api/change-password", "/api/logout"
        ):
            self.send_json({"error": "请先修改初始密码", "mustChangePassword": True}, 403)
            return None
        if admin and user["role"] != "admin":
            self.send_json({"error": "无权限访问"}, 403)
            return None
        return user

    def query_value(self, name):
        values = self.query.get(name)
        return values[0] if values else None

    def handle_api(self, method):
        connection = db.connect(self.server.db_path)
        try:
            if method == "POST" and self.path == "/api/login":
                body = self.read_json()
                token, user = auth.login(
                    connection, body.get("phone"), body.get("password")
                )
                self.send_json({"token": token, "user": user})
                return

            if method == "GET" and self.path == "/api/health":
                self.send_json({"status": "ok", "time": iso_now()})
                return

            if method == "GET" and self.path == "/api/me":
                user = self.require_user(connection)
                if user:
                    self.send_json(auth.public_user(user))
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
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
        finally:
            connection.close()

    def handle_protected_api(self, connection, method):
        if method == "GET" and self.path == "/api/dashboard":
            user = self.require_user(connection)
            if user:
                self.send_json(service.dashboard(connection))
            return

        if method == "GET" and self.path == "/api/templates":
            user = self.require_user(connection)
            if not user:
                return
            self.send_json([
                service.get_template(connection, "inbound"),
                service.get_template(connection, "outbound"),
            ])
            return

        match = re.fullmatch(r"/api/templates/(inbound|outbound)", self.path)
        if match and method == "PUT":
            user = self.require_user(connection, admin=True)
            if not user:
                return
            body = self.read_json()
            self.send_json(
                service.update_template(
                    connection, match.group(1), body.get("columns")
                )
            )
            return

        match = re.fullmatch(r"/api/templates/(inbound|outbound)/status", self.path)
        if match and method == "POST":
            user = self.require_user(connection, admin=True)
            if not user:
                return
            body = self.read_json()
            self.send_json(
                service.set_template_status(
                    connection, match.group(1), body.get("status")
                )
            )
            return

        match = re.fullmatch(
            r"/api/templates/(inbound|outbound)/download", self.path
        )
        if match and method == "GET":
            user = self.require_user(connection)
            if not user:
                return
            template_type = match.group(1)
            data = service.template_workbook(connection, template_type)
            version = service.get_template(connection, template_type)["version"]
            filename = f"{template_type}-template-v{version}.xlsx"
            self.send_bytes(
                data,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename,
            )
            return

        if method == "POST" and self.path == "/api/import-errors/export":
            user = self.require_user(connection)
            if not user:
                return
            body = self.read_json()
            rows = [["行号", "列名", "错误信息"]]
            rows.extend([
                [item.get("row", ""), item.get("column", ""), item.get("message", "")]
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
            user = self.require_user(connection)
            if not user:
                return
            filename, data = self.multipart_file()
            if not filename.lower().endswith(".xlsx"):
                raise ValueError("仅支持xlsx格式文件")
            if len(data) > service.MAX_FILE_SIZE:
                raise ValueError("文件不能超过5MB")
            importer = (
                service.import_inbound
                if match.group(1) == "inbound"
                else service.import_outbound
            )
            self.send_json(importer(connection, user, data))
            return

        if method == "GET" and self.path == "/api/orders":
            user = self.require_user(connection)
            if not user:
                return
            self.send_json(service.list_orders(
                connection,
                order_type=self.query_value("type"),
                keyword=self.query_value("keyword"),
                operator_name=self.query_value("operator"),
                start_date=self.query_value("startDate"),
                end_date=self.query_value("endDate"),
            ))
            return

        match = re.fullmatch(r"/api/orders/(\d+)", self.path)
        if match and method == "GET":
            user = self.require_user(connection)
            if user:
                self.send_json(service.get_order(connection, int(match.group(1))))
            return

        if method == "GET" and self.path == "/api/inventory":
            user = self.require_user(connection)
            if not user:
                return
            self.send_json(service.list_inventory(
                connection,
                product_id=self.query_value("productId"),
                product_name=self.query_value("productName"),
                spec=self.query_value("spec"),
                batch_no=self.query_value("batchNo"),
                location=self.query_value("location"),
            ))
            return

        if method == "GET" and self.path == "/api/inventory/export":
            user = self.require_user(connection)
            if not user:
                return
            rows = service.list_inventory(
                connection,
                product_id=self.query_value("productId"),
                product_name=self.query_value("productName"),
                spec=self.query_value("spec"),
                batch_no=self.query_value("batchNo"),
                location=self.query_value("location"),
            )
            self.send_bytes(
                service.inventory_workbook(rows),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "inventory.xlsx",
            )
            return

        if method == "GET" and self.path == "/api/stock-logs":
            user = self.require_user(connection)
            if user:
                self.send_json(
                    service.list_stock_logs(connection, self.query_value("productId"))
                )
            return

        if method == "GET" and self.path == "/api/picking-orders":
            user = self.require_user(connection)
            if not user:
                return
            self.send_json(
                service.list_picking_orders(connection, self.query_value("status"))
            )
            return

        match = re.fullmatch(r"/api/picking-orders/(\d+)", self.path)
        if match and method == "GET":
            user = self.require_user(connection)
            if user:
                self.send_json(
                    service.get_picking_order(connection, int(match.group(1)))
                )
            return

        match = re.fullmatch(r"/api/picking-orders/(\d+)/complete", self.path)
        if match and method == "POST":
            user = self.require_user(connection)
            if user:
                self.send_json(
                    service.complete_picking(
                        connection, user, int(match.group(1))
                    )
                )
            return

        match = re.fullmatch(r"/api/picking-orders/(\d+)/export", self.path)
        if match and method == "GET":
            user = self.require_user(connection)
            if not user:
                return
            detail = service.get_picking_order(connection, int(match.group(1)))
            self.send_bytes(
                service.picking_workbook(detail),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                f"picking-{detail['order']['pick_no']}.xlsx",
            )
            return

        if method == "GET" and self.path == "/api/users":
            user = self.require_user(connection, admin=True)
            if user:
                rows = connection.execute(
                    "SELECT * FROM users ORDER BY id"
                ).fetchall()
                self.send_json([auth.public_user(row) for row in rows])
            return

        if method == "POST" and self.path == "/api/users":
            operator = self.require_user(connection, admin=True)
            if not operator:
                return
            body = self.read_json()
            self.send_json(service.create_user(
                connection,
                operator,
                body.get("phone"),
                body.get("role"),
                body.get("password"),
            ))
            return

        match = re.fullmatch(r"/api/users/(\d+)/reset-password", self.path)
        if match and method == "POST":
            operator = self.require_user(connection, admin=True)
            if not operator:
                return
            body = self.read_json()
            service.reset_user_password(
                connection, operator, int(match.group(1)), body.get("password")
            )
            self.send_json({"ok": True})
            return

        match = re.fullmatch(r"/api/users/(\d+)/status", self.path)
        if match and method == "POST":
            operator = self.require_user(connection, admin=True)
            if not operator:
                return
            body = self.read_json()
            service.set_user_status(
                connection, operator, int(match.group(1)), body.get("status")
            )
            self.send_json({"ok": True})
            return

        if method == "GET" and self.path == "/api/audit-logs":
            user = self.require_user(connection, admin=True)
            if user:
                self.send_json(
                    service.list_audit_logs(connection, self.query_value("action"))
                )
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


def create_server(host, port, database_path):
    db.init_db(database_path)
    return WarehouseServer((host, port), Handler, database_path)
