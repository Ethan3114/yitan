from datetime import datetime
from typing import Any

import httpx


class CloudDatabaseError(RuntimeError):
    def __init__(self, status: int, code: str):
        self.status = status
        self.code = code
        super().__init__("Cloud database request failed")


class CloudRepository:
    def __init__(self, env_id: str, api_key: str):
        if not env_id or not api_key or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in env_id):
            raise ValueError("CLOUDBASE_ENV_ID and CLOUDBASE_API_KEY are required")
        self.client = httpx.Client(
            base_url=f"https://{env_id}.api.tcloudbasegateway.com/v1/rdb/rest/",
            headers={"Authorization": "Bearer " + api_key, "Accept": "application/json"},
            timeout=httpx.Timeout(30, connect=5), follow_redirects=False,
        )

    def close(self):
        self.client.close()

    def request(self, method: str, resource: str, *, params=None, payload=None):
        if not resource.startswith("yitan_") and not resource.startswith("rpc/yitan_"):
            raise ValueError("Cloud repository is restricted to yitan resources")
        response = self.client.request(method, resource, params=params, json=payload, headers={"Prefer": "return=representation"})
        if response.status_code not in {200, 201, 204, 206}:
            body = response.json()
            code = body.get("code", "cloud_database_error") if isinstance(body, dict) else "cloud_database_error"
            code = code.removeprefix("DATABASE_")
            raise CloudDatabaseError(response.status_code, code)
        return response.json() if response.content else None

    def rows(self, table: str, *, filters: dict[str, str] | None = None, columns: str = "*", order: str = "created_at.asc", page_size: int = 500):
        offset = 0
        while True:
            params = {"select": columns, "order": order, "limit": str(page_size), "offset": str(offset), **(filters or {})}
            batch = self.request("GET", "yitan_" + table, params=params)
            if not isinstance(batch, list):
                raise TypeError("Cloud database must return rows")
            yield from batch
            if len(batch) < page_size:
                break
            offset += len(batch)

    def one(self, table: str, *, filters: dict[str, str], columns: str = "*") -> dict[str, Any] | None:
        rows = self.request("GET", "yitan_" + table, params={"select": columns, "limit": "2", **filters})
        if not isinstance(rows, list) or len(rows) > 1:
            raise ValueError("Expected at most one cloud record")
        return rows[0] if rows else None

    def insert(self, table: str, row: dict):
        return self.request("POST", "yitan_" + table, payload=row)

    def update(self, table: str, filters: dict[str, str], values: dict):
        if not filters:
            raise ValueError("Update requires explicit filters")
        return self.request("PATCH", "yitan_" + table, params=filters, payload=values)

    def remove(self, table: str, filters: dict[str, str]):
        if not filters:
            raise ValueError("Deletion requires explicit filters")
        return self.request("DELETE", "yitan_" + table, params=filters)

    def rpc(self, name: str, payload: dict):
        return self.request("POST", "rpc/yitan_" + name, payload=payload)

    def owner_from_session(self, session_id: str, now: datetime) -> str | None:
        session = self.one("sessions", filters={"id": "eq." + session_id, "expires_at": "gt." + now.isoformat()})
        if session is None:
            return None
        account = self.one("accounts", filters={"owner_id": "eq." + session["owner_id"], "enabled": "eq.true"}, columns="owner_id")
        return account["owner_id"] if account else None
