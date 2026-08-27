import base64
import hashlib
import hmac
import os
import re
from datetime import datetime, timedelta, timezone


PBKDF2_ITERATIONS = 120000
PASSWORD_PATTERN = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).{8,20}$")


def hash_password(password, iterations=PBKDF2_ITERATIONS):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return "$".join((
        "pbkdf2_sha256",
        str(iterations),
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    ))


def verify_password(password, encoded):
    try:
        algorithm, iterations_text, salt_text, expected_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        salt = base64.b64decode(salt_text)
        expected = base64.b64decode(expected_text)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def valid_password(password):
    return bool(PASSWORD_PATTERN.fullmatch(password or ""))


def new_session_token():
    return base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode("ascii")


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def iso_in(hours=0, minutes=0):
    return (datetime.now(timezone.utc) + timedelta(hours=hours, minutes=minutes)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def parse_iso(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
