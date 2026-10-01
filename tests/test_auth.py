import pytest
from fastapi import HTTPException

from types import SimpleNamespace
import base64
import json

from app.api.routes import _require_verified_company_user, _viewer_department_role, auth_config, require_user
from app.services.auth_service import AuthError, AuthService, is_public_auth_key
from app.services.employee_directory_service import EmployeeDirectoryService


class FakeSupabaseClient:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error
        self.query = None

    def select(self, table, params):
        self.query = (table, params)
        if self.error:
            raise self.error
        return self.rows


def test_auth_service_accepts_publishable_and_legacy_anon_keys_only():
    claims = base64.urlsafe_b64encode(json.dumps({"role": "anon"}).encode()).decode().rstrip("=")
    legacy_anon_key = f"eyJhbGci. {claims}.signature".replace(" ", "")

    assert is_public_auth_key("sb_publishable_example")
    assert is_public_auth_key(legacy_anon_key)
    assert not is_public_auth_key("sb_secret_example")
    assert not is_public_auth_key(None)


def test_auth_service_never_sends_secret_key_to_supabase():
    service = AuthService("https://project.supabase.co", "sb_secret_example")

    with pytest.raises(AuthError, match="anon/publishable key"):
        service._headers()


def test_auth_config_does_not_expose_a_secret_key():
    config = auth_config(SimpleNamespace(
        supabase_url="https://project.supabase.co",
        supabase_anon_key="sb_secret_example",
        allowed_email_domains="company.org",
    ))

    assert config == {"enabled": False, "supabase_url": None, "supabase_anon_key": None}


def test_directory_normalizes_email_and_selects_only_active_profile_fields():
    client = FakeSupabaseClient(rows=[{
        "email": "staff@example.org",
        "display_name": "Nhân viên",
        "department": "HR",
        "job_title": "Chuyên viên",
        "employment_start_date": "2024-01-02",
    }])
    directory = EmployeeDirectoryService(client)

    employee = directory.get_active_employee(" Staff@Example.org ")

    assert employee["email"] == "staff@example.org"
    assert client.query == (
        "employee_directory",
        {
            "select": "email,display_name,department,job_title,employment_start_date",
            "email": "eq.staff@example.org",
            "active": "eq.true",
            "limit": "1",
        },
    )


def test_directory_returns_none_for_missing_email_or_inactive_employee():
    client = FakeSupabaseClient()
    directory = EmployeeDirectoryService(client)

    assert directory.get_active_employee("  ") is None
    assert directory.get_active_employee("staff@example.org") is None


def test_unverified_email_is_denied():
    with pytest.raises(HTTPException) as error:
        _require_verified_company_user({"email": "staff@company.org"}, SimpleNamespace(allowed_email_domains="company.org"))

    assert error.value.status_code == 403


def test_verified_company_user_can_access_without_hr_roster_entry():
    directory = EmployeeDirectoryService(FakeSupabaseClient())

    class FakeAuthService:
        def get_user(self, token):
            assert token == "access-token"
            return {
                "id": "user-1",
                "email": "staff@company.org",
                "email_confirmed_at": "2024-01-02T00:00:00Z",
                "user_metadata": {"name": "Staff"},
            }

    user = require_user(
        "Bearer access-token",
        FakeAuthService(),
        directory,
        SimpleNamespace(allowed_email_domains="company.org"),
    )

    assert user["employee_profile"] is None


def test_verified_email_outside_company_domain_is_denied():
    with pytest.raises(HTTPException) as error:
        _require_verified_company_user(
            {"email": "staff@outside.org", "email_confirmed_at": "2024-01-02T00:00:00Z"},
            SimpleNamespace(allowed_email_domains="company.org"),
        )

    assert error.value.status_code == 403


def test_missing_company_domain_allowlist_fails_closed():
    with pytest.raises(HTTPException) as error:
        _require_verified_company_user(
            {"email": "staff@company.org", "email_confirmed_at": "2024-01-02T00:00:00Z"},
            SimpleNamespace(allowed_email_domains=""),
        )

    assert error.value.status_code == 503


def test_protected_request_attaches_roster_profile_when_available():
    employee = {
        "email": "staff@example.org",
        "display_name": "Nhân viên",
        "department": "HR",
        "job_title": "Chuyên viên",
        "employment_start_date": "2024-01-02",
    }
    directory = EmployeeDirectoryService(FakeSupabaseClient(rows=[employee]))

    class FakeAuthService:
        def get_user(self, token):
            assert token == "access-token"
            return {"email": "staff@example.org", "email_confirmed_at": "2024-01-02T00:00:00Z"}

    user = require_user(
            "Bearer access-token",
            FakeAuthService(),
            directory,
            SimpleNamespace(allowed_email_domains="example.org"),
    )

    assert user["employee_profile"] == employee


def test_department_comes_from_hr_profile_not_user_editable_metadata():
    user = {
        "employee_profile": {"department": "HR"},
        "user_metadata": {"department": "Finance", "role": "admin"},
    }

    assert _viewer_department_role(user) == ("HR", "staff")


def test_directory_failure_does_not_block_verified_company_login():
    directory = EmployeeDirectoryService(FakeSupabaseClient(error=RuntimeError("database unavailable")))

    class FakeAuthService:
        def get_user(self, token):
            return {"email": "staff@example.org", "email_confirmed_at": "2024-01-02T00:00:00Z"}

    user = require_user(
        "Bearer access-token",
        FakeAuthService(),
        directory,
        SimpleNamespace(allowed_email_domains="example.org"),
    )

    assert user["employee_profile"] is None