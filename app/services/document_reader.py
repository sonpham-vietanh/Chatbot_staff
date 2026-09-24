"""Cong cu doc file .docx/.xlsx rieng cho Ingest Agent - KHONG dung Bash/shell,
chi doc dung 1 file duoc chi dinh, tra ve text markdown. Dang ky thanh 1 SDK tool
hep pham vi (khong phai cap quyen Bash chung chung).

Anh nhung trong docx duoc tach ra file rieng (khong tu mo ta bang AI khac) - Agent
tu dung chinh cong cu Read cua no (da co san, xem duoc anh) de xem truc tiep, dung
y het cach lam thu cong truoc day (giai nen + Read) thay vi goi them 1 LLM rieng
de mo ta anh ho."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool


def _extract_docx_images(document, dest_dir: Path) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    seen_parts = set()
    for rel in document.part.rels.values():
        if not rel.reltype.endswith("/image") or rel.target_part.partname in seen_parts:
            continue
        seen_parts.add(rel.target_part.partname)
        ext = Path(str(rel.target_part.partname)).suffix or ".png"
        out_path = dest_dir / f"image_{len(saved) + 1}{ext}"
        out_path.write_bytes(rel.target_part.blob)
        saved.append(out_path)
    return saved


def _read_docx(path: Path) -> str:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(str(path))

    def iter_block_items():
        for child in document.element.body.iterchildren():
            if child.tag == qn("w:p"):
                yield Paragraph(child, document)
            elif child.tag == qn("w:tbl"):
                yield Table(child, document)

    out: list[str] = []
    for block in iter_block_items():
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if not text:
                continue
            style = block.style.name if block.style else ""
            if "Heading" in style or style == "Title":
                level = 1
                if style[-1].isdigit():
                    level = int(style[-1]) + 1
                out.append(f"\n{'#' * min(level, 6)} {text}\n")
            else:
                out.append(text)
        else:
            out.append("\n[BẢNG]")
            for row in block.rows:
                cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                out.append(" | ".join(cells))
            out.append("[/BẢNG]\n")

    dest_dir = Path(tempfile.gettempdir()) / "ingest-agent-images" / path.stem
    images = _extract_docx_images(document, dest_dir)
    if images:
        out.append(f"\n[CÓ {len(images)} HÌNH ẢNH NHÚNG — BẮT BUỘC xem trực tiếp bằng công cụ Read trước khi tóm tắt,"
                    " vì hình có thể chứa sơ đồ/số liệu quan trọng không có trong text ở trên. Đường dẫn từng ảnh:]")
        for img in images:
            out.append(f"  - {img}")
    return "\n".join(out)


def _read_xlsx(path: Path) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(str(path), data_only=True)
    out: list[str] = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        out.append(f"\n===== SHEET: {sheet_name} =====")
        max_r = min(ws.max_row, 2000)
        max_c = min(ws.max_column, 50)
        for r in range(1, max_r + 1):
            row_vals = []
            has_val = False
            for c in range(1, max_c + 1):
                v = ws.cell(row=r, column=c).value
                if v is not None:
                    has_val = True
                row_vals.append("" if v is None else str(v))
            if has_val:
                out.append(f"R{r}: " + " | ".join(row_vals))
        if ws.max_row > max_r:
            out.append(f"... (còn {ws.max_row - max_r} dòng nữa, đã cắt bớt vì quá dài)")
    return "\n".join(out)


READERS = {".docx": _read_docx, ".xlsx": _read_xlsx, ".xls": _read_xlsx}


@tool(
    "read_document",
    "Doc noi dung day du (van ban, bang, tieu de) cua 1 file .docx hoac .xlsx duoc chi dinh "
    "bang duong dan tuyet doi, tra ve dang text/markdown. Day la cong cu DUY NHAT de doc file "
    "nhi phan nguon - khong dung de doc file .md (dung Read binh thuong cho .md).",
    {"file_path": str},
)
async def read_document(args: dict[str, Any]) -> dict[str, Any]:
    path = Path(args["file_path"])
    if not path.is_file():
        return {"content": [{"type": "text", "text": f"LOI: khong tim thay file {path}"}], "is_error": True}
    reader = READERS.get(path.suffix.lower())
    if reader is None:
        return {
            "content": [{"type": "text", "text": f"LOI: khong ho tro doc file phan mo rong '{path.suffix}' (chi ho tro .docx, .xlsx, .xls)"}],
            "is_error": True,
        }
    try:
        text = reader(path)
    except Exception as exc:  # noqa: BLE001
        return {"content": [{"type": "text", "text": f"LOI khi doc file: {exc}"}], "is_error": True}
    return {"content": [{"type": "text", "text": text}]}


document_tools_server = create_sdk_mcp_server(
    name="document-reader",
    version="1.0.0",
    tools=[read_document],
)
