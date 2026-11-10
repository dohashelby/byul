from pathlib import Path
import pathlib,datetime,hashlib,json,os,signal,subprocess,sys,time,shlex,base64,zlib
select_archive=None
IID='i-05e8da8ea99593196'
IDS=[IID]
OUT=Path(__file__).parent
PACKET=None
boto3=None
SSM=None
EC2=None

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()

def put(name,value):
    path=OUT/name;path.parent.mkdir(mode=0o700,parents=True,exist_ok=True);path.open('x').write(json.dumps(value,default=str,indent=2)+'\n')

def states():
    r=EC2.describe_instances(InstanceIds=IDS)
    return {x['InstanceId']:x['State']['Name'] for row in r['Reservations'] for x in row['Instances']}

def active_commands():
    # Server filters avoid scanning completed history; Pending is account-wide
    # because InstanceId may not have been assigned. No fallback or retry.
    active={}
    for key,value in (('Status','Pending'),('ExecutionStage','Executing')):
        pages=SSM.get_paginator('list_commands').paginate(Filters=[{'key':key,'value':value}])
        for page in pages:
            for command in page['Commands']:
                if command['Status'] in ('Pending','InProgress','Delayed','Cancelling'):
                    active[command['CommandId']]=command['Status']
    return sorted(active)

def uncertainty_barrier(stage,known_uncertain=False):
    pending=boto3.unresolved_mutations(include_inflight=True)
    if pending or known_uncertain:
        name='supervisor-NEEDS-READONLY-RECONCILIATION.json'
        if not (OUT/name).exists():put(name,{'at':now(),'stage':stage,'unresolved_or_nonterminal_requests':pending,'typed_uncertain_failure':known_uncertain,'following_SSM_stop_or_retry_submitted':False,'required':'positive exact original request acceptance/terminal read receipts before frozen cleanup primitives; active=[] alone is insufficient','original_backstop_unchanged':True})
        raise RuntimeError('UNRESOLVED_API_ACCEPTANCE_OR_NONTERMINAL_REQUEST_NO_FOLLOWING_MUTATION')

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

def start():
    assert not PACKET.get('not_ready',True),'FINAL_COST_FREEZE_AND_REQUIRED_REVIEW_NEEDED'
    uncertainty_barrier('before-start')
    boto3.assert_identity()
    at=now(); current=states()
    assert all(s=='stopped' for s in current.values()) and not active_commands()
    raw=EC2.describe_instances(InstanceIds=[IID])
    node,=[i for r in raw['Reservations'] for i in r['Instances']]
    assert node['InstanceId']==IID and node['InstanceType']=='g6.2xlarge'
    assert node['IamInstanceProfile']['Arn']==PACKET['expected_instance_profile_arn']
    assert sorted(x['Ebs']['VolumeId'] for x in node['BlockDeviceMappings'])==PACKET['expected_volume_ids']
    assert sorted(x['GroupId'] for x in node['SecurityGroups'])==PACKET['expected_security_group_ids']
    budget_owner = PACKET.get('resource_budget_confirmation_owner', 'operator')
    if budget_owner == 'user':
        delegation = PACKET['user_resource_budget_delegation']
        assert delegation == {
            'scope': 'SUP16-existing-Seoul-one-L4-TP1-once',
            'account': '834058543745', 'instance_id': IID,
            'physical_GPU_count': 1, 'TP': 1,
            'confirmation_owner': 'user',
            'fresh_resource_inventory_and_budget_confirmation_delegated': True,
            'existing_cap_and_original_time_protection_unchanged': True,
            'source': 'user-direct-instruction-confirmed-by-authorized-root',
        }, 'EXACT_USER_DELEGATION_SCOPE_REQUIRED'
        assert PACKET['cost_as_of'] is None and PACKET['cost_estimate'] is None, 'DO_NOT_CLAIM_NEW_OPERATOR_BUDGET_OBSERVATION'
        upper = None
    else:
        assert budget_owner == 'operator', 'KNOWN_RESOURCE_BUDGET_CONFIRMATION_OWNER_REQUIRED'
        age=(datetime.datetime.fromisoformat(at)-datetime.datetime.fromisoformat(PACKET['cost_as_of'])).total_seconds()
        upper=PACKET['cost_estimate']+age/3600*PACKET['storage_rate_per_hour']+PACKET['next_worst_including_cleanup']
        assert 0<=age<1800 and upper<95 and PACKET['next_worst_including_cleanup']>=6.21
    assert EC2.describe_instance_attribute(InstanceId=IID,Attribute='instanceInitiatedShutdownBehavior')['InstanceInitiatedShutdownBehavior']['Value']=='stop'
    put('start-intent.json',{'at':at,'states':current,'controller_sha256':PACKET['controller_sha256'],'worst_cost_USD':upper,'resource_budget_confirmation_owner':budget_owner,'fresh_operator_budget_observation_claimed':budget_owner=='operator','configured_physical_GPU_count':1,'changed_type':'g6.2xlarge'})
    response=EC2.start_instances(InstanceIds=[IID])
    put('start-response.json',{'at':at,'response':response})
    print(json.dumps({'at':at,'states':response['StartingInstances']}),flush=True)

def stop():
    uncertainty_barrier("before-stop")
    at=now();assert states()[IID]=='running' and not active_commands()
    response=EC2.stop_instances(InstanceIds=[IID]);put('stop-response.json',{'at':at,'response':response});print(at,response['StoppingInstances'])


def normal_stop_once():
    uncertainty_barrier('before-original-normal-stop-once')
    assert not (OUT/'stop-response.json').exists()
    put('normal-stop-once-intent.json',{'at':now(),'instance_id':IID,'normal_stop_only':True,'force':False})
    stop()

def wait_command(label,cid,seconds):
    import time
    deadline=time.monotonic()+seconds
    while True:
        try:r=SSM.get_command_invocation(CommandId=cid,InstanceId=IID)
        except SSM.exceptions.InvocationDoesNotExist:
            assert time.monotonic()<deadline;time.sleep(3);continue
        if r['Status'] not in ['Pending','InProgress','Delayed','Cancelling']:break
        assert time.monotonic()<deadline;time.sleep(3)
    put(label+'-terminal.json',r)
    print(json.dumps({'phase':label,'status':r['Status'],'response_code':r['ResponseCode'],'stdout_tail':r['StandardOutputContent'][-1400:],'stderr_tail':r['StandardErrorContent'][-2200:]}),flush=True)
    assert r['Status']=='Success' and r['ResponseCode']==0
    return r


def checked_file(relative, expected):
    path = OUT / relative
    assert not any(p.is_symlink() for p in [path, *path.parents])
    meta = path.lstat()
    import stat
    assert stat.S_ISREG(meta.st_mode) and meta.st_uid == 1000 and meta.st_nlink == 1
    assert stat.S_IMODE(meta.st_mode) == 0o600
    raw = path.read_bytes()
    assert len(raw) == expected['bytes'] and hashlib.sha256(raw).hexdigest() == expected['sha256']
    return raw


def submit(label, argv, seconds):
    uncertainty_barrier('before-' + label)
    assert states()[IID] == 'running' and not active_commands()
    info = SSM.describe_instance_information(Filters=[{'Key': 'InstanceIds', 'Values': [IID]}])['InstanceInformationList']
    assert len(info) == 1 and info[0]['PingStatus'] == 'Online'
    shell = shlex.join(argv)
    request = {'InstanceIds': [IID], 'DocumentName': 'AWS-RunShellScript',
        'Comment': 'BYUL REQ-SUP16 TP1 ' + label + ' final source frozen',
        'Parameters': {'commands': [shell], 'executionTimeout': [str(seconds)]},
        'TimeoutSeconds': 120, 'MaxConcurrency': '1', 'MaxErrors': '0'}
    request_sha = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    put(label + '-intent.json', {'at': now(), 'phase': label, 'request': request, 'request_sha256': request_sha})
    response = SSM.send_command(**request)
    record = {'at': now(), 'phase': label, 'request': request, 'request_sha256': request_sha, 'response': response}
    put(label + '-submission.json', record)
    return record


def health():
    raw=checked_file('health_readonly.py',PACKET['local_files']['health_readonly.py'])
    encoded=base64.b64encode(zlib.compress(raw,9)).decode()
    code="import base64,zlib,hashlib,json,pathlib;raw=zlib.decompress(base64.b64decode("+repr(encoded)+"));assert hashlib.sha256(raw).hexdigest()=="+repr(hashlib.sha256(raw).hexdigest())+";ns={'__name__':'s2_original_health'};exec(compile(raw,'same-original-health','exec'),ns);ns['main']();p=pathlib.Path("+repr(PACKET['remote_health_output']+'/health-receipt.json')+");b=p.read_bytes();v=json.loads(b);print(json.dumps({'original_health_binding':{'path':str(p),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest(),'health_complete':v['health_complete'],'physical_GPU_count':v['physical_GPU_count'],'prior_GPU_processes':v['prior_GPU_processes'],'timer_reset':v['timer_reset'],'PG_terminal':v['PG_terminal']}}))"
    record=submit('health',[PACKET['bootstrap_python'],'-I','-S','-B','-c',code,'--output',PACKET['remote_health_output']],420)
    terminal=wait_command('health',record['response']['Command']['CommandId'],480)
    rows=[]
    for line in terminal['StandardOutputContent'].splitlines():
        try:value=json.loads(line)
        except ValueError:continue
        if type(value) is dict and 'original_health_binding' in value:rows.append(value['original_health_binding'])
    assert len(rows)==1,'ONE_ACTUAL_ORIGINAL_HEALTH_BINDING_REQUIRED'
    value=rows[0]
    assert value['path']==PACKET['remote_health_output']+'/health-receipt.json'
    assert value['health_complete'] is True and value['physical_GPU_count']==1 and value['prior_GPU_processes']==0
    assert value['timer_reset'] is False and value['PG_terminal'] is True
    assert __import__('re').fullmatch('[a-f0-9]{64}',value['sha256'])
    put('current-health-binding.json',{'at':now(),'producer':record['response']['Command']['CommandId'],'binding':value,'historical_experiment_identity_reused':False})
    return value


def upload_assets():
    helper=OUT/'copy_helper-exact-download-v1.py'
    for item in PACKET['uploads']:
        label,relative,remote=item['label'],item['local'],item['remote_name']
        assert __import__('re').fullmatch('[a-z0-9-]{1,48}',label)
        assert __import__('re').fullmatch('[A-Za-z0-9][A-Za-z0-9._-]{0,120}',remote)
        source=OUT/relative
        expected=PACKET['local_files'][relative]
        checked_file(relative,expected)
        result=run_transfer_group('upload-'+label,[sys.executable,str(helper),'put',str(source),remote,'--log',str(OUT/('upload-'+label+'.jsonl'))])
        assert result.returncode==0
        receipt=json.loads(result.stdout.decode().strip().splitlines()[-1])
        assert receipt['size']==expected['bytes'] and receipt['sha256']==expected['sha256']
        assert receipt['remote']==PACKET['remote_transfer_base']+'/'+remote
        put('upload-'+label+'-verified.json',{'at':now(),'receipt':receipt,'source_is_new_operating_assets_not_model':True})


def install_assets():
    # Exact argv is generated and reviewed with final source and payload pins.
    phase('install')


def phase(mode):
    limits={'install':(420,480),'source-install':(900,1020),'preflight':(420,480),'prepare':(1800,1920),'observe':(1800,1920),'preserve':(300,420),'quiesce':(360,480),'archive':(300,420)}
    assert mode in limits
    command=PACKET['phases'][mode]
    assert set(command)=={'argv','execution_timeout','watch_timeout'}
    seconds,watch=limits[mode]
    assert command['execution_timeout']==seconds and command['watch_timeout']==watch
    argv=command['argv']
    assert isinstance(argv,list) and argv and all(isinstance(x,str) and x for x in argv)
    assert not any('<actual-' in x or '<final-' in x for x in argv)
    record=submit(mode,argv,seconds)
    terminal=wait_command(mode,record['response']['Command']['CommandId'],watch)
    return record,terminal


def collect_current_archive(whole_submission,whole_terminal,probe_submission,probe_terminal):
    selected=select_archive(whole_submission,whole_terminal,probe_submission,probe_terminal,
        expected_whole_argv=whole_submission['actual_argv'],expected_probe_argv=PACKET['probe_argv'],
        configuration_sha256=PACKET['configuration_sha256'])
    put('exact-new-archive-binding.json',{'at':now(),'selected':selected,'static_old_archive_lookup_used':False})
    if selected is None:return False
    folder=OUT/'actual-original';folder.mkdir(mode=0o700)
    archive=folder/selected['remote_name']
    helper=OUT/'copy_helper-exact-download-v1.py'
    result=run_transfer_group('export-new-archive',[sys.executable,str(helper),'get',selected['remote_name'],str(archive),
        '--expected-size',str(selected['bytes']),'--expected-sha256',selected['sha256'],'--log',str(folder/'copy.jsonl')])
    assert result.returncode==0
    assert archive.stat().st_size==selected['bytes'] and hashlib.sha256(archive.read_bytes()).hexdigest()==selected['sha256']
    put('exact-new-archive-recovered.json',{'at':now(),'binding':selected,'local':str(archive),
        'exact_bytes_SHA_verified':True,'inner_CRC_or_semantics_verified':False,'required_next':'S6 first integrity and actual role/source/runtime verification'})
    return True


def client_entry_preflight_argv(actual_health):
    source=checked_file('client_entry_preflight.py',PACKET['local_files']['client_entry_preflight.py'])
    controller=checked_file('client_entry_preflight_controller.py',PACKET['local_files']['client_entry_preflight_controller.py'])
    root=checked_file('root_contract.py',PACKET['local_files']['root_contract.py'])
    payload={'source':base64.b64encode(zlib.compress(source,9)).decode(),
        'source_sha256':hashlib.sha256(source).hexdigest(),
        'controller':base64.b64encode(zlib.compress(controller,9)).decode(),
        'controller_sha256':hashlib.sha256(controller).hexdigest(),
        'root':base64.b64encode(zlib.compress(root,9)).decode(),'root_sha256':hashlib.sha256(root).hexdigest(),
        'program_sha256':PACKET['client_entry_program_sha256'],
        'health_path':actual_health['path'],'health_sha256':actual_health['sha256']}
    code=("import base64,zlib,hashlib,json,types;v=json.loads("+repr(json.dumps(payload))+");"
          "r=zlib.decompress(base64.b64decode(v['root']));assert hashlib.sha256(r).hexdigest()==v['root_sha256'];"
          "s=zlib.decompress(base64.b64decode(v['source']));assert hashlib.sha256(s).hexdigest()==v['source_sha256'];"
          "c=zlib.decompress(base64.b64decode(v['controller']));assert hashlib.sha256(c).hexdigest()==v['controller_sha256'];"
          "rc=types.ModuleType('unchanged_root_contract');exec(compile(r,'original-root-contract-27ee','exec'),rc.__dict__);"
          "n={'__name__':'fixed_R6_C1_preflight_controller'};exec(compile(c,'fixed-R6-C1-preflight-controller','exec'),n);"
          "raise SystemExit(n['main'](rc,s,v['program_sha256'],v['health_path'],v['health_sha256']))")
    return [PACKET['bootstrap_python'],'-I','-S','-B','-c',code]


def observe_client_entry_preflight(record):
    label='root-client-entry-preflight'
    cid=record['response']['Command']['CommandId']
    terminal=None
    try:
        terminal=wait_command(label,cid,240)
    except Exception:
        # Failed1 is a preserved outcome. A stored exact server terminal plus
        # the complete root contract wire is required before any following stop.
        path=OUT/(label+'-terminal.json')
        assert path.exists(),'ORIGINAL_CLIENT_PREFLIGHT_TERMINAL_REQUIRED'
        terminal=json.loads(path.read_bytes())
    assert terminal['CommandId']==cid and terminal['InstanceId']==IID
    assert terminal['Status'] in ('Success','Failed','TimedOut','Cancelled')
    wires=[]
    for line in terminal['StandardOutputContent'].splitlines():
        try:value=json.loads(line)
        except ValueError:continue
        if type(value) is dict and value.get('schema')=='S2-R6-C1-preflight-wire/1':wires.append(value)
    assert len(wires)==1,'EXACT_COMPLETE_ROOT_CLIENT_PREFLIGHT_WIRE_REQUIRED'
    wire=wires[0];raw=zlib.decompress(base64.b64decode(wire['zlib_base64'],validate=True))
    assert len(raw)==wire['raw_bytes']<=2*1024**2
    assert hashlib.sha256(raw).hexdigest()==wire['raw_sha256']
    observation=json.loads(raw)
    assert observation['schema']=='S2-R6-C1-preflight-root-original/1'
    assert observation['controller_identity']['uid']==observation['controller_identity']['euid']==0
    assert observation['guest_all_children_terminal'] is True
    assert all(row.get('child_reaped') is True and row.get('group_terminal') is True and
               row.get('cleanup_errors',[])==[] for row in observation['commands'])
    if observation['permission_API_success']:
        assert terminal['Status']=='Success' and terminal['ResponseCode']==0
        actual=observation['actual_client_result']
        assert actual['caller']['uid']==actual['caller']['euid']==1000
        assert actual['capture_result']['root_supervision']['group_terminal'] is True
        assert actual['capture_result']['root_supervision']['pid_scope']=='transient_request_namespace'
    with (OUT/'root-client-entry-preflight-original.json').open('xb') as stream:stream.write(raw)
    return observation


def collect_small_originals_once():
    # This readonly original-source collection precedes the bulk archive GET.
    # It executes no child/worker/model and adds no new product success flags.
    source=checked_file('read_small_originals_v1.py',PACKET['local_files']['read_small_originals_v1.py'])
    code=("import base64,zlib,hashlib;raw=zlib.decompress(base64.b64decode("+
        repr(base64.b64encode(zlib.compress(source,9)).decode())+
        "));assert hashlib.sha256(raw).hexdigest()=="+repr(hashlib.sha256(source).hexdigest())+
        ";exec(compile(raw,'R6-exact-small-originals-before-bulk','exec'))")
    record=submit('small-originals-before-bulk',[PACKET['bootstrap_python'],'-I','-S','-B','-c',code],60)
    terminal=wait_command('small-originals-before-bulk',record['response']['Command']['CommandId'],120)
    wires=[]
    for line in terminal['StandardOutputContent'].splitlines():
        try:value=json.loads(line)
        except ValueError:continue
        if type(value) is dict and value.get('schema')=='S2-R6-small-current-originals-wire/1':wires.append(value)
    assert len(wires)==1,'EXACT_SMALL_ORIGINAL_WIRE_REQUIRED'
    wire=wires[0];compressed=base64.b64decode(wire['zlib_base64'],validate=True)
    assert 0<wire['raw_bytes']<=2*1024**2
    decoder=zlib.decompressobj();raw=decoder.decompress(compressed,wire['raw_bytes']+1)
    assert decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail
    assert len(raw)==wire['raw_bytes'] and hashlib.sha256(raw).hexdigest()==wire['raw_sha256']
    report=json.loads(raw);assert report['schema']=='S2-R6-small-current-originals/1'
    assert [x['name'] for x in report['files']]==['whole-entry-return.json','current-inputs.json','first-failure-capsule.json']
    target=OUT/'small-originals-before-bulk';target.mkdir(mode=0o700)
    for row in report['files']:
        if row['complete_raw_returned'] is True:
            original=base64.b64decode(row['raw_base64'],validate=True)
            assert len(original)==row['bytes']<=262144 and hashlib.sha256(original).hexdigest()==row['sha256']
            with (target/row['name']).open('xb') as stream:stream.write(original)
    with (target/'original-source-wire.json').open('xb') as stream:stream.write(raw)
    put('small-originals-before-bulk-completed.json',{'at':now(),'boot_id':report['boot_id'],
        'source_wire_sha256':hashlib.sha256(raw).hexdigest(),'files':[{'name':x['name'],
        'status':x['status'],'complete_raw_returned':x['complete_raw_returned']} for x in report['files']],
        'bulk_archive_started_yet':False,'new_product_verdict_claimed':False})
    return report


def execute():
    failures=[];installed=False;whole_submission=None;whole_terminal=None;exported=False;guest_terminal=False
    preflight_submission=None;preflight_guest_terminal=False
    def failed(label,error):
        row={'at':now(),'phase':label,'type':type(error).__name__,'message':str(error),
             'mutation_uncertain':bool(getattr(error,'mutation_uncertain',False)),
             'mutation_tag':getattr(error,'mutation_tag',None)}
        failures.append(row);print(json.dumps(row),flush=True)
    current='upload'
    try:
        upload_assets()
        current='install';phase('install');installed=True
        current='health';actual_health=health()
        current='root-client-entry-preflight'
        preflight_submission=submit('root-client-entry-preflight',client_entry_preflight_argv(actual_health),180)
        preflight_observation=observe_client_entry_preflight(preflight_submission)
        preflight_guest_terminal=preflight_observation['guest_all_children_terminal'] is True
        assert preflight_observation['permission_API_success'] is True,'ACTUAL_ROOT_CLIENT_ENTRY_PREFLIGHT_FAILED_NO_ROLE'
        current='whole'
        argv=[*PACKET['whole_argv_prefix'],'--health-sha256',actual_health['sha256'],
              '--challenge-code-base64',PACKET['challenge_code_base64'],
              '--challenge-code-sha256',PACKET['challenge_code_sha256'],
              '--challenge-reference-base64',PACKET['challenge_reference_base64'],'--execute']
        whole_submission=submit('whole',argv,7200)
        whole_submission['actual_argv']=argv
        # The guest original2h unit and individual action guards remain the
        # execution bounds. This is only the outer API observation ceiling.
        whole_terminal=wait_command('whole',whole_submission['response']['Command']['CommandId'],7200)
    except Exception as error:
        failed(current,error)
    finally:
        uncertainty_barrier('before-whole-result-observation',known_uncertain=any(x['mutation_uncertain'] for x in failures))
        assert not active_commands(),'ACTIVE_COMMAND_NO_FOLLOWING_PROBE_OR_STOP'
        if whole_submission is not None:
            if whole_terminal is None:
                # wait_command preserves the server's original terminal even
                # when Failed; absence never becomes a successful empty proof.
                original_path=OUT/'whole-terminal.json'
                assert original_path.exists(),'ORIGINAL_WHOLE_TERMINAL_MISSING_NO_FOLLOWUP'
                whole_terminal=json.loads(original_path.read_bytes())
            try:
                probe=submit('whole-result-probe',PACKET['probe_argv'],60)
                probe_terminal=wait_command('whole-result-probe',probe['response']['Command']['CommandId'],120)
                try:
                    collect_small_originals_once()
                except Exception as thin_error:
                    put('small-originals-before-bulk-error.json',{'at':now(),'type':type(thin_error).__name__,
                        'message':str(thin_error),'complete_raw_returned':False})
                    uncertainty_barrier('small-originals-before-bulk-failure',
                        known_uncertain=bool(getattr(thin_error,'mutation_uncertain',False)))
                    assert not active_commands(),'ACTIVE_SMALL_SOURCE_COMMAND_NO_BULK_OR_STOP'
                # Validate the exact original request/server-terminal/guest
                # child-terminal join before treating a stop as safe.
                selected=select_archive(whole_submission,whole_terminal,probe,probe_terminal,
                    expected_whole_argv=whole_submission['actual_argv'],expected_probe_argv=PACKET['probe_argv'],
                    configuration_sha256=PACKET['configuration_sha256'])
                guest_terminal=True
                if selected is not None:
                    exported=collect_current_archive(whole_submission,whole_terminal,probe,probe_terminal)
            except Exception as error:
                failed('whole-result-or-export',error)
                uncertainty_barrier('whole-result-or-export-failure',known_uncertain=bool(getattr(error,'mutation_uncertain',False)))
        elif preflight_submission is not None:
            # Server terminal alone is insufficient: the actual original root
            # child's bounded group cleanup wire must be positively joined.
            guest_terminal=preflight_guest_terminal
        else:
            # Only original health and data-only installer preceded a submitted
            # preflight; no new root client child was submitted on this branch.
            guest_terminal=True
        if not guest_terminal:
            put('supervisor-guest-terminal-unresolved.json',{'at':now(),'failures':failures,
                'normal_stop_submitted':False,'force_or_retry_submitted':False,'original2h_timer_unchanged':True})
            raise RuntimeError('CURRENT_GUEST_CHILD_TERMINAL_NOT_PROVEN_NO_FOLLOWING_MUTATION')
        uncertainty_barrier('before-final-normal-stop')
        assert not active_commands(),'ACTIVE_COMMAND_NO_STOP'
        put('supervisor-before-stop.json',{'at':now(),'failures':failures,'installed':installed,
            'exact_archive_recovered':exported,'guest_terminal_positive_join':guest_terminal,'product_PASS_claimed':False})
        if states()[IID]=='running':normal_stop_once()
        deadline=time.monotonic()+180
        while states()[IID] in ('running','stopping'):
            assert time.monotonic()<deadline,'STOPPED_NOT_YET_OBSERVED'
            time.sleep(3)
        final={'at':now(),'states':states(),'active_commands':active_commands(),'failures':failures,
               'exact_archive_recovered':exported,'normal_stop_repeated':False,'product_PASS_claimed':False}
        put('supervisor-final.json',final)
        assert final['states'][IID]=='stopped' and not final['active_commands']
    if failures:raise RuntimeError('ORIGINAL_OPERATIONAL_FAILURES_PRESERVED')


def main():
    global PACKET, boto3, SSM, EC2, select_archive
    import argparse
    import stat
    parser = argparse.ArgumentParser(description='S2 once-only SUP16 three-role operator; requires final admitted packet')
    parser.add_argument('mode', choices=('start', 'execute'))
    a = parser.parse_args()
    directory = OUT.lstat()
    assert directory.st_uid == 1000 and stat.S_IMODE(directory.st_mode) == 0o700
    os.umask(0o077)
    packet_raw = (OUT / 'operator-freeze.json').read_bytes()
    PACKET = json.loads(packet_raw)
    assert PACKET['not_ready'] is False, 'REVIEW_CURRENT_COST_AND_PROTECTION_BINDING_REQUIRED'
    for name, expected in PACKET['local_files'].items():
        checked_file(name, expected)
    assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == PACKET['driver_sha256']
    if a.mode == 'execute':
        proof = json.loads((OUT / 'start-local-terminal-v1.json').read_bytes())
        assert proof['returncode'] == 0 and proof['child_reaped'] is True and proof['group_terminal'] is True
        assert proof['driver_sha256'] == PACKET['driver_sha256']
        assert proof['packet_sha256'] == hashlib.sha256(packet_raw).hexdigest()
        assert not proof.get('interrupted') and not proof.get('timed_out') and not proof.get('local_metadata_error_type')
    put('original-' + a.mode + '-invocation.json', {'at': now(), 'mode': a.mode,
        'pid': os.getpid(), 'argv': sys.argv, 'driver_sha256': PACKET['driver_sha256']})
    # Python -I omits the script directory. Load only the exact private bytes
    # already pinned above; do not change sys.path or product/venv bindings.
    import types
    def module_from_private(name, filename):
        raw = checked_file(filename, PACKET['local_files'][filename])
        module = types.ModuleType(name)
        module.__file__ = str(OUT / filename)
        sys.modules[name] = module
        exec(compile(raw, module.__file__, 'exec'), module.__dict__)
        return module
    binding_module = module_from_private('sup03_archive_binding', 'archive_binding.py')
    select_archive = binding_module.select_archive
    boto3 = module_from_private('aws_cli_adapter_v2', 'aws_cli_adapter_v2.py')
    SSM = boto3.client('ssm', region_name='ap-northeast-2')
    EC2 = boto3.client('ec2', region_name='ap-northeast-2')
    boto3.assert_identity()
    current = states()
    assert set(current) == set(IDS)
    {'start': start, 'execute': execute}[a.mode]()


if __name__ == '__main__':
    main()

