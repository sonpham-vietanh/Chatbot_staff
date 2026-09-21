import pytest

from app.services.api_key_service import ApiKeyNotFoundError, ApiKeyService


class FakeClient:
    def __init__(self):
        self.rows = []
        self._next_id = 1

    def select(self, table, params):
        rows = self.rows
        if params.get("key"):
            wanted = params["key"].removeprefix("eq.")
            rows = [r for r in rows if r["key"] == wanted]
        if params.get("status"):
            wanted = params["status"].removeprefix("eq.")
            rows = [r for r in rows if r["status"] == wanted]
        return list(rows)

    def insert(self, table, rows, returning=True):
        for row in rows:
            row = {**row, "id": str(self._next_id), "status": "active", "created_at": "2026-01-01T00:00:00Z", "last_used_at": None}
            self._next_id += 1
            self.rows.append(row)
        return rows if not returning else self.rows[-len(rows):]

    def update(self, table, params, patch):
        key_id = params["id"].removeprefix("eq.")
        matched = [r for r in self.rows if r["id"] == key_id]
        for row in matched:
            row.update(patch)
        return matched

    def delete(self, table, params):
        key_id = params["id"].removeprefix("eq.")
        self.rows = [r for r in self.rows if r["id"] != key_id]


def test_create_key_has_prefix_and_is_active():
    service = ApiKeyService(FakeClient())
    key = service.create_key("Intranet HR", "https://intranet.truongvietanh.com/")

    assert key["key"].startswith("vas_")
    assert key["status"] == "active"
    assert key["allowed_origin"] == "https://intranet.truongvietanh.com"  # trailing slash bị cắt


def test_get_active_key_ignores_revoked():
    client = FakeClient()
    service = ApiKeyService(client)
    created = service.create_key("Intranet HR", "https://intranet.truongvietanh.com")

    assert service.get_active_key(created["key"]) is not None

    service.revoke_key(created["id"])
    assert service.get_active_key(created["key"]) is None


def test_revoke_missing_key_raises():
    service = ApiKeyService(FakeClient())
    with pytest.raises(ApiKeyNotFoundError):
        service.revoke_key("does-not-exist")


def test_delete_key_removes_it():
    client = FakeClient()
    service = ApiKeyService(client)
    created = service.create_key("Intranet HR", "https://intranet.truongvietanh.com")

    service.delete_key(created["id"])

    assert service.list_keys() == []
