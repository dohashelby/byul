"""EXAMPLE safe multi-frame observation tests; real typed modules run separately."""
import ast
import hashlib
import os
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


# AST dump SHA256 from accepted bd491e846b8ee74284bf538426b6b79b19772ef8,
# generated with supported Python 3.12 and include_attributes=False. No Git
# history is needed at test time (including checkout fetch-depth=1).
ACCEPTED_UNTYPED_AST_SHA256 = {
    'verify_source': '337bb496c72ca546b0455c4e1951d89f8a4a7711a8f427fbf4613b3e03f021d2',
    'source_scope': '9162138923fcf4d36106ffde2acf73bb9fd328339a8d5b5c010b6bd0d23b3ac4',
    'run': 'c0afe6b545b881e30bdd4af619e495f5d1b19cb6ff1fb8cc0c8da28fecc9c108',
}


def test_untyped_helpers_ast_identical_to_accepted_base():
    current = {n.name: hashlib.sha256(ast.dump(n, include_attributes=False).encode()).hexdigest()
               for n in ast.parse(Path(tool.__file__).read_text()).body if isinstance(n, ast.FunctionDef)}
    assert {name: current[name] for name in ACCEPTED_UNTYPED_AST_SHA256} == ACCEPTED_UNTYPED_AST_SHA256


def test_optimized_helper_keeps_original_assertion_failure(tmp_path):
    path = tmp_path / 'tests/v1/test_serial_utils.py'
    path.parent.mkdir(parents=True)
    path.write_text('def test_dict_serialization():\n assert 1 == 1\n'
        'def test_tensor_serialization():\n assert False, "PRIVATE_OPTIMIZED_ASSERT"\n'
        'def test_numpy_array_serialization():\n assert 2 == 2\n')
    update = Path(tool.__file__).resolve().parents[1]
    code = ('import json,sys; from pathlib import Path; '
            'from tools.b2_decoder_cpu import typed_official_controls; '
            'print(json.dumps(typed_official_controls(Path(sys.argv[1]),None,None,None,None)))')
    result = subprocess.run([sys.executable, '-O', '-c', code, str(tmp_path)],
        cwd=update, env={**os.environ, 'PYTHONPATH': os.pathsep.join([str(update / 'src'), str(update)])},
        text=True, capture_output=True)
    assert result.returncode == 0
    failed, = [row for row in json.loads(result.stdout) if row['outcome'] != 'passed']
    assert failed['case_id'] == 'test_tensor_serialization' and failed['exception_type'] == 'AssertionError'
    assert 'PRIVATE_OPTIMIZED_ASSERT' not in result.stdout + result.stderr


@pytest.mark.parametrize('optimization', ['-O', '-OO'])
@pytest.mark.parametrize('profile', ['typed', 'untyped'])
def test_optimized_cli_rejects_before_source_or_target_execution(tmp_path, optimization, profile):
    update = Path(tool.__file__).resolve().parents[1]
    output = tmp_path / 'output.json'
    result = subprocess.run([sys.executable, optimization, str(Path(tool.__file__).resolve()),
        '--profile', profile, '--source', str(tmp_path / 'no-target-checkout'), '--output', str(output)],
        cwd=update, env={**os.environ, 'PYTHONPATH': os.pathsep.join([str(update / 'src'), str(update)])},
        text=True, capture_output=True)
    assert result.returncode != 0 and 'DECODER_CPU_REQUIRES_ASSERTIONS' in result.stderr
    assert 'verify_source' not in result.stderr and 'vllm' not in result.stdout
    assert not output.exists()


def test_main_defaults_to_existing_untyped_api(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(tool, 'run', lambda source: calls.append(source) or {'trials': []})
    monkeypatch.setattr(tool, 'run_typed', lambda source: pytest.fail('typed was selected by default'))
    output = tmp_path / 'output.json'
    monkeypatch.setattr(sys, 'argv', ['b2_decoder_cpu.py', '--source', str(tmp_path), '--output', str(output)])
    assert tool.main() == 0
    assert calls == [tmp_path.resolve()] and json.loads(output.read_text()) == {'trials': []}
