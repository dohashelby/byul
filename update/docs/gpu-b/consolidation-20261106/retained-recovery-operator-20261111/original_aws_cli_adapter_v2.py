"""Existing official local AWS CLI, with durable uncertainty/terminal barriers.

No mutating request is retried. Unknown API acceptance, a killed actor, or an
accepted SSM command without an actual terminal invocation receipt blocks later
operator phases/stop. Read-only reconciliation requires exact request correlation.
No credential export, config/access change or synthesized HTTP metadata.
"""
from pathlib import Path
import datetime,hashlib,json,os,re,subprocess,types,uuid,sys,stat,base64
AWS='/usr/local/bin/aws';REGION='ap-northeast-2'
INSTANCE='i-05e8da8ea99593196';OUT=Path('/home/cloudshell-user/BYUL-SUP16-ROLES-CLI-20261111-R8')
OPS={'ec2':{'describe_instances','describe_instance_attribute','start_instances','stop_instances'},'ssm':{'list_commands','describe_instance_information','send_command','get_command_invocation'},'sts':{'get_caller_identity'}}
MUTATIONS={'start_instances','stop_instances','send_command'}
TERMINAL={'Success','Cancelled','TimedOut','Failed'}
class InvocationDoesNotExist(RuntimeError):pass
class CLITransportError(RuntimeError):pass
class CLIUncertainMutation(CLITransportError):
    mutation_uncertain=True
    def __init__(self,tag):super().__init__('AWS request acceptance uncertain; read-only reconciliation required: '+tag);self.mutation_tag=tag
exceptions=types.SimpleNamespace(InvocationDoesNotExist=InvocationDoesNotExist)
def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def digest(b):return hashlib.sha256(b).hexdigest()
def root():
    OUT.mkdir(mode=0o700,exist_ok=True)
    if OUT.is_symlink() or OUT.stat().st_uid!=os.geteuid() or OUT.stat().st_mode&0o077:raise ValueError('OWN_PRIVATE_LOCAL_TRANSPORT_ROOT')
def write(p,b):
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:f.write(b);f.flush();os.fsync(f.fileno())
def publish(p,value):
    # A concurrent transfer can only observe a complete marker. Neither an old
    # intent nor an original result is overwritten by a resolution.
    b=(json.dumps(value,sort_keys=True,indent=2)+'\n').encode();temp=p.with_name('.'+p.name+'.'+uuid.uuid4().hex+'.publishing')
    write(temp,b);os.link(temp,p,follow_symlinks=False);temp.unlink()
def read(p):return json.loads(p.read_bytes())
def unresolved_mutations(include_inflight=True):
    root();rows=[]
    # One call-local directory snapshot; no cross-call terminal cache.
    terminal_index={}
    if include_inflight:
        for marker in OUT.glob("command-*.terminal-observation.json"):
            match=re.fullmatch(r"command-([a-f0-9-]{36})-[\s\S]*\.terminal-observation\.json",marker.name)
            if match:terminal_index.setdefault(match.group(1),[]).append(marker)
    for p in sorted(OUT.glob('*.mutation-intent.json')):
        tag=p.name[:-len('.mutation-intent.json')];intent=read(p);rp=OUT/(tag+'.mutation-result.json');resolution=OUT/(tag+'.positive-resolution.json')
        if resolution.exists():
            r=read(resolution)
            if r.get('intent_sha256')==digest(p.read_bytes()) and r.get('actual_terminal_positive_correlation_verified') is True:continue
        if not rp.exists():
            if include_inflight:rows.append({'tag':tag,'state':'CLI_ACTOR_INFLIGHT_OR_DIED_BEFORE_RESULT','intent':str(p)})
            continue
        result=read(rp)
        if result['outcome']!='ACCEPTED':rows.append({'tag':tag,'state':'ACCEPTANCE_UNCERTAIN','intent':str(p),'result':str(rp)});continue
        if include_inflight and intent['operation']=='send_command':
            cid=result['CommandId']
            if re.fullmatch(r'[a-f0-9-]{36}',cid):
                observed=False
                for marker in terminal_index.get(cid,[]):
                    try:marker.lstat()
                    except FileNotFoundError:continue
                    observed=True;break
            else:
                # Preserve original glob semantics for noncanonical old input.
                observed=bool(list(OUT.glob('command-'+cid+'-*.terminal-observation.json')))
            if not observed:rows.append({'tag':tag,'state':'ACCEPTED_COMMAND_NOT_YET_OBSERVED_TERMINAL','CommandId':cid,'intent':str(p)})
    return rows
def assert_mutations_resolved():
    rows=unresolved_mutations(include_inflight=True)
    if rows:raise CLITransportError('UNRESOLVED_OR_NONTERMINAL_PRIOR_AWS_REQUESTS '+json.dumps(rows,sort_keys=True))
SDK_CHILD_SHA = "7a057bca14ab3f164414d8b8aa2c737d041550ba168b55f4d004c3bc29684656"
SDK_CHILD_BYTES = 4587

def verified_sdk_child():
    path=Path(__file__).with_name('get_command_invocation_once.py')
    parent=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    fd=None
    try:
        directory=os.fstat(parent)
        if directory.st_uid!=1000 or stat.S_IMODE(directory.st_mode)!=0o700:
            raise PermissionError('PRIVATE_SDK_CHILD_PARENT_REQUIRED')
        fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
        before=os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid!=1000
                or before.st_nlink!=1 or stat.S_IMODE(before.st_mode)!=0o600
                or before.st_size!=SDK_CHILD_BYTES):
            raise PermissionError('OWNER1000_REGULAR600_SDK_CHILD_REQUIRED')
        raw=os.read(fd,SDK_CHILD_BYTES+1)
        after=os.fstat(fd);named=os.stat(path.name,dir_fd=parent,follow_symlinks=False)
        identity=lambda v:(v.st_dev,v.st_ino,v.st_uid,v.st_gid,v.st_mode,
                           v.st_nlink,v.st_size,v.st_mtime_ns,v.st_ctime_ns)
        if (identity(before)!=identity(after) or identity(after)!=identity(named)
                or len(raw)!=SDK_CHILD_BYTES or digest(raw)!=SDK_CHILD_SHA
                or identity(directory)!=identity(path.parent.lstat())):
            raise ValueError('SDK_CHILD_IDENTITY_OR_BYTES_CHANGED')
        return path,raw
    finally:
        if fd is not None:os.close(fd)
        os.close(parent)

def invoke(service,operation,request,*,correlation_comment=None):
    if service not in OPS or operation not in OPS[service] or type(request) is not dict:raise ValueError('FIXED_OPERATOR_API_ONLY')
    root();mut=operation in MUTATIONS;tag=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'-'+uuid.uuid4().hex
    original=json.loads(json.dumps(request));request=json.loads(json.dumps(request))
    if mut:
        # Parallel chunks of the same reviewed transfer remain allowed; a
        # failed/unknown API result blocks even a new transfer chunk. The fixed
        # supervisor additionally requires all accepted commands terminal.
        prior=unresolved_mutations(include_inflight=True)
        if prior:
            same_transfer=(operation=='send_command' and original.get('Comment')=='GB binary file copy')
            for pending in prior:
                previous=read(Path(pending['intent']))
                if (not same_transfer or pending['state']=='ACCEPTANCE_UNCERTAIN'
                        or previous['actor_pid']!=os.getpid()
                        or previous['original_request'].get('Comment')!='GB binary file copy'):
                    raise CLITransportError('PRIOR_UNCERTAIN_OR_NONTERMINAL_REQUEST_BLOCKS_NEW_MUTATION')
        if request.get('InstanceIds')!=[INSTANCE]:raise ValueError('EXACT_APPROVED_SINGLE_NODE_MUTATION_ONLY')
        if operation=='send_command':request['Comment']=str(original.get('Comment','BYUL M20'))[:40]+' rid-'+uuid.uuid4().hex
    raw=(json.dumps(request,sort_keys=True)+'\n').encode();path=OUT/(tag+'.request.json');write(path,raw)
    if mut:publish(OUT/(tag+'.mutation-intent.json'),{'tag':tag,'at':utc(),'actor_pid':os.getpid(),'service':service,'operation':operation,'original_request':original,'actual_request':request,'actual_request_sha256':digest(raw),'original_request_sha256':digest(json.dumps(original,sort_keys=True).encode()),'automatic_retries':1,'result_unknown_until_actual_response':True})
    argv=[AWS,service,operation.replace('_','-'),'--cli-input-json','file://'+str(path),'--region',REGION,'--output','json','--no-cli-pager','--cli-connect-timeout','10','--cli-read-timeout','45']
    use_read_sdk=(service=='ssm' and operation=='get_command_invocation')
    if use_read_sdk:
        sdk_path,sdk_raw=verified_sdk_child()
        # Execute the bytes just verified through the same FD, rather than
        # reopening the source path in a child after verification.
        code="import base64;exec(compile(base64.b64decode("+repr(base64.b64encode(sdk_raw).decode())+"),"+repr(str(sdk_path))+",'exec'))"
        argv=[sys.executable,'-I','-B','-c',code,'--request',str(path),
              '--request-sha256',digest(raw),'--instance',INSTANCE,'--region',REGION]
    query=None
    if operation=='list_commands':
        if correlation_comment is None:query="Commands[?Status=='Pending'||Status=='InProgress'||Status=='Delayed'||Status=='Cancelling'].{CommandId:CommandId,Status:Status}"
        else:
            if re.fullmatch(r'[A-Za-z0-9 .:/_-]{1,100}',correlation_comment) is None:raise ValueError('FIXED_REQUEST_CORRELATION_COMMENT_REQUIRED')
            query="Commands[?Comment=='"+correlation_comment+"']"
        argv+=['--query',query]
    env=os.environ.copy()
    if mut:env['AWS_MAX_ATTEMPTS']='1'
    started=utc();out=b'';err=b'';code=None;timed=False
    try:
        r=subprocess.run(argv,capture_output=True,timeout=65,env=env);out=r.stdout;err=r.stderr;code=r.returncode
    except subprocess.TimeoutExpired as exc:out=exc.stdout or b'';err=exc.stderr or b'';timed=True
    except BaseException:
        # The already published intent remains unresolved after interruption.
        if mut:publish(OUT/(tag+'.mutation-result.json'),{'outcome':'UNKNOWN','reason':'local actor interrupted','at':utc(),'originals_preserved':True})
        raise
    write(OUT/(tag+'.stdout'),out);write(OUT/(tag+'.stderr'),err)
    receipt={'at':started,'ended_at':utc(),'service':service,'operation':operation,'argv':argv,'request_sha256':digest(raw),'stdout_sha256':digest(out),'stderr_sha256':digest(err),'exit_code':code,'timed_out':timed,'mutation_auto_retry_disabled':mut,'query_projection':query,'HTTP_status_or_request_id_exposed':False,'credentials_exported_or_permissions_changed':False}
    if use_read_sdk:
        metadata={}
        try:
            observed=json.loads(out) if code==0 else json.loads(err.decode().splitlines()[-1]).get('response',{})
            if type(observed) is dict and type(observed.get('ResponseMetadata')) is dict:
                metadata=observed['ResponseMetadata']
        except (ValueError,IndexError,UnicodeError,AttributeError):pass
        receipt.update(transport='existing-official-boto3-readonly-child',
            sdk_child_sha256=SDK_CHILD_SHA,sdk_child_bytes=SDK_CHILD_BYTES,
            source_verified_FD_bytes_executed=True,
            original_parent_timeout_seconds=65,
            HTTP_status_or_request_id_exposed=bool(metadata.get('RequestId') is not None
                or metadata.get('HTTPStatusCode') is not None))
    publish(OUT/(tag+'.receipt.json'),receipt)
    if code!=0:
        if mut:publish(OUT/(tag+'.mutation-result.json'),{'outcome':'UNKNOWN','at':utc(),'receipt':str(OUT/(tag+'.receipt.json')),'CLI_direct_call_finished':True});raise CLIUncertainMutation(tag)
        if operation=='get_command_invocation' and re.search(rb'\(InvocationDoesNotExist\)',err):raise InvocationDoesNotExist('original AWS invocation-not-found: '+tag)
        raise CLITransportError('original AWS CLI failure, no action retry: '+tag)
    try:
        value=json.loads(out)
        if query is not None:
            if type(value) is not list:raise ValueError('CLI projected command array required')
            if correlation_comment is None and any(type(x) is not dict or set(x)!={'CommandId','Status'} for x in value):raise ValueError('active command projection mismatch')
            value={'Commands':value}
        if type(value) is not dict:raise ValueError('API JSON object required')
        if mut:
            accepted={'outcome':'ACCEPTED','at':utc(),'receipt':str(OUT/(tag+'.receipt.json')),'CLI_direct_call_finished':True}
            if operation=='send_command':
                c=value['Command'];cid=c['CommandId']
                if not re.fullmatch('[a-f0-9-]{36}',cid) or c['InstanceIds']!=[INSTANCE]:raise ValueError('actual accepted command identity missing')
                accepted['CommandId']=cid
            elif operation in ['start_instances','stop_instances']:
                key='StartingInstances' if operation=='start_instances' else 'StoppingInstances'
                if len(value[key])!=1 or value[key][0]['InstanceId']!=INSTANCE:raise ValueError('actual accepted instance transition missing')
            publish(OUT/(tag+'.mutation-result.json'),accepted)
        if operation=='get_command_invocation' and value.get('Status') in TERMINAL:
            if value.get('CommandId')!=request['CommandId'] or value.get('InstanceId')!=request['InstanceId']:raise ValueError('invocation identity changed')
            publish(OUT/('command-'+request['CommandId']+'-'+tag+'.terminal-observation.json'),{'at':utc(),'CommandId':request['CommandId'],'InstanceId':request['InstanceId'],'Status':value['Status'],'ResponseCode':value['ResponseCode'],'original_response_sha256':digest(out),'receipt':str(OUT/(tag+'.receipt.json'))})
        return value
    except BaseException:
        if mut and not (OUT/(tag+'.mutation-result.json')).exists():publish(OUT/(tag+'.mutation-result.json'),{'outcome':'UNKNOWN','reason':'unparseable/mismatched actual API response','at':utc(),'CLI_direct_call_finished':True})
        if mut:raise CLIUncertainMutation(tag)
        raise

def reconcile_SSM_unknown_once():
    """Read only. No match/active=[] never proves rejection or permits cleanup."""
    outcomes=[]
    for row in unresolved_mutations(include_inflight=True):
        p=Path(row['intent']);intent=read(p);tag=row['tag'];rp=OUT/(tag+'.mutation-result.json')
        if intent['operation']!='send_command' or not rp.exists() or not read(rp).get('CLI_direct_call_finished'):
            outcomes.append(dict(row,resolved=False,reason='needs actual resource/client terminal evidence, no synthetic resolution'));continue
        actual=intent['actual_request'];matched=invoke('ssm','list_commands',{'InstanceId':INSTANCE},correlation_comment=actual['Comment'])['Commands']
        if len(matched)!=1:
            outcomes.append(dict(row,resolved=False,reason='no unique exact original request correlation'));continue
        c=matched[0]
        if c.get('InstanceIds')!=[INSTANCE] or c.get('DocumentName')!=actual['DocumentName'] or c.get('Parameters')!=actual['Parameters']:
            outcomes.append(dict(row,resolved=False,reason='server command does not match exact original request body'));continue
        inv=invoke('ssm','get_command_invocation',{'CommandId':c['CommandId'],'InstanceId':INSTANCE})
        if inv.get('Status') not in TERMINAL:
            outcomes.append(dict(row,resolved=False,CommandId=c['CommandId'],reason='accepted original command not terminal'));continue
        resolution={'at':utc(),'intent_sha256':digest(p.read_bytes()),'actual_terminal_positive_correlation_verified':True,'Command':c,'terminal_Invocation':inv,'original_API_transport_error_preserved':True,'additional_mutation_or_retry':False}
        publish(OUT/(tag+'.positive-resolution.json'),resolution);outcomes.append({'tag':tag,'resolved':True,'CommandId':c['CommandId'],'terminal_status':inv['Status']})
    return outcomes
class Client:
    exceptions=exceptions
    def __init__(self,service):self.service=service
    def __getattr__(self,operation):
        if operation not in OPS[self.service]:raise AttributeError(operation)
        return lambda **request:invoke(self.service,operation,request)
    def get_paginator(self,operation):
        if (self.service,operation)!=('ssm','list_commands'):raise ValueError('ONLY_ORIGINAL_COMMAND_PAGINATOR')
        return types.SimpleNamespace(paginate=lambda **request:iter([invoke(self.service,operation,request)]))
def client(service,region_name=None):
    if service not in OPS or region_name not in [None,REGION]:raise ValueError('ORIGINAL_REGION_SERVICE_ONLY')
    return Client(service)
def assert_identity():
    x=invoke('sts','get_caller_identity',{})
    if x.get('Account')!='834058543745':raise ValueError('AUTHORIZED_ACCOUNT_REQUIRED')
    return x
