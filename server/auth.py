import re

from security import (
    iso_in,
    iso_now,
    new_session_token,
    parse_iso,
    token_hash,
    verify_password,
)


USERNAME_PATTERN = re.compile(r"^[a-z]{2,50}$")
PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")


def public_user(row):
    return {
        "id": row["id"],
        "username": row["username"],
        "displayName": row["display_name"],
        "contactPhone": row["contact_phone"],
        "role": row["role"],
        "status": row["status"],
        "mustChangePassword": bool(row["must_change_password"]),
        "createdAt": row["created_at"],
    }


def valid_username(username):
    return bool(USERNAME_PATTERN.fullmatch(username or ""))


def valid_phone(phone):
    return phone in (None, "") or bool(PHONE_PATTERN.fullmatch(phone or ""))


def login(connection, username, password):
    row = connection.execute(
        "SELECT * FROM users WHERE username = ?", ((username or "").strip(),)
    ).fetchone()
    generic_error = ValueError("用户名或密码错误")
    if row is None:
        raise generic_error

    now = parse_iso(iso_now())
    if row["locked_until"]:
        locked_until = parse_iso(row["locked_until"])
        if locked_until > now:
            remaining = int((locked_until - now).total_seconds() // 60) + 1
            raise ValueError(f"账号已锁定，请{remaining}分钟后重试")
        connection.execute(
            "UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE id = ?",
            (row["id"],),
        )
        connection.commit()

    if not verify_password(password or "", row["password_hash"]):
        attempts = int(row["failed_attempts"]) + 1
        locked_until = iso_in(minutes=10) if attempts >= 5 else None
        connection.execute(
            """UPDATE users
               SET failed_attempts = ?, locked_until = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (attempts, locked_until, row["id"]),
        )
        connection.commit()
        if attempts >= 5:
            raise ValueError("账号已锁定，请10分钟后重试")
        raise generic_error

    if row["status"] != "active":
        raise ValueError("账号已禁用，请联系管理员")

    connection.execute("DELETE FROM sessions WHERE expires_at < datetime('now')")
    token = new_session_token()
    connection.execute(
        """INSERT INTO sessions(token_hash, user_id, expires_at, created_at)
           VALUES (?, ?, ?, ?)""",
        (token_hash(token), row["id"], iso_in(24), iso_now()),
    )
    connection.execute(
        """UPDATE users SET failed_attempts = 0, locked_until = NULL,
           updated_at = datetime('now') WHERE id = ?""",
        (row["id"],),
    )
    connection.commit()
    return token, public_user(row)


def user_for_token(connection, token):
    if not token:
        return None
    row = connection.execute(
        """SELECT u.* FROM sessions s
           JOIN users u ON u.id = s.user_id
           WHERE s.token_hash = ? AND s.expires_at >= datetime('now')""",
        (token_hash(token),),
    ).fetchone()
    if not row or row["status"] != "active":
        return None
    return row


def logout(connection, token):
    connection.execute(
        "DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),)
    )
    connection.commit()


def change_password(connection, user, old_password, new_password, active_token=""):
    row = connection.execute(
        "SELECT * FROM users WHERE id = ?", (user["id"],)
    ).fetchone()
    if not verify_password(old_password or "", row["password_hash"]):
        raise ValueError("原密码错误")
    if old_password == new_password:
        raise ValueError("新密码不能与原密码相同")

    from security import hash_password, valid_password

    if not valid_password(new_password):
        raise ValueError("密码须为8-20位，且同时包含字母和数字")
    connection.execute(
        """UPDATE users SET password_hash = ?, must_change_password = 0,
           updated_at = ? WHERE id = ?""",
        (hash_password(new_password), iso_now(), user["id"]),
    )
    connection.execute(
        "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
        (user["id"], token_hash(active_token)),
    )
    connection.commit()
