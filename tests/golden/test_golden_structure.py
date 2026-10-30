"""Kiểm tra cấu trúc tests/golden/questions.json (không chạy bot, không cần mạng)."""
import json
import re
from pathlib import Path

QUESTIONS = Path(__file__).with_name("questions.json")
REQUIRED = {"id", "question", "group", "must_contain_any", "must_not_contain"}


def _load():
    return json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]


def test_required_fields_and_types():
    for q in _load():
        assert REQUIRED <= q.keys(), f"{q.get('id')} thiếu trường: {REQUIRED - q.keys()}"
        assert isinstance(q["question"], str) and len(q["question"].strip()) >= 8
        assert isinstance(q["must_contain_any"], list) and isinstance(q["must_not_contain"], list)
        for pattern in q.get("must_not_regex", []):
            re.compile(pattern)


def test_ids_unique():
    ids = [q["id"] for q in _load()]
    assert len(ids) == len(set(ids))


def test_each_question_has_a_check():
    """Câu thường (không tranh cãi, không từ chối) phải có từ khoá đáp án; câu từ chối/tranh cãi thì không bắt buộc."""
    for q in _load():
        if not q.get("expect_refusal") and not q.get("disputed"):
            assert q["must_contain_any"], f"{q['id']} không có must_contain_any"


def test_mix_is_reasonable():
    qs = _load()
    assert sum(1 for q in qs if q.get("expect_refusal")) >= 12
    assert sum(1 for q in qs if q["group"] == "bao-mat") >= 8
