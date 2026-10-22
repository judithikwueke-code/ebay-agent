"""
Add or update an Amazon account for auto-ordering (prompts without echoing secrets).
    python add_amazon_account.py            # on the server, in the agent folder
Writes amazon_accounts.json with owner-only permissions. Use a DEDICATED Amazon account (Prime).
"""
import getpass
import json
import os
import re
import sys

from auto_order import ACCOUNTS_FILE, totp, _load_accounts, _load_state, _save_state


def main():
    name = input("Account label (e.g. acc1): ").strip()
    email = input("Amazon email: ").strip()
    password = getpass.getpass("Amazon password (hidden): ")
    secret = getpass.getpass("2FA setup key (the long key under 'Can't scan the barcode', hidden): ")
    secret = re.sub(r"\s+", "", secret).upper()
    try:
        print("Check: current code from the key is", totp(secret), "- it should match your authenticator app.")
    except Exception as e:
        print("That key doesn't look valid:", e)
        sys.exit(1)
    accounts = [a for a in _load_accounts() if a["name"] != name]
    accounts.append({"name": name, "email": email, "password": password, "totp_secret": secret, "enabled": True})
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(accounts, f, indent=1)
    try:
        os.chmod(ACCOUNTS_FILE, 0o600)
    except Exception:
        pass
    st = _load_state()
    st.pop(name, None)
    _save_state(st)
    print(f"Saved account '{name}' ({len(accounts)} configured). Set AMAZON_AUTO_ORDER=dry in .env to test.")


if __name__ == "__main__":
    main()
