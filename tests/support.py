"""Authenticated HTTPS client for pre-existing workflow regressions."""

from fastapi.testclient import TestClient as BaseClient

from backend.auth import Account

ORIGIN = "https://testserver"
PASSWORD = "synthetic-regression-password"
WORKER = {
    "full_name": "Synthetic Worker", "phone": "9876543210",
    "village": "Synthetic Area", "password": PASSWORD, "confirm_password": PASSWORD,
}


class TestClient(BaseClient):
    def __init__(self, app):
        super().__init__(app, base_url=ORIGIN, headers={"Origin": ORIGIN})

    def __enter__(self):
        super().__enter__()
        accounts = self.app.state.accounts
        accounts.create(Account.model_validate(WORKER))
        user, = accounts.list_workers()
        accounts.manage_worker(user["id"], "active", user["village"])
        assert self.post("/api/auth/login", json={"phone": WORKER["phone"], "password": PASSWORD}).status_code == 200
        return self

    def websocket_connect(self, url, **kwargs):
        if url.startswith("/"):
            url = "wss://testserver" + url
        return super().websocket_connect(url, **kwargs)
