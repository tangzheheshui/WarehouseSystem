"""V5 平台层：公司注册表与万顺控制台账号。

注册表 data/platform.json 是公司租户的唯一事实来源：
- companies: 每家公司一个前缀、一个独立 SQLite 数据库文件；
- platform_admin: 万顺平台管理员（登录名 + PBKDF2 哈希，与业务侧同套加密）；
- console_sessions: 控制台会话（与各公司库内的业务会话完全隔离）。
"""

import json
import os
import re
import secrets
import string
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import db
from security import (
    hash_password,
    iso_in,
    iso_now,
    new_session_token,
    parse_iso,
    token_hash,
    valid_password,
    verify_password,
)

RESERVED_PREFIXES = {
    "console", "www", "admin", "api", "app", "mail", "smtp", "pop", "imap",
    "ftp", "sftp", "ns", "ns1", "ns2", "dns", "vpn", "cdn", "static", "media",
    "dev", "test", "stage", "staging", "demo", "portal", "platform", "wms",
    "warehouse", "status", "git", "lab", "docs", "help", "support", "backup",
}

PREFIX_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,30}[a-z0-9]$")

MAX_COMPANY_NAME_LENGTH = 50

# 公司管理员账号固定，不再由控制台操作员命名
DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_DISPLAY_NAME = "管理员"

# 订阅：按年下单（开通即首单），到期后阻断该公司访问
MAX_SUBSCRIPTION_YEARS = 10
EXPIRY_WARN_DAYS = 30
AMOUNT_PATTERN = re.compile(r"^\d{1,10}(\.\d{1,2})?$")


def valid_prefix(prefix):
    return bool(PREFIX_PATTERN.fullmatch(prefix or ""))


def _today():
    return datetime.now(timezone.utc).date()


def _add_years(start, years):
    """订阅到期日按整年顺延；2月29日回落到2月28日。"""
    try:
        return start.replace(year=start.year + years)
    except ValueError:
        return start.replace(year=start.year + years, month=2, day=28)


def _parse_date(value):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def subscription_state(row):
    """推导订阅状态：perpetual（未设到期日）/ normal / expiring / expired。"""
    expires = _parse_date(row.get("expires_at"))
    if expires is None:
        return "perpetual"
    if expires < _today():
        return "expired"
    if expires <= _today() + timedelta(days=EXPIRY_WARN_DAYS):
        return "expiring"
    return "normal"


def _valid_years(value):
    try:
        years = int(value)
    except (TypeError, ValueError):
        raise PlatformError(f"订阅时长须为1-{MAX_SUBSCRIPTION_YEARS}的整数年")
    if not 1 <= years <= MAX_SUBSCRIPTION_YEARS:
        raise PlatformError(f"订阅时长须为1-{MAX_SUBSCRIPTION_YEARS}的整数年")
    return years


def _clean_amount(value):
    amount = str(value or "").strip()
    if not amount:
        return None
    if not AMOUNT_PATTERN.fullmatch(amount):
        raise PlatformError("金额格式无效，示例：3980 或 3980.50")
    return amount


def generate_password(length=12):
    alphabet = string.ascii_letters + string.digits
    while True:
        chars = [secrets.choice(alphabet) for _ in range(length)]
        if (any(c in string.ascii_letters for c in chars)
                and any(c in string.digits for c in chars)):
            return "".join(chars)


def public_company(row):
    state = subscription_state(row)
    public = {
        "prefix": row["prefix"],
        "name": row["name"],
        "status": row["status"],
        "adminUsername": row["admin_username"],
        "createdAt": row["created_at"],
        "updatedAt": row.get("updated_at") or row["created_at"],
        "expiresAt": row.get("expires_at"),
        "subscriptionState": state,
        "orderCount": len(row.get("orders") or []),
    }
    expires = _parse_date(row.get("expires_at"))
    if expires is not None:
        public["daysLeft"] = (expires - _today()).days
    return public


class PlatformError(ValueError):
    """平台层业务错误，消息直接展示给控制台用户。"""


class PlatformRegistry:
    def __init__(self, path):
        self.path = Path(path)
        self.data_dir = self.path.parent
        self.lock = threading.RLock()
        self._load()

    # ---- 注册表读写 ----

    def _load(self):
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                raise PlatformError(f"公司注册表损坏：{self.path}")
        else:
            self.data = {"version": 1, "companies": []}
        self.data.setdefault("version", 1)
        self.data.setdefault("companies", [])
        for row in self.data["companies"]:
            # 存量公司没有 expires_at 视为永久有效，订单记录从零开始
            row.setdefault("orders", [])

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp, self.path)

    def _reload(self):
        """在持有锁的前提下重读磁盘：应急恢复等进程外修改能立即生效，
        不会被本进程的内存旧态覆盖。必须在 self.lock 内调用。"""
        self._load()

    def company_db_path(self, prefix):
        return self.data_dir / "companies" / prefix / "warehouse.db"

    def companies(self):
        with self.lock:
            self._reload()
            return [dict(row) for row in self.data["companies"]]

    def get_company(self, prefix):
        with self.lock:
            self._reload()
            row = self._company_row(prefix)
            return dict(row) if row else None

    def _company_row(self, prefix):
        """返回注册表内的原始行（可变对象），供写操作就地修改。"""
        for row in self.data["companies"]:
            if row["prefix"] == prefix:
                return row
        return None

    # ---- 公司管理 ----

    def _require_unique(self, prefix):
        if not valid_prefix(prefix):
            raise PlatformError(
                "公司前缀须为2-32位小写字母、数字或连字符，且以字母开头、以字母或数字结尾"
            )
        if prefix in RESERVED_PREFIXES:
            raise PlatformError(f"公司前缀 {prefix} 为系统保留字")
        if self.get_company(prefix):
            raise PlatformError(f"公司前缀 {prefix} 已存在")
        if self.company_db_path(prefix).exists():
            raise PlatformError(f"公司数据目录 {prefix} 已存在")

    def create_company(self, prefix, name, years=1, amount=None, remark=None,
                       operator=None):
        """开通公司：固定 admin 管理员账号，首笔订阅订单即开通单。"""
        prefix = str(prefix or "").strip().lower()
        name = str(name or "").strip()
        if not name or len(name) > MAX_COMPANY_NAME_LENGTH:
            raise PlatformError(f"公司名称必填且不超过{MAX_COMPANY_NAME_LENGTH}字")
        years = _valid_years(years)
        amount = _clean_amount(amount)
        remark = str(remark or "").strip() or None
        operator = str(operator or "").strip() or None
        password = generate_password()
        if not valid_password(password):
            raise PlatformError("密码须为8-20位，且同时包含字母和数字")

        with self.lock:
            self._reload()
            self._require_unique(prefix)
            db_path = self.company_db_path(prefix)
            db.init_db(
                db_path,
                admin_username=DEFAULT_ADMIN_USERNAME,
                admin_display_name=DEFAULT_ADMIN_DISPLAY_NAME,
                admin_password=password,
            )
            connection = db.connect(db_path)
            try:
                admin_row = connection.execute(
                    "SELECT id FROM users WHERE username = ? AND role = 'admin'",
                    (DEFAULT_ADMIN_USERNAME,),
                ).fetchone()
                admin_user_id = admin_row["id"] if admin_row else None
            finally:
                connection.close()

            today = _today()
            expires_at = _add_years(today, years).isoformat()
            now = iso_now()
            record = {
                "prefix": prefix,
                "name": name,
                "status": "active",
                "db_path": str(db_path),
                "admin_username": DEFAULT_ADMIN_USERNAME,
                "admin_user_id": admin_user_id,
                "expires_at": expires_at,
                "orders": [{
                    "id": 1,
                    "created_at": now,
                    "years": years,
                    "start_at": today.isoformat(),
                    "end_at": expires_at,
                    "amount": amount,
                    "remark": remark,
                    "operator": operator,
                }],
                "created_at": now,
                "updated_at": now,
            }
            self.data["companies"].append(record)
            self._save()
            return dict(record), password

    def renew_company(self, prefix, years, amount=None, remark=None,
                      operator=None):
        """续费下单：未到期从当前到期日顺延，已到期/永久从今天起算。"""
        years = _valid_years(years)
        amount = _clean_amount(amount)
        remark = str(remark or "").strip() or None
        operator = str(operator or "").strip() or None
        with self.lock:
            self._reload()
            row = self._company_row(prefix)
            if not row:
                raise PlatformError("公司不存在")
            today = _today()
            current = _parse_date(row.get("expires_at"))
            start = max(current, today) if current else today
            end = _add_years(start, years)
            row["expires_at"] = end.isoformat()
            orders = row.setdefault("orders", [])
            orders.append({
                "id": max(
                    (order.get("id") or 0 for order in orders), default=0
                ) + 1,
                "created_at": iso_now(),
                "years": years,
                "start_at": start.isoformat(),
                "end_at": end.isoformat(),
                "amount": amount,
                "remark": remark,
                "operator": operator,
            })
            row["updated_at"] = iso_now()
            self._save()
            return dict(row)

    def company_orders(self, prefix):
        """返回公司原始行与订阅订单列表，供控制台详情接口。"""
        with self.lock:
            self._reload()
            row = self._company_row(prefix)
            if not row:
                raise PlatformError("公司不存在")
            return dict(row), [dict(order) for order in row["orders"]]

    def set_company_status(self, prefix, status):
        if status not in ("active", "disabled"):
            raise PlatformError("公司状态无效")
        with self.lock:
            self._reload()
            row = self._company_row(prefix)
            if not row:
                raise PlatformError("公司不存在")
            row["status"] = status
            row["updated_at"] = iso_now()
            self._save()
            return dict(row)

    def rename_company(self, prefix, name):
        name = str(name or "").strip()
        if not name or len(name) > MAX_COMPANY_NAME_LENGTH:
            raise PlatformError(f"公司名称必填且不超过{MAX_COMPANY_NAME_LENGTH}字")
        with self.lock:
            self._reload()
            row = self._company_row(prefix)
            if not row:
                raise PlatformError("公司不存在")
            row["name"] = name
            row["updated_at"] = iso_now()
            self._save()
            return dict(row)

    def reset_company_admin_password(self, prefix, password=None):
        """应急通道：重置公司管理员密码并清空其全部会话。"""
        with self.lock:
            self._reload()
            row = self._company_row(prefix)
            if not row:
                raise PlatformError("公司不存在")
            if password is None:
                password = generate_password()
            if not valid_password(password):
                raise PlatformError("密码须为8-20位，且同时包含字母和数字")

            connection = db.connect(row["db_path"])
            try:
                target = None
                if row.get("admin_user_id") is not None:
                    target = connection.execute(
                        "SELECT id, username, display_name FROM users WHERE id = ?",
                        (row["admin_user_id"],),
                    ).fetchone()
                if target is None:
                    target = connection.execute(
                        "SELECT id, username, display_name FROM users"
                        " WHERE role = 'admin' ORDER BY id LIMIT 1"
                    ).fetchone()
                if target is None:
                    raise PlatformError("该公司没有管理员账号")
                connection.execute(
                    """UPDATE users SET password_hash = ?, must_change_password = 1,
                       failed_attempts = 0, locked_until = NULL,
                       updated_at = ? WHERE id = ?""",
                    (hash_password(password), iso_now(), target["id"]),
                )
                connection.execute(
                    "DELETE FROM sessions WHERE user_id = ?", (target["id"],)
                )
                connection.commit()
            finally:
                connection.close()

            if row.get("admin_username") != target["username"]:
                row["admin_username"] = target["username"]
                row["admin_user_id"] = target["id"]
                row["updated_at"] = iso_now()
                self._save()
            return password

    # ---- 平台管理员与控制台会话 ----

    def ensure_platform_admin(self):
        """首启初始化平台管理员；返回一次性初始密码（未生成则返回 None）。"""
        with self.lock:
            self._reload()
            admin = self.data.get("platform_admin")
            if admin:
                return None
            username = os.environ.get("WMS_PLATFORM_ADMIN_USERNAME", "wanshun")
            display_name = os.environ.get(
                "WMS_PLATFORM_ADMIN_NAME", "平台管理员"
            )
            password = os.environ.get("WMS_PLATFORM_ADMIN_PASSWORD")
            generated = None
            if not password:
                password = generate_password()
                generated = password
            self.data["platform_admin"] = {
                "username": username,
                "display_name": display_name,
                "password_hash": hash_password(password),
                # 控制台归平台管理员本人所有：改密自愿，不做首登强制
                "must_change_password": 0,
                "failed_attempts": 0,
                "locked_until": None,
            }
            self.data["console_sessions"] = []
            self._save()
            return generated

    def _admin(self):
        admin = self.data.get("platform_admin")
        if not admin:
            raise PlatformError("平台管理员未初始化")
        return admin

    def admin_login(self, username, password):
        with self.lock:
            self._reload()
            admin = self._admin()
            generic_error = PlatformError("用户名或密码错误")
            if admin["username"] != (username or "").strip():
                raise generic_error

            now = parse_iso(iso_now())
            if admin.get("locked_until"):
                locked_until = parse_iso(admin["locked_until"])
                if locked_until > now:
                    remaining = int(
                        (locked_until - now).total_seconds() // 60
                    ) + 1
                    raise PlatformError(f"账号已锁定，请{remaining}分钟后重试")
                admin["failed_attempts"] = 0
                admin["locked_until"] = None

            if not verify_password(password or "", admin["password_hash"]):
                attempts = int(admin.get("failed_attempts", 0)) + 1
                admin["failed_attempts"] = attempts
                admin["locked_until"] = (
                    iso_in(minutes=10) if attempts >= 5 else None
                )
                self._save()
                if attempts >= 5:
                    raise PlatformError("账号已锁定，请10分钟后重试")
                raise generic_error

            admin["failed_attempts"] = 0
            admin["locked_until"] = None
            token = new_session_token()
            sessions = self.data.setdefault("console_sessions", [])
            sessions.append({
                "token_hash": token_hash(token),
                "expires_at": iso_in(24),
                "created_at": iso_now(),
            })
            sessions[:] = [
                row for row in sessions
                if row["expires_at"] >= iso_now()
            ]
            self._save()
            return token, {
                "username": admin["username"],
                "displayName": admin["display_name"],
                "mustChangePassword": bool(admin.get("must_change_password")),
            }

    def admin_for_token(self, token):
        if not token:
            return None
        with self.lock:
            self._reload()
            digest = token_hash(token)
            sessions = self.data.setdefault("console_sessions", [])
            match = None
            for row in sessions:
                if row["token_hash"] == digest and row["expires_at"] >= iso_now():
                    match = row
                    break
            if not match:
                return None
            return dict(self._admin())

    def admin_logout(self, token):
        with self.lock:
            self._reload()
            digest = token_hash(token)
            sessions = self.data.setdefault("console_sessions", [])
            sessions[:] = [row for row in sessions if row["token_hash"] != digest]
            self._save()

    def admin_change_password(self, token, old_password, new_password):
        with self.lock:
            self._reload()
            admin = self._admin()
            if not verify_password(old_password or "", admin["password_hash"]):
                raise PlatformError("原密码错误")
            if old_password == new_password:
                raise PlatformError("新密码不能与原密码相同")
            if not valid_password(new_password):
                raise PlatformError("密码须为8-20位，且同时包含字母和数字")
            admin["password_hash"] = hash_password(new_password)
            admin["must_change_password"] = 0
            digest = token_hash(token)
            sessions = self.data.setdefault("console_sessions", [])
            sessions[:] = [
                row for row in sessions if row["token_hash"] == digest
            ]
            self._save()
