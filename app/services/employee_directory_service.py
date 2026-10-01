from app.services.supabase_client import SupabaseClient


class EmployeeDirectoryService:
    """HR-maintained active employee roster; never ingested into the RAG index."""

    def __init__(self, client: SupabaseClient):
        self.client = client

    def get_active_employee(self, email: str) -> dict | None:
        normalized_email = email.strip().casefold()
        if not normalized_email:
            return None
        rows = self.client.select(
            "employee_directory",
            {
                "select": "email,display_name,department,job_title,employment_start_date",
                "email": f"eq.{normalized_email}",
                "active": "eq.true",
                "limit": "1",
            },
        )
        return rows[0] if rows else None