"""Explicit-approval-only AWS/SSH launcher. Default performs no external command."""
from __future__ import annotations
import argparse,json,math,re,shlex,time
from pathlib import Path
from common import BASELINE,PRICE,approval,digest,now,private_json,run,save
from cleanup_check import aws,inventory
from plan import ROUND_MINUTES

def check(code):
    if code:raise ValueError('REMOTE_OPERATION_FAILED')

def choose(c):
    args=['ec2','describe-instances']
    args+=['--instance-ids',c['instance_id']] if c.get('instance_id') else ['--filters',*c.get('instance_filters',['Name=instance-type,Values=g6.2xlarge'])]
    rows=[i for r in aws(c,*args)['Reservations'] for i in r['Instances'] if i['State']['Name']!='terminated' and (c.get('instance_id') or any('byul' in t.get('Value','').lower() for t in i.get('Tags',[])))]
    if len(rows)!=1:raise ValueError('EXACTLY_ONE_EXISTING_DEV_REQUIRED_NO_CREATE')
    d=rows[0]
    if d['InstanceType']!='g6.2xlarge' or d['State']['Name']!='stopped':
        raise ValueError('EXPECTED_STOPPED_DEV_G6_2XLARGE')
    if c.get('expected_instance_sha256'):
        import hashlib
        if hashlib.sha256(d['InstanceId'].encode()).hexdigest()!=c['expected_instance_sha256']:
            raise ValueError('EXISTING_DEV_SELECTION_CHANGED')
    c['instance_id']=d['InstanceId']
    behavior=aws(c,'ec2','describe-instance-attribute','--instance-id',c['instance_id'],
                 '--attribute','instanceInitiatedShutdownBehavior')
    if behavior['InstanceInitiatedShutdownBehavior']['Value']!='stop':
        raise ValueError('HOST_SHUTDOWN_MUST_STOP_NOT_TERMINATE')
    return d

def budget(c):
    # Identity stays in memory; any notification with a subscriber satisfies the revised rule.
    account=aws(c,'sts','get-caller-identity')['Account']
    alerts=aws(c,'budgets','describe-notifications-for-budget','--account-id',account,'--budget-name',c['budget_name'])['Notifications']
    if not alerts:raise ValueError('BUDGET_NOTIFICATION_MISSING')
    for notification in alerts:
        result=aws(c,'budgets','describe-subscribers-for-notification','--account-id',account,
                   '--budget-name',c['budget_name'],'--notification',json.dumps(notification))
        if result.get('Subscribers'):return
    raise ValueError('BUDGET_SUBSCRIBER_MISSING')

def execute(a):
    receipt=approval(a.approval_file)
    if a.round not in receipt.get('rounds',[]):raise ValueError('ROUND_NOT_APPROVED')
    c=private_json(a.config)
    for k in ['ssh_alias','remote_dir','server_checkout']:
        if not re.fullmatch(r'[A-Za-z0-9_./-]+',c[k]):raise ValueError('PRIVATE_PATH_FORMAT')
    if c['remote_dir'] in {'/','.','..'} or c['server_checkout'] in {'/','.','..'}:
        raise ValueError('DEDICATED_REMOTE_PATH_REQUIRED')
    ledger=Path(a.ledger)
    old=private_json(ledger) if ledger.exists() else {'runs':[]}
    streaks={};closed=set()
    for previous in reversed(old['runs']):
        if previous.get('product_baseline',BASELINE)!=BASELINE:continue
        for ident,result in previous.get('results',{}).items():
            if ident in closed:continue
            if result=='passed':closed.add(ident)
            else:streaks[ident]=streaks.get(ident,0)+1
    if any(x>=3 for x in streaks.values()):raise ValueError('THREE_CONSECUTIVE_FAILURES_STOP')
    seconds=sum(x['upper_bound_seconds'] for x in old['runs'])
    planned=ROUND_MINUTES[a.round]*60
    reserve=float(c.get('auxiliary_reserve_usd',3))
    if reserve<0 or seconds*PRICE/3600+reserve>=12 or (seconds+planned)*PRICE/3600+reserve>15:
        raise ValueError('CUMULATIVE_COST_GUARD')
    artifact=Path(a.artifacts);build=json.loads((artifact/'build.json').read_text())
    for name in ['serving-gateway-rt-7741687d.tar','byul-7741687d.bundle']:
        if digest(artifact/name)!=build[name]:raise ValueError('LOCAL_ARTIFACT_HASH_MISMATCH')
    budget(c);instance=choose(c)
    # Existing BYUL dev tags are preserved; no tag mutation is needed.
    ssh=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',c['ssh_alias']]
    if c.get('ssh_config_file'):
        if not Path(c['ssh_config_file']).is_file():raise ValueError('SSH_CONFIG_MISSING')
        ssh[1:1]=['-F',c['ssh_config_file']]
    started=now();start=time.monotonic();start_sent=False
    # Persist the entire planned cap BEFORE starting: a killed laptop cannot erase billing time.
    reservation={'round':a.round,'product_baseline':BASELINE,'started_at':started,'upper_bound_seconds':planned,
                 'state':'reserved_before_start','cost_upper_bound':planned*PRICE/3600}
    old['runs'].append(reservation)
    temporary=ledger.with_suffix('.next')
    if temporary.exists():raise ValueError('LEDGER_NEXT_EXISTS')
    save(temporary,old);temporary.replace(ledger)
    diagnostics=Path(str(a.output)+'-diagnostics')
    last_remote_text=''
    def remote(command,timeout=60):
        nonlocal last_remote_text
        code,text=run([*ssh,command],timeout=timeout)
        last_remote_text=text
        # SSH diagnostics may contain public addresses, so never echo them.
        if code:
            from export_evidence import Sanitizer,leaks
            safe=Sanitizer().text(text)
            if not leaks(safe):
                folder=diagnostics;folder.mkdir(parents=True,exist_ok=True,mode=0o700)
                target=folder/('remote-failure-'+str(time.time_ns())+'.txt')
                target.write_text(safe);target.chmod(0o600)
        check(code);return text
    try:
        start_sent=True
        aws(c,'ec2','start-instances','--instance-ids',c['instance_id'])
        aws(c,'ec2','wait','instance-running','--instance-ids',c['instance_id'],timeout=180)
        # Re-read changing public address into memory only; preserve SSH identity/host-key checks.
        running=aws(c,'ec2','describe-instances','--instance-ids',c['instance_id'])['Reservations'][0]['Instances'][0]
        if running.get('PublicIpAddress'):
            ssh[1:1]=['-o','Hostname='+running['PublicIpAddress']]
        # Arm the timer on the first SSH command, before checkout/install.
        until=time.monotonic()+240;renewed_ssh_key=False
        while True:
            try:
                remote('sudo shutdown -h +'+str(ROUND_MINUTES[a.round])+' && umask 077 && mkdir -p '+
                       shlex.quote(c['remote_dir'])+' && date +%s > '+shlex.quote(c['remote_dir']+'/timer-start.txt'))
                break
            except ValueError:
                if 'Permission denied' in last_remote_text and c.get('ssh_public_key_file') and not renewed_ssh_key:
                    # Renew only this user-specified temporary public key, using the Mac profile.
                    result=aws(c,'ec2-instance-connect','send-ssh-public-key','--instance-id',c['instance_id'],
                         '--instance-os-user','ubuntu','--availability-zone',running['Placement']['AvailabilityZone'],
                         '--ssh-public-key','file://'+c['ssh_public_key_file'])
                    if not result.get('Success'):raise ValueError('NAMED_SSH_PUBLIC_KEY_RENEWAL_FAILED')
                    renewed_ssh_key=True
                if time.monotonic()>=until:raise
                time.sleep(5)
        aws(c,'ec2','wait','instance-status-ok','--instance-ids',c['instance_id'],timeout=360)
        local=Path(__file__).resolve().parent;remote_dir=c['remote_dir']
        for initial in ['common.py','export_evidence.py','failure_export.py']:
            code,_=run(['scp','-q',*ssh[1:-1],str(local/initial),c['ssh_alias']+':'+remote_dir+'/'+initial],timeout=30);check(code)
        if c.get('discover_server_inputs'):
            helper=local/'server_inputs.py'
            code,_=run(['scp','-q',*ssh[1:-1],str(helper),c['ssh_alias']+':'+remote_dir+'/'+helper.name],timeout=30)
            check(code)
            value=json.loads(remote('sudo python3 '+shlex.quote(remote_dir+'/'+helper.name)+' --output '+shlex.quote(remote_dir+'/server-config.json'),timeout=45))
            target=Path(c['server_config_file'])
            target.write_text(json.dumps(value,indent=2)+'\n');target.chmod(0o600)
        files=[artifact/'serving-gateway-rt-7741687d.tar',artifact/'byul-7741687d.bundle',artifact/'build.json',
               Path(a.approval_file),Path(c['server_config_file'])]
        files+=list(local.glob('*.py'))+list(local.glob('*.sh'))
        for p in files:
            code,_=run(['scp','-q',*ssh[1:-1],str(p),c['ssh_alias']+':'+remote_dir+'/'+p.name],timeout=300)
            check(code)
        # Remote names of private receipt/config must match caller-supplied names.
        cfg=remote_dir+'/'+Path(c['server_config_file']).name
        receipt_path=remote_dir+'/'+Path(a.approval_file).name
        remote('chmod 600 '+shlex.quote(cfg)+' '+shlex.quote(receipt_path))
        # Clone only once; repeat rounds reuse exactly the same verified baseline.
        checkout=c['server_checkout'];bundle=remote_dir+'/byul-7741687d.bundle'
        setup=('cd '+shlex.quote(remote_dir)+' && python3 bootstrap_server.py --config '+shlex.quote(cfg)+
               ' --approval-file '+shlex.quote(receipt_path))
        # This setup never prints source metadata or SSH diagnostics to the chat.
        remote(setup,timeout=420)
        remote_evidence=remote_dir+'/evidence-round'+str(a.round)+'-'+str(int(time.time()))
        worker='python3 round_runner.py --execute --round '+str(a.round)+\
               ' --approval-file '+shlex.quote(receipt_path)+' --config '+shlex.quote(cfg)+\
               ' --evidence '+shlex.quote(remote_evidence)+' --timer-state '+shlex.quote(remote_dir+'/timer-start.txt')+\
               ' >'+shlex.quote(remote_evidence+'.worker.log')+' 2>&1; printf %s $? > '+shlex.quote(remote_evidence+'.exit')
        launch='cd '+shlex.quote(remote_dir)+' || exit 1; nohup sh -c '+shlex.quote(worker)+' >/dev/null 2>&1 </dev/null &'
        remote(launch)
        ready=remote_evidence+'-export.ready.json'
        next_state_check=0
        while time.monotonic()-start<planned-120:
            probe='if test -f '+shlex.quote(ready)+'; then cat '+shlex.quote(ready)+'; elif test -f '+shlex.quote(remote_evidence+'-export.failed.json')+'; then cat '+shlex.quote(remote_evidence+'-export.failed.json')+'; elif test -f '+shlex.quote(remote_evidence+'.exit')+'; then printf '+shlex.quote('{"worker_exited_without_export":true}')+'; else exit 1; fi'
            code,text=run([*ssh,probe],timeout=20)
            if code==0:
                metadata=json.loads(text)
                if metadata.get('export_failed') or metadata.get('worker_exited_without_export'):raise ValueError('WORKER_EXPORT_FAILED_NO_WAIT')
                break
            if time.monotonic()>=next_state_check:
                state=aws(c,'ec2','describe-instances','--instance-ids',c['instance_id'])['Reservations'][0]['Instances'][0]['State']['Name']
                if state!='running':raise ValueError('REMOTE_STOPPED_BEFORE_EXPORT')
                next_state_check=time.monotonic()+30
            time.sleep(5)
        else:raise ValueError('ROUND_EXPORT_TIMEOUT')
        destination=Path(a.output);destination.mkdir(parents=True,exist_ok=False,mode=0o700)
        archive=destination/'round-export.tgz'
        code,_=run(['scp','-q',*ssh[1:-1],c['ssh_alias']+':'+remote_evidence+'-export.tgz',str(archive)],timeout=180)
        check(code)
        if digest(archive)!=metadata['archive_sha256']:raise ValueError('DOWNLOADED_EVIDENCE_HASH_MISMATCH')
        import tarfile
        from export_evidence import leaks
        with tarfile.open(archive) as t:
            for member in t.getmembers():
                if not member.isfile():continue
                if not member.name.startswith(Path(remote_evidence+'-export').name+'/'):
                    raise ValueError('ARCHIVE_PATH_UNEXPECTED')
                if leaks(t.extractfile(member).read().decode()):raise ValueError('DOWNLOADED_EVIDENCE_LEAK')
                if Path(member.name).name=='round-summary.json':
                    summary=json.loads(t.extractfile(member).read())
                    reservation['results']={x['id']:x['result'] for x in summary['items']}
        save(destination/'download-verification.json',metadata)
        remote('touch '+shlex.quote(remote_evidence+'-export.ack'))
    except Exception:
        if start_sent:
            diagnostics.mkdir(parents=True,exist_ok=True,mode=0o700)
            try:
                manifest=json.loads(remote('cd '+shlex.quote(c['remote_dir'])+' && python3 failure_export.py',timeout=60))
                target=diagnostics/('server-failure-'+str(time.time_ns())+'.tgz')
                code,_=run(['scp','-q',*ssh[1:-1],c['ssh_alias']+':'+manifest['archive'],str(target)],timeout=60)
                if code or digest(target)!=manifest['archive_sha256']:raise ValueError('FAILURE_EVIDENCE_TRANSFER_FAILED')
                from export_evidence import leaks
                import tarfile
                with tarfile.open(target) as archive_check:
                    for member in archive_check.getmembers():
                        if member.isfile() and leaks(archive_check.extractfile(member).read().decode()):raise ValueError('FAILURE_EVIDENCE_LEAK')
                save(diagnostics/('failure-receipt-'+str(time.time_ns())+'.json'),{'downloaded':True,'sha256':digest(target),'leak_hits':0})
            except Exception as exc:
                save(diagnostics/('failure-collection-'+str(time.time_ns())+'.json'),{'server_diagnostics_recovered':False,'error_class':type(exc).__name__})
        raise
    finally:
        if start_sent:
            # A local stop request is always sent, even when SSH/export/setup fails.
            for attempt in range(3):
                try:
                    aws(c,'ec2','stop-instances','--instance-ids',c['instance_id'])
                    aws(c,'ec2','wait','instance-stopped','--instance-ids',c['instance_id'],timeout=360)
                    break
                except ValueError:
                    if attempt==2:raise ValueError('STOP_UNCONFIRMED_TIMER_REMAINS_ARMED')
            reservation.update(ended_at=now(),elapsed_seconds=time.monotonic()-start,state='stopped_confirmed')
            # Before start keep the full cap; after confirmed stop reconcile to elapsed + 60s.
            reservation['reserved_upper_bound_seconds']=planned
            reservation['upper_bound_seconds']=min(planned,math.ceil(reservation['elapsed_seconds'])+60)
            reservation['cost_upper_bound']=reservation['upper_bound_seconds']*PRICE/3600
            save(temporary,old);temporary.replace(ledger)
            result=inventory(c)
            safe_output=diagnostics
            safe_output.mkdir(parents=True,exist_ok=True,mode=0o700)
            save(safe_output/'cleanup.json',result)

def main():
    p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');p.add_argument('--round',type=int,choices=(1,2),default=1)
    for name in ['approval-file','config','ledger','artifacts','output']:p.add_argument('--'+name)
    a=p.parse_args()
    if not a.execute:
        print(json.dumps({'mode':'offline-plan','aws_commands_executed':0,
          'order':['explicit approval receipt','verify local artifact hashes','budget/subscriber check',
                   'exactly one stopped existing dev + shutdown=stop','reserve cumulative cap','start',
                   'first SSH arms relative timer','copy/verify baseline artifacts','round',
                   'download sanitized archive/hash/leak scan','ack','stop and confirm','read-only cleanup'],
          'no_create_or_terminate':True},indent=2));return
    if not all([a.approval_file,a.config,a.ledger,a.artifacts,a.output]):p.error('all private execution inputs required')
    try:execute(a)
    except Exception as e:
        # Do not surface cloud/SSH exception text or identifiers.
        print('EXECUTION_STOPPED: '+type(e).__name__);raise SystemExit(1)
if __name__=='__main__':main()
