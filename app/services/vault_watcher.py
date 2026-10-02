"""Theo doi vault Obsidian (dong bo xuong server qua Google Drive - rclone bisync
chay o service rieng, xem drive-sync/) de tu dong kich hoat Ingest Agent khi co
nguon moi trong raw/, va tu phat hien wiki/ bi sua tay ngoai luong (khong phai do
chinh Agent vua chay) de ghi log canh bao thay vi am tham bo qua. Thay the hoan
toan cho endpoint /api/leader/submit truoc day - leader gio chi thao tac trong
Obsidian, khong can web form nao ca.

Dung polling (khong dung OS file-event nhu watchdog/inotify) vi thu muc vault la
mot Docker volume duoc drive-sync ghi vao tu 1 container/tien trinh khac - khong
dam bao inotify/ReadDirectoryChanges bao chinh xac tren moi nen tang/filesystem overlay.

QUAN TRONG: uvicorn chay nhieu worker process (UVICORN_WORKERS), moi worker se tu
tao 1 instance FastAPI app + 1 VaultWatcher rieng qua lifespan - neu khong khoa lai,
N worker se cung poll va cung goi Ingest Agent cho cung 1 file (da xac nhan bang
test that: 4 worker -> 4 loi giong het nhau cho cung 1 nguon). Dung file lock
(fcntl, POSIX) de dam bao chi dung 1 worker duy nhat thuc su chay vong lap."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.ingest_agent import IngestAgent
from app.services.wiki_sync_service import WikiSyncService

try:
    import fcntl
except ImportError:  # Windows (dev local) - khong co fcntl, khong multi-worker nen bo qua khoa
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

CONTROL_FILES = ["index.md", "log.md", "can-xu-ly.md", "CLAUDE.md"]

# Cau truc vault tu 2026-09-24: moi domain (nhan-su, tai-chinh...) la 1 THU MUC GOC
# rieng (co raw/ + wiki/ rieng), "core/" la thu muc dung chung khong co raw/. Cac
# thu muc khac o goc vault khong phai domain (git, config Obsidian, scaffold cong
# cu AI khac...) - xem CLAUDE.md muc 1.
NON_DOMAIN_DIRS = {"core", ".git", ".obsidian", ".claude", ".opencode", ".copilot", "copilot"}


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _domain_dirs(vault: Path) -> list[Path]:
    if not vault.is_dir():
        return []
    return [
        p for p in vault.iterdir()
        if p.is_dir() and p.name not in NON_DOMAIN_DIRS and not p.name.startswith(".")
    ]


def _wiki_snapshot(vault: Path) -> dict[str, str]:
    snap: dict[str, str] = {}
    for name in CONTROL_FILES:
        p = vault / name
        if p.is_file():
            snap[name] = _hash_file(p)
    core_dir = vault / "core"
    if core_dir.is_dir():
        for p in sorted(core_dir.rglob("*.md")):
            snap[str(p.relative_to(vault))] = _hash_file(p)
    for domain_dir in _domain_dirs(vault):
        wiki_dir = domain_dir / "wiki"
        if wiki_dir.is_dir():
            for p in sorted(wiki_dir.rglob("*.md")):
                snap[str(p.relative_to(vault))] = _hash_file(p)
    return snap


IGNORED_RAW_NAMES = {"thumbs.db", "desktop.ini"}
IGNORED_RAW_SUFFIXES = (".partial", ".tmp", ".crdownload")


def _is_source_file(raw_dir: Path, path: Path) -> bool:
    """Loai file khong phai nguon that: anh dinh kem, file an, file khoa cua Office
    ("~$ten.docx" sinh ra khi dang mo file), va file dang tai do (rclone ghi tam
    "<ten>.<hash>.partial" roi moi doi ten) - khong co dong nay thi moi lan poll trung
    luc do se chay Ingest Agent (ton phi that) tren 1 file rac/chua tai xong."""
    parts = path.relative_to(raw_dir).parts
    if "assets" in parts or any(part.startswith(".") for part in parts):
        return False
    name = path.name.lower()
    return not (name.startswith("~$") or name in IGNORED_RAW_NAMES or name.endswith(IGNORED_RAW_SUFFIXES))


def _raw_files(vault: Path) -> set[str]:
    result: set[str] = set()
    for domain_dir in _domain_dirs(vault):
        raw_dir = domain_dir / "raw"
        if not raw_dir.is_dir():
            continue
        for p in raw_dir.rglob("*"):
            if p.is_file() and _is_source_file(raw_dir, p):
                result.add(str(p.relative_to(vault)))
    return result


class VaultWatcher:
    def __init__(
        self,
        vault_path: str,
        ingest_agent: IngestAgent,
        wiki_sync: WikiSyncService,
        state_path: Path,
        poll_interval: int = 30,
        ready_marker: str | None = None,
    ):
        self.vault = Path(vault_path)
        self.ingest_agent = ingest_agent
        self.wiki_sync = wiki_sync
        self.state_path = state_path
        self.poll_interval = poll_interval
        self.ready_marker = ready_marker
        """Ten file (tinh tu goc vault) ma drive-sync tao ra SAU KHI keo xong vault lan
        dau (xem drive-sync/entrypoint.sh). Co cau hinh thi watcher dung im cho toi khi
        file nay xuat hien; None (dev local, khong co drive-sync) = khong cho."""
        self._task: asyncio.Task | None = None
        self._lock_file = None
        self._state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if self.state_path.is_file():
            try:
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
                # JSON hop le nhung sai hinh dang (null, [], known_raw = null...) cung phai
                # coi nhu hong - neu khong moi tick deu nem loi va watcher chet han.
                if (isinstance(state, dict) and isinstance(state.get("known_raw", []), list)
                        and isinstance(state.get("snapshot", {}), dict)):
                    return state
                logger.error("Vault watcher state sai dinh dang, khoi tao lai tu dau")
            except Exception:
                logger.exception("Khong doc duoc vault watcher state, khoi tao lai tu dau")
        return {"known_raw": [], "snapshot": {}}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")

    def start(self) -> None:
        if self._task is not None:
            return

        if fcntl is not None:
            lock_path = self.state_path.parent / "vault_watcher.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_file = open(lock_path, "w")
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                # Worker khac (trong so nhieu uvicorn worker) da giu lock nay -
                # KHONG khoi dong vong lap o day, tranh N worker cung goi Ingest
                # Agent cho cung 1 file (da gap that trong test da worker).
                logger.info("Vault watcher: worker khac da giu lock, bo qua o worker nay.")
                lock_file.close()
                return
            self._lock_file = lock_file  # giu file mo suot vong doi process de giu lock

        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._lock_file is not None:
            fcntl.flock(self._lock_file, fcntl.LOCK_UN)
            self._lock_file.close()
            self._lock_file = None

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except Exception:
                logger.exception("Vault watcher tick that bai")
            await asyncio.sleep(self.poll_interval)

    def _hash_can_xu_ly(self) -> str | None:
        p = self.vault / "can-xu-ly.md"
        return _hash_file(p) if p.is_file() else None

    async def _tick(self) -> None:
        if not self.vault.is_dir():
            return
        if self.ready_marker and not (self.vault / self.ready_marker).is_file():
            # drive-sync chua keo xong vault lan dau - vault co the dang rong hoac moi
            # co 1 nua. Chot baseline luc nay thi moi file raw/ den sau se bi coi la
            # "nguon moi" va chay Ingest Agent hang loat.
            return

        # Baseline chot TRUOC khi co ready marker (state cu, vd watcher tung chay tren
        # volume rong) khong dang tin - chot lai 1 lan khi vault da san sang that.
        baseline_is_stale = bool(self.ready_marker) and not self._state.get("baseline_after_ready")
        if not self._state.get("baseline_done") or baseline_is_stale:
            # Lan chay dau tien cua watcher: vault co the da co san rat nhieu file
            # raw/ va trang wiki/ tu truoc (vd 15 nguon HR da ingest bang tay).
            # Chi thiet lap baseline - KHONG duoc coi chung la "moi"/"sua tay" va
            # tu dong xu ly, neu khong se chay lai Agent tren toan bo du lieu cu
            # (ton phi that + tao trang trung lap) va bao "sua tay" gia mao hang loat.
            self._state["known_raw"] = sorted(_raw_files(self.vault))
            self._state["snapshot"] = _wiki_snapshot(self.vault)
            self._state["can_xu_ly_hash"] = self._hash_can_xu_ly()
            self._state["baseline_done"] = True
            self._state["baseline_after_ready"] = bool(self.ready_marker)
            self._save_state()
            return

        known_raw = set(self._state.get("known_raw", []))
        new_raw = sorted(_raw_files(self.vault) - known_raw)

        if new_raw:
            # Xu ly tung file mot (khong chay song song) de tranh 2 lan chay Agent
            # cung luc dung tay vao 1 vault.
            for rel_path in new_raw:
                await self._process_new_source(rel_path)
                # Ghi nhan NGAY tung nguon da xu ly: neu buoc sau do loi (hoac process
                # bi tat giua chung), lan poll ke tiep khong duoc chay lai Ingest Agent
                # (ton phi that) tren nguon da xu ly roi.
                known_raw.add(rel_path)
                self._state["known_raw"] = sorted(known_raw)
                self._save_state()
        else:
            prev_snapshot = self._state.get("snapshot", {})
            self._flag_unexpected_changes(prev_snapshot, _wiki_snapshot(self.vault))

        self._state["known_raw"] = sorted(_raw_files(self.vault))
        self._state["snapshot"] = _wiki_snapshot(self.vault)
        self._save_state()

    async def _process_new_source(self, rel_path: str) -> None:
        source_path = self.vault / rel_path
        logger.info("Vault watcher: phat hien nguon moi %s, chay Ingest Agent", rel_path)
        # Lay moc can-xu-ly.md NGAY TRUOC khi Agent chay: so voi moc luu tu baseline/lan
        # ingest truoc thi moi thay doi khac o giua (nguoi sua tay, chinh watcher ghi canh
        # bao sua tay) deu bi tinh nham la "nguon nay gay mau thuan" va chan dong bo.
        prev_hash = self._hash_can_xu_ly()
        try:
            outcome = await self.ingest_agent.run(source_path)
        except Exception:
            logger.exception("Ingest Agent loi khi xu ly %s", rel_path)
            return

        if outcome.get("is_error"):
            logger.error("Ingest Agent bao loi cho %s: %s", rel_path, outcome.get("result"))
            return

        current_hash = self._hash_can_xu_ly()
        self._state["can_xu_ly_hash"] = current_hash

        if current_hash != prev_hash:
            logger.warning(
                "Nguon %s co the gay mau thuan/can xac nhan - xem can-xu-ly.md, "
                "CHUA dong bo len Supabase cho toi khi duoc xac nhan.",
                rel_path,
            )
            return

        try:
            sync_result = self.wiki_sync.sync_all(wipe=True, status="approved")
        except Exception:
            # Supabase loi luc dong bo KHONG duoc lam nguon nay bi coi la "chua xu ly"
            # (se chay lai Ingest Agent moi 30s chung nao Supabase con loi).
            logger.exception("Dong bo Supabase that bai sau khi ingest %s - can dong bo lai thu cong", rel_path)
            return
        logger.info("Da dong bo Supabase sau khi ingest %s: %s", rel_path, sync_result)

    def _flag_unexpected_changes(self, prev: dict[str, str], current: dict[str, str]) -> None:
        changed = [k for k in current if prev.get(k) and current[k] != prev[k]]
        added = [k for k in current if k not in prev]
        affected = changed + added
        if not affected:
            return
        logger.warning("Phat hien thay doi wiki/ ngoai luong Ingest Agent: %s", affected)
        self._log_manual_edit(affected)

    def _log_manual_edit(self, paths: list[str]) -> None:
        log_path = self.vault / "log.md"
        can_xu_ly_path = self.vault / "can-xu-ly.md"
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        links = ", ".join(f"[[{Path(p).stem}]]" for p in paths)

        if log_path.is_file():
            entry = (
                f"\n## [{today}] manual-edit | He thong tu phat hien sua tay ngoai luong\n"
                f"- Nguon/ngu canh: Vault watcher phat hien thay doi tren cac trang sau ma "
                f"khong phai do Ingest Agent vua chay tao ra.\n"
                f"- Trang da tao/sua: {links}\n"
                f"- Ghi chu: Can nguoi kiem tra lai noi dung sua co dung quy uoc CLAUDE.md "
                f"khong (frontmatter, dat ten, lien ket), roi dong bo lai Supabase thu cong "
                f"neu hop le.\n"
            )
            with log_path.open("a", encoding="utf-8") as f:
                f.write(entry)

        if can_xu_ly_path.is_file():
            with can_xu_ly_path.open("a", encoding="utf-8") as f:
                f.write(f"\n- ⚠️ Sửa tay ngoài luồng phát hiện tự động ({today}): {links} — cần rà lại.\n")
