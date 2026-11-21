"""Create the first administrator from a trusted interactive server terminal."""

import argparse
import os
import sys
from getpass import getpass

from dotenv import load_dotenv
from pydantic import TypeAdapter, ValidationError

from backend.auth import Account, Accounts, IndianMobile
from backend.db import Register
from backend.live import ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recover", action="store_true", help="Reset an existing administrator password from the trusted terminal.")
    args = parser.parse_args()
    if not sys.stdin.isatty():
        raise SystemExit("Use a trusted interactive terminal.")
    load_dotenv(ROOT / ".env")
    key = os.getenv("DATA_KEY")
    path = os.getenv("DATABASE_PATH")
    if not key or not path:
        raise SystemExit("Set the existing persistent DATABASE_PATH and DATA_KEY before bootstrapping.")
    accounts = Accounts(Register(ROOT / path, key.encode()))
    if args.recover:
        worker_id = input("Administrator ID: ").strip()
        password = getpass("New password: ")
        if password != getpass("Repeat password: "):
            raise SystemExit("Passwords must match.")
        try:
            replacement = input("Verified replacement mobile number (blank to keep the current number): ").strip()
            phone = TypeAdapter(IndianMobile).validate_python(replacement) if replacement else None
            accounts.recover_admin(worker_id, password, phone)
        except ValidationError:
            raise SystemExit("Enter a 10-digit Indian mobile number, with optional +91.") from None
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        print("Administrator password reset and previous sessions revoked.")
        return
    try:
        administrator = Account(
            full_name=input("Full name: "), phone=input("Mobile number: "),
            village=input("Assigned area: "), password=getpass("Password (at least 12 characters): "),
            confirm_password=getpass("Repeat password: "),
        )
    except ValidationError:
        raise SystemExit("The account fields did not pass validation.") from None
    try:
        worker_id = accounts.create(administrator, bootstrap=True)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    print(f"Administrator created. Sign in with the mobile number. Recovery ID: {worker_id}")


if __name__ == "__main__":
    main()
