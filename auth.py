"""Login. Passwords are stored only as salted PBKDF2 hashes, never in plain text."""
import hashlib
import hmac
import datetime as dt
from urllib.parse import quote

ADMIN_EMAIL = "divyansh.sharma0990@gmail.com"
TEMP_DAYS = 30

# Permanent accounts (never expire)
PERMANENT = {
    "admin": {"salt": "7605e4b359a84f0a9460cb1cf0ff7bc4",
              "hash": "d903d75b53f9c4263435ff2cf949ebd7d2e8ae4290390c1835eac08803248eb9"},
    "charu": {"salt": "96e5db789c37f218777ce88a47374e4f",
              "hash": "4af91c100cab90b5aec3d239967177ea65ad62b9fb59684f5486871cae68354b"},
}

# 30-day accounts: generate each line with  python add_user.py <username>
# Expired entries are rejected automatically; delete the old lines whenever you like.
TEMP_USERS = [
]


def _hash(pw, salt_hex):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), 200_000).hex()


def login(username, password):
    """Return (ok, display_name, days_left). days_left is None for permanent users."""
    u = username.strip().lower()
    rec, days_left = PERMANENT.get(u), None
    if rec is None:
        for t in TEMP_USERS:
            if t["username"].lower() == u:
                age = (dt.date.today() - dt.date.fromisoformat(t["created"])).days
                if age < TEMP_DAYS:
                    rec, days_left = t, TEMP_DAYS - age
                break
    salt = rec["salt"] if rec else "00" * 16
    good = hmac.compare_digest(_hash(password, salt), rec["hash"] if rec else "0" * 64)
    if rec and good:
        return True, u.title(), days_left
    return False, None, None


def access_request_link(username):
    body = f"Hello,\n\nPlease create an OriginCheck account for me.\n\nRequested username: {username}\n\nThank you."
    return f"mailto:{ADMIN_EMAIL}?subject={quote('OriginCheck access request')}&body={quote(body)}"
