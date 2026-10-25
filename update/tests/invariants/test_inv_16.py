"""INV-16: 객체는 append-only. 기존 revision을 수정·삭제하지 않는다. 참조는 revision과 해시로 고정."""

import sqlite3

import pytest

from guard.models import DataOrigin
from guard.store import AppendOnlyError, IntegrityError, Store
from tests.builders import environment, policy


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / ".guard")


def test_same_revision_cannot_be_rewritten(store):
    env = environment()
    store.put(env)
    changed = env.model_copy(update={"data_origin": DataOrigin.DECLARED}).seal()
    with pytest.raises(AppendOnlyError):
        store.put(changed)
    assert store.get(env.id, 1) == env


def test_identical_reput_is_idempotent(store):
    env = environment()
    store.put(env)
    store.put(env)
    assert store.revisions(env.id) == [1]


def test_new_content_needs_next_revision(store):
    env = environment()
    store.put(env)
    skip = env.model_copy(update={"revision": 3}).seal()
    with pytest.raises(AppendOnlyError):
        store.put(skip)
    rev2 = env.model_copy(update={"revision": 2, "data_origin": DataOrigin.DECLARED}).seal()
    store.put(rev2)
    assert store.get(env.id, 1) == env  # 이전 revision은 그대로
    assert store.get(env.id, 2) == rev2


def test_sql_update_and_delete_are_blocked(store):
    store.put(environment())
    con = sqlite3.connect(store.db_path)
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE objects SET body = '{}'")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM objects")
    con.close()


def test_unsealed_or_tampered_object_rejected(store):
    env = environment()
    with pytest.raises(IntegrityError):
        store.put(env.model_copy(update={"content_sha256": ""}))
    with pytest.raises(IntegrityError):
        store.put(env.model_copy(update={"data_origin": DataOrigin.DECLARED}))


def test_refs_must_resolve_exactly(store):
    env = environment()
    store.put(env)
    good = policy(refs=[env.ref()])
    store.put(good)
    bad_ref = env.ref().model_copy(update={"sha256": "f" * 64})
    with pytest.raises(IntegrityError):
        store.put(policy(id="POL-BAD", refs=[bad_ref]))
    missing = env.ref().model_copy(update={"revision": 9})
    with pytest.raises(IntegrityError):
        store.put(policy(id="POL-BAD2", refs=[missing]))


def test_resolve_ref_checks_hash(store):
    env = environment()
    store.put(env)
    assert store.resolve(env.ref()) == env
    with pytest.raises(IntegrityError):
        store.resolve(env.ref().model_copy(update={"sha256": "0" * 64}))


def test_artifacts_are_content_addressed(store):
    a = store.put_artifact(b"hello")
    b = store.put_artifact(b"hello")
    assert a == b
    assert (store.root / a.path).read_bytes() == b"hello"
    assert a.path == f"artifacts/{a.sha256}"
    assert len(list((store.root / "artifacts").iterdir())) == 1


def test_verify_detects_tampered_artifact(store):
    art = store.put_artifact(b"log line")
    assert store.verify() == []
    (store.root / art.path).write_bytes(b"changed")
    assert store.verify()
