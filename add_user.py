"""Create a 30-day user.   Usage:  python add_user.py rahul
Prints the password to send to the user and the line to paste into TEMP_USERS in auth.py."""
import sys, os, secrets, hashlib, datetime

if len(sys.argv) < 2:
    sys.exit("Usage: python add_user.py <username>")
user = sys.argv[1].strip().lower()
pw = secrets.token_urlsafe(8)
salt = os.urandom(16)
h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000).hex()
print(f"\nSend to user ->  username: {user}   password: {pw}\n")
print("Paste this line inside TEMP_USERS in auth.py:\n")
print(f'    {{"username": "{user}", "salt": "{salt.hex()}", "hash": "{h}", "created": "{datetime.date.today()}"}},\n')
