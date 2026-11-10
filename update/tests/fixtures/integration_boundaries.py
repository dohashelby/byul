"""Exact selected candidate functions; no operating globals or AWS client."""

import argparse,ast,hashlib,json,os,pathlib,re,signal,subprocess,sys,time,types

from pathlib import Path

import importlib.util
_capsule_path=Path(__file__).resolve().parents[2]/'tools/gpu_b_failure_capsule.py'
_spec=importlib.util.spec_from_file_location('portable_first_failure_capsule',_capsule_path)
_capsule=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_capsule)
FirstFailure=_capsule.FirstFailure
FIRST_FAILURE=FirstFailure()
ACTUAL_CHILDREN=[]
PENDING_HANDOFF_CHILD=None
PENDING_HANDOFF_CLEANUP=None


def track_actual_children(rc):
    original = rc.subprocess
    proxy = types.SimpleNamespace(**vars(original))
    def popen(*args, **kwargs):
        global PENDING_HANDOFF_CHILD, PENDING_HANDOFF_CLEANUP
        child = original.Popen(*args, **kwargs)
        # Retain the actual handle before the potentially failing list handoff.
        # A failed handoff terminates this caller; finally must still see it.
        PENDING_HANDOFF_CHILD = child
        PENDING_HANDOFF_CLEANUP = None
        try:
            ACTUAL_CHILDREN.append(child)
        except BaseException as primary:
            try:
                PENDING_HANDOFF_CLEANUP = rc.wait_child(child, 0)
            except BaseException as secondary:
                primary.add_note('Actual child handoff cleanup failed: ' + type(secondary).__name__)
            raise
        PENDING_HANDOFF_CHILD = None
        return child
    proxy.Popen = popen
    rc.subprocess = proxy
    FIRST_FAILURE.attach(rc)

def record_wrapper_finally(primary):
    require(os.getuid() == os.geteuid() == 0, 'PREP_TERMINAL_ROOT_REQUIRED')
    require(not any(p.is_symlink() for p in (ROOT, *ROOT.parents)), 'PREP_TERMINAL_NO_PATH_LINKS')
    meta = ROOT.lstat()
    require(stat.S_ISDIR(meta.st_mode) and meta.st_uid == 0
            and stat.S_IMODE(meta.st_mode) == 0o700, 'PREP_TERMINAL_OWN_ROOT700')
    caller = ACTIVE_CALLER
    children = []
    if caller is not None:
        rc = caller.modules['root_contract']
        children = [{'pid': child.pid, 'returncode': child.poll(),
                     'group_terminal': not rc.group_active(child.pid)} for child in ACTUAL_CHILDREN]
        if PENDING_HANDOFF_CHILD is not None and not any(
                row['pid'] == PENDING_HANDOFF_CHILD.pid for row in children):
            children.append({'pid': PENDING_HANDOFF_CHILD.pid,
                'returncode': PENDING_HANDOFF_CHILD.poll(),
                'group_terminal': not rc.group_active(PENDING_HANDOFF_CHILD.pid)})
    handoff_closed = PENDING_HANDOFF_CHILD is None or (
        isinstance(PENDING_HANDOFF_CLEANUP, dict)
        and PENDING_HANDOFF_CLEANUP.get('child_reaped') is True
        and PENDING_HANDOFF_CLEANUP.get('group_terminal') is True
        and PENDING_HANDOFF_CLEANUP.get('cleanup_errors') == [])
    closed = handoff_closed and all(
        row['returncode'] is not None and row['group_terminal'] for row in children)
    phase_blocked = bool(caller is not None and (caller.supervisor.blocked or caller.outer.blocked
                                                or caller.supervisor.active is not None))
    value = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
             'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
             'wrapper_pid': os.getpid(), 'actual_children': children,
             'tracked_children_terminal': closed, 'phase_or_outer_blocked': phase_blocked,
             'pending_handoff_child_pid': None if PENDING_HANDOFF_CHILD is None else PENDING_HANDOFF_CHILD.pid,
             'pending_handoff_cleanup': PENDING_HANDOFF_CLEANUP,
             'caller_constructed': caller is not None,
             'configuration_sha256': None if caller is None else caller.static['aggregate_config']['configuration_sha256'],
             'exception_type': None if primary is None else type(primary).__name__,
             'exception_message': None if primary is None else str(primary)[:1600],
             'external_SSM_terminal_still_required': True,
             'product_PASS_or_GPU_measurement_inferred': False}
    try:
        value['first_failure_capsule'] = FIRST_FAILURE.write(
            ROOT / 'first-failure-capsule.json',
            binding={k: value[k] for k in ('boot_id', 'configuration_sha256')},
            owner=0, input_owner=1000)
    except BaseException as diagnostic_error:
        value['first_failure_capsule_error'] = {'type': type(diagnostic_error).__name__, 'message': str(diagnostic_error)}
        if primary is not None:
            primary.add_note('First failure capsule unavailable: ' + type(diagnostic_error).__name__)
    raw = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
    fd = os.open(ROOT / 'current-wrapper-finally.json',
                 os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps({'wrapper_finally_saved': True, 'tracked_children_terminal': closed,
                      'phase_or_outer_blocked': phase_blocked}), flush=True)

def group_active(pgid):

    for entry in Path('/proc').iterdir():

        if not entry.name.isdigit():

            continue

        try:

            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()

            if int(fields[2]) == pgid and fields[0] != 'Z':

                return True

        except (FileNotFoundError, ProcessLookupError):

            continue

    return False

def wait_child(proc, timeout):

    result = {'timed_out': False, 'child_reaped': False, 'group_terminal': False,

        'cleanup_errors': []}

    error = None

    try:

        result['returncode'] = proc.wait(timeout=timeout)

        result['child_reaped'] = True

    except BaseException as caught:

        error = caught

        result['timed_out'] = isinstance(caught, subprocess.TimeoutExpired)

        result['wait_error'] = type(caught).__name__ + ': ' + str(caught)

    # Kill any remaining guard descendants even when the direct CLI already exited.

    try:

        if group_active(proc.pid):

            os.killpg(proc.pid, signal.SIGKILL)

    except ProcessLookupError:

        pass

    except OSError as caught:

        result['cleanup_errors'].append(type(caught).__name__ + ': ' + str(caught))

    if not result['child_reaped']:

        try:

            result['returncode'] = proc.wait(timeout=30)

            result['child_reaped'] = True

        except BaseException as caught:

            result['cleanup_errors'].append(type(caught).__name__ + ': ' + str(caught))

    until = time.monotonic() + 30

    while group_active(proc.pid) and time.monotonic() < until:

        time.sleep(.05)

    result['group_terminal'] = not group_active(proc.pid)

    if error is not None and not result['timed_out']:

        result['interrupted'] = True

    return result

def run_transfer_group(label,argv):
    is_get = len(argv) > 2 and argv[2] == 'get'
    original_deadline = time.monotonic() + 900 if is_get else None
    if is_get:
        argv = [*argv, '--deadline-monotonic', str(original_deadline)]

    uncertainty_barrier('before-local-'+label)
    helper=pathlib.Path(argv[1]);assert hashlib.sha256(helper.read_bytes()).hexdigest()==PACKET['copy_helper_sha256']
    put(label+'-group-intent.json',{'at':now(),'argv':argv,'helper_sha256':PACKET['copy_helper_sha256']})
    stdout=OUT/(label+'.group.stdout');stderr=OUT/(label+'.group.stderr')
    with stdout.open('xb') as out,stderr.open('xb') as err:
        proc=subprocess.Popen(argv,stdout=out,stderr=err,start_new_session=True)
        try:
            row={'at':now(),'PID':proc.pid,'PGID':proc.pid,'argv':argv,'helper_sha256':PACKET['copy_helper_sha256']}
            try:row['local_start_ticks']=int((Path('/proc')/str(proc.pid)/'stat').read_text().rsplit(')',1)[1].split()[19])
            except FileNotFoundError:
                if proc.poll() is None:raise
                row.update(local_start_ticks=None,start_ticks_unobserved_child_already_exited=True)
            put(label+'-group-PID.json',row)
        except BaseException as error:
            cleanup=wait_child(proc,0)
            try:
                put(label+'-group-terminal.json',{'at':now(),'argv':argv,'pid':proc.pid,'PGID':proc.pid,
                    **cleanup,'local_metadata_error':type(error).__name__})
            except OSError as secondary:
                error.add_note('Transfer terminal preservation failed: '+type(secondary).__name__)
            raise
        result=wait_child(proc,max(0,original_deadline-time.monotonic()) if is_get else 900)
    put(label+'-group-terminal.json',{'at':now(),'argv':argv,'pid':proc.pid,'PGID':proc.pid,**result,'scope':'own local transport helper and descendants only; no AWS force stop'})
    assert result['child_reaped'] and result['group_terminal'] and not result['cleanup_errors'] and not result['timed_out'],'LOCAL_TRANSFER_PROCESS_GROUP_MUST_BE_TERMINAL'
    return subprocess.CompletedProcess(argv,result['returncode'],stdout.read_bytes(),stderr.read_bytes())

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    put = sub.add_parser("put")
    put.add_argument("source", type=Path)
    put.add_argument("remote_relative")
    get = sub.add_parser("get")
    get.add_argument("remote_name")
    get.add_argument("destination", type=Path)
    get.add_argument("--expected-size", type=int, required=True)
    get.add_argument("--expected-sha256", required=True)
    for p in (put, get):
        p.add_argument("--log", type=Path, required=True, help="new private JSONL metadata log")
    get.add_argument('--deadline-monotonic', type=float, required=True)
    a = parser.parse_args(argv)
    # Import only on actual use; CPU fake-SDK contracts never touch AWS credentials.
    import aws_cli_adapter_v2
    sdk = aws_cli_adapter_v2.client("ssm", region_name=REGION)
    transfer = Transfer(sdk, a.log) if a.operation == 'put' else bind_transfer(sys.modules[__name__])(sdk, a.log, deadline=a.deadline_monotonic)
    try:
        result = transfer.put(a.source, a.remote_relative) if a.operation == "put" else transfer.get(a.remote_name, a.destination, expected_size=a.expected_size, expected_sha256=a.expected_sha256)
    finally:
        transfer.close()
    print(json.dumps(result, sort_keys=True))

def command(label,argv,timeout=45,allowed=(0,)):
    global serial
    serial+=1;name=f'{serial:03d}-{label}'
    require(re.fullmatch('[a-z0-9][a-z0-9-]{0,79}',name),'ORIGINAL_COMMAND_NAME_GUARD')
    if active_command_deadline is not None:
        remaining=active_command_deadline-time.monotonic();require(remaining>0,'ACTIVE_PREPARATION_DEADLINE_EXHAUSTED');timeout=min(timeout,remaining)
    rc.verify_controller(state)
    rc.call_cli(name,argv,state['output'],state['update'],state['environment'],state['commands'],timeout=timeout,allowed=allowed)
    rc.verify_controller(state)
    return (state['output']/(name+'.stdout')).read_bytes()

def require(ok, message):
    if not ok:
        raise ValueError(message)

def _function(value):
    value = value.__func__ if isinstance(value, types.MethodType) else value
    require(isinstance(value, types.FunctionType) and value.__closure__ is None,
            'SUP16_BINDER_REVIEWED_UNBOUND_FUNCTION_REQUIRED')
    return value

def _rebind(value, namespace):
    """Reuse the supplied code object; never duplicate validator logic."""
    value = _function(value)
    result = types.FunctionType(value.__code__, namespace, value.__name__, value.__defaults__)
    result.__kwdefaults__ = value.__kwdefaults__
    result.__annotations__ = dict(value.__annotations__)
    return result

def once(source,needle,replacement,label):
    if source.count(needle)!=1:
        raise ValueError('REQUIRED_REPLACEMENT_EXACTLY_ONCE:'+label)
    return source.replace(needle,replacement,1)

def validate_sites(wrapper,driver):
    wtree,dtree=ast.parse(wrapper),ast.parse(driver)
    track=next(n for n in wtree.body if isinstance(n,ast.FunctionDef) and n.name=='track_actual_children')
    attached=[n for n in ast.walk(track) if isinstance(n,ast.Call) and ast.unparse(n.func)=='FIRST_FAILURE.attach']
    if len(attached)!=1 or [ast.unparse(n) for n in attached[0].args]!=['rc']:
        raise ValueError('ACTUAL_WRAPPER_OBSERVER_ATTACH_EXACTLY_ONCE')
    main=next(n for n in wtree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
    call=[n for n in ast.walk(main) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='track_actual_children']
    if len(call)!=1 or ast.unparse(call[0].args[0])!="caller.modules['root_contract']":
        raise ValueError('ACTUAL_ENTRY_ROOT_CONTRACT_OBJECT_REQUIRED')
    function=next(n for n in dtree.body if isinstance(n,ast.FunctionDef) and n.name=='run_transfer_group')
    stores=[n for n in ast.walk(function) if isinstance(n,ast.Name) and n.id=='original_deadline' and isinstance(n.ctx,ast.Store)]
    flags=[n for n in ast.walk(function) if isinstance(n,ast.Constant) and n.value=='--deadline-monotonic']
    waits=[n for n in ast.walk(function) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='wait_child'
           and len(n.args)>1 and any(isinstance(x,ast.Name) and x.id=='original_deadline' for x in ast.walk(n.args[1]))]
    if len(stores)!=1 or len(flags)!=1 or len(waits)!=1:
        raise ValueError('DRIVER_DEFINE_FORWARD_WAIT_SAME_DEADLINE_REQUIRED')
    wait=waits[0].args[1]
    if not isinstance(wait,ast.IfExp) or not isinstance(wait.orelse,ast.Constant) or wait.orelse.value!=900:
        raise ValueError('PUT_ORIGINAL_900_WAIT_REQUIRED')
    return dict(observer_attach=1,entry_actual_root_contract=1,deadline_definition=1,get_flag=1,parent_remaining_wait=1,PUT_wait=900)
