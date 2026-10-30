"""Chạy bộ câu hỏi chuẩn (tests/golden/questions.json) để phát hiện hồi quy sau khi sửa wiki / code / deploy.

Ba cách chạy:

  # 1. Rẻ, không gọi LLM, không ghi DB: top-5 đoạn truy hồi có chứa từ khoá đáp án không? (đọc Supabase production, chỉ SELECT)
  python scripts/eval_golden.py --mode retrieval-only

  # 2. Offline: dựng chỉ mục từ tệp wiki (không cần mạng/Supabase)
  python scripts/eval_golden.py --mode retrieval-only --wiki-dir E:\\Workspace\\wiki_obsidian

  # 3. Đầy đủ: gọi AdvancedRAGPipeline.chat thật (TỐN TIỀN LLM, câu bị từ chối có thể tạo note nháp thật cho HR)
  python scripts/eval_golden.py --mode pipeline --yes-spend

Kết quả: Đạt / Trượt / Cần xem (+ Bỏ qua cho câu không chấm được ở chế độ retrieval-only). Báo cáo markdown + json ghi vào
tests/golden/results/ (đã .gitignore) và so với lần chạy trước cùng chế độ để nêu câu MỚI trượt."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
GOLDEN = ROOT / "tests" / "golden" / "questions.json"
RESULTS = ROOT / "tests" / "golden" / "results"
MAX_WORKERS = 3
DRAFT_PREFIX = "[CẦN BỔ SUNG] "

from app.rag.lexical_store import LexicalStore, fold  # noqa: E402

# Dấu hiệu bot từ chối / báo chưa có tài liệu (đã bỏ dấu để so khớp)
REFUSAL_MARKERS = (
    "chua co", "chua tim thay", "chua thay", "khong tim thay", "khong co thong tin", "khong co quy dinh", "khong co tai lieu",
    "chua duoc cap nhat", "lien he", "hoi hr", "hoi phong", "phong nhan su", "khong the cung cap", "khong the chia se", "khong duoc phep",
    "khong chia se", "khong cung cap", "bao mat", "rieng tu", "chi ho tro", "khong ho tro", "khong the", "thong tin nhay cam",
)


def load_questions(path: Path = GOLDEN) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["questions"]


def has_any(text: str, needles: list[str]) -> bool:
    folded = fold(text)
    return any(fold(n) in folded for n in needles)


def is_refusal(answer: str) -> bool:
    folded = fold(answer)
    return any(marker in folded for marker in REFUSAL_MARKERS)


def violates(answer: str, q: dict) -> str | None:
    folded = fold(answer)
    for banned in q.get("must_not_contain", []):
        if fold(banned) in folded:
            return f"chứa chuỗi cấm '{banned}'"
    for pattern in q.get("must_not_regex", []):
        found = re.search(pattern, answer, re.IGNORECASE)
        if found:
            return f"khớp mẫu cấm /{pattern}/ ('{found.group(0)[:30]}')"
    return None


# ---------------------------------------------------------------- chấm điểm
def grade_answer(q: dict, answer: str) -> tuple[str, str]:
    bad = violates(answer, q)
    if bad:
        return "Trượt", bad
    if q.get("disputed"):
        return "Cần xem", "câu đang tranh cãi/chưa chốt trong wiki, chỉ xem tay"
    if q.get("expect_refusal"):
        if is_refusal(answer):
            return "Đạt", "từ chối / báo chưa có tài liệu, không bịa"
        return "Cần xem", "kỳ vọng từ chối nhưng không thấy dấu hiệu từ chối — kiểm tra có bịa không"
    if has_any(answer, q["must_contain_any"]):
        return "Đạt", "có chứa đáp án kỳ vọng"
    if is_refusal(answer):
        return "Trượt", "bot từ chối/báo thiếu tài liệu trong khi wiki có đáp án"
    return "Trượt", f"không thấy {q['must_contain_any']}"


def grade_retrieval(q: dict, chunks: list[dict]) -> tuple[str, str]:
    if q.get("expect_refusal"):
        return "Bỏ qua", "câu từ chối/bảo mật — chỉ chấm được ở chế độ pipeline"
    if q.get("disputed") or not q["must_contain_any"]:
        return "Cần xem", "câu tranh cãi — không chấm số"
    needles = q.get("retrieval_any") or q["must_contain_any"]
    for rank, chunk in enumerate(chunks, 1):
        if has_any(chunk["text"] + " " + (chunk["metadata"].get("heading") or ""), needles):
            return "Đạt", f"từ khoá có ở đoạn hạng {rank}"
    return "Trượt", f"top-{len(chunks)} không chứa {needles}"


# ---------------------------------------------------------------- dựng chỉ mục offline từ tệp wiki
class WikiFileClient:
    """Giả SupabaseClient chỉ có select(), dựng knowledge_notes/knowledge_chunks từ các trang wiki (chunk theo heading)."""

    def __init__(self, wiki_dir: Path):
        wiki_dir = Path(wiki_dir)
        roots = [wiki_dir / "nhan-su" / "wiki", wiki_dir / "core"]
        roots = [r for r in roots if r.is_dir()] or [wiki_dir]
        files = sorted(f for r in roots for f in r.rglob("*.md")
                       if not any(x in f.name for x in ("phieu", "phan-hoi", "huong-dan-setup")))
        self.notes: list[dict] = []
        self.chunks: list[dict] = []
        for i, f in enumerate(files):
            text = f.read_text(encoding="utf-8")
            m = re.search(r'^title:\s*"?(.+?)"?\s*$', text, re.M)
            title = m.group(1) if m else f.stem
            body = re.sub(r"^---.*?---", "", text, count=1, flags=re.S)
            self.notes.append({"id": i, "title": title, "department": "Unassigned", "access_level": "all", "version": "1", "status": "approved"})
            k = 0
            for part in re.split(r"(?m)^(?=#{1,3} )", body):
                if part.strip():
                    self.chunks.append({"note_id": i, "chunk_index": k, "heading": part.splitlines()[0].lstrip("# ").strip(), "text": part[:1500]})
                    k += 1

    def select(self, table: str, params: dict) -> list[dict]:
        rows = self.notes if table == "knowledge_notes" else self.chunks
        off = int(params.get("offset", 0))
        return rows[off: off + int(params["limit"])]


def with_synonyms(query: str) -> str:
    try:
        from app.rag.pipeline import AdvancedRAGPipeline
        return AdvancedRAGPipeline._with_synonyms(query)
    except Exception as exc:  # noqa: BLE001
        print(f"(cảnh báo: không nạp được từ đồng nghĩa của pipeline: {exc.__class__.__name__}; tìm không có đồng nghĩa)", file=sys.stderr)
        return query


# ---------------------------------------------------------------- chế độ chạy
def run_retrieval(questions: list[dict], wiki_dir: str | None, top_k: int) -> tuple[list[dict], str]:
    if wiki_dir:
        client, source = WikiFileClient(Path(wiki_dir)), f"tệp wiki {wiki_dir} (offline)"
    else:
        from app.config import get_settings
        from app.services.supabase_client import SupabaseClient
        settings = get_settings()
        client, source = SupabaseClient(settings.supabase_url, settings.supabase_service_key), "Supabase (chỉ SELECT)"
    store = LexicalStore(client)
    n = store.warm()
    if not n:
        raise SystemExit("Không nạp được chỉ mục (kiểm tra mạng/Supabase hoặc --wiki-dir).")
    print(f"Chỉ mục: {n} đoạn từ {source}")
    synonyms_on = True
    out = []
    for q in questions:
        chunks = store.search(with_synonyms(q["question"]) if synonyms_on else q["question"], top_k)
        status, why = grade_retrieval(q, chunks)
        top = []
        for c in chunks[:3]:
            label = f"{c['metadata']['title']} > {c['metadata'].get('heading') or ''}"
            top.append(label[:90])
        out.append({"id": q["id"], "status": status, "why": why, "detail": top})
    return out, f"retrieval-only, nguồn: {source}, top-{top_k}"


def _draft_snapshot(supabase) -> dict[str, str]:
    rows = supabase.select("knowledge_notes", {"select": "id,title", "status": "eq.draft", "created_by": "eq.AI_Bot", "limit": "1000"})
    return {str(r["id"]): r["title"] for r in rows}


def run_pipeline(questions: list[dict]) -> tuple[list[dict], str]:
    from app.config import get_settings
    from app.rag.pipeline import AdvancedRAGPipeline
    pipe = AdvancedRAGPipeline(get_settings())
    pipe.vector_store.warm()
    golden_titles = {DRAFT_PREFIX + q["question"][:150] for q in questions}
    before = _draft_snapshot(pipe.supabase)

    def ask(q: dict) -> dict:
        t0 = time.time()
        try:
            res = pipe.chat(q["question"])
            answer = res["answer"]
            status, why = grade_answer(q, answer)
            cites = [c.get("source") or c.get("title") or str(c) for c in res.get("citations", [])][:3]
            return {"id": q["id"], "status": status, "why": why, "answer": answer, "detail": cites, "secs": round(time.time() - t0, 1)}
        except Exception as exc:  # noqa: BLE001
            return {"id": q["id"], "status": "Trượt", "why": f"lỗi khi chạy: {exc.__class__.__name__}: {str(exc)[:120]}", "answer": "", "detail": [], "secs": round(time.time() - t0, 1)}

    with ThreadPoolExecutor(MAX_WORKERS) as pool:
        out = list(pool.map(ask, questions))
    time.sleep(10)  # note nháp được tạo ở luồng nền: chờ chúng ghi xong rồi mới chụp "sau"
    after = _draft_snapshot(pipe.supabase)
    new = {i: t for i, t in after.items() if i not in before}
    removed = 0
    for note_id, title in new.items():
        if title in golden_titles:  # chỉ xoá nháp có tiêu đề khớp CHÍNH XÁC một câu golden
            pipe.admin.delete_note(note_id)
            removed += 1
    left = [t for i, t in new.items() if t not in golden_titles]
    note = f"pipeline (LLM thật); đã dọn {removed} note nháp sinh bởi lượt chạy"
    if left:
        note += f"; {len(left)} nháp mới KHÔNG khớp câu golden (có thể của người dùng thật) — giữ nguyên"
    print(note)
    return out, note


# ---------------------------------------------------------------- báo cáo
def previous_run(mode_key: str) -> dict | None:
    files = sorted(RESULTS.glob(f"{mode_key}-*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def write_report(mode_key: str, description: str, questions: list[dict], results: list[dict]) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    by_id = {q["id"]: q for q in questions}
    prev = previous_run(mode_key)
    prev_status = {r["id"]: r["status"] for r in prev["results"]} if prev else {}
    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    new_fail = [r["id"] for r in results if r["status"] == "Trượt" and prev_status.get(r["id"]) not in (None, "Trượt")]
    fixed = [r["id"] for r in results if r["status"] == "Đạt" and prev_status.get(r["id"]) == "Trượt"]
    json_path = RESULTS / f"{mode_key}-{stamp}.json"
    json_path.write_text(json.dumps({"time": stamp, "description": description, "results": [{"id": r["id"], "status": r["status"]} for r in results]}, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = [f"# Kết quả golden set — {stamp}", "", f"- Chế độ: {description}", f"- Tổng: {len(results)} câu — " + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))]
    if prev:
        lines.append(f"- So với lần trước ({prev['time']}): " + (f"**MỚI TRƯỢT: {', '.join(new_fail)}**" if new_fail else "không có câu nào mới trượt") + (f"; mới được sửa: {', '.join(fixed)}" if fixed else ""))
    else:
        lines.append("- Chưa có lần chạy trước cùng chế độ để so sánh.")
    lines += ["", "## Câu trượt", ""]
    failed = [r for r in results if r["status"] == "Trượt"]
    lines += [f"- **{r['id']}** [{by_id[r['id']]['group']}] {by_id[r['id']]['question']} — {r['why']}" + (" (MỚI)" if r["id"] in new_fail else "") for r in failed] or ["(không có)"]
    lines += ["", "## Câu cần xem tay", ""]
    for r in (r for r in results if r["status"] == "Cần xem"):
        lines.append(f"- **{r['id']}** {by_id[r['id']]['question']} — {r['why']}")
        if r.get("answer"):
            lines.append(f"  - Trả lời: {r['answer'][:300].replace(chr(10), ' ')}")
    lines += ["", "## Toàn bộ", "", "| id | nhóm | kết quả | ghi chú | top |", "|---|---|---|---|---|"]
    for r in results:
        q = by_id[r["id"]]
        lines.append(f"| {r['id']} | {q['group']} | {r['status']} | {r['why'].replace('|', '/')} | {'; '.join(r.get('detail', [])[:2]).replace('|', '/')} |")
    md_path = RESULTS / f"{mode_key}-{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:6]))
    print(f"Báo cáo: {md_path}")
    return md_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["pipeline", "retrieval-only"], default="retrieval-only")
    ap.add_argument("--wiki-dir", help="chạy offline: dựng chỉ mục từ thư mục wiki (có nhan-su/wiki và core), không cần Supabase")
    ap.add_argument("--yes-spend", action="store_true", help="bắt buộc cho --mode pipeline: xác nhận chấp nhận tốn tiền LLM và tạo/xoá note nháp thật")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--only", help="chỉ chạy các id/nhóm, cách nhau dấu phẩy (vd NP01,luong-thuong)")
    ap.add_argument("--questions", default=str(GOLDEN))
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    questions = load_questions(Path(args.questions))
    if args.only:
        wanted = {x.strip() for x in args.only.split(",")}
        questions = [q for q in questions if q["id"] in wanted or q["group"] in wanted]

    if args.mode == "pipeline":
        calls = len(questions)
        refusals = sum(1 for q in questions if q.get("expect_refusal"))
        if not args.yes_spend:
            print(f"CHƯA CHẠY. Chế độ pipeline sẽ gọi LLM thật ít nhất {calls} lượt (có thể thêm lượt mở rộng từ khoá khi kết quả yếu, "
                  f"tối đa khoảng {2 * calls}), TỐN TIỀN OpenRouter.\n"
                  f"Có {refusals} câu kỳ vọng bị từ chối: bot có thể tạo note nháp '[CẦN BỔ SUNG] ...' THẬT trong Supabase production; "
                  f"script sẽ tự xoá đúng các nháp mới có tiêu đề khớp câu golden (chụp danh sách nháp trước/sau), nhưng nên cân nhắc.\n"
                  f"Thêm cờ --yes-spend nếu chấp nhận. Muốn kiểm tra rẻ: --mode retrieval-only")
            return 2
        print(f"Chạy {calls} câu với LLM thật, {MAX_WORKERS} luồng...")
        results, description = run_pipeline(questions)
        mode_key = "pipeline"
    else:
        results, description = run_retrieval(questions, args.wiki_dir, args.top_k)
        mode_key = "retrieval-offline" if args.wiki_dir else "retrieval"
    write_report(mode_key, description, questions, results)
    return 1 if any(r["status"] == "Trượt" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
