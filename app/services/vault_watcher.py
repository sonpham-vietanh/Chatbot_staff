from __future__ import annotations

from pathlib import Path
import logging
from threading import Lock, Timer
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer


logger = logging.getLogger(__name__)


class _VaultEventHandler(FileSystemEventHandler):
    def __init__(self, schedule_reindex: Callable[[], None]):
        self.schedule_reindex = schedule_reindex

    def on_created(self, event: FileSystemEvent) -> None:
        self._handle(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._handle(event)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._handle(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        self._handle(event)

    @staticmethod
    def _is_markdown(event: FileSystemEvent) -> bool:
        path = getattr(event, "src_path", "")
        destination = getattr(event, "dest_path", "")
        return Path(path).suffix.casefold() == ".md" or Path(destination).suffix.casefold() == ".md"

    def _handle(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._is_markdown(event):
            self.schedule_reindex()


class VaultWatcher:
    """Theo dõi Vault và reindex sau khi file Markdown được lưu."""

    def __init__(self, vault_path: Path, reindex: Callable[[], object], debounce_seconds: float = 2.0):
        self.vault_path = vault_path
        self.reindex = reindex
        self.debounce_seconds = debounce_seconds
        self.observer = Observer()
        self._timer: Timer | None = None
        self._timer_lock = Lock()
        self._reindex_lock = Lock()
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        handler = _VaultEventHandler(self.schedule_reindex)
        self.observer.schedule(handler, str(self.vault_path), recursive=True)
        self.observer.start()
        self._started = True
        logger.info("Obsidian Vault watcher started: %s", self.vault_path)

    def stop(self) -> None:
        with self._timer_lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
        if self._started:
            self.observer.stop()
            self.observer.join(timeout=5)
            self._started = False
            logger.info("Obsidian Vault watcher stopped")

    def schedule_reindex(self) -> None:
        with self._timer_lock:
            if self._timer:
                self._timer.cancel()
            self._timer = Timer(self.debounce_seconds, self._run_reindex)
            self._timer.daemon = True
            self._timer.start()

    def _run_reindex(self) -> None:
        if not self._reindex_lock.acquire(blocking=False):
            return
        try:
            logger.info("Markdown change detected; reindexing Obsidian Vault")
            stats = self.reindex()
            logger.info(
                "Vault reindex completed: %s files, %s chunks, %s graph edges",
                stats.indexed_files,
                stats.indexed_chunks,
                stats.graph_edges,
            )
        except Exception:
            logger.exception("Automatic Obsidian Vault reindex failed")
        finally:
            self._reindex_lock.release()
