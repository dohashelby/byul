"""Offline host installer. Configuration and subprocess output are never printed."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import grp
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

SOURCE = Path(__file__).resolve().parents[2]
DEST = Path("/opt/byul/runtime")
ETC = Path("/etc/serving-runtime")
STATE = Path("/var/lib/serving-runtime")
OBSERVATION = Path("/srv/serving/observation")
DATA = Path("/srv/serving/runtime/data")
BASELINE = STATE / "baseline.json"
GRUB = Path("/etc/default/grub.d/99-byul-bpf-lsm.cfg")
LSM_FILE = Path("/sys/kernel/security/lsm")
KERNEL_CONFIG = Path("/boot/config-" + os.uname().release)
GRUB_DEFAULT = Path("/etc/default/grub")
SUDOERS_DIR = Path("/etc/sudoers.d")
SUDOERS_NAME = "serving-runtime-agent"
UNITS = tuple(f"serving-runtime-{name}.service" for name in
              ("tetragon-status", "monitor-window", "agent"))
COPY = ("src", "tools", "deploy", "pyproject.toml", "README.md")
PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
DEFAULTS = {"PLATFORM_CA_FILE": "", "GATEWAY_DATA_GID": "10000",
            "OBSERVE_GID": "10010", "OBSERVATION_USER": "ubuntu"}
REQUIRED = {"CONTEXT", "NAMESPACE", "SERVER_ID", "PLATFORM_URL",
            "AGENT_TOKEN_FILE", "VIEWER_ENV_FILE", "KUBECONFIG_FILE"}


class InstallError(Exception):
    def __init__(self, message, code="CHECK_FAILED"):
        super().__init__(message)
        self.code = code
        self.stage = None
        self.check = None


def require(condition, message, code="CHECK_FAILED"):
    if not condition:
        raise InstallError(message, code)


def permission_code(error):
    # Never echo arbitrary input paths or exception text, even on permission errors.
    filename = str(error.filename or "")
    for path in ("/var/lib/serving-runtime", "/etc/serving-runtime", "/etc/sudoers.d",
                 "/opt/byul/runtime", "/etc/systemd/system", "/srv/serving/observation",
                 "/srv/serving/runtime/data", "/srv/serving/bundles",
                 "/etc/default/grub.d", "/etc/default/grub", "/sys/kernel/security/lsm",
                 "/run/byul-runtime-install.lock"):
        if filename == path or filename.startswith(path + "/"):
            return "PERMISSION_DENIED:" + path
    return "PERMISSION_DENIED"


@contextmanager
def checking(stage, name):
    try:
        yield
    except (InstallError, OSError, ValueError, KeyError, TypeError,
            subprocess.SubprocessError) as error:
        if isinstance(error, PermissionError):
            failure = InstallError("읽기·실행·쓰기 권한을 확인하세요.", permission_code(error))
        elif isinstance(error, InstallError):
            failure = error
        else:
            failure = InstallError("입력·호스트 검증 또는 설치 실패(상세 값 비노출).",
                                   "HOST_IO_FAILED" if isinstance(error, OSError) else "VALIDATION_FAILED")
        if failure.stage is None:
            failure.stage, failure.check = stage, name
        raise failure from None


def plain_path(path):
    """Reject symlink ancestors too, before any privileged read/write."""
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts, "절대 경로가 필요합니다.")
    for part in (path, *path.parents):
        require(not part.is_symlink(), "심볼릭 링크 경로를 거부합니다.")
    return path


def input_bytes(path, *, private=False):
    path = plain_path(path)
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= 1024 * 1024,
            "입력은 크기가 제한된 비어 있지 않은 일반 파일이어야 합니다.")
    owners = {0, os.getuid()}
    # sudo changes uid to root, but inputs may still belong to its invoking operator.
    sudo_uid = os.environ.get("SUDO_UID", "")
    if os.geteuid() == 0 and re.fullmatch(r"[0-9]+", sudo_uid):
        owners.add(int(sudo_uid))
    require(info.st_uid in owners and not info.st_mode & 0o022,
            "입력 소유자 또는 쓰기 권한이 안전하지 않습니다.")
    require(not private or not info.st_mode & 0o077, "비밀 입력은 0600이어야 합니다.")
    return path.read_bytes()


def load_config(path):
    config = dict(DEFAULTS)
    seen = set()
    for line in input_bytes(path, private=True).decode("utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        require(sep and key in REQUIRED | DEFAULTS.keys() and key not in seen,
                "설정 키가 잘못되었거나 중복됐습니다.")
        require(value == value.strip() and not any(ord(c) < 32 for c in value),
                "설정 값 형식이 잘못됐습니다.")
        config[key] = value
        seen.add(key)
    require(all(config.get(key) for key in REQUIRED), "필수 설정이 누락됐습니다.")
    for key in ("CONTEXT", "SERVER_ID", "OBSERVATION_USER"):
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", config[key]),
                "식별자 형식이 잘못됐습니다.")
    require(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", config["NAMESPACE"]),
            "namespace 형식이 잘못됐습니다.")
    for key in ("GATEWAY_DATA_GID", "OBSERVE_GID"):
        require(re.fullmatch(r"[1-9][0-9]{0,8}", config[key]) and int(config[key]) < 2**31,
                "그룹 번호가 잘못됐습니다.")
    require(config["GATEWAY_DATA_GID"] != config["OBSERVE_GID"], "공유 그룹은 달라야 합니다.")
    url = config["PLATFORM_URL"]
    require(re.fullmatch(r"[A-Za-z0-9:/._-]+", url), "플랫폼 URL 형식이 잘못됐습니다.")
    parsed = urlsplit(url)
    require(parsed.hostname and not parsed.username and not parsed.password
            and (parsed.scheme == "https" or
                 (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"})),
            "플랫폼은 HTTPS 또는 loopback HTTP만 허용합니다.")
    # Also validate port without exposing ValueError's input value.
    parsed.port
    for key in ("AGENT_TOKEN_FILE", "VIEWER_ENV_FILE", "KUBECONFIG_FILE", "PLATFORM_CA_FILE"):
        if config[key]:
            require(re.fullmatch(r"/[A-Za-z0-9_./+-]+", config[key]), "입력 경로 형식이 잘못됐습니다.")
            input_bytes(config[key], private=key in {"AGENT_TOKEN_FILE", "VIEWER_ENV_FILE"})
    return config


def render(config, execute=False):
    """Return artifacts in memory; never echo rendered configuration."""
    result = {}
    for name in UNITS:
        text = (SOURCE / "deploy/agent" / name).read_text()
        text = text.replace("lab-context", config["CONTEXT"])
        text = text.replace("--namespace serving", "--namespace " + config["NAMESPACE"])
        text = text.replace("--server server-01", "--server " + config["SERVER_ID"])
        text = re.sub(r"^SupplementaryGroups=.*$",
                      "SupplementaryGroups=" + config["GATEWAY_DATA_GID"] + " " + config["OBSERVE_GID"],
                      text, flags=re.M)
        if name == "serving-runtime-agent.service":
            if execute:
                text = text.replace(" --dry-run", " --execute")
            if config["PLATFORM_CA_FILE"]:
                text = text.replace(" --sudo-bundle-apply", " --ca-file /etc/serving-runtime/platform-ca.pem --sudo-bundle-apply")
        result[name] = text.encode()
    text = (SOURCE / "deploy/agent/sudoers.example").read_text()
    # Validated identifiers contain only letters/digits/_.-; only '.' is an ERE
    # metacharacter here. sudoers argument regexes need no extra backslash layer.
    context = config["CONTEXT"].replace(".", r"\.")
    result["sudoers"] = text.replace("--context lab-context", "--context " + context).replace(
        "--namespace serving", "--namespace " + config["NAMESPACE"]).encode()
    result["agent.env"] = ("PLATFORM_URL=" + config["PLATFORM_URL"] + "\n").encode()
    return result


def run(command, *, capture=False, reject_stderr=False):
    name = Path(command[0]).name
    # Executable names may also come from a path; emit only known tool labels.
    if name not in {"visudo", "systemctl", "systemd-analyze", "sudo", "groupadd",
                    "useradd", "usermod", "update-grub", "python", "python3", "python3.12"}:
        name = "external"
    code = "VISUDO_FAILED" if name == "visudo" else "COMMAND_FAILED:" + name
    try:
        result = subprocess.run([str(v) for v in command], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                                stderr=subprocess.PIPE if reject_stderr else subprocess.DEVNULL,
                                check=False, timeout=120,
                                env={"PATH": PATH, "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"})
    except (OSError, subprocess.SubprocessError) as error:
        if isinstance(error, PermissionError):
            raise InstallError("명령 실행 권한이 없습니다(출력 생략).", permission_code(error)) from None
        raise InstallError("외부 명령 실행 실패(출력 생략).", code) from None
    require(result.returncode == 0 and not (reject_stderr and result.stderr),
            "외부 명령 검증에 실패했습니다(출력 생략).", code)
    return result.stdout if capture else None


def check_sudoers_conflicts():
    """Inspect every other file, including backups; never remove or print contents."""
    conflicts = []
    alias_conflict = False
    plain_path(SUDOERS_DIR)
    for path in sorted(SUDOERS_DIR.iterdir()):
        if path.name == SUDOERS_NAME:
            continue  # The managed file is replaced and then checked by visudo -c.
        plain_path(path)
        require(path.is_file(), "sudoers 항목의 일반 파일 여부를 검수하세요.", "SUDOERS_FILE_INVALID")
        text = path.read_text().replace("\\\n", "")
        text = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
        alias = re.search(r"(?<![A-Za-z0-9_.-])BYUL_BUNDLE_APPLY(?![A-Za-z0-9_.-])", text)
        rule = re.search(r"(?<![A-Za-z0-9_.-])runtime-agent(?![A-Za-z0-9_.-])", text)
        if alias or rule:
            conflicts.append(json.dumps(path.name, ensure_ascii=True))
            alias_conflict |= bool(alias)
    require(not conflicts, "충돌 파일: " + ", ".join(conflicts),
            "SUDOERS_ALIAS_CONFLICT" if alias_conflict else "SUDOERS_RULE_CONFLICT")


def active(unit):
    try:
        result = subprocess.run(["systemctl", "is-active", "--quiet", unit],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=15, env={"PATH": PATH})
    except (OSError, subprocess.SubprocessError) as error:
        if isinstance(error, PermissionError):
            raise InstallError("명령 실행 권한이 없습니다(출력 생략).", permission_code(error)) from None
        raise InstallError("서비스 확인 명령 실행 실패(출력 생략).", "COMMAND_FAILED:systemctl") from None
    return result.returncode == 0


def secure_target(path):
    for parent in (plain_path(path), *Path(path).parents):
        if parent.exists():
            info = parent.stat()
            require(info.st_uid == 0 and not info.st_mode & 0o022,
                    "설치 경로는 root 소유이며 그룹/기타 쓰기가 없어야 합니다.")


def metadata(path, uid, gid, mode):
    info = path.stat()
    if (info.st_uid, info.st_gid) != (uid, gid):
        os.chown(path, uid, gid)
    if stat.S_IMODE(path.stat().st_mode) != mode:
        path.chmod(mode)
    info = path.stat()
    require((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (uid, gid, mode),
            "파일 권한 검증에 실패했습니다.")


def directory(path, uid=0, gid=0, mode=0o755):
    path = plain_path(path)
    require(not path.exists() or path.is_dir(), "설치 폴더 형식이 잘못됐습니다.")
    path.mkdir(parents=True, exist_ok=True)
    metadata(path, uid, gid, mode)


def write_file(path, data, *, uid=0, gid=0, mode=0o644):
    path = plain_path(path)
    require(not path.exists() or path.is_file(), "대상 파일 형식이 잘못됐습니다.")
    if not path.exists() or path.read_bytes() != data:
        fd, temporary = tempfile.mkstemp(prefix=".byul-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                os.fchown(stream.fileno(), uid, gid)
                os.fchmod(stream.fileno(), mode)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    metadata(path, uid, gid, mode)
    require(path.read_bytes() == data, "파일 내용 검증에 실패했습니다.")


def check_baseline(config):
    validate_baseline(BASELINE, config)


def validate_baseline(path, config):
    info = plain_path(path).stat()
    require(info.st_uid in {0, pwd.getpwnam("runtime-agent").pw_uid} and not info.st_mode & 0o022
            and stat.S_ISREG(info.st_mode) and 0 < info.st_size <= 1024 * 1024,
            "baseline 권한·크기를 검수해야 합니다.")
    value = json.loads(path.read_bytes())
    require(isinstance(value, dict) and value.get("schema_version") == "runtime-tetragon-baseline/1.0"
            and value.get("context") == config["CONTEXT"] and isinstance(value.get("policies"), list)
            and value["policies"],
            "baseline context·정책 범위를 검수해야 합니다.")
    require(all(isinstance(p, dict) and p.get("mode") in {"monitor", "monitor_only"}
                and all(isinstance(p.get(k), str) and p[k] for k in ("node", "namespace", "name"))
                and isinstance(p.get("spec_sha256"), str) and re.fullmatch(r"[a-f0-9]{64}", p["spec_sha256"])
                for p in value["policies"]), "baseline은 monitor 정책만 허용합니다.")


def group_check(name, gid):
    try:
        require(grp.getgrgid(gid).gr_name == name, "공유 GID가 다른 그룹과 충돌합니다.")
    except KeyError:
        pass
    try:
        require(grp.getgrnam(name).gr_gid == gid, "공유 그룹 이름이 다른 GID와 충돌합니다.")
    except KeyError:
        pass


def bpf_plan(enable):
    lsms = LSM_FILE.read_text().strip()
    require(re.fullmatch(r"[a-z0-9_]+(?:,[a-z0-9_]+)*", lsms), "활성 LSM 목록을 확인할 수 없습니다.")
    if "bpf" in lsms.split(","):
        return None
    require(enable, "BPF LSM이 없습니다. 유지보수 시 --enable-bpf-lsm을 명시하세요.")
    require("CONFIG_BPF_LSM=y" in KERNEL_CONFIG.read_text().splitlines(),
            "커널 CONFIG_BPF_LSM=y가 필요합니다.")
    require(shutil.which("update-grub", path=PATH), "update-grub 설치가 필요합니다.")
    content = ('# Managed by byul runtime installer\nGRUB_CMDLINE_LINUX="$GRUB_CMDLINE_LINUX lsm='
               + lsms + ',bpf"\n').encode()
    # Do not source arbitrary GRUB shell or silently override an existing lsm=.
    for path in [GRUB_DEFAULT, *GRUB.parent.glob("*.cfg")]:
        plain_path(path)
        if path == GRUB:
            require(path.read_bytes() == content, "기존 BPF LSM 설정을 수동으로 통합해야 합니다.")
        elif path.exists():
            require(not any(re.search(r"\blsm\s*=", line) for line in path.read_text().splitlines()
                            if not line.lstrip().startswith("#")),
                    "기존 GRUB lsm= 설정을 수동으로 통합해야 합니다.")
    return content


def preflight(config, args):
    with checking("4", "SUDOERS_CONFLICTS"):
        check_sudoers_conflicts()
    require(sys.version_info >= (3, 12), "Python 3.12 이상이 필요합니다.")
    for name in ("systemctl", "systemd-analyze", "visudo", "sudo", "kubectl", "groupadd", "useradd", "usermod"):
        require(shutil.which(name, path=PATH), "필수 호스트 도구가 없습니다. 설치 문서를 확인하세요.")
    require(shutil.which("crictl", path=PATH),
            "crictl이 없습니다. 버전·체크섬·CRI endpoint를 검수해 수동 설치 후 재실행하세요. 자동 다운로드하지 않습니다.",
            "MISSING_TOOL:crictl")
    for name, key in (("serving-bundles", "GATEWAY_DATA_GID"), ("serving-observe", "OBSERVE_GID")):
        group_check(name, int(config[key]))
    observer = pwd.getpwnam(config["OBSERVATION_USER"])
    require(observer.pw_uid != 0, "관측 수집기는 별도 비root 사용자여야 합니다.")
    try:
        account = pwd.getpwnam("runtime-agent")
    except KeyError:
        account = None
    if account is not None:
        require(account.pw_uid != 0 and account.pw_gid != 0
                and grp.getgrgid(account.pw_gid).gr_name == "runtime-agent",
                "기존 runtime-agent 계정을 검수해야 합니다.")
    for target in (DEST, ETC, Path("/etc/systemd/system"), SUDOERS_DIR, STATE,
                   OBSERVATION.parent, DATA.parent, Path("/srv/serving/bundles")):
        # STATE may already be owned by runtime-agent; validate its parent only.
        secure_target(target.parent if target == STATE else target)
    for target in (STATE, OBSERVATION, DATA):
        plain_path(target)
    for name in COPY:
        source = SOURCE / name
        paths = [source, *source.rglob("*")] if source.is_dir() else [source]
        require(all(not p.is_symlink() and (p.is_file() or p.is_dir()) for p in paths),
                "코드 원본의 링크·특수 파일을 거부합니다.")
    require(DEST != SOURCE and DEST not in SOURCE.parents, "설치본 내부에서 재설치하지 마세요.")
    if config["PLATFORM_CA_FILE"]:
        import ssl
        ssl.create_default_context(cafile=config["PLATFORM_CA_FILE"])
    grub = bpf_plan(args.enable_bpf_lsm)
    if grub is not None:
        secure_target(GRUB)
    with checking("9", "BASELINE_PREFLIGHT"):
        if BASELINE.exists():
            check_baseline(config)
        else:
            require(args.capture_baseline or grub is not None,
                    "baseline이 없습니다. 정상 monitor 정책 확인 후 --capture-baseline을 명시하세요.")
    return grub


def install_accounts(config):
    for name, key in (("serving-bundles", "GATEWAY_DATA_GID"), ("serving-observe", "OBSERVE_GID")):
        gid = int(config[key])
        try:
            grp.getgrgid(gid)
        except KeyError:
            run(["groupadd", "--gid", gid, name])
        group_check(name, gid)
    try:
        grp.getgrnam("runtime-agent")
    except KeyError:
        run(["groupadd", "--system", "runtime-agent"])
    try:
        pwd.getpwnam("runtime-agent")
    except KeyError:
        run(["useradd", "--system", "--gid", "runtime-agent", "--home-dir", STATE,
             "--shell", "/usr/sbin/nologin", "runtime-agent"])
    required = {int(config[k]) for k in ("GATEWAY_DATA_GID", "OBSERVE_GID")}
    account = pwd.getpwnam("runtime-agent")
    if not required <= set(os.getgrouplist("runtime-agent", account.pw_gid)):
        run(["usermod", "--append", "--groups", ",".join(map(str, sorted(required))), "runtime-agent"])
    require(required <= set(os.getgrouplist("runtime-agent", account.pw_gid)), "계정 보조 그룹 검증 실패.")
    return account


def install_code():
    directory(DEST)
    for name in COPY:
        source = SOURCE / name
        for path in [source, *sorted(source.rglob("*"))] if source.is_dir() else [source]:
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            target = DEST / path.relative_to(SOURCE)
            if path.is_dir():
                directory(target)
            else:
                write_file(target, path.read_bytes(), mode=0o755 if path.stat().st_mode & 0o111 else 0o644)
    venv = DEST / ".venv"
    plain_path(venv)
    if not venv.exists():
        run([sys.executable, "-m", "venv", "--without-pip", venv])
    # Existing environments must also be root-owned and go-w, including symlinks.
    for path in [DEST, *DEST.rglob("*")]:
        info = path.lstat()
        require(info.st_uid == 0 and not (not path.is_symlink() and info.st_mode & 0o022),
                "코드·venv의 root 소유/go-w 검증에 실패했습니다.")
        if path.is_symlink():
            secure_target(path.resolve())
    sites = list(venv.glob("lib/python3.*/site-packages"))
    require(len(sites) == 1, "venv site-packages 검증 실패.")
    write_file(sites[0] / "byul-runtime.pth", f"{DEST}/src\n{DEST}\n".encode())
    run([venv / "bin/python", "-I", "-B", "-c",
         "import serving_runtime, tools.tetragon_status, tools.tetragon_monitor_window; "
         "import sys; assert sys.version_info >= (3,12)"])


def install_files(config, account, artifacts):
    with checking("3", "CONFIG_FILES"):
        directory(ETC, mode=0o750, gid=account.pw_gid)
        for key, name, uid, mode in (("AGENT_TOKEN_FILE", "agent.token", account.pw_uid, 0o600),
                                    ("VIEWER_ENV_FILE", "viewer.env", 0, 0o640),
                                    ("KUBECONFIG_FILE", "kubeconfig", 0, 0o640)):
            write_file(ETC / name, input_bytes(config[key], private=key != "KUBECONFIG_FILE"),
                       uid=uid, gid=account.pw_gid, mode=mode)
        write_file(ETC / "agent.env", artifacts["agent.env"], gid=account.pw_gid, mode=0o640)
        if config["PLATFORM_CA_FILE"]:
            write_file(ETC / "platform-ca.pem", input_bytes(config["PLATFORM_CA_FILE"]),
                       gid=account.pw_gid, mode=0o640)
    with tempfile.TemporaryDirectory(prefix="byul-install-") as temporary:
        temporary = Path(temporary)
        with checking("4", "SUDOERS_VALIDATE"):
            check_sudoers_conflicts()
            sudoers = temporary / "sudoers"
            write_file(sudoers, artifacts["sudoers"], mode=0o440)
            run(["visudo", "-cf", sudoers], reject_stderr=True)
            write_file(SUDOERS_DIR / SUDOERS_NAME, artifacts["sudoers"], mode=0o440)
            # The full policy can fail or warn even when this file parses alone.
            run(["visudo", "-c"], reject_stderr=True)
        with checking("5", "SYSTEMD_VALIDATE"):
            # Verify rendered units before replacing the installed units.
            for name in UNITS:
                write_file(temporary / name, artifacts[name])
            run(["systemd-analyze", "verify", *[temporary / name for name in UNITS]])
            for name in UNITS:
                write_file(Path("/etc/systemd/system") / name, artifacts[name])
            run(["systemctl", "daemon-reload"])


def capture_baseline(config, capture):
    if BASELINE.exists():
        check_baseline(config)
        print("baseline: 기존 검증본 유지(덮어쓰기 없음)")
    else:
        require(capture, "최초 baseline 캡처 옵션이 필요합니다.")
        # Capture to a fresh candidate: a failed capture must not become tomorrow's baseline.
        candidate = STATE / "baseline.capture.json"
        require(not plain_path(candidate).exists(), "미완료 baseline 후보를 검수·제거 후 재실행하세요.")
        # shell arguments are positional; neither config nor secrets become shell code.
        run(["sudo", "-u", "runtime-agent", "--", "env", f"KUBECONFIG={ETC}/kubeconfig",
             "/bin/sh", "-c", 'umask 022; cd /opt/byul/runtime || exit; exec "$@"', "byul-baseline",
             DEST / ".venv/bin/python", "-B", "-m", "tools.tetragon_status",
             "--context", config["CONTEXT"], "--evidence-dir", STATE / "evidence",
             "--output", STATE / "tetragon-state.json",
             "--public-output", OBSERVATION / "tetragon-status/tetragon-state.json",
             "--capture-baseline", candidate])
        # Reject enforce/unknown policies before atomically accepting the candidate.
        validate_baseline(candidate, config)
        # link fails if a concurrent writer has created BASELINE; never overwrite it.
        plain_path(BASELINE)
        os.link(candidate, BASELINE)
        candidate.unlink()
        check_baseline(config)
    run(["sudo", "-u", "runtime-agent", "--", "test", "-r", BASELINE])


def summary():
    healthy = True
    for name in UNITS:
        running = active(name)
        print(name + (": active" if running else ": inactive"))
        healthy &= running
    try:
        value = json.loads((STATE / "tetragon-state.json").read_bytes())
        at = datetime.fromisoformat(value["generated_at"].replace("Z", "+00:00"))
        require(0 <= (datetime.now(timezone.utc) - at).total_seconds() <= 20, "상태가 오래됐습니다.")
        monitoring = value["monitoring"]
        state = monitoring.get("state")
        label = state if state in {"healthy", "recovering", "error", "not_configured"} else "unknown"
        checks = ("policies_verified", "writer_verified", "identities_verified", "events_verified")
        count = sum(monitoring.get(key) is True for key in checks)
        print(f"복구 상태: {label}; 검증 조건 {count}/4")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, InstallError):
        print("복구 상태: unknown; 최신 상태 재확인 필요")
    require(healthy, "서비스 활성 확인 실패. 로그는 비밀값을 가리고 별도 검수하세요.")


@contextmanager
def install_lock():
    path = plain_path("/run/byul-runtime-install.lock")
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        require(os.fstat(fd).st_uid == 0, "설치 잠금 소유자가 잘못됐습니다.")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def install(config, args, artifacts, grub):
    with checking("0", "INSTALL_LOCK"), install_lock():
        with checking("4", "SUDOERS_CONFLICTS"):
            check_sudoers_conflicts()
        with checking("0", "SERVICES_STOP"):
            # Stop before changing code, credentials or baseline; failures leave services stopped.
            for unit in reversed(UNITS):
                if active(unit):
                    run(["systemctl", "stop", unit])
                require(not active(unit), "서비스 중지 검증 실패.")
        with checking("1", "ACCOUNTS"):
            account = install_accounts(config)
            print("1/9 계정·그룹 검증 완료")
        with checking("2", "CODE_VENV"):
            install_code()
            print("2/9 root 소유 코드·venv·.pth 검증 완료")
        with checking("3–5", "INSTALL_FILES"):
            install_files(config, account, artifacts)
        print("3–5/9 설정·sudoers·systemd 검증 완료")
        with checking("6–7", "SHARED_PERMISSIONS"):
            directory(STATE, account.pw_uid, account.pw_gid, 0o750)
            directory(STATE / "evidence", account.pw_uid, account.pw_gid, 0o750)
            directory(Path("/srv/serving/bundles"), 0, int(config["GATEWAY_DATA_GID"]), 0o2750)
            directory(OBSERVATION, pwd.getpwnam(config["OBSERVATION_USER"]).pw_uid,
                      int(config["OBSERVE_GID"]), 0o2750)
            # Give the status writer its own directory; the shared parent stays go-w.
            directory(OBSERVATION / "tetragon-status", account.pw_uid,
                      int(config["OBSERVE_GID"]), 0o2750)
            directory(OBSERVATION / "lab-reports", pwd.getpwnam(config["OBSERVATION_USER"]).pw_uid,
                      int(config["OBSERVE_GID"]), 0o2750)
            # The Gateway UID is 10000, independently of its configured data GID.
            directory(DATA, 10000, int(config["GATEWAY_DATA_GID"]), 0o750)
            for path in (ETC / "kubeconfig", ETC / "viewer.env", OBSERVATION, DATA):
                run(["sudo", "-u", "runtime-agent", "--", "test", "-r", path])
            print("6–7/9 공유 폴더·읽기 권한·crictl 확인 완료")
        with checking("8", "BPF_LSM"):
            if grub is not None:
                directory(GRUB.parent)
                write_file(GRUB, grub)
                run(["update-grub"])
                print("8/9 재부팅 필요: BPF LSM 부팅 설정 검증 완료. 서비스는 중지 상태입니다.")
                print("자동 재부팅하지 않습니다. 재부팅 후 같은 설치 명령을 다시 실행하세요.")
                return 2
            print("8/9 활성 BPF LSM 확인 완료")
        with checking("9", "BASELINE_SERVICES"):
            capture_baseline(config, args.capture_baseline)
            for unit in UNITS:
                run(["systemctl", "enable", unit])
                run(["systemctl", "is-enabled", "--quiet", unit])
                run(["systemctl", "start", unit])
                require(active(unit), "서비스 시작 검증 실패.")
            print("9/9 서비스 enable/start 완료; active는 운영 준비 완료 판정이 아닙니다.")
            time.sleep(6)
            summary()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="런타임 노드 설치(설정 값·자식 명령 출력 비노출)")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true", help="무변경 로컬 사전 점검; 클러스터 조회 없음")
    parser.add_argument("--execute", action="store_true", help="설치될 agent만 실제 운영 실행 모드로 설정")
    parser.add_argument("--enable-bpf-lsm", action="store_true", help="필요할 때만 GRUB 설정; 자동 재부팅 없음")
    parser.add_argument("--capture-baseline", action="store_true", help="검수된 monitor 정책의 최초 baseline만 캡처")
    args = parser.parse_args(argv)
    try:
        with checking("0", "CONFIG_INPUT"):
            config = load_config(args.config)
        with checking("0", "RENDER"):
            artifacts = render(config, args.execute)
        print("설정·치환 검사 완료(값 비노출); agent 모드: " + ("execute" if args.execute else "dry-run"))
        print("사용자 서비스: 별도 사용자 설치 필요 — deploy/install/README.md 참조")
        with checking("0", "PREFLIGHT"):
            grub = preflight(config, args)
        if args.dry_run:
            print("dry-run: 1–9 사전 점검 완료; 계정·파일·서비스·GRUB 무변경, 클러스터 조회 없음")
            print("대기 작업: " + ("BPF LSM 설정·재부팅 후 재실행" if grub else "baseline 검증 후 서비스 시작"))
            return 0
        with checking("0", "ROOT_PERMISSION"):
            require(os.geteuid() == 0, "실제 설치에는 root 권한이 필요합니다.", "PERMISSION_DENIED")
        os.umask(0o022)
        return install(config, args, artifacts, grub)
    except InstallError as error:
        print(f"중단: 단계 {error.stage}/9; 점검 {error.check}; 이유 {error.code}; {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
