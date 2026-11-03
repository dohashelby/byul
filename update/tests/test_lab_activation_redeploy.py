"""LIF-05 재배포의 서로 다른 모델 별칭과 복구 계약."""

from guard.executors import lab_activation as activation


class StubLab:
    def __init__(self, before='before', after='after'):
        self.cfg = {'update_manifest': 'update.json', 'good_manifest': 'good.json',
            'generation_models': {'before': before, 'after': after}}
        self.restore_model = None
        self.notes = []
        self.missing = []
        self.applied = []

    def miss(self, code):
        self.missing.append(code)

    def note(self, step, **facts):
        self.notes.append((step, facts))

    def apply(self, name):
        self.applied.append(name)


def test_redeploy_applies_new_generation_and_restores_old_alias(monkeypatch):
    observed = []

    def replace(lab, change, *, before_model, after_model):
        observed.append((before_model, after_model, lab.restore_model))
        change()

    monkeypatch.setattr(activation, '_replace', replace)
    lab = StubLab()
    activation._redeploy(lab, {})
    assert observed == [('before', 'after', 'before')]
    assert lab.applied == ['update_manifest']
    assert lab.notes == [('redeploy-generations', {'generations': lab.cfg['generation_models']})]


def test_redeploy_same_alias_refuses_transition(monkeypatch):
    monkeypatch.setattr(activation, '_replace', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))
    lab = StubLab(after='before')
    activation._redeploy(lab, {})
    assert lab.missing == ['GENERATION_NOT_DISTINGUISHABLE']
    assert lab.applied == []


def test_redeploy_missing_restore_manifest_refuses_transition(monkeypatch):
    monkeypatch.setattr(activation, '_replace', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()))
    lab = StubLab()
    del lab.cfg['good_manifest']
    activation._redeploy(lab, {})
    assert lab.missing == ['CONFIG_REQUIRED:good_manifest']
    assert lab.applied == []
