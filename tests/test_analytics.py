from app.services.analytics_service import AnalyticsService


class FakeClient:
    def __init__(self, rows):
        self.rows = rows

    def select(self, table, params):
        return self.rows


def test_summary_ranks_top_sources_and_questions():
    rows = [
        {"question": "Nghỉ phép trước bao lâu?", "citations": [{"source": "Quy định nghỉ phép"}], "grounded": True},
        {"question": "nghỉ phép trước bao lâu?", "citations": [{"source": "Quy định nghỉ phép"}], "grounded": True},
        {"question": "Công tác phí sao tính?", "citations": [{"source": "Quy định công tác phí"}], "grounded": True},
        {"question": "Hi", "citations": [], "grounded": False},
    ]

    summary = AnalyticsService(FakeClient(rows)).summary(limit=5)

    assert summary["scanned_logs"] == 4
    assert summary["grounded_count"] == 3
    assert summary["top_sources"][0] == {"source": "Quy định nghỉ phép", "count": 2}
    assert summary["top_questions"][0]["count"] == 2
    assert summary["top_questions"][0]["question"] in ("Nghỉ phép trước bao lâu?", "nghỉ phép trước bao lâu?")


def test_summary_handles_empty_logs():
    summary = AnalyticsService(FakeClient([])).summary()

    assert summary == {"scanned_logs": 0, "grounded_count": 0, "top_sources": [], "top_questions": []}
