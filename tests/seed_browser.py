"""Seed a worker and administrator in a disposable browser test database only."""

import os
from pathlib import Path

from backend.auth import Account, Accounts
from backend.db import Register, demo_key

path = Path(os.environ["DATABASE_PATH"])
if not path.is_relative_to("/tmp") or path.exists():
    raise SystemExit("Use a new database under /tmp for the browser tests.")
accounts = Accounts(Register(path, demo_key(path.with_suffix(".key"))))
password = os.environ["ASHA_TEST_PASSWORD"]
profile = dict(full_name="Synthetic Worker", phone="9876543210", village="Test area",
               password=password, confirm_password=password)
accounts.create(Account(worker_id="BROWSER-WORKER", **profile))
worker, = accounts.list_workers()
accounts.manage_worker(worker["id"], "active", profile["village"])
accounts.create(Account(worker_id="BROWSER-ADMIN", **{**profile, "full_name": "Synthetic Administrator"}), bootstrap=True)
