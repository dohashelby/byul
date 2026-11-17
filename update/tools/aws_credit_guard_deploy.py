"""Deploy the reviewed owned-lab credit guard via an already authenticated CLI.

No access keys are persisted. Configuration contains identifiers and bounds only.
Runtime Boto3 is supplied by Lambda; this tool adds no package dependency.
"""
from __future__ import annotations
import argparse,base64,hashlib,io,json,os,subprocess,time,zipfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
from tools import aws_credit_guard as guard


def execution_policy(cfg,infra):
    region,account=cfg['region'],cfg['account_id'];base=f'arn:aws:ec2:{region}:{account}:'
    own={'StringEquals':{'aws:ResourceTag/'+guard.TAG:cfg['guard_id'],'aws:ResourceTag/'+guard.EPHEMERAL:'true'}}
    request={'aws:RequestTag/'+guard.TAG:cfg['guard_id'],'aws:RequestTag/'+guard.EPHEMERAL:'true'}
    statements=[
        {'Effect':'Deny','Action':['ec2:*','ssm:SendCommand','ssm:StartSession'],'Resource':'*',
         'Condition':{'StringNotEquals':{'aws:RequestedRegion':region}}},
        {'Effect':'Allow','Action':['ec2:Describe*','sts:GetCallerIdentity','freetier:GetAccountPlanState'], 'Resource':'*'},
        {'Effect':'Allow','Action':'ec2:RunInstances','Resource':[
            f'arn:aws:ec2:{region}::image/'+image for image in infra['image_ids']]+[
            base+'subnet/'+infra['subnet_id'],base+'network-interface/*',base+'security-group/*']},
        {'Effect':'Allow','Action':'ec2:RunInstances','Resource':base+'instance/*',
         'Condition':{'StringEquals':{**request,'ec2:InstanceType':list(cfg['hourly_upper_usd'])}}},
        {'Effect':'Allow','Action':'ec2:RunInstances','Resource':base+'volume/*',
         'Condition':{'StringEquals':request,'Bool':{'ec2:Encrypted':'true'},'NumericLessThanEquals':{'ec2:VolumeSize':200}}},
        {'Effect':'Allow','Action':'ec2:CreateSecurityGroup','Resource':base+'vpc/'+infra['vpc_id']},
        {'Effect':'Allow','Action':'ec2:CreateSecurityGroup','Resource':base+'security-group/*','Condition':{'StringEquals':request}},
        {'Effect':'Allow','Action':'ec2:CreateTags','Resource':[base+'instance/*',base+'volume/*',base+'security-group/*'],
         'Condition':{'StringEquals':{'ec2:CreateAction':['RunInstances','CreateSecurityGroup']}}},
        {'Effect':'Allow','Action':['ec2:TerminateInstances','ec2:StopInstances','ec2:DeleteVolume','ec2:DeleteSecurityGroup'],
         'Resource':base+'*/*','Condition':own},
        {'Effect':'Allow','Action':['ssm:GetCommandInvocation','ssm:DescribeInstanceInformation','ssm:GetConnectionStatus',
          'ssm:ListCommands','ssm:ListCommandInvocations','ssm:TerminateSession','ssm:ResumeSession'],'Resource':'*'},
        {'Effect':'Allow','Action':['ssm:SendCommand','ssm:StartSession'],'Resource':base+'instance/*',
         'Condition':{'StringEquals':{'ssm:resourceTag/'+guard.TAG:cfg['guard_id']}}},
        {'Effect':'Allow','Action':['ssm:SendCommand','ssm:StartSession'],'Resource':[
          f'arn:aws:ssm:{region}::document/AWS-RunShellScript',f'arn:aws:ssm:{region}::document/AWS-StartPortForwardingSession']},
        {'Effect':'Allow','Action':'ssm:GetParameter','Resource':f'arn:aws:ssm:{region}:{account}:parameter'+cfg['state_parameter']},
        {'Effect':'Allow','Action':'ssm:PutParameter','Resource':f'arn:aws:ssm:{region}:{account}:parameter'+cfg['state_parameter'].removesuffix('/state')+'/launch-lock'},
        {'Effect':'Allow','Action':'iam:PassRole','Resource':infra['instance_role_arn'],
         'Condition':{'StringEquals':{'iam:PassedToService':'ec2.amazonaws.com'}}},
        {'Effect':'Allow','Action':'iam:GetInstanceProfile','Resource':infra['instance_profile_arn']},
    ]
    return {'Version':'2012-10-17','Statement':statements}


def watcher_policy(cfg):
    region,account=cfg['region'],cfg['account_id'];base=f'arn:aws:ec2:{region}:{account}:'
    return {'Version':'2012-10-17','Statement':[
        {'Effect':'Allow','Action':['freetier:GetAccountPlanState','ec2:DescribeInstances','ec2:DescribeVolumes','ec2:DescribeSecurityGroups'],'Resource':'*'},
        {'Effect':'Allow','Action':['iam:PutRolePolicy','iam:ListRolePolicies'],'Resource':f'arn:aws:iam::{account}:role/'+cfg['execution_role']},
        {'Effect':'Allow','Action':['ec2:TerminateInstances','ec2:DeleteVolume','ec2:DeleteSecurityGroup'],'Resource':base+'*/*',
         'Condition':{'StringEquals':{'aws:ResourceTag/'+guard.TAG:cfg['guard_id'],'aws:ResourceTag/'+guard.EPHEMERAL:'true'}}},
        {'Effect':'Allow','Action':['ssm:PutParameter'],'Resource':f'arn:aws:ssm:{region}:{account}:parameter'+cfg['state_parameter']},
        {'Effect':'Allow','Action':'events:DisableRule','Resource':f'arn:aws:events:{region}:{account}:rule/'+cfg['guard_id']},
        {'Effect':'Allow','Action':['logs:CreateLogStream','logs:PutLogEvents'],
         'Resource':f'arn:aws:logs:{region}:{account}:log-group:/aws/lambda/'+cfg['guard_id']+':*'}]}


class CLI:
    def __init__(self,binary,profile,region,output):
        self.prefix=[str(binary),'--profile',profile,'--region',region,'--no-cli-pager',
            '--cli-connect-timeout','5','--cli-read-timeout','60','--output','json']
        self.output=Path(output);self.output.mkdir(mode=0o700,parents=True,exist_ok=True)
    def call(self,service,operation,*args,missing=None):
        r=subprocess.run(self.prefix+[service,operation,*map(str,args)],capture_output=True)
        if r.returncode:
            error=r.stderr.decode()
            if missing and '('+missing+')'in error:return None
            raise RuntimeError(error[:1600])
        return json.loads(r.stdout)if r.stdout.strip()else{}
    def document(self,name,value):
        p=self.output/name;p.write_text(guard.canonical(value)+'\n',encoding='utf-8');p.chmod(0o600)
        return 'file://'+str(p)


def ensure_role(api,name,trust,guard_id):
    value=api.call('iam','get-role','--role-name',name,missing='NoSuchEntity')
    if value is None:
        value=api.call('iam','create-role','--role-name',name,'--max-session-duration','3600',
            '--assume-role-policy-document',api.document(name+'-trust.json',trust),
            '--tags',json.dumps([{'Key':guard.TAG,'Value':guard_id}]))
    else:
        assert any(t['Key']==guard.TAG and t['Value']==guard_id for t in value['Role'].get('Tags',[])), 'Existing unowned role refused'
        assert value['Role']['AssumeRolePolicyDocument']==trust,'Existing role trust changed'
    return value['Role']['Arn']


def deploy(api,cfg,infra):
    guard.validate_config(cfg)
    assert datetime.now(timezone.utc)<guard.timestamp(cfg['monitor_deadline_at'])<=datetime.now(timezone.utc)+timedelta(hours=24)
    assert api.call('sts','get-caller-identity')['Account']==cfg['account_id']
    balance=guard.credit_balance(api.call('freetier','get-account-plan-state'),cfg['account_id'])
    assert balance>guard.amount(cfg['reserve_usd'])
    trust={'Version':'2012-10-17','Statement':[
        {'Effect':'Allow','Principal':{'AWS':infra['instance_role_arn']},'Action':'sts:AssumeRole'},
        {'Effect':'Allow','Principal':{'Service':'lambda.amazonaws.com'},'Action':'sts:AssumeRole'}]}
    exec_arn=ensure_role(api,cfg['execution_role'],trust,cfg['guard_id'])
    watcher_trust={'Version':'2012-10-17','Statement':[{'Effect':'Allow','Principal':{'Service':'lambda.amazonaws.com'},'Action':'sts:AssumeRole'}]}
    watcher_name=cfg['guard_id']+'-watcher';watcher_arn=ensure_role(api,watcher_name,watcher_trust,cfg['guard_id'])
    for role,name,policy in[(cfg['execution_role'],'OwnedLabBase',execution_policy(cfg,infra)),
            (cfg['execution_role'],guard.LEASE,guard.deny_document(expires=datetime.now(timezone.utc)-timedelta(seconds=1))),
            (watcher_name,'OwnedLabCreditWatcher',watcher_policy(cfg))]:
        api.call('iam','put-role-policy','--role-name',role,'--policy-name',name,'--policy-document',api.document(role+'-'+name+'.json',policy))
    log='/aws/lambda/'+cfg['guard_id']
    existing=api.call('logs','describe-log-groups','--log-group-name-prefix',log)['logGroups']
    if not any(g['logGroupName']==log for g in existing):api.call('logs','create-log-group','--log-group-name',log,'--tags',json.dumps({guard.TAG:cfg['guard_id']}))
    else:
        assert api.call('logs','list-tags-log-group','--log-group-name',log).get('tags',{}).get(guard.TAG)==cfg['guard_id'],'Unowned log group refused'
    api.call('logs','put-retention-policy','--log-group-name',log,'--retention-in-days','1')
    source=Path(guard.__file__).read_bytes();z=api.output/'lambda.zip'
    with zipfile.ZipFile(z,'w',zipfile.ZIP_DEFLATED)as archive:archive.writestr('lambda_function.py',source)
    environment=api.document('lambda-environment.json',{'Variables':{'CREDIT_GUARD_CONFIG':guard.canonical(cfg)}})
    old=api.call('lambda','get-function','--function-name',cfg['guard_id'],missing='ResourceNotFoundException')
    if old is None:
        for attempt in range(10):
            try:
                value=api.call('lambda','create-function','--function-name',cfg['guard_id'],'--runtime','python3.12',
                    '--role',watcher_arn,'--handler','lambda_function.handler','--timeout','50','--memory-size','128',
                    '--zip-file','fileb://'+str(z),'--environment',environment,'--tags',json.dumps({guard.TAG:cfg['guard_id']}));break
            except RuntimeError as e:
                if 'cannot be assumed by Lambda'not in str(e)or attempt==9:raise
                time.sleep(2)
    else:
        assert old.get('Tags',{}).get(guard.TAG)==cfg['guard_id'],'Unowned Lambda refused'
        api.call('lambda','update-function-code','--function-name',cfg['guard_id'],'--zip-file','fileb://'+str(z))
        api.call('lambda','wait','function-updated-v2','--function-name',cfg['guard_id'])
        value=api.call('lambda','update-function-configuration','--function-name',cfg['guard_id'],
            '--role',watcher_arn,'--handler','lambda_function.handler','--timeout','50','--memory-size','128','--environment',environment)
    api.call('lambda','wait','function-active-v2','--function-name',cfg['guard_id'])
    concurrency=1
    try:api.call('lambda','put-function-concurrency','--function-name',cfg['guard_id'],'--reserved-concurrent-executions','1')
    except RuntimeError as e:
        if 'minimum'not in str(e)or 'UnreservedConcurrentExecution'not in str(e):raise
        concurrency=None  # One-minute/no-retry events remain idempotent without this optional quota.
    arn=value['FunctionArn']
    existing_rule=api.call('events','describe-rule','--name',cfg['guard_id'],missing='ResourceNotFoundException')
    if existing_rule:
        rule_tags=api.call('events','list-tags-for-resource','--resource-arn',existing_rule['Arn'])
        assert any(t['Key']==guard.TAG and t['Value']==cfg['guard_id']for t in rule_tags['Tags']),'Unowned schedule refused'
    rule=api.call('events','put-rule','--name',cfg['guard_id'],'--schedule-expression','rate(1 minute)',
        '--state','ENABLED','--tags',json.dumps([{'Key':guard.TAG,'Value':cfg['guard_id']}]))
    try:api.call('lambda','add-permission','--function-name',cfg['guard_id'],'--statement-id','OwnedCreditSchedule',
        '--action','lambda:InvokeFunction','--principal','events.amazonaws.com','--source-arn',rule['RuleArn'])
    except RuntimeError as e:
        if 'ResourceConflictException'not in str(e):raise
        policy=json.loads(api.call('lambda','get-policy','--function-name',cfg['guard_id'])['Policy'])
        row,=[s for s in policy['Statement']if s['Sid']=='OwnedCreditSchedule']
        assert row['Condition']['ArnLike']['AWS:SourceArn']==rule['RuleArn']
    api.call('events','put-targets','--rule',cfg['guard_id'],'--targets',json.dumps([{
        'Id':'OwnedCreditGuard','Arn':arn,'RetryPolicy':{'MaximumEventAgeInSeconds':60,'MaximumRetryAttempts':0}}]))
    response_file=api.output/'initial-invocation.json'
    invoke=api.call('lambda','invoke','--function-name',cfg['guard_id'],'--payload','fileb://'+str(Path(api.document('event.json',{}).removeprefix('file://'))),str(response_file))
    assert not invoke.get('FunctionError'),response_file.read_text()[:1600]
    result=json.loads(response_file.read_bytes())
    assert result['schema']==guard.SCHEMA and not result['blocked']and result['source_sha256']==hashlib.sha256(source).hexdigest()
    result.update(function_arn=arn,rule_arn=rule['RuleArn'],execution_role_arn=exec_arn,
        installed_schedule='rate(1 minute)',automatic_stop_live_canary_verified=False,
        reserved_concurrency=concurrency)
    (api.output/'deployment.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in('config','aws-binary','profile','output'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args();value=json.loads(Path(args.config).read_bytes())
    api=CLI(args.aws_binary,args.profile,value['guard']['region'],args.output)
    result=deploy(api,value['guard'],value['infrastructure'])
    print(json.dumps(result))


if __name__=='__main__':main()
