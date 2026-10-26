"""Actual local child-process pipes; no Docker daemon or serving engine."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from guard.executors.log_analysis import _FILE_READER
from guard.runtime.bounded import capture_pipes
from guard.runtime.docker import CmdResult, Docker


def capture(code, *, cap=4096, timeout=3):
    return capture_pipes([sys.executable, "-c", code], timeout=timeout, env_extra={},
                         max_stdout_bytes=cap, max_stderr_bytes=cap)


def test_both_pipes_flood_without_unbounded_queue_or_deadlock():
    started = time.monotonic()
    result = capture("import os,threading; a=threading.Thread(target=lambda: [os.write(1,b'x'*4096) for _ in range(10000)]); "
                     "a.start(); [os.write(2,b'y'*4096) for _ in range(10000)]; a.join()")
    assert time.monotonic() - started < 5
    assert len(result["stdout_bytes"]) <= 4096 and len(result["stderr_bytes"]) <= 4096
    assert result["capture"]["truncated"] and not result["capture"]["complete"]
    assert result["capture"]["pipes_closed"]


def test_deadline_retains_partial_evidence_and_reaps_only_owned_child():
    started = time.monotonic()
    result = capture("import os,time; os.write(1,b'EXAMPLE partial\\n'); time.sleep(20)", timeout=0.3)
    assert time.monotonic() - started < 3
    assert result["stdout_bytes"] == b"EXAMPLE partial\n"
    assert result["capture"]["timed_out"] and not result["capture"]["complete"]
    assert result["returncode"] is not None


def test_raw_bytes_hashes_preserve_crlf_and_invalid_utf8():
    raw = b"\xff\r\n\xe3\x85\x87\n"
    result = capture("import os; os.write(1," + repr(raw) + ")")
    assert result["stdout_bytes"] == raw and result["capture"]["complete"]
    assert result["capture"]["streams"]["stdout"]["retained_sha256"] == hashlib.sha256(raw).hexdigest()
    docker = Docker("example-log", binary=sys.executable)
    decoded = docker._bounded_read(["-c", "import os; os.write(1," + repr(raw) + ")"], timeout=3, max_bytes=4096)
    assert decoded.stdout_bytes == raw and "\ufffd" in decoded.stdout
    assert "\r\n" in decoded.stdout


def test_utf8_split_between_writes_decodes_once_after_capture():
    result = capture("import os,time; os.write(1,b'\\xed'); time.sleep(.02); os.write(1,b'\\x95\\x9c\\r\\n')")
    assert result["stdout_bytes"].decode() == "한\r\n" and result["capture"]["complete"]


def test_exact_cap_and_empty_output_are_complete_not_truncated():
    result = capture("import os; os.write(1,b'x'*4096)")
    assert result["capture"]["complete"] and not result["capture"]["truncated"]
    assert capture("pass")["capture"]["complete"]


def test_bounded_stdin_roundtrip_does_not_put_payload_in_argv():
    payload = b"EXAMPLE credential via stdin only\x00\xff" * 1000
    result = capture_pipes([sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
                           timeout=3, env_extra={}, max_stdout_bytes=len(payload), max_stderr_bytes=4096,
                           input_bytes=payload)
    assert result["capture"]["complete"] and result["stdout_bytes"] == payload


def test_stdin_writer_cannot_extend_deadline_when_child_never_reads():
    started = time.monotonic()
    result = capture_pipes([sys.executable, "-c", "import time; time.sleep(20)"], timeout=0.3,
                           env_extra={}, max_stdout_bytes=4096, max_stderr_bytes=4096, input_bytes=b"x" * 1024**2)
    assert time.monotonic() - started < 3
    assert result["capture"]["timed_out"] and not result["capture"]["complete"]


@pytest.mark.parametrize("payload", ["not bytes", b"x" * (1024**2 + 1)], ids=["not_bytes", "over_limit"])
def test_invalid_stdin_is_rejected_before_child_creation(payload):
    with pytest.raises(ValueError, match="stdin must be bounded bytes"):
        capture_pipes(["never-run"], timeout=3, env_extra={}, max_stdout_bytes=4, max_stderr_bytes=4, input_bytes=payload)


def test_injected_runner_keeps_old_signature_but_does_not_claim_source_bound():
    calls = []
    def legacy(argv, *, input, timeout, env_extra):
        calls.append((argv, input, timeout, env_extra))
        return CmdResult(0, "x" * 30, "secret=EXAMPLE raw stderr")
    result = Docker("example-log", runner=legacy).logs_window("EXAMPLE", since="s", until="u", timeout=3, max_bytes=8)
    assert len(result.stdout.encode()) == len(result.stderr.encode()) == 8
    assert not result.capture["bounded_at_source"] and not result.capture["complete"]
    assert calls and calls[0][1] is None


@pytest.mark.parametrize("limit", [0, -1, True, 16 * 1024**2 + 1])
def test_pipe_capture_rejects_invalid_budget_before_start(limit):
    with pytest.raises(ValueError):
        capture_pipes(["never-start"], timeout=1, env_extra={}, max_stdout_bytes=limit, max_stderr_bytes=10)


@pytest.mark.skipif(sys.platform == "win32", reason="container_file reader is a Linux in-container path contract")
def test_descriptor_reader_returns_exact_regular_bytes_and_rejects_symlinks(tmp_path):
    path = tmp_path / "log.json"
    path.write_bytes(b'{"request_id":"EXAMPLE"}\r\n')
    def read(source):
        result = subprocess.run([sys.executable, "-c", _FILE_READER, str(source), "1024"], capture_output=True, timeout=5, check=True)
        return json.loads(result.stdout)
    normal = read(path)
    assert normal["file_identity_stable"] and normal["nofollow_components"] and normal["regular_file"]
    assert normal["raw_retained_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    link = tmp_path / "link"
    link.symlink_to(path)
    assert read(link)["file_identity_stable"] is False
    directory_link = tmp_path / "dir-link"
    directory_link.symlink_to(tmp_path, target_is_directory=True)
    assert read(directory_link / "log.json")["file_identity_stable"] is False


@pytest.mark.skipif(sys.platform == "win32", reason="container_file reader requires Linux nofollow/dirfd")
def test_descriptor_reader_bounds_bytes_and_rejects_nonregular_source(tmp_path):
    path = tmp_path / "large"
    path.write_bytes(b"x" * 10000)
    read = lambda source: json.loads(subprocess.run([sys.executable, "-c", _FILE_READER, str(source), "10"],
                                                   capture_output=True, timeout=5, check=True).stdout)
    result = read(path)
    assert result["truncated"] and not result["source_complete"] and result["raw_retained_bytes"] == 10
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    assert read(fifo)["file_identity_stable"] is False


@pytest.mark.skipif(sys.platform == "win32", reason="the in-container descriptor reader uses Linux dirfd/nofollow")
@pytest.mark.parametrize("mutation", ["append", "replace", "truncate"])
def test_descriptor_reader_detects_file_change_during_actual_descriptor_read(tmp_path, mutation):
    source = tmp_path / "race.log"
    source.write_bytes(b"EXAMPLE original log\n" * 100)
    # Inject an OS race immediately after the real first read. The production
    # reader itself still uses its original open/fstat/path checks unchanged.
    hook = f'''import os,sys
_original_read=os.read
_changed=False
def _race_read(fd,size):
    global _changed
    data=_original_read(fd,size)
    if not _changed:
        _changed=True
        p=sys.argv[1]
        mutation={mutation!r}
        if mutation=='replace':
            os.rename(p,p+'.old')
            with open(p,'wb') as target: target.write(b'EXAMPLE replacement')
        elif mutation=='append':
            with open(p,'ab') as target: target.write(b'EXAMPLE appended')
        else:
            with open(p,'wb') as target: target.write(b'')
    return data
os.read=_race_read
'''
    result = json.loads(subprocess.run([sys.executable, "-c", hook + _FILE_READER, str(source), "10000"],
                                      capture_output=True, timeout=5, check=True).stdout)
    assert result["file_identity_stable"] is False and result["source_complete"] is False
