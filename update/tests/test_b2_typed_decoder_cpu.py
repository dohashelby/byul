"""EXAMPLE safe multi-frame observation tests; real typed modules run separately."""
import ast
import json
from pathlib import Path
import subprocess
import sys
import pytest
from tools import b2_decoder_cpu as tool


class Decoder:
    aux_buffers = ()
    def decode(self, frames):
        self.aux_buffers = frames
        try:
            if frames == [b'broken']:
                raise ValueError('PRIVATE_EXCEPTION_DO_NOT_SAVE')
            return 'control' if frames == [b'normal'] else 'PRIVATE_DECODED_DO_NOT_SAVE'
        finally:
            self.aux_buffers = ()


@pytest.mark.parametrize('frames,outcome,error', [([b'broken'], 'exception', 'ValueError'),
    ([b'opaque-private-input'], 'decoded', None)])
def test_failure_then_same_reader_recovers_without_raw_values(frames, outcome, error):
    decoder = Decoder()
    result = tool.collect_typed_trial(decoder, frames, case_id='fixed-case',
        control_frames=[b'normal'], control_check=lambda value: value == 'control')
    assert result['outcome'] == outcome and result['exception_type'] == error
    assert result['normal_before']['outcome'] == result['normal_after']['outcome'] == 'decoded_matches'
    assert result['case_aux_buffers_cleared'] and result['final_aux_buffers_cleared'] and decoder.aux_buffers == ()
    encoded = json.dumps(result)
    assert 'PRIVATE_' not in encoded and 'opaque-private-input' not in encoded


def test_frame_hash_preserves_boundaries_without_values():
    one, two = tool.typed_fingerprint([b'a', b'bc']), tool.typed_fingerprint([b'ab', b'c'])
    assert one['input_frames_sha256'] != two['input_frames_sha256']
    assert one['frame_bytes'] == [1, 2] and one['input_bytes'] == 3
    assert tool.typed_fingerprint([])['frame_count'] == 0


def test_missing_before_control_does_not_run_case_but_after_is_observed():
    calls = []
    class BadControl(Decoder):
        def decode(self, frames):
            calls.append(frames)
            raise RuntimeError('PRIVATE_CONTROL_ERROR')
    result = tool.collect_typed_trial(BadControl(), [b'case'], case_id='fixed-case',
        control_frames=[b'normal'], control_check=lambda value: True)
    assert result['outcome'] == 'not_attempted' and calls == [[b'normal'], [b'normal']]
    assert result['normal_before']['exception_type'] == result['normal_after']['exception_type'] == 'RuntimeError'
    assert 'PRIVATE_' not in json.dumps(result)


def test_aux_leak_is_observed_independently_from_decoded_outcome():
    class Leaky(Decoder):
        def decode(self, frames):
            self.aux_buffers = frames
            return 'control'
    result = tool.collect_typed_trial(Leaky(), [b'case'], case_id='fixed-case',
        control_frames=[b'normal'], control_check=lambda value: True)
    assert result['outcome'] == 'not_attempted'
    assert not result['normal_before']['aux_buffers_cleared'] and not result['final_aux_buffers_cleared']


def test_official_assertions_execute_and_only_safe_results_remain(tmp_path):
    path = tmp_path / 'tests/v1/test_serial_utils.py'
    path.parent.mkdir(parents=True)
    path.write_text('def test_dict_serialization():\n assert 1 == 1\n'
        'def test_tensor_serialization():\n assert False, "PRIVATE_ORIGINAL_ASSERT"\n'
        'def test_numpy_array_serialization():\n assert 2 == 2\n')
    result = tool.typed_official_controls(tmp_path, None, None, None, None)
    failed, = [row for row in result if row['outcome'] != 'passed']
    assert failed['case_id'] == 'test_tensor_serialization' and failed['exception_type'] == 'AssertionError'
    assert 'PRIVATE_' not in json.dumps(result)


def test_untyped_helpers_ast_identical_to_accepted_base():
    root = Path(__file__).resolve().parents[2]
    before = subprocess.check_output(['git', '-C', str(root), 'show',
        'bd491e846b8ee74284bf538426b6b79b19772ef8:update/tools/b2_decoder_cpu.py'], text=True)
    def functions(source):
        return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)}
    original, current = functions(before), functions(Path(tool.__file__).read_text())
    for name in ('verify_source', 'source_scope', 'run'):
        assert current[name] == original[name]


def test_main_defaults_to_existing_untyped_api(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(tool, 'run', lambda source: calls.append(source) or {'trials': []})
    monkeypatch.setattr(tool, 'run_typed', lambda source: pytest.fail('typed was selected by default'))
    output = tmp_path / 'output.json'
    monkeypatch.setattr(sys, 'argv', ['b2_decoder_cpu.py', '--source', str(tmp_path), '--output', str(output)])
    assert tool.main() == 0
    assert calls == [tmp_path.resolve()] and json.loads(output.read_text()) == {'trials': []}
