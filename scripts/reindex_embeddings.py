"""Tạo lại embeddings cho TOÀN BỘ tài liệu bằng mô hình đang cấu hình (EMBEDDING_PROVIDER).

Cần chạy khi quay lại RETRIEVAL_MODE=vector sau thời gian dùng RETRIEVAL_MODE=lexical: ở chế độ lexical, tài liệu được
tạo/sửa chỉ có vector 0 nên tìm bằng embeddings sẽ không thấy chúng.

    python scripts/reindex_embeddings.py            # chỉ in số tài liệu sẽ làm, không ghi gì
    python scripts/reindex_embeddings.py --apply    # tạo lại thật (tốn một ít credit embeddings)

Chạy với RETRIEVAL_MODE=vector trong .env (script từ chối chạy nếu đang ở lexical)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.rag.pipeline import AdvancedRAGPipeline  # noqa: E402


def main() -> int:
    settings = get_settings()
    if settings.retrieval_mode != "vector":
        print("RETRIEVAL_MODE đang là 'lexical' — đặt RETRIEVAL_MODE=vector (và key embeddings) trước khi tạo lại vector.")
        return 2
    rag = AdvancedRAGPipeline(settings)
    notes = rag.admin.list_notes("approved")
    print(f"{len(notes)} tài liệu đã duyệt sẽ được tạo lại embeddings bằng {settings.embedding_provider}.")
    if "--apply" not in sys.argv:
        print("Chạy thử (không ghi). Thêm --apply để thực hiện.")
        return 0
    failed = 0
    for index, note in enumerate(notes, 1):
        try:
            rag.admin._sync_chunks(note)
            print(f"[{index}/{len(notes)}] {note['title'][:70]}")
        except Exception as error:  # noqa: BLE001
            failed += 1
            print(f"[{index}/{len(notes)}] LỖI {note['title'][:60]}: {error}")
    print(f"Xong. Lỗi: {failed}.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
