"""WP7 preparation helpers. Importing this module performs no external action."""
from __future__ import annotations
import hashlib, json, os, re, signal, stat, subprocess, time
from datetime import datetime, timezone
from pathlib import Path

BASELINE = '7741687d7f365bab3ab9681bc536e8c6250f2af6'
PRICE = 1.2021  # Directive planning estimate; not a fresh price quotation.

def now():
    return datetime.now(timezone.utc).isoformat()

def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(data)

def private_json(path):
    p = Path(path)
    if p.is_symlink() or not p.is_file() or stat.S_IMODE(p.stat().st_mode) != 0o600:
        raise ValueError('PRIVATE_INPUT_REQUIRES_REGULAR_0600_FILE')
    return json.loads(p.read_text())

def approval(path):
    d = private_json(path)
    if d.get('approved_by') != '민기' or d.get('scope') != 'WP7 AWS execution' or d.get('baseline') != BASELINE:
        raise ValueError('EXPLICIT_AWS_APPROVAL_REQUIRED')
    expires = datetime.fromisoformat(d['expires_at'])
    if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
        raise ValueError('APPROVAL_EXPIRED')
    # A file is an execution interlock; it is created only after the chat approval.
    return d

def run(argv, *, timeout=60, cwd=None, env=None, input=None):
    """Bound the entire process group, including kubectl/ssh grandchildren."""
    p = subprocess.Popen([str(x) for x in argv], cwd=cwd, env=env, text=True,
                         stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        output, _ = p.communicate(input, timeout=timeout)
        return p.returncode, output
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGTERM)
        try:
            output, _ = p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            output, _ = p.communicate()
        return 124, output
    except BaseException:
        # A round alarm must not leave a mutation tool running in the background.
        if p.poll() is None:
            os.killpg(p.pid, signal.SIGTERM)
            try:p.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL);p.communicate()
        raise

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
