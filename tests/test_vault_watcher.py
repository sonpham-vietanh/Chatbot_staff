import asyncio
import json
from pathlib import Path

from app.services.vault_watcher import VaultWatcher

READY = ".drive-sync-ready"


class FakeIngestAgent:
    def __init__(self):
        self.runs = []

    async def run(self, source_path):
        self.runs.append(source_path.name)
        return {"is_error": False, "result": "ok"}


class FakeWikiSync:
    def __init__(self):
        self.calls = 0

    def sync_all(self, wipe=False, status="approved"):
        self.calls += 1
        return {"created": 0, "failed": 0, "total": 0}


def make_vault(tmp_path, raw_names):
    vault = tmp_path / "vault"
    (vault / "nhan-su" / "raw").mkdir(parents=True)
    (vault / "nhan-su" / "wiki").mkdir()
    (vault / "can-xu-ly.md").write_text("# Cần xử lý\n", encoding="utf-8")
    for name in raw_names:
        (vault / "nhan-su" / "raw" / name).write_text("nguồn", encoding="utf-8")
    return vault


def make_watcher(tmp_path, vault, ready_marker=READY):
    agent, sync = FakeIngestAgent(), FakeWikiSync()
    watcher = VaultWatcher(str(vault), agent, sync, tmp_path / "state.json", ready_marker=ready_marker)
    return watcher, agent, sync


def test_watcher_waits_for_ready_marker_before_baselining(tmp_path):
    """drive-sync đang kéo vault về dở dang: watcher không được chốt baseline, nếu không
    mọi file raw/ đến sau sẽ bị coi là nguồn mới và chạy Ingest Agent hàng loạt."""
    vault = make_vault(tmp_path, ["a.docx"])
    watcher, agent, _ = make_watcher(tmp_path, vault)

    asyncio.run(watcher._tick())
    assert not (tmp_path / "state.json").exists()

    (vault / "nhan-su" / "raw" / "b.docx").write_text("nguồn", encoding="utf-8")  # phần còn lại về sau
    (vault / READY).touch()
    asyncio.run(watcher._tick())

    assert agent.runs == []
    assert len(watcher._state["known_raw"]) == 2

    (vault / "nhan-su" / "raw" / "c.docx").write_text("nguồn mới thật", encoding="utf-8")
    asyncio.run(watcher._tick())

    assert agent.runs == ["c.docx"]


def test_watcher_rebaselines_state_taken_before_vault_was_ready(tmp_path):
    """State cũ đã 'baseline_done' trên volume rỗng (trước khi có drive-sync): khi vault
    thật về, phải chốt lại baseline chứ không ingest lại toàn bộ dữ liệu sẵn có."""
    vault = make_vault(tmp_path, ["a.docx", "b.docx", "c.docx"])
    (vault / READY).touch()
    (tmp_path / "state.json").write_text(
        json.dumps({"known_raw": [], "snapshot": {}, "baseline_done": True}), encoding="utf-8"
    )
    watcher, agent, sync = make_watcher(tmp_path, vault)

    asyncio.run(watcher._tick())

    assert agent.runs == []
    assert sync.calls == 0
    assert len(watcher._state["known_raw"]) == 3
    assert watcher._state["baseline_after_ready"] is True


def test_sync_failure_does_not_rerun_ingest_agent_on_next_poll(tmp_path):
    """Supabase lỗi lúc đồng bộ không được làm nguồn bị coi là 'chưa xử lý' — nếu không
    Ingest Agent (tốn phí thật) chạy lại mỗi 30 giây chừng nào Supabase còn lỗi."""
    vault = make_vault(tmp_path, ["a.docx"])
    watcher, agent, sync = make_watcher(tmp_path, vault, ready_marker=None)
    asyncio.run(watcher._tick())  # baseline

    def broken_sync(wipe=False, status="approved"):
        raise RuntimeError("supabase down")

    sync.sync_all = broken_sync
    (vault / "nhan-su" / "raw" / "b.docx").write_text("nguồn mới", encoding="utf-8")
    for _ in range(3):
        asyncio.run(watcher._tick())

    assert agent.runs == ["b.docx"]


def test_clean_ingest_is_synced_even_if_can_xu_ly_changed_earlier(tmp_path):
    """Chỉ thay đổi can-xu-ly.md do CHÍNH lần ingest này gây ra mới chặn đồng bộ; người
    sửa tay file đó từ trước thì không liên quan."""
    vault = make_vault(tmp_path, ["a.docx"])
    watcher, agent, sync = make_watcher(tmp_path, vault, ready_marker=None)
    asyncio.run(watcher._tick())  # baseline

    (vault / "can-xu-ly.md").write_text("# Cần xử lý\n- đã xoá 1 dòng\n", encoding="utf-8")
    (vault / "nhan-su" / "raw" / "b.docx").write_text("nguồn mới", encoding="utf-8")
    asyncio.run(watcher._tick())

    assert agent.runs == ["b.docx"]
    assert sync.calls == 1


def test_junk_and_half_downloaded_files_in_raw_are_not_ingested(tmp_path):
    vault = make_vault(tmp_path, ["a.docx"])
    watcher, agent, _ = make_watcher(tmp_path, vault, ready_marker=None)
    asyncio.run(watcher._tick())  # baseline

    raw = vault / "nhan-su" / "raw"
    for name in ["~$bao-cao.docx", "bao-cao.docx.a1b2c3d4.partial", ".DS_Store", "Thumbs.db", "desktop.ini"]:
        (raw / name).write_text("rác", encoding="utf-8")
    (raw / ".tmp.driveupload").mkdir()
    (raw / ".tmp.driveupload" / "1001").write_text("rác", encoding="utf-8")
    (raw / "assets").mkdir()
    (raw / "assets" / "hinh.png").write_text("ảnh", encoding="utf-8")
    (raw / "bao-cao.docx").write_text("nguồn thật", encoding="utf-8")
    asyncio.run(watcher._tick())

    assert agent.runs == ["bao-cao.docx"]


def test_state_file_with_wrong_shape_is_rebuilt_instead_of_crashing_every_tick(tmp_path):
    vault = make_vault(tmp_path, ["a.docx"])
    for broken in ("null", "[]", '{"baseline_done": true, "known_raw": null, "snapshot": null}'):
        (tmp_path / "state.json").write_text(broken, encoding="utf-8")
        watcher, agent, _ = make_watcher(tmp_path, vault, ready_marker=None)

        asyncio.run(watcher._tick())

        assert agent.runs == []
        assert watcher._state["known_raw"] == [str(Path("nhan-su") / "raw" / "a.docx")]


def test_watcher_without_marker_configured_keeps_old_behaviour(tmp_path):
    vault = make_vault(tmp_path, ["a.docx"])
    watcher, agent, _ = make_watcher(tmp_path, vault, ready_marker=None)

    asyncio.run(watcher._tick())  # baseline
    (vault / "nhan-su" / "raw" / "b.docx").write_text("nguồn mới", encoding="utf-8")
    asyncio.run(watcher._tick())

    assert agent.runs == ["b.docx"]
