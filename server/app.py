import json
import mimetypes
import re
from email.parser import BytesParser
from email.policy import default as default_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import auth
import db
import platform_registry
import service
from security import iso_now


STATIC_ROOT = Path(__file__).resolve().parent.parent / "static"
TOKEN_PATTERN = re.compile(r"^Bearer\s+(.+)$", re.I)
SYSTEM_NAME = "万顺WMS仓库管理系统"
COMPANY_NAME_MAX_LENGTH = platform_registry.MAX_COMPANY_NAME_LENGTH


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
    def __init__(self, address, handler, database_path, base_path="/",
                 registry=None, domain=None):
        self.db_path = str(database_path)
        self.base_path = normalize_base_path(base_path)
        self.registry = registry
        self.domain = (domain or "").strip().lower().lstrip(".") or None
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
        self.raw_body = self._drain_body()
        try:
            if self.server.registry is not None:
                if not self._resolve_platform_request():
                    return
                if self.console_mode:
                    if self.path.startswith("/api/"):
                        self.handle_console_api(method)
                    elif method == "GET":
                        self.handle_console_static()
                    else:
                        self.send_json({"error": "方法不允许"}, 405)
                    return
            if self.path.startswith("/api/"):
                self.handle_api(method)
            elif method == "GET":
                self.handle_static()
            else:
                self.send_json({"error": "方法不允许"}, 405)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except ValueError as exc:
            try:
                self.send_json({"error": str(exc)}, 400)
            except Exception:
                pass
        except Exception as exc:
            try:
                self.send_json({"error": str(exc)}, 500)
            except Exception:
                pass

    BODY_DRAIN_LIMIT = service.MAX_HISTORY_FILE_SIZE + 1024 * 1024

    def _drain_body(self):
        """一次性读完请求体：HTTP/1.1 长连接上未读 body 会被误当成
        下一条请求解析，必须在响应前消费掉。"""
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return b""
        if length > self.BODY_DRAIN_LIMIT:
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
            raise ValueError("请求体过大")
        return self.rfile.read(length)

    # ---- V5 平台层：按 Host 分发公司数据库或进入控制台 ----

    def request_host(self):
        host = (
            self.headers.get("X-Forwarded-Host", "")
            or self.headers.get("Host", "")
        )
        return host.split(",")[0].strip().lower().partition(":")[0]

    def _resolve_platform_request(self):
        """每请求解析 Host：console 前缀进控制台，公司前缀选库。

        返回 False 表示已直接回包（公司不存在/已停用），终止后续分发。
        """
        self.console_mode = False
        self.company = None
        self.db_path_override = None

        host = self.request_host()
        domain = self.server.domain
        prefix = None
        if domain:
            if host == f"console.{domain}":
                self.console_mode = True
                return True
            if host.endswith(f".{domain}"):
                label = host[: -len(domain) - 1]
                if label and "." not in label:
                    prefix = label
        if prefix is None and "." in host:
            prefix = host.partition(".")[0]

        company = self.server.registry.get_company(prefix or "")
        if not company:
            self.send_platform_page(
                "公司不存在，请使用公司子域名访问系统", 404
            )
            return False
        if company["status"] != "active":
            self.send_platform_page(
                f"公司「{company['name']}」已停用，请联系万顺平台管理员", 403
            )
            return False
        self.company = company
        self.db_path_override = company["db_path"]
        return True

    def send_platform_page(self, message, status):
        if self.path.startswith("/api/"):
            self.send_json({"error": message}, status)
            return
        body = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{SYSTEM_NAME}</title><link rel="stylesheet" href="/styles.css">
</head><body><div class="platform-notice"><h1>{SYSTEM_NAME}</h1>
<p>{message}</p></div></body></html>"""
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def request_db_path(self):
        return getattr(self, "db_path_override", None) or self.server.db_path

    def console_admin(self, registry):
        """控制台鉴权：返回管理员信息；须改密时限制可用接口。"""
        admin = registry.admin_for_token(self.token())
        if not admin:
            self.send_json({"error": "请先登录控制台"}, 401)
            return None
        if admin.get("must_change_password") and self.path not in (
            "/api/console/me", "/api/console/change-password",
            "/api/console/logout",
        ):
            self.send_json(
                {"error": "请先修改初始密码", "mustChangePassword": True}, 403
            )
            return None
        return admin

    def handle_console_api(self, method):
        registry = self.server.registry
        try:
            if method == "GET" and self.path == "/api/console/health":
                self.send_json({
                    "status": "ok",
                    "time": iso_now(),
                    "mode": "console",
                    "domain": self.server.domain,
                })
                return

            if method == "POST" and self.path == "/api/console/login":
                body = self.read_json()
                token, admin = registry.admin_login(
                    body.get("username"), body.get("password")
                )
                self.send_json({"token": token, "user": admin})
                return

            if not str(self.path).startswith("/api/console/"):
                self.send_json({"error": "接口不存在"}, 404)
                return

            admin = self.console_admin(registry)
            if not admin:
                return

            if method == "GET" and self.path == "/api/console/me":
                self.send_json({
                    "username": admin["username"],
                    "displayName": admin["display_name"],
                    "mustChangePassword": bool(admin.get("must_change_password")),
                    "domain": self.server.domain,
                })
                return

            if method == "POST" and self.path == "/api/console/logout":
                registry.admin_logout(self.token())
                self.send_json({"ok": True})
                return

            if method == "POST" and self.path == "/api/console/change-password":
                body = self.read_json()
                registry.admin_change_password(
                    self.token(), body.get("oldPassword"),
                    body.get("newPassword"),
                )
                self.send_json({"ok": True})
                return

            if method == "GET" and self.path == "/api/console/companies":
                self.send_json([
                    platform_registry.public_company(row)
                    for row in registry.companies()
                ])
                return

            if method == "POST" and self.path == "/api/console/companies":
                body = self.read_json()
                record, password = registry.create_company(
                    body.get("prefix"), body.get("name"),
                    body.get("adminUsername"), body.get("adminDisplayName"),
                    body.get("password"),
                )
                result = platform_registry.public_company(record)
                result["initialPassword"] = password
                result["subdomain"] = (
                    f"{record['prefix']}.{self.server.domain}"
                    if self.server.domain else record["prefix"]
                )
                self.send_json(result, 201)
                return

            match = re.fullmatch(
                r"/api/console/companies/([a-z0-9-]+)/status", self.path
            )
            if match and method == "POST":
                body = self.read_json()
                row = registry.set_company_status(
                    match.group(1), body.get("status")
                )
                self.send_json(platform_registry.public_company(row))
                return

            match = re.fullmatch(
                r"/api/console/companies/([a-z0-9-]+)/reset-admin-password",
                self.path,
            )
            if match and method == "POST":
                password = registry.reset_company_admin_password(
                    match.group(1)
                )
                self.send_json({"password": password})
                return

            self.send_json({"error": "接口不存在"}, 404)
        except platform_registry.PlatformError as exc:
            self.send_json({"error": str(exc)}, 400)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)

    def read_body(self, limit=1024 * 1024):
        if len(self.raw_body) > limit:
            raise ValueError("请求体过大")
        return self.raw_body

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

    def inventory_query(self):
        query = {
            "warehouse_name": self.query_value("warehouseName"),
            "storage_area": self.query_value("storageArea"),
            "cargo_area": self.query_value("cargoArea"),
            "location_name": self.query_value("locationName"),
            "product_code": self.query_value("productCode"),
            "product_name": self.query_value("productName"),
            "batch_no": self.query_value("batchNo"),
            "quality_status": self.query_value("qualityStatus"),
            "prohibit_outbound": self.query_value("prohibitOutbound"),
            "production_start_date": self.query_value("productionStartDate"),
            "production_end_date": self.query_value("productionEndDate"),
            "expiry_start_date": self.query_value("expiryStartDate"),
            "expiry_end_date": self.query_value("expiryEndDate"),
            "inbound_start_date": self.query_value("inboundStartDate"),
            "inbound_end_date": self.query_value("inboundEndDate"),
        }
        query["summary"] = self.query_value("summary")
        return query

    def handle_api(self, method):
        connection = db.connect(self.request_db_path())
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

            if method == "GET" and self.path == "/api/branding":
                company = getattr(self, "company", None)
                if company:
                    name = company["name"]
                else:
                    row = connection.execute(
                        "SELECT value FROM meta WHERE key = 'company_name'"
                    ).fetchone()
                    name = row["value"] if row else ""
                self.send_json({
                    "systemName": SYSTEM_NAME,
                    "companyName": name,
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
        if method == "PUT" and self.path == "/api/company-name":
            user = self.require_admin(connection)
            if not user:
                return
            body = self.read_json()
            name = str(body.get("companyName") or "").strip()
            if not name or len(name) > COMPANY_NAME_MAX_LENGTH:
                raise ValueError(
                    f"公司名称必填且不超过{COMPANY_NAME_MAX_LENGTH}字"
                )
            company = getattr(self, "company", None)
            if company:
                self.server.registry.rename_company(company["prefix"], name)
            else:
                connection.execute(
                    """INSERT INTO meta(key, value) VALUES ('company_name', ?)
                       ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                    (name,),
                )
            service.audit(connection, user, "company.rename",
                          f"修改公司名称为 {name}")
            connection.commit()
            self.send_json({"ok": True})
            return

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
            project = connection.execute(
                "SELECT project_name FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            project_name = project["project_name"] if project else "项目"
            label = "入库模板" if template_type == "inbound" else "出库模板"
            data = service.template_workbook(
                connection, template_type, project_id
            )
            self.send_bytes(
                data,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                f"{project_name}-{label}.xlsx",
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

        match = re.fullmatch(r"/api/(inbound|outbound)/manual", self.path)
        if match and method == "POST":
            user = self.require_user(connection, role="project_admin")
            if not user:
                return
            body = self.read_json()
            creator = (
                service.create_manual_inbound
                if match.group(1) == "inbound"
                else service.create_manual_outbound
            )
            self.send_json(
                creator(connection, user, self.project_id(user), body)
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

        if method == "GET" and self.path == "/api/inventory/filters":
            user = self.require_user(connection, role="project_admin")
            if not user:
                return
            self.send_json(service.inventory_filter_options(
                connection, user, self.project_id(user)
            ))
            return

        if method == "GET" and self.path == "/api/inventory":
            user = self.require_user(connection, role="project_admin")
            if not user:
                return
            self.send_json(service.list_inventory(
                connection, user, self.project_id(user),
                **self.inventory_query(),
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
                **self.inventory_query(),
            )
            self.send_bytes(
                service.inventory_workbook(
                    project["project_name"], rows,
                    summary=self.query_value("summary") in ("1", "true"),
                ),
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
                service.picking_workbook(
                    detail, self.query_value("sort") or "location"
                ),
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
                body.get("category"), body.get("unitWeight"),
                body.get("volume"), body.get("shelfLifeDays"),
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
                body.get("category"), body.get("unitWeight"),
                body.get("volume"), body.get("shelfLifeDays"),
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
                connection, user, body.get("projectName"),
                body.get("warehouseId"), body.get("storageArea"),
                body.get("remark"),
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
                body.get("projectName"), body.get("warehouseId"),
                body.get("storageArea"), body.get("remark"),
                body.get("status"),
            ))
            return

        match = re.fullmatch(r"/api/projects/(\d+)/products", self.path)
        if match and method == "PUT":
            user = self.require_admin(connection)
            if not user:
                return
            body = self.read_json()
            self.send_json(service.set_project_products(
                connection, user, int(match.group(1)),
                body.get("productIds"),
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
            # RFC 5987：filename* 支持中文文件名，filename 提供 ASCII 兜底
            quoted = quote(filename)
            self.send_header(
                "Content-Disposition",
                f"attachment; filename=\"{quoted}\"; filename*=UTF-8''{quoted}",
            )
        self.end_headers()
        self.wfile.write(data)

    def handle_static(self, default="index.html"):
        if self.path == "/":
            path = STATIC_ROOT / default
        else:
            path = (STATIC_ROOT / self.path.lstrip("/")).resolve()
            if not str(path).startswith(str(STATIC_ROOT.resolve())):
                self.send_json({"error": "路径无效"}, 403)
                return
            if not path.exists() and path.suffix not in (".css", ".js", ".ico"):
                path = STATIC_ROOT / default
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

    def handle_console_static(self):
        self.handle_static(default="console.html")


def create_server(host, port, database_path, base_path="/", registry=None,
                  domain=None):
    if registry is not None:
        generated = registry.ensure_platform_admin()
        if generated:
            print("平台管理员初始密码（仅本次显示，请立即保存）：", generated)
    else:
        db.init_db(database_path)
    return WarehouseServer(
        (host, port), Handler, database_path, base_path=base_path,
        registry=registry, domain=domain,
    )
