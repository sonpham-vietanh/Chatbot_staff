"""Đo độ chính xác tìm tài liệu (RETRIEVAL_MODE=lexical) bằng câu hỏi do Claude tự viết cho từng đoạn tài liệu.

Mô hình sinh câu hỏi KHÔNG biết bộ tìm kiếm hoạt động thế nào; sau đó ta xem bộ tìm kiếm có tìm lại đúng đoạn/tài liệu gốc không.

    python scripts/blind_eval.py            # 60 đoạn ngẫu nhiên
    python scripts/blind_eval.py 100        # 100 đoạn
    python scripts/blind_eval.py 60 out.json  # ghi chi tiết từng câu ra file

Cần LLM_* trong .env (mỗi đoạn tốn 1 lời gọi mô hình). Số bot thực nhận là top_k đoạn, nên cột top-20 là cột quyết định.
Lưu ý: câu hỏi do mô hình viết nên sát tài liệu hơn câu hỏi thật của nhân viên; dùng để so sánh trước/sau khi chỉnh, không phải con số tuyệt đối."""
from __future__ import annotations

import json
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.rag.pipeline import AdvancedRAGPipeline  # noqa: E402

GEN = (
    "Dưới đây là một đoạn trích từ tài liệu nội bộ của một trường học. Hãy viết ĐÚNG MỘT câu hỏi ngắn, tự nhiên bằng tiếng Việt đời thường "
    "mà một nhân viên có thể hỏi và có câu trả lời nằm trong đoạn này. KHÔNG sao chép nguyên cụm từ dài trong đoạn, hãy diễn đạt theo cách người "
    "bình thường nói (đồng nghĩa, nói gọn, có thể không dấu). Chỉ trả về câu hỏi.\n\nTiêu đề tài liệu: {title}\nĐoạn trích:\n{text}\n"
)


def main() -> int:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    out_path = sys.argv[2] if len(sys.argv) > 2 else None
    settings = get_settings()
    if settings.retrieval_mode != "lexical":
        print("Script này đo chế độ lexical — đặt RETRIEVAL_MODE=lexical trong .env.")
        return 2
    rag = AdvancedRAGPipeline(settings)
    store = rag.vector_store
    store._ensure_fresh()
    docs = [d for d in store._docs if len(d["chunk"]["text"]) >= 220 and not d["note"]["title"].startswith("Nguồn:")]
    random.seed(7)
    sample = random.sample(docs, min(count, len(docs)))
    print(f"{len(docs)} đoạn đủ điều kiện (bỏ trang 'Nguồn:' và đoạn ngắn); lấy mẫu {len(sample)}")
    generator = getattr(rag.llm, "primary", rag.llm)

    def make_question(doc: dict) -> str:
        for _ in range(2):
            try:
                question = generator.complete(GEN.format(title=doc["note"]["title"], text=doc["chunk"]["text"][:1500]), max_tokens=80)
                if question:
                    return question.strip().strip('"')
            except Exception:  # noqa: BLE001
                time.sleep(1)
        return ""

    started = time.time()
    with ThreadPoolExecutor(2) as pool:
        questions = list(pool.map(make_question, sample))
    print(f"Đã sinh câu hỏi ({time.time() - started:.0f}s)")

    rows = []
    for doc, question in zip(sample, questions):
        if not question:
            continue
        note_id, index = doc["note"]["id"], doc["chunk"]["chunk_index"]
        results = rag.retrieve(question, None)
        ids = [r["id"] for r in results]
        target = f"{note_id}:{index}"
        rows.append({
            "q": question,
            "title": doc["note"]["title"],
            "chunk_rank": ids.index(target) + 1 if target in ids else None,
            "note_rank": next((i + 1 for i, item in enumerate(ids) if item.split(":")[0] == note_id), None),
            "top": results[0]["metadata"]["title"] if results else None,
        })
    if not rows:
        print("Không sinh được câu hỏi nào (kiểm tra LLM_BASE_URL / LLM_API_KEY).")
        return 1
    if out_path:
        Path(out_path).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    def rate(key: str, k: int) -> float:
        return sum(1 for r in rows if r[key] and r[key] <= k) / len(rows)

    print(f"\nSố câu hỏi đo được: {len(rows)}")
    for key, name in (("chunk_rank", "ĐÚNG ĐOẠN"), ("note_rank", "ĐÚNG TÀI LIỆU")):
        print(f"{name:14s} top1 {rate(key, 1):.0%} | top5 {rate(key, 5):.0%} | top10 {rate(key, 10):.0%} | top20 {rate(key, 20):.0%}")
    print("\nCÁC CÂU KHÔNG TÌM RA TRONG TOP 20:")
    for r in rows:
        if not r["note_rank"]:
            print(f"  - {r['q'][:90]}  [cần: {r['title'][:40]}] [top1: {(r['top'] or '-')[:30]}]")
    print("\nMỘT SỐ CÂU TÌM RA Ở HẠNG > 5:")
    for r in rows:
        if r["note_rank"] and r["note_rank"] > 5:
            print(f"  - hạng {r['note_rank']}: {r['q'][:90]} [{r['title'][:35]}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
