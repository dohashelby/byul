"""Our source-slice adapter guards, independent of optional engine dependencies."""
import pytest

from tools.b2_reference_cpu import selected


def test_missing_definition_aborts_before_any_selected_source_executes(tmp_path):
    (tmp_path / "source.py").write_text("FLAG = dangerous()\ndef supported(): return FLAG\n")
    scope = {"dangerous": lambda: pytest.fail("incomplete inventory must not execute source")}
    with pytest.raises(ValueError, match="inventory"):
        selected(tmp_path, "source.py", ["FLAG", "missing"], scope)


def test_selected_definition_keeps_its_original_source_location_and_ignores_engine_import(tmp_path):
    (tmp_path / "source.py").write_text("import unavailable_engine\n\ndef supported():\n    return 7\n")
    scope = {}
    selected(tmp_path, "source.py", ["supported"], scope)
    assert scope["supported"]() == 7
    assert scope["supported"].__code__.co_firstlineno == 3
    assert scope["supported"].__code__.co_filename == str(tmp_path / "source.py")
