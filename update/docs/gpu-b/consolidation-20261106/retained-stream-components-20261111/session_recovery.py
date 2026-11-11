"""고정 GB12 회수 감독 후보. main/API 자동실행 없음. 실제 실행 결합 전 NOT_READY."""
import base64,datetime,hashlib,json,math,os,re,select,shlex,subprocess,sys,time,uuid
from pathlib import Path

IID='i-05e8da8ea99593196'
ACCOUNT='834058543745'
REGION='ap-northeast-2'
DOCUMENT='AWS-StartNonInteractiveCommand'
CONTENT_SHA='3c6fff6f4fd03784f52e47b5b008ef991762d01c0db5dfebf1e3105cc4510dee'
TERMINAL=b'GB12_TERMINAL '
STREAM_CAP=24*1024*1024
ERROR_CAP=65536

class RecoveryError(RuntimeError):
    mutation_uncertain=False

class UnknownSession(RecoveryError):
    mutation_uncertain=True

def require(ok,code):
    if not ok:raise RecoveryError(code)

def jb(x):return json.dumps(x,sort_keys=True,separators=(',',':')).encode()
def sha(x):return hashlib.sha256(x).hexdigest()
def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def boot():return str(uuid.UUID(Path('/proc/sys/kernel/random/boot_id').read_text().strip()))
def canonical(x):
    require(type(x) is str and str(uuid.UUID(x))==x and uuid.UUID(x).version==4,'UUID4_REQUIRED')
    return x
def ticks(pid):return int(Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()[19])

def remaining(end):
    n=end-time.monotonic();require(math.isfinite(n) and n>0,'ORIGINAL_TRANSFER900_EXPIRED');return n

def pipe_deadline(end,authentication_end,consumer):
    # 최초 SessionId banner 이전 인증/접속도 원 요청65 안에 끝나야 한다.
    return min(end,authentication_end) if consumer is not None and consumer.sid is None else end

def guest_clock_code(nonce):
    canonical(nonce)
    return ('import json,os,time;from pathlib import Path;'
        'print(json.dumps({"nonce":'+repr(nonce)+',"boot":Path("/proc/sys/kernel/random/boot_id").read_text().strip(),'
        '"monotonic":time.monotonic(),"UID":os.getuid(),"EUID":os.geteuid()}),flush=True)')

def mapped_deadline(end,before,after,observation,nonce,recovery_boot):
    require(type(before) in (float,int) and type(after) in (float,int) and before<=after<end,'CLOCK_REQUEST_INTERVAL_REQUIRED')
    require(observation['nonce']==nonce and observation['boot']==recovery_boot,'CLOCK_NONCE_BOOT_JOIN_REQUIRED')
    guest=observation['monotonic']
    require(type(guest) in (int,float) and math.isfinite(guest) and guest>=0,'FINITE_GUEST_MONOTONIC_REQUIRED')
    # guest 관측~응답 지연만큼 짧아지는 보수적 mapping. host mono를 guest에 복사하지 않는다.
    return guest+(end-after)

def remote_command(source,source_sha,nonce,recovery_boot,guest_end):
    require(sha(source)==source_sha and 0<len(source)<=32000,'EXACT_BOUNDED_STREAM_SOURCE_REQUIRED')
    canonical(nonce);str(uuid.UUID(recovery_boot))
    require(type(guest_end) in (int,float) and math.isfinite(guest_end) and guest_end>0,'FINITE_GUEST_DEADLINE_REQUIRED')
    # 파일 설치/수정 없이 동일 프로세스에 고정 source를 SHA 확인 후 결합한다.
    # default Session RunAs는 미확인. 원 archive UID1000을 읽는 기존 sudo -n만 사용한다.
    code='''import base64,hashlib,json,os,sys,types
raw=base64.b64decode(SOURCE,validate=True)
assert len(raw)==SIZE and hashlib.sha256(raw).hexdigest()==SHA
m=types.ModuleType('s2_fixed_gb12_session_sender');m.__file__='<fixed-gb12-stream>'
sys.modules[m.__name__]=m
exec(compile(raw,m.__file__,'exec'),m.__dict__)
sys.argv=['<fixed-gb12-stream>','send','--request',NONCE,'--recovery-boot',BOOT,'--deadline-monotonic',END,'--deadline-boot-id',BOOT]
rc=1
try:
 rc=m.main()
except BaseException:
 rc=1
finally:
 print('GB12_TERMINAL '+json.dumps({'nonce':NONCE,'source_sha256':SHA,'recovery_boot':BOOT,'sender_UID':os.getuid(),'sender_EUID':os.geteuid(),'returncode':rc},sort_keys=True,separators=(',',':')),flush=True)
raise SystemExit(rc)
'''
    values={'SOURCE':repr(base64.b64encode(source).decode()),'SIZE':str(len(source)),
        'SHA':repr(source_sha),'NONCE':repr(nonce),'BOOT':repr(recovery_boot),'END':repr(str(guest_end))}
    for key,value in values.items():code=re.sub(r'\b'+key+r'\b',lambda _:value,code)
    command=shlex.join(['sudo','-n','/usr/bin/python3.12','-I','-S','-B','-c',code])
    require(len(command.encode())<=65535,'MANAGED_COMMAND_PARAMETER_LIMIT')
    return command

def request_for(command,nonce):
    canonical(nonce)
    return {'Target':IID,'DocumentName':DOCUMENT,'Reason':'GB12-readonly-recovery-'+nonce,
        'Parameters':{'command':[command],'separateOutputStream':['false']}}

class Transcript:
    """frame는 Receiver.feed만 호출. EOF/CLI/server 검증 전 finish하지 않는다."""
    def __init__(self,receiver,nonce,source_sha,recovery_boot):
        self.receiver=receiver;self.nonce=nonce;self.source_sha=source_sha;self.recovery_boot=recovery_boot
        self.pending=b'';self.sid=None;self.marker=None;self.seen_frame=False;self.eof=False
    def feed(self,chunk):
        self.pending+=chunk
        require(len(self.pending)<=65536,'BOUNDED_SESSION_LINE_REQUIRED')
        while b'\n' in self.pending:
            line,self.pending=self.pending.split(b'\n',1);line=line.removesuffix(b'\r')
            if line.startswith(b'Starting session with SessionId: '):
                require(self.sid is None and not self.seen_frame,'SINGLE_INITIAL_SESSION_BANNER_REQUIRED')
                sid=line[len(b'Starting session with SessionId: '):].strip().decode('ascii')
                require(re.fullmatch(r'[A-Za-z0-9_@.+=,-]{1,96}',sid) is not None,'VALID_BANNER_SESSION_ID_REQUIRED')
                self.sid=sid
            if line.startswith(b'GB12/1 '):
                require(self.marker is None,'NO_FRAME_AFTER_REMOTE_TERMINAL');self.seen_frame=True
            if line.startswith(TERMINAL):
                require(self.marker is None and self.seen_frame,'SINGLE_REMOTE_TERMINAL_AFTER_FRAME_REQUIRED')
                value=json.loads(line[len(TERMINAL):])
                require(line[len(TERMINAL):]==jb(value),'CANONICAL_REMOTE_TERMINAL_REQUIRED')
                require(all(type(value.get(k)) is int for k in ('sender_UID','sender_EUID','returncode')),
                    'REMOTE_TERMINAL_INTEGER_FIELDS_REQUIRED')
                require(value=={'nonce':self.nonce,'source_sha256':self.source_sha,
                    'recovery_boot':self.recovery_boot,'sender_UID':0,'sender_EUID':0,'returncode':0},
                    'EXACT_REMOTE_TERMINAL_SUCCESS_REQUIRED')
                self.marker=value
            self.receiver.feed(line+b'\n')
    def end(self):
        require(not self.pending,'SESSION_STDOUT_INCOMPLETE_LINE')
        self.eof=True
        require(self.sid is not None and self.marker is not None,'SESSION_ID_REMOTE_TERMINAL_REQUIRED')

class SessionCLI:
    """정식 CLI만 실행. 토큰/StreamUrl/프로세스 argv/env를 관측·기록하지 않는다."""
    def __init__(self,driver):self.driver=driver;self.root=Path(driver.OUT);self.count=0
    def invoke(self,operation,request,end,consumer=None):
        require(operation in ('start-session','describe-sessions','terminate-session'),'NARROW_SESSION_API_ONLY')
        self.count+=1;tag='session-%03d-%s'%(self.count,operation)
        rp=self.root/(tag+'.request.json')
        with rp.open('xb') as f:f.write(jb(request))
        argv=['aws','ssm',operation,'--region',REGION,'--cli-input-json','file://'+str(rp),
            '--cli-connect-timeout','10','--cli-read-timeout','45','--no-cli-pager']
        if operation!='start-session':argv+=['--output','json']
        env=os.environ.copy();env.update(AWS_MAX_ATTEMPTS='1',AWS_RETRY_MODE='standard',AWS_PAGER='',AWS_CLI_AUTO_PROMPT='off')
        # stdin은 파이프로 열어두고 어떤 입력도 쓰지 않는다. NonInteractive stdin EOF 조기종료 방지.
        # 원 request/출력 파일 열기 실패는 실제 CLI 시작 전에 끝난다.
        rawout=bytearray();rawerr=bytearray();original=None;terminal=None
        caps={'stdout':STREAM_CAP if consumer is not None else 1024*1024,'stderr':ERROR_CAP}
        paths={key:self.root/(tag+'.'+key) for key in ('stdout','stderr')}
        streams={};p=None;identity={};start=utc()
        try:
            for key in paths:streams[key]=paths[key].open('xb')
            remaining(end)
            authentication_end=min(end,time.monotonic()+65)
            p=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                env=env,start_new_session=True)
            identity={'PID':p.pid,'PGID':p.pid,'start_ticks':ticks(p.pid)}
            self.driver.put(tag+'.local-PID.json',{'at':utc(),**identity,'operation':operation,'request_sha256':sha(jb(request))})
            fds={p.stdout.fileno():('stdout',p.stdout),p.stderr.fileno():('stderr',p.stderr)}
            for fd in fds:os.set_blocking(fd,False)
            counts={'stdout':0,'stderr':0}
            while fds:
                ready,_,_=select.select(list(fds),[],[],min(.5,remaining(pipe_deadline(end,authentication_end,consumer))))
                for fd in ready:
                    key,handle=fds[fd]
                    try:data=os.read(fd,4096)
                    except BlockingIOError:continue
                    if not data:del fds[fd];continue
                    counts[key]+=len(data);require(counts[key]<=caps[key],'SESSION_OUTPUT_CAP_EXCEEDED')
                    streams[key].write(data)
                    if key=='stdout' and consumer is not None:consumer.feed(data)
                    elif key=='stdout':rawout.extend(data)
                    else:rawerr.extend(data)
            terminal=self.driver.wait_child(p,remaining(end))
            require(terminal.get('child_reaped') is True and terminal.get('group_terminal') is True
                and not terminal.get('cleanup_errors') and not terminal.get('timed_out'),'CLI_OWN_GROUP_TERMINAL_REQUIRED')
            require(terminal.get('returncode')==0,'CLI_NATIVE_EXIT_NONZERO')
            if consumer is not None:consumer.end()
        except BaseException as exc:original=exc
        finally:
            if p is not None and terminal is None:
                try:terminal=self.driver.wait_child(p,0)
                except BaseException as cleanup:
                    if original is None:original=cleanup
                    else:original.add_note('CLI group cleanup: '+type(cleanup).__name__)
            close_errors=[]
            handles=([p.stdin,p.stdout,p.stderr] if p is not None else [])+list(streams.values())
            for handle in handles:
                try:handle.close()
                except BaseException as error:
                    close_errors.append(type(error).__name__)
                    if original is None:original=error
                    else:original.add_note('Independent close: '+type(error).__name__)
            try:self.driver.put(tag+'.terminal.json',{'started_at':start,'ended_at':utc(),**identity,
                'operation':operation,'request_sha256':sha(jb(request)),'terminal':terminal,
                'original_error_type':None if original is None else type(original).__name__,'close_errors':close_errors,
                'stdout_bytes':paths['stdout'].stat().st_size if paths['stdout'].exists() else None,
                'stderr_bytes':paths['stderr'].stat().st_size if paths['stderr'].exists() else None,
                'credentials_or_token_or_env_or_proc_argv_logged':False})
            except BaseException as diagnostic:
                if original is None:raise
                original.add_note('CLI receipt preservation: '+type(diagnostic).__name__)
        if original is not None:raise original.with_traceback(original.__traceback__)
        if consumer is not None:return terminal
        value=json.loads(bytes(rawout));require(type(value) is dict,'OBJECT_API_RESPONSE_REQUIRED');return value

def session_row(cli,request,owners,sid,end,started_after,allow_transient_empty=False):
    rows=[]
    filters=[{'key':'Target','value':IID}]
    if sid is not None:filters.append({'key':'SessionId','value':sid})
    for state in ('History','Active'):
        r=cli.invoke('describe-sessions',{'State':state,'Filters':filters,'MaxResults':200},min(end,time.monotonic()+65))
        require(not r.get('NextToken'),'BOUNDED_SESSION_JOIN_NOT_COMPLETE')
        for row in r['Sessions']:
            if sid is not None:require(row.get('SessionId')==sid and row.get('Reason')==request['Reason'],'EXACT_SESSION_REASON_REQUIRED')
            if row.get('Reason')==request['Reason']:rows.append(row)
    if not rows and allow_transient_empty:return None
    require(len(rows)==1,'EXACT_ONE_ORIGINAL_REASON_SESSION_REQUIRED')
    row=rows[0]
    require(row['Target']==IID and row['DocumentName']==DOCUMENT and row['Owner'] in owners
        and (sid is None or row['SessionId']==sid),'EXACT_SESSION_TARGET_DOCUMENT_OWNER_REQUIRED')
    start=row.get('StartDate')
    if type(start) is str:start=datetime.datetime.fromisoformat(start.replace('Z','+00:00')).timestamp()
    require(type(start) in (int,float) and math.isfinite(start)
        and started_after<=start<=datetime.datetime.now(datetime.timezone.utc).timestamp(),
        'ORIGINAL_REQUEST_SESSION_START_DATE_REQUIRED')
    require(re.fullmatch(r'[A-Za-z0-9_@.+=,-]{1,96}',row['SessionId']) is not None,'VALID_SERVER_SESSION_ID_REQUIRED')
    return row

class Recovery:
    """runtime은 기존 검토loader의 새private 객체. 구 controller.run_once는 호출하지 않는다."""
    def __init__(self,runtime,stream,source,source_pin):
        self.rt=runtime;self.driver=runtime.driver;self.stream=stream;self.source=source
        self.pin=source_pin;self.cli=SessionCLI(self.driver);self.session_submitted=False
        self.server_terminal=False;self.request=None;self.sid=None;self.owners=None;self.transcript=None;self.started_after=None
        require(len(source)==source_pin['bytes'] and sha(source)==source_pin['sha256'],'FIXED_STREAM_SOURCE_PIN_REQUIRED')
        require(stream.CHUNK==12000,'FIXED12000_STREAM_REQUIRED')

    def clock_once(self,end,nonce,recovery_boot):
        require(remaining(end)>=65,'CLOCK_REQUEST65_FITS_ORIGINAL900_REQUIRED')
        before=time.monotonic()
        rec=self.driver.submit('recovery-clock-once',['/usr/bin/python3.12','-I','-S','-B','-c',guest_clock_code(nonce)],60)
        cmd=rec['response']['Command'];cid=cmd['CommandId']
        require(cmd['InstanceIds']==[IID] and cmd['DocumentName']==rec['request']['DocumentName']
            and cmd['Parameters']==rec['request']['Parameters'],'EXACT_CLOCK_ACCEPTANCE_REQUIRED')
        terminal=self.driver.wait_command('recovery-clock-once',cid,min(65,remaining(end)))
        after=time.monotonic()
        require(terminal['CommandId']==cid and terminal['InstanceId']==IID and terminal['ResponseCode']==0
            and terminal['Status']=='Success' and not terminal['StandardErrorContent'],'EXACT_CLOCK_TERMINAL_REQUIRED')
        observation=json.loads(terminal['StandardOutputContent'])
        guest_end=mapped_deadline(end,before,after,observation,nonce,recovery_boot)
        self.driver.put('session-clock-map.json',{'at':utc(),'CommandId':cid,'nonce':nonce,
            'host_boot':boot(),'host_before':before,'host_after':after,'host_original_end':end,
            'guest_observation':observation,'guest_absolute_end':guest_end,'reset':False})
        return guest_end

    def collect(self,health_controller,nonce,output):
        canonical(nonce)
        # 기존 actual health의 정상 guards와 현재 boot를 원 controller 메서드로 결합한다.
        recovery_boot=health_controller.observe_health()
        host_boot=boot();end=time.monotonic()+900
        self.driver.put('stream-original-deadline.json',{'at':utc(),'host_boot':host_boot,'absolute_end':end,'seconds':900,'reset':False})
        identity=self.rt.adapter.assert_identity();require(identity['Account']==ACCOUNT,'ACCOUNT_REQUIRED')
        remaining(end)
        # DescribeSessions Owner는 service가 반환하는 실제 사용자 ID다.
        # 정확 STS Arn 또는 정확 UserId만 허용하고 추정 ARN 변환을 만들지 않는다.
        self.owners=(identity['Arn'],identity['UserId'])
        guest_end=self.clock_once(end,nonce,recovery_boot)
        command=remote_command(self.source,self.pin['sha256'],nonce,recovery_boot,guest_end)
        self.request=request_for(command,nonce)
        self.driver.uncertainty_barrier('before-original-session-once')
        require(self.driver.states()[IID]=='running' and not self.driver.active_commands(),'SELECTED_RUNNING_IDLE_REQUIRED')
        require(boot()==host_boot,'HOST_BOOT_CHANGED')
        receiver=self.stream.Receiver(output,nonce,recovery_boot,self.stream.Deadline(end))
        self.transcript=Transcript(receiver,nonce,self.pin['sha256'],recovery_boot)
        try:
            self.started_after=datetime.datetime.now(datetime.timezone.utc).timestamp()
            self.driver.put('start-session-once-intent.json',{'at':utc(),'request':self.request,
                'request_sha256':sha(jb(self.request)),'actual_STS_owner_values':self.owners,'original_end':end,'host_boot':host_boot})
            self.session_submitted=True
            self.cli.invoke('start-session',self.request,end,self.transcript)
            self.sid=self.transcript.sid
            row=self.wait_original_session_terminal(end)
            self.server_terminal=row['Status'] in ('Terminated','Failed') and row.get('EndDate') is not None
            require(self.server_terminal and row['Status']=='Terminated','POSITIVE_ORIGINAL_SESSION_TERMINATED_REQUIRED')
            require(boot()==host_boot,'HOST_BOOT_CHANGED')
            remaining(end)
            # footer만으로 publish하지 않는다. native0/groupterminal/EOF/remote0/serverterminal 전부 필요.
            result=receiver.finish()
            self.driver.put('GB12-stream-recovered.json',{'at':utc(),'request_sha256':sha(jb(self.request)),
                'Session':row,'remote_terminal':self.transcript.marker,'receiver':result,
                'whole_archive_CRC_complete':False,'product_success':False})
            return result
        except BaseException as original:
            try:receiver.abort()
            except BaseException as cleanup:original.add_note('Receiver abort: '+type(cleanup).__name__)
            raise

    def wait_original_session_terminal(self,end):
        # Active→History 이동의 일시0행/Terminating은 같은 원900잔여 안에서만 관측한다.
        # 원 CLI가 EOF/native0인 뒤에도 server 종료는 별도 증거로 확인한다.
        while True:
            remaining(end)
            row=session_row(self.cli,self.request,self.owners,self.sid,end,self.started_after,True)
            if row is not None:
                self.sid=row['SessionId']
                if row['Status'] in ('Terminated','Failed') and row.get('EndDate') is not None:return row
                require(row['Status'] in ('Connected','Connecting','Disconnected','Terminating','Terminated','Failed'),
                    'KNOWN_ORIGINAL_SESSION_STATUS_REQUIRED')
                intent=Path(self.driver.OUT)/'terminate-own-session-once-intent.json'
                if row['Status'] in ('Connected','Connecting','Disconnected') and not intent.exists():
                    self.terminate_join_once(row,end)
            time.sleep(min(3,remaining(end)))

    def terminate_join_once(self,row,end):
        # exclusive intent는 동일 session의 두 번째 Terminate 호출을 API 전에 거부한다.
        self.driver.put('terminate-own-session-once-intent.json',{'at':utc(),'SessionId':row['SessionId'],
            'original_request_sha256':sha(jb(self.request)),'server_join':row})
        response=self.cli.invoke('terminate-session',{'SessionId':row['SessionId']},min(end,time.monotonic()+65))
        require(response.get('SessionId')==row['SessionId'],'EXACT_TERMINATION_RESPONSE_REQUIRED')

    def reconcile_cleanup_once(self):
        if not self.session_submitted:return
        if self.server_terminal:return
        # 실패 정리는 전송 deadline을 연장하지 않는다. 각 기존 CLI65 상한만 적용.
        sid=self.transcript.sid if self.transcript is not None else None
        try:
            row=session_row(self.cli,self.request,self.owners,sid,time.monotonic()+130,self.started_after)
            self.sid=row['SessionId']
            if row['Status'] in ('Terminated','Failed') and row.get('EndDate') is not None:
                self.server_terminal=True;return
            self.terminate_join_once(row,time.monotonic()+65)
            row=session_row(self.cli,self.request,self.owners,self.sid,time.monotonic()+130,self.started_after)
            require(row['Status'] in ('Terminated','Failed') and row.get('EndDate') is not None,'SESSION_TERMINAL_NOT_YET_PROVEN')
            self.server_terminal=True
            self.driver.put('original-session-positive-terminal.json',{'at':utc(),'session':row,'transfer_deadline_reset':False})
        except BaseException as exc:
            unknown=UnknownSession('EXACT_SESSION_TERMINAL_UNPROVEN')
            try:self.driver.put('session-UNKNOWN-no-following-mutation.json',{'at':utc(),
                    'original_request_sha256':sha(jb(self.request)),'SessionId':sid,
                    'error_type':type(exc).__name__,'following_stop_or_retry':False})
            except BaseException as diagnostic:unknown.add_note('Session uncertainty record: '+type(diagnostic).__name__)
            raise unknown from exc

    def run_once(self,health_controller,nonce,output):
        self.driver.put('stream-recovery-once-intent.json',{'at':utc(),'scope':'fixed GB12 readonly recovery only',
            'nonce':canonical(nonce),'stream_source_sha256':self.pin['sha256']})
        original=None;result=None
        try:result=self.collect(health_controller,nonce,output)
        except BaseException as exc:
            original=exc
            try:self.driver.put('stream-original-failure.json',{'at':utc(),'type':type(exc).__name__,
                'message':str(exc),'session_submitted':self.session_submitted,'server_terminal':self.server_terminal})
            except BaseException as diagnostic:exc.add_note('Failure preservation: '+type(diagnostic).__name__)
        try:
            self.reconcile_cleanup_once()
            require(health_controller.health_complete,'ORIGINAL_HEALTH_TERMINAL_REQUIRED')
            self.driver.uncertainty_barrier('before-stream-normal-stop')
            require(not self.driver.active_commands(),'NO_ACTIVE_COMMAND_BEFORE_STOP')
            state=self.driver.states()[IID]
            if state=='running':self.driver.normal_stop_once()
            else:require(state in ('stopped','stopping'),'UNEXPECTED_SELECTED_STATE')
            # 원 stop180은 1회 그대로. 이후 actual observation은 별도 후속으로 보존한다.
            end=time.monotonic()+180
            while self.driver.states()[IID]!='stopped':
                require(time.monotonic()<end,'STOPPED_NOT_YET_OBSERVED');time.sleep(3)
            self.driver.put('stream-selected-stopped.json',{'at':utc(),'InstanceId':IID,
                'collector_request_id':canonical(nonce),'exact_off_UTC':None})
        except BaseException as cleanup:
            if original is None:raise
            if getattr(cleanup,'mutation_uncertain',False) or self.session_submitted and not self.server_terminal:
                original.mutation_uncertain=True
            original.add_note('Independent cleanup: '+type(cleanup).__name__+': '+str(cleanup))
        if original is not None:raise original.with_traceback(original.__traceback__)
        return result
