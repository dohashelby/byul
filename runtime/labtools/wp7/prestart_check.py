"""Approved read-only preflight. Account and instance identities stay in memory."""
import json,os,re,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[5]
WP=ROOT/'runtime/project-context/refactor-2026-11/wp7'
sys.path.insert(0,str(WP))
from common import approval,save
from export_evidence import Sanitizer,leaks
WORK=Path(os.environ['WP7_EXEC_WORK']).resolve()
RECEIPT=WORK/'approval.json'
LOG=WORK/'prestart-evidence'/datetime.now(timezone.utc).strftime('attempt-%Y%m%dT%H%M%SZ')
PROFILE='byul-lab'
class QueryFailure(Exception):pass
def call(service,operation,*args):
    approval(RECEIPT)
    region='us-east-1' if service=='budgets' else 'ap-northeast-2'
    cmd=['aws','--profile',PROFILE,'--region',region,'--no-cli-pager',service,operation,*args,'--output','json']
    try:p=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
    except subprocess.TimeoutExpired:raise QueryFailure('AWS_QUERY_TIMEOUT') from None
    if p.returncode:
        match=re.search(r'An error occurred \(([A-Za-z0-9_.-]+)\)',p.stderr)
        reason=match[1] if match else 'AWS_AUTH_OR_QUERY_FAILED'
        raise QueryFailure(reason)
    return json.loads(p.stdout) if p.stdout.strip() else {}
def record(name,data):
    safe=Sanitizer().value(data)
    assert not leaks(json.dumps(safe)),'PREFLIGHT_EXPORT_UNSAFE'
    save(LOG/name,safe)
def main():
    approval(RECEIPT);LOG.mkdir(mode=0o700,parents=True,exist_ok=False)
    summary={'checked_at':datetime.now(timezone.utc).isoformat(),'profile':PROFILE,
             'region':'ap-northeast-2','started_instances':0,'mutations':0}
    account=call('sts','get-caller-identity')['Account']
    expected=WORK/'expected-profile-account.json'
    if expected.exists():
        from hashlib import sha256
        if sha256(account.encode()).hexdigest()!=json.loads(expected.read_text())['account_sha256']:
            raise QueryFailure('PROFILE_ACCOUNT_MISMATCH')
    summary['profile_account_matches_expected']=True
    # Find an existing project dev, never select a server by size alone.
    rows=call('ec2','describe-instances','--filters','Name=instance-type,Values=g6.2xlarge',
              'Name=instance-state-name,Values=stopped,running,pending,stopping')['Reservations']
    candidates=[]
    for reservation in rows:
        for item in reservation['Instances']:
            tags={x['Key']:x['Value'] for x in item.get('Tags',[])}
            values=' '.join(tags.values()).lower()
            if 'byul' in values:
                candidates.append(item)
    record('dev-candidate-states.json',{'candidates':[{'instance':x['InstanceId'],
         'state':x['State']['Name'],'instance_type':x['InstanceType'],
         'attached_disks':len(x.get('BlockDeviceMappings',[]))} for x in candidates]})
    summary['existing_dev_candidates']=len(candidates)
    summary['existing_dev_states']=[x['State']['Name'] for x in candidates]
    budgets=call('budgets','describe-budgets','--account-id',account)['Budgets']
    eligible=[]
    for b in budgets:
        name=b['BudgetName']
        notifications=call('budgets','describe-notifications-for-budget','--account-id',account,'--budget-name',name)['Notifications']
        usable=[]
        for n in notifications:
            subscriptions=call('budgets','describe-subscribers-for-notification','--account-id',account,
                              '--budget-name',name,'--notification',json.dumps(n))['Subscribers']
            if subscriptions:
                usable.append({'type':n.get('NotificationType'),'threshold':n.get('Threshold'),
                               'threshold_type':n.get('ThresholdType'),'subscriber_count':len(subscriptions)})
        if usable:eligible.append((name,usable))
    summary['budget_notification_with_subscriber']=bool(eligible)
    summary['eligible_budget_count']=len(eligible)
    if not eligible:
        summary['decision']='STOP_BEFORE_START_BUDGET_ALARM_MISSING'
        record('summary.json',summary)
        print(json.dumps(summary,ensure_ascii=False,indent=2));return 2
    if len(candidates)!=1:
        summary['decision']='STOP_EXISTING_DEV_NOT_UNIQUE'
        record('summary.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2));return 3
    selected=candidates[0]
    if selected['State']['Name']!='stopped':
        summary['decision']='HOLD_EXISTING_DEV_NOT_STOPPED'
        record('summary.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2));return 4
    behavior=call('ec2','describe-instance-attribute','--instance-id',selected['InstanceId'],
                  '--attribute','instanceInitiatedShutdownBehavior')['InstanceInitiatedShutdownBehavior']['Value']
    summary['shutdown_behavior']=behavior
    if behavior!='stop':
        summary['decision']='STOP_SHUTDOWN_IS_NOT_STOP'
        record('summary.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2));return 5
    # Only non-identity selection information is written to private controls.
    tags={x['Key']:x['Value'] for x in selected.get('Tags',[])}
    name=tags.get('Name')
    if name and leaks(name):raise QueryFailure('PRIVATE_INSTANCE_SELECTION_NEEDS_REVIEW')
    import hashlib
    control={'aws_profile':PROFILE,'budget_name':eligible[0][0],
             'instance_filters':(['Name=tag:Name,Values='+name] if name else [])+['Name=instance-type,Values=g6.2xlarge'],
             'expected_instance_sha256':hashlib.sha256(selected['InstanceId'].encode()).hexdigest(),
             'auxiliary_reserve_usd':3}
    save(WORK/'resolved-selection.json',control)
    summary['decision']='READ_ONLY_PRESTART_PASSED'
    summary['budget_notifications']=eligible[0][1]
    record('summary.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':
    try:raise SystemExit(main())
    except QueryFailure as e:
        result={'decision':'STOP_BEFORE_START_QUERY_UNVERIFIED','reason':str(e),'started_instances':0,'mutations':0}
        if LOG.exists() and not (LOG/'query-failure.json').exists():record('query-failure.json',result)
        print(json.dumps(result));raise SystemExit(1)
    except Exception as e:
        print(json.dumps({'decision':'STOP_BEFORE_START','reason_class':type(e).__name__,'started_instances':0,'mutations':0}))
        raise SystemExit(1)
