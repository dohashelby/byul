"""Same-process SUP16 phase boundaries around the unchanged operating CLI.

No AWS, model, source, proof or timer is produced at import/construction. The
runtime caller supplies its fixed budget key, original total deadline and rc.
CLI stdout/stderr and original wait_child remain owned by the pinned rc.
"""
import datetime
import math
import os
from pathlib import Path
import signal
import stat
import subprocess
import threading
import time
import types

ORIGINAL_BUDGETS = {'observe': 1800, 'preserve': 300, 'quiesce': 360, 'archive': 300}


def require(value, code):
    if not value:
        raise ValueError(code)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def proc_stat(pid):
    try:
        fields = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()
        return {'pid': pid, 'state': fields[0], 'ppid': int(fields[1]),
                'pgid': int(fields[2]), 'start_ticks': int(fields[19])}
    except (FileNotFoundError, ProcessLookupError):
        return None


def group_members(pgid):
    result = {}
    for entry in Path('/proc').iterdir():
        if entry.name.isdigit():
            row = proc_stat(int(entry.name))
            if row and row['pgid'] == pgid and row['state'] != 'Z':
                result[row['pid']] = row['start_ticks']
    return result


def fd_identity():
    result = {}
    for entry in Path('/proc/self/fd').iterdir():
        try:
            result[entry.name] = os.readlink(entry)
        except FileNotFoundError:
            pass
    return result


class PhaseExpired(TimeoutError):
    pass


class Supervisor:
    def __init__(self, *, root_contract, write_new, owner, original_budget_keys,
                 additional_deadlines, serial):
        require(callable(write_new) and callable(additional_deadlines) and callable(serial),
                'SUP16_PHASE_ACTUAL_CALLBACKS_REQUIRED')
        require(original_budget_keys == {'normal': 'observe', 'preserve': 'preserve',
                'quiesce': 'quiesce', 'archive': 'archive', 'aggregate': 'archive'},
                'SUP16_PHASE_EXACT_ACTION_KEYS_REQUIRED')
        self.rc, self.write, self.owner = root_contract, write_new, owner
        self.keys = dict(original_budget_keys)
        self.additional, self.serial = additional_deadlines, serial
        self.active, self.blocked = None, False

    def phase_guard_factory(self, role, kind, total):
        require(not self.blocked and self.active is None,
                'SUP16_PHASE_NONTERMINAL_OR_OVERLAP_NO_MUTATION')
        require(kind in self.keys, 'SUP16_PHASE_EXACT_ACTION_REQUIRED')
        return Guard(self, role, kind, total)

    def assert_phase_terminal(self, guard, role, kind):
        require(guard.manager is self and guard.role is role and guard.kind == kind,
                'SUP16_PHASE_EXACT_GUARD_REQUIRED')
        require(guard.terminal is not None and guard.terminal['terminal'] is True,
                'SUP16_PHASE_INNER_PG_IO_OR_REAP_NOT_TERMINAL')


class Guard:
    def __init__(self, manager, role, kind, total):
        self.manager, self.role, self.kind, self.total = manager, role, kind, total
        self.intent, self.terminal = None, None
        self.children, self.calls = [], []
        self.expired = None
        self.saved = None
        self.armed = False

    def _alarm(self, signum, frame):
        if self.expired is None:
            self.expired = PhaseExpired('SUP16_ORIGINAL_PHASE_EXPIRED:' + self.kind)
        raise self.expired

    def _remaining(self, original_timeout):
        require(finite(original_timeout) and original_timeout >= 0,
                'SUP16_PHASE_ORIGINAL_CLI_TIMEOUT_REQUIRED')
        left = self.deadline - time.monotonic()
        if left <= 0:
            self._alarm(signal.SIGALRM, None)
        return min(original_timeout, left)

    def _popen(self, *args, **kwargs):
        # Register the ACTUAL returned child before a pending phase alarm can
        # interrupt the caller. Do not change argv, session, pipes or cwd.
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
        try:
            proc = self.saved['subprocess'].Popen(*args, **kwargs)
            observed = proc_stat(proc.pid)
            child = {'process': proc, 'pid': proc.pid,
                'start_ticks': None if observed is None else observed['start_ticks'],
                'new_session': kwargs.get('start_new_session', False) is True,
                'wait_attempts': []}
            self.children.append(child)
            original_wait = proc.wait
            def tracked_wait(timeout=None):
                attempt = {'at': now(), 'timeout': timeout, 'returned': False}
                child['wait_attempts'].append(attempt)
                try:
                    value = original_wait(timeout=timeout)
                    attempt.update(returned=True, returncode=value)
                    return value
                except BaseException as error:
                    attempt['error'] = repr(error)
                    raise
            proc.wait = tracked_wait
            return proc
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)

    def _call_cli(self, name, argv, stage, update, environment, receipts,
                  *, allowed=(0,), timeout=900):
        call = {'name': name, 'stage': str(stage), 'returned_or_unwound': False,
                'rows': receipts, 'offset': len(receipts)}
        self.calls.append(call)
        try:
            return self.saved['call_cli'](name, argv, stage, update, environment,
                receipts, allowed=allowed, timeout=self._remaining(timeout))
        except BaseException as error:
            if self.expired is not None and error is not self.expired:
                self.expired.add_note('ORIGINAL_CLI_AFTER_PHASE_ALARM:' + repr(error))
                raise self.expired
            raise
        finally:
            call['returned_or_unwound'] = True

    def _metadata(self, argv, timeout=45, limit=2097152):
        result = self.saved['bounded_metadata_query'](argv,
            timeout=self._remaining(timeout), limit=limit)
        if self.expired is not None:
            raise self.expired
        return result

    def __enter__(self):
        m = self.manager
        require(os.name == 'posix' and threading.current_thread() is threading.main_thread(),
                'SUP16_PHASE_MAIN_THREAD_LINUX_REQUIRED')
        require(m.active is None and not m.blocked, 'SUP16_PHASE_NO_OVERLAP_REQUIRED')
        require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0),
                'SUP16_PHASE_EXISTING_ALARM_MUST_NOT_BE_RESET')
        key = m.keys[self.kind]
        budget = self.role['fixed_config']['phase_budgets'][key]
        require(key in ORIGINAL_BUDGETS and budget == ORIGINAL_BUDGETS[key],
                'SUP16_PHASE_ORIGINAL_INDIVIDUAL_BUDGET_REQUIRED')
        require(finite(self.total), 'SUP16_PHASE_ORIGINAL_TOTAL_REQUIRED')
        extra = tuple(m.additional(self.role, self.kind))
        require(all(finite(v) for v in extra), 'SUP16_PHASE_ORIGINAL_ADDITIONAL_REQUIRED')
        parent = Path(self.role['outer_root'])
        meta = parent.lstat()
        require(not any(p.is_symlink() for p in (parent, *parent.parents))
                and stat.S_ISDIR(meta.st_mode) and stat.S_IMODE(meta.st_mode) == 0o700
                and meta.st_uid == m.owner == os.geteuid(), 'SUP16_PHASE_OWN_ROOT700_REQUIRED')
        self.intent_path = parent / ('phase-' + self.kind + '-intent.json')
        self.terminal_path = parent / ('phase-' + self.kind + '-terminal.json')
        require(not self.intent_path.exists() and not self.terminal_path.exists(),
                'SUP16_PHASE_ORIGINAL_INTENT_NO_REPEAT')
        started = time.monotonic()
        self.deadline = min(started + budget, self.total, *extra)
        require(self.deadline > started, 'SUP16_PHASE_ORIGINAL_WINDOW_EXPIRED')
        counter = m.serial(self.role, self.kind)
        require(type(counter) is int and counter >= 0, 'SUP16_PHASE_ACTUAL_SERIAL_REQUIRED')
        self.intent = {'phase': key, 'phase_started_monotonic': started,
            'serial': counter, 'kind': self.kind, 'role_phase': self.role['phase'],
            'at': now(), 'controller': proc_stat(os.getpid()), 'budget_seconds': budget,
            'total_deadline_monotonic': self.total,
            'additional_deadlines_monotonic': list(extra),
            'effective_deadline_monotonic': self.deadline, 'deadline_reset': False}
        # Exclusive durable intent precedes interception and every phase CLI.
        m.write(self.intent_path, self.intent, m.owner)
        m.active = self
        self.before_fds = fd_identity()
        self.parent_pgid = os.getpgrp()
        self.before_group = group_members(self.parent_pgid)
        rc = m.rc
        self.saved = {name: getattr(rc, name) for name in
                      ('subprocess', 'call_cli', 'bounded_metadata_query')}
        self.previous_handler = signal.getsignal(signal.SIGALRM)
        try:
            proxy = types.SimpleNamespace(**vars(self.saved['subprocess']))
            proxy.Popen = self._popen
            rc.subprocess = proxy
            rc.call_cli = self._call_cli
            rc.bounded_metadata_query = self._metadata
            signal.signal(signal.SIGALRM, self._alarm)
            # Anchor is the original exclusive intent, not a restarted budget.
            signal.setitimer(signal.ITIMER_REAL, self._remaining(budget))
            self.armed = True
            return self.intent
        except BaseException as error:
            self.__exit__(type(error), error, error.__traceback__)
            raise

    def __exit__(self, exc_type, error, tb):
        m = self.manager
        if self.terminal is not None:
            return False
        if self.saved is None:
            return False  # Entry failed before any owned phase was activated.
        failures = []
        try:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self.previous_handler)
            for name, value in self.saved.items():
                setattr(m.rc, name, value)
            # Original rc.wait_child is responsible for reaping/owned-PG
            # cleanup. Audit its actual result, not an optimistic receipt.
            children = []
            for row in self.children:
                proc = row['process']
                # SIGALRM may arrive after real spawn but BEFORE the original
                # caller assigns its proc variable. The original raw may then
                # say no_child_created. Keep that raw and reclaim the actual
                # registered child whose caller never entered wait. Never
                # retry an already-attempted original cleanup or reset its
                # 30-second/metadata5-second cleanup allowance.
                pending_group = row['new_session'] and m.rc.group_active(proc.pid)
                if not row['wait_attempts'] and (proc.returncode is None or pending_group):
                    try:
                        if row['new_session']:
                            cleanup = m.rc.wait_child(proc, 0)
                        else:
                            if proc.poll() is None:
                                proc.kill()
                            code = proc.wait(timeout=5)
                            cleanup = {'returncode': code, 'child_reaped': True,
                                       'metadata_direct_child_only': True}
                        row['spawn_handoff_cleanup'] = cleanup
                        if cleanup.get('cleanup_errors') or cleanup.get('child_reaped') is not True:
                            failures.append('REGISTERED_SPAWN_CLEANUP_FAILED:' + str(proc.pid))
                        for name in ('stdout', 'stderr', 'stdin'):
                            stream = getattr(proc, name)
                            if stream is not None:
                                stream.close()
                    except BaseException as later:
                        row['spawn_handoff_cleanup_error'] = repr(later)
                        failures.append('REGISTERED_SPAWN_CLEANUP_FAILED:' + str(proc.pid))
                observed = proc_stat(proc.pid)
                same_live = observed is not None and observed['state'] != 'Z' and (
                    row['start_ticks'] is None or observed['start_ticks'] == row['start_ticks'])
                group_done = not m.rc.group_active(proc.pid) if row['new_session'] else None
                pipes_closed = all(getattr(proc, name) is None or getattr(proc, name).closed
                                   for name in ('stdout', 'stderr', 'stdin'))
                child = {key: value for key, value in row.items() if key != 'process'}
                child.update(returncode=proc.returncode, child_reaped=proc.returncode is not None,
                    same_child_live=same_live, owned_session_group_terminal=group_done,
                    pipe_handles_closed=pipes_closed)
                children.append(child)
                if proc.returncode is None or same_live or not pipes_closed or group_done is False:
                    failures.append('CHILD_PG_REAP_OR_PIPE_NOT_TERMINAL:' + str(proc.pid))
            calls = []
            for call in self.calls:
                rows = call['rows'][call['offset']:]
                copied = [{k: v for k, v in row.items()} for row in rows]
                calls.append({'name': call['name'], 'stage': call['stage'],
                    'returned_or_unwound': call['returned_or_unwound'], 'cli_receipts': copied})
                if not call['returned_or_unwound'] or not rows or any(
                    row.get('child_reaped') is not True or row.get('group_terminal') is not True
                    or row.get('cleanup_errors') for row in rows):
                    failures.append('ORIGINAL_CLI_RECEIPT_NOT_TERMINAL:' + call['name'])
            after_fds = fd_identity()
            fd_delta = {key: value for key, value in after_fds.items()
                        if self.before_fds.get(key) != value}
            remaining_group = {str(pid): ticks for pid, ticks in group_members(self.parent_pgid).items()
                               if self.before_group.get(pid) != ticks}
            if fd_delta:
                failures.append('OUTER_PHASE_FD_NOT_CLOSED')
            if remaining_group:
                failures.append('NEW_CALLER_GROUP_MEMBER_NOT_TERMINAL')
            self.terminal = {'at': now(), 'phase': self.intent['phase'], 'kind': self.kind,
                'role_phase': self.role['phase'], 'original_intent': str(self.intent_path),
                'ended_monotonic': time.monotonic(), 'phase_expired': self.expired is not None,
                'original_error': None if error is None else repr(error), 'children': children,
                'cli_calls': calls, 'outer_fd_delta': fd_delta,
                'new_caller_group_members': remaining_group, 'failures': failures,
                'terminal': not failures, 'success_claimed': False, 'deadline_reset': False}
            m.write(self.terminal_path, self.terminal, m.owner)
        except BaseException as later:
            m.blocked = True
            if error is not None:
                error.add_note('SUP16_PHASE_TERMINAL_AUDIT_ERROR:' + repr(later))
                raise error.with_traceback(tb)
            raise
        finally:
            m.active = None
        if failures:
            m.blocked = True
            if error is not None:
                error.add_note('SUP16_PHASE_NONTERMINAL:' + repr(failures))
                return False
            raise RuntimeError('SUP16_PHASE_NONTERMINAL:' + repr(failures))
        if self.expired is not None and error is None:
            raise self.expired
        return False
