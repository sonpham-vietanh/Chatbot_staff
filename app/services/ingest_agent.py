"""Ingest Agent - chay quy trinh ingest CLAUDE.md (Section 5.1) tu dong qua Claude
Agent SDK, thao tac truc tiep len vault Obsidian. Da kiem chung qua project rieng
ingest-agent/ (2 lan test doc lap thanh cong) truoc khi gop vao day.

Agent CHI duoc cap quyen Read/Write/Edit/Glob/Grep + 1 tool doc file rieng
(document_reader) trong vault - KHONG co Bash/PowerShell, nen khong the tu chay
git commit/push du CLAUDE.md co nhac buoc 9. Con nguoi tu quyet dinh khi nao
commit that."""
from __future__ import annotations

from pathlib import Path

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, ToolUseBlock, query

from app.config import Settings
from app.services.document_reader import document_tools_server

TASK_TEMPLATE = """\
Ban la Ingest Agent cho Wiki Truong Viet Anh. Lam viec trong thu muc vault hien tai
(working directory). Truoc tien doc file CLAUDE.md o goc thu muc va tuan thu DUNG
TUYET DOI moi quy uoc trong do (cau truc thu muc, frontmatter YAML bat buoc,
quy tac dat ten, quy tac bao mat PII o Section 4).

NHIEM VU: Ingest 1 nguon moi tai duong dan tuyet doi sau:

{source_path}

Ten file goc: {source_filename}
Goi y phong ban/domain (neu co): {department_hint}

XAC DINH DOMAIN: vault duoc to chuc theo domain (moi domain 1 thu muc GOC rieng,
vd "nhan-su/", xem CLAUDE.md muc 1-2). File nguon nay da nam san trong dung thu
muc domain cua no (thu muc con dau tien tinh tu goc vault trong duong dan tuyet
doi o tren, ngay truoc "/raw/") - LAY DOMAIN TU CHINH DUONG DAN DO, khong doan.
Moi thao tac wiki/ lien quan den nguon nay deu lam trong dung thu muc
"<domain>/wiki/", TRU rieng cac trang entities/ (luon nam o "core/entities/",
dung chung moi domain, xem buoc 4).

BUOC DOC NGUON: neu duoi file la .docx hoac .xlsx/.xls, PHAI dung cong cu
"read_document" (KHONG dung Read thong thuong, Read khong doc duoc file nhi phan)
de lay noi dung day du (van ban, bang, tieu de). Neu duoi file la .md/.txt thi dung
Read binh thuong. Doc THAT KY, KHONG duoc bo sot bat ky phan nao cua nguon (day la
yeu cau quan trong nhat - do chinh xac uu tien hon toc do). Neu file co ghi chu
"co N hinh anh nhung khong doc duoc noi dung", ghi ro dieu nay trong trang tom tat
de nguoi xem lai biet can kiem tra bang mat.

Thuc hien DUNG theo quy trinh Section 5.1 cua CLAUDE.md, cac buoc 1-8 (BO QUA buoc 9
"commit git" - ban khong co quyen chay lenh git, khong can lam buoc do). File nguon
da duoc dat dung cho (buoc 1 coi nhu xong san) - bat dau tu buoc 2:

2. Tao trang tom tat trong <domain>/wiki/sources/<slug>.md (type: source-summary),
   trich dan lai duong dan raw/... o tren.
3. QUAN TRONG NHAT: tim trong "<domain>/wiki/" (VA ca "core/" neu lien quan toi
   entity/khai niem dung chung) xem co trang nao lien quan/bi anh huong boi noi
   dung nguon nay khong (dung Grep/Glob de tim, doc cac trang ung vien). Neu nguon
   nay la BAN CAP NHAT/SUA DOI cho 1 trang da co - SUA TRANG DO TAI CHO, KHONG tao
   trang moi trung lap. Ghi ro trong noi dung sua: cai gi doi, hieu luc tu khi nao,
   va giu lai cac phan khong doi.
4. Cap nhat/tao cac trang o "core/entities/" lien quan neu nguon nhac toi co
   so/nguoi/doi thu chua co trang (entities LUON nam o core/, khong nam trong
   thu muc domain, vi day la thong tin dung chung khong nhay cam).
5. Cap nhat trang "<domain>/wiki/overview.md". Neu noi dung moi MAU THUAN voi
   claim cu (vd so lieu cu con luu o cho khac chua duoc cap nhat dong bo), dung
   khoi "> [!contradiction]" de ghi chu ro, KHONG am tham ghi de.
5b. Doc file can-xu-ly.md o goc vault. Neu buoc 5 vua phat hien mau thuan hoac
   thong tin chi moi suy luan duoc (chua xac nhan truc tiep voi nguoi dung) -
   THEM 1 dong moi vao can-xu-ly.md (link toi trang bi anh huong + mo ta ngan).
   Neu noi dung nguon nay VUA GIAI QUYET 1 diem dang co san trong can-xu-ly.md
   (nguoi dung xac nhan truc tiep qua chinh noi dung nguon) - XOA dong do khoi
   can-xu-ly.md (nhung van ghi lai viec giai quyet do vao log.md o buoc 8).
6. Neu du quan trong, cap nhat trang "<domain>/wiki/synthesis/" lien quan (co the
   bo qua neu khong co synthesis nao lien quan).
7. Them dong vao index.md dung section, duong dan phai bat dau bang dung ten
   thu muc domain (vd "nhan-su/wiki/...") hoac "core/..." tuy trang.
8. Append 1 entry vao log.md dung dinh dang Section 6 cua CLAUDE.md (loai =
   "ingest"), neu-content ro rang day la file TEST thi ghi ro trong "Ghi chu"
   la day la lan test Ingest Agent, khong phai nguon that.

Sau khi xong, tra loi bang tieng Viet: liet ke chinh xac tung file da tao/sua
(duong dan day du), va tom tat ngan gon quyet dinh quan trong nhat ban da dua ra
(vd: sua trang nao tai cho thay vi tao moi, va ly do)."""


class IngestAgent:
    def __init__(self, settings: Settings):
        if not settings.vault_path:
            raise ValueError("VAULT_PATH chua duoc cau hinh trong .env")
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY chua duoc cau hinh trong .env")
        self.vault_path = settings.vault_path
        self.cli_path = settings.claude_cli_path  # None -> SDK tu tim trong PATH

    async def run(self, source_path: Path, department_hint: str = "") -> dict:
        if not source_path.is_file():
            raise FileNotFoundError(f"Khong tim thay file nguon: {source_path}")

        prompt = TASK_TEMPLATE.format(
            source_path=str(source_path.resolve()),
            source_filename=source_path.name,
            department_hint=department_hint or "(khong ro, tu suy luan tu noi dung)",
        )
        options = ClaudeAgentOptions(
            cwd=self.vault_path,
            tools=["Read", "Write", "Edit", "Glob", "Grep", "mcp__document-reader__read_document"],
            disallowed_tools=["Bash", "PowerShell", "WebFetch", "WebSearch"],
            permission_mode="bypassPermissions",
            cli_path=self.cli_path,
            mcp_servers={"document-reader": document_tools_server},
        )

        outcome = {"result": "", "cost_usd": 0.0, "num_turns": 0, "is_error": False, "tool_calls": []}
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        outcome["tool_calls"].append({"tool": block.name, "input": block.input})
                    elif isinstance(block, TextBlock) and block.text.strip():
                        outcome["result"] = block.text
            elif isinstance(message, ResultMessage):
                outcome["result"] = message.result or outcome["result"]
                outcome["cost_usd"] = message.total_cost_usd or 0.0
                outcome["num_turns"] = message.num_turns
                outcome["is_error"] = message.is_error
        return outcome
