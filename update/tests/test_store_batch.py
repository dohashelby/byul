import pytest
from guard.store import Store, IntegrityError
from guard.models import DataOrigin
from tests.builders import environment, policy


def test_batch_can_reference_an_earlier_object(tmp_path):
    store = Store(tmp_path)
    env = environment()
    pol = policy(refs=[env.ref()])
    assert store.put_many([env, pol]) == [env.ref(), pol.ref()]
    assert store.verify() == []


def test_invalid_later_object_rolls_back_prior_insert(tmp_path):
    store = Store(tmp_path)
    env = environment()
    invalid = policy().model_copy(update={"content_sha256":"invalid"})
    with pytest.raises(IntegrityError): store.put_many([env, invalid])
    assert list(store.iter_kind("ENV")) == [] and list(store.iter_kind("POL")) == []


def test_batch_failure_preserves_existing_revisions(tmp_path):
    store = Store(tmp_path)
    env = environment(); store.put(env)
    newer = env.model_copy(update={"revision":2,"data_origin":DataOrigin.DECLARED}).seal()
    bad = policy(refs=[env.ref().model_copy(update={"sha256":"wrong"})])
    with pytest.raises(IntegrityError): store.put_many([newer,bad])
    assert store.revisions(env.id) == [1] and store.resolve(env.ref()) == env
