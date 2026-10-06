import hashlib
import itertools
import os

import pytest
from pydantic import ValidationError

from footnoteone.schema import Prompt, canonical_json, sha256_of
from footnoteone.store import JsonlStore, RawStore


def test_jsonl_store_appends_and_iterates_in_order(tmp_path):
    store = JsonlStore(tmp_path / ".footnote")
    store.append("prompts", Prompt(text="first"))
    store.append("prompts", Prompt(text="second"))
    texts = [p.text for p in store.iter("prompts", Prompt)]
    assert texts == ["first", "second"]
    assert (tmp_path / ".footnote" / "prompts.jsonl").exists()


def test_jsonl_store_iter_on_missing_file_is_empty(tmp_path):
    store = JsonlStore(tmp_path / ".footnote")
    assert list(store.iter("runs", Prompt)) == []


def test_jsonl_store_iter_on_an_empty_file_is_empty(tmp_path):
    root = tmp_path / ".footnote"
    root.mkdir()
    (root / "prompts.jsonl").write_text("")
    assert list(JsonlStore(root).iter("prompts", Prompt)) == []


def test_jsonl_store_iter_on_a_newline_only_file_is_empty(tmp_path):
    root = tmp_path / ".footnote"
    root.mkdir()
    (root / "prompts.jsonl").write_text("\n\n")
    assert list(JsonlStore(root).iter("prompts", Prompt)) == []


def test_jsonl_store_skips_blank_lines_and_ignores_unknown_fields(tmp_path):
    root = tmp_path / ".footnote"
    root.mkdir()
    (root / "prompts.jsonl").write_text('{"id":"x","text":"a","extra":1}\n\n{"id":"y","text":"b"}\n')
    assert [p.text for p in JsonlStore(root).iter("prompts", Prompt)] == ["a", "b"]


def test_jsonl_store_skips_a_torn_final_line(tmp_path):
    root = tmp_path / ".footnote"
    root.mkdir()
    (root / "prompts.jsonl").write_text('{"id":"x","text":"a"}\n{"id":"y","te')
    assert [p.text for p in JsonlStore(root).iter("prompts", Prompt)] == ["a"]


def test_jsonl_store_skips_a_torn_final_line_that_splits_a_multibyte_character(tmp_path):
    root = tmp_path / ".footnote"
    root.mkdir()
    (root / "prompts.jsonl").write_bytes(b'{"id":"x","text":"a"}\n{"id":"y","text":"caf\xc3')
    assert [p.text for p in JsonlStore(root).iter("prompts", Prompt)] == ["a"]


def test_jsonl_store_raises_on_a_malformed_line_before_the_last(tmp_path):
    root = tmp_path / ".footnote"
    root.mkdir()
    (root / "prompts.jsonl").write_text('{"id":"x","te\n{"id":"y","text":"b"}\n')
    with pytest.raises(ValueError, match=r"prompts\.jsonl:1:") as excinfo:
        list(JsonlStore(root).iter("prompts", Prompt))
    assert isinstance(excinfo.value.__cause__, ValidationError)


def test_jsonl_store_raises_on_a_final_line_that_is_valid_json_but_not_a_record(tmp_path):
    root = tmp_path / ".footnote"
    root.mkdir()
    (root / "prompts.jsonl").write_text('{"id":"x","text":"a"}\n\n{"id":"y"}\n')
    with pytest.raises(ValueError, match=r"prompts\.jsonl:3:"):
        list(JsonlStore(root).iter("prompts", Prompt))


def test_jsonl_store_raises_on_a_newline_terminated_invalid_last_line(tmp_path):
    root = tmp_path / ".footnote"
    root.mkdir()
    (root / "prompts.jsonl").write_text('{"id":"x","text":"a"}\n{"id":"y","te\n')
    with pytest.raises(ValueError, match=r"prompts\.jsonl:2:"):
        list(JsonlStore(root).iter("prompts", Prompt))


def test_jsonl_store_append_cuts_a_torn_tail_before_writing(tmp_path):
    store = JsonlStore(tmp_path / ".footnote")
    store.append("prompts", Prompt(text="first"))
    store.append("prompts", Prompt(text="second"))
    with store.path("prompts").open("a", encoding="utf-8") as fh:
        fh.write('{"id":"torn","te')
    store.append("prompts", Prompt(text="third"))
    assert [p.text for p in store.iter("prompts", Prompt)] == ["first", "second", "third"]
    content = store.path("prompts").read_text(encoding="utf-8")
    assert '"torn"' not in content
    assert len(content.splitlines()) == 3


def test_jsonl_store_append_replaces_a_lone_torn_fragment(tmp_path):
    store = JsonlStore(tmp_path / ".footnote")
    store.path("prompts").write_text('{"id":"torn","te', encoding="utf-8")
    record = Prompt(text="only")
    store.append("prompts", record)
    assert store.path("prompts").read_text(encoding="utf-8") == record.model_dump_json() + "\n"


def test_jsonl_store_append_terminates_an_unterminated_valid_last_record(tmp_path):
    root = tmp_path / ".footnote"
    root.mkdir()
    (root / "prompts.jsonl").write_text('{"id":"x","text":"a"}\n{"id":"y","text":"b"}')
    store = JsonlStore(root)
    assert [p.text for p in store.iter("prompts", Prompt)] == ["a", "b"]
    store.append("prompts", Prompt(text="c"))
    assert [p.text for p in store.iter("prompts", Prompt)] == ["a", "b", "c"]


def test_raw_store_is_content_addressed_and_idempotent(tmp_path):
    raw = RawStore(tmp_path / ".footnote")
    sha1 = raw.put({"b": 2, "a": 1})
    sha2 = raw.put({"a": 1, "b": 2})
    assert sha1 == sha2 and len(sha1) == 64
    assert raw.exists(sha1)
    assert raw.get(sha1) == {"a": 1, "b": 2}
    assert len(list((tmp_path / ".footnote" / "raw").iterdir())) == 1


def test_raw_store_put_leaves_only_the_final_blob(tmp_path):
    raw = RawStore(tmp_path / ".footnote")
    obj = {"answer": "caf\u00e9", "sources": [{"url": "https://a.example/x", "rank": 1}]}
    sha = raw.put(obj)
    raw_dir = tmp_path / ".footnote" / "raw"
    assert [p.name for p in raw_dir.iterdir()] == [f"{sha}.json"]
    assert (raw_dir / f"{sha}.json").read_bytes() == canonical_json(obj).encode()
    assert raw.get(sha) == obj


def test_raw_store_put_that_fails_before_the_rename_leaves_nothing_behind(tmp_path, monkeypatch):
    raw = RawStore(tmp_path / ".footnote")

    def crash(src, dst):
        raise OSError("simulated crash before the rename")

    monkeypatch.setattr(os, "replace", crash)
    with pytest.raises(OSError, match="simulated crash"):
        raw.put({"a": 1})
    assert not raw.exists(sha256_of({"a": 1}))
    assert list((tmp_path / ".footnote" / "raw").iterdir()) == []


def test_raw_store_blob_name_is_the_sha256_of_its_bytes(tmp_path):
    ticks = itertools.count()

    class Tick:
        """str() differs on every call, so two serializations of one object would disagree."""

        def __str__(self) -> str:
            return f"tick-{next(ticks)}"

    raw = RawStore(tmp_path / ".footnote")
    sha = raw.put({"value": Tick()})
    blob = tmp_path / ".footnote" / "raw" / f"{sha}.json"
    assert hashlib.sha256(blob.read_bytes()).hexdigest() == sha
