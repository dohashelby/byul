"""Tests for the explicitly approved WP7 budget and N-03 amendments."""
import contextlib,importlib.util,io,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import execute_from_mac as mac
import round_runner,server_checks
from common import BASELINE

class BudgetRules(unittest.TestCase):
    def check_budget(self,notifications,subscribed):
        calls=[]
        def aws(c,*args,**kwargs):
            calls.append(args)
            if args[:2]==('sts','get-caller-identity'):return {'Account':'offline-account'}
            if args[:2]==('budgets','describe-notifications-for-budget'):return {'Notifications':notifications}
            if args[:2]==('budgets','describe-subscribers-for-notification'):
                return {'Subscribers':[{'SubscriptionType':'EMAIL','Address':'fixture.invalid'}] if subscribed else []}
            raise AssertionError('Unexpected API operation')
        with patch.object(mac,'aws',side_effect=aws):
            if notifications and subscribed:mac.budget({'budget_name':'fixture'})
            else:
                with self.assertRaises(ValueError):mac.budget({'budget_name':'fixture'})
        self.assertTrue(all(x[1].startswith(('describe-','get-')) for x in calls))
    def test_existing_small_alarm_is_accepted(self):
        self.check_budget([{'Threshold':1,'ThresholdType':'PERCENTAGE','NotificationType':'ACTUAL'}],True)
    def test_amount_is_not_a_gate(self):
        self.check_budget([{'Threshold':9999,'ThresholdType':'ABSOLUTE_VALUE','NotificationType':'FORECASTED'}],True)
    def test_missing_alarm_rejected(self):self.check_budget([],False)
    def test_missing_subscriber_rejected(self):
        self.check_budget([{'Threshold':1,'ThresholdType':'PERCENTAGE'}],False)

class PrestartRules(unittest.TestCase):
    def test_prestart_accepts_existing_alarm_without_dev_tag(self):
        path=Path(__file__).parent/'prestart_check.py'
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ,{'WP7_EXEC_WORK':temp}):
                spec=importlib.util.spec_from_file_location('wp7_prestart_fixture',path)
                module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            module.WORK=Path(temp);module.LOG=Path(temp)/'preflight'
            def call(service,op,*args):
                if (service,op)==('sts','get-caller-identity'):return {'Account':'offline-account'}
                if (service,op)==('ec2','describe-instances'):
                    return {'Reservations':[{'Instances':[{'InstanceId':'offline-instance','InstanceType':'g6.2xlarge',
                        'State':{'Name':'stopped'},'Tags':[{'Key':'Name','Value':'byul-fixture'}]}]}]}
                if op=='describe-budgets':return {'Budgets':[{'BudgetName':'fixture','BudgetLimit':{'Amount':'1','Unit':'USD'}}]}
                if op=='describe-notifications-for-budget':
                    return {'Notifications':[{'Threshold':1,'ThresholdType':'PERCENTAGE','NotificationType':'ACTUAL'}]}
                if op=='describe-subscribers-for-notification':return {'Subscribers':[{'SubscriptionType':'EMAIL'}]}
                if op=='describe-instance-attribute':return {'InstanceInitiatedShutdownBehavior':{'Value':'stop'}}
                raise AssertionError('Unexpected API operation')
            with patch.object(module,'approval'),patch.object(module,'call',side_effect=call),patch.object(module,'record'),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(module.main(),0)
            self.assertTrue((Path(temp)/'resolved-selection.json').is_file())

class RoundRules(unittest.TestCase):
    def execute_with_failure(self,fail):
        called=[];commands=[]
        class FakeLive:
            def __init__(self,*_):self.command_log=[];self.output_log=[]
            def emergency_snapshot(self):return None
            def fresh_gateway(self):pass
            def normal(self):pass
            def services(self):pass
            def close(self):pass
            def run(self,ident):
                called.append(ident)
                if ident==fail:raise server_checks.CheckFailure('SYNTHETIC_COMMAND_FAILED_124')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);token=root/'tokens.env';token.write_text('SYNTHETIC=opaque-local-fixture-only\n')
            evidence=root/'round';(root/'round-export.ack').touch()
            def run(argv,**kwargs):
                commands.append(argv)
                return (0,BASELINE+'\n') if argv[0]=='git' else (0,'')
            with patch.object(round_runner,'approval'),patch.object(round_runner,'private_json',return_value={'runtime_root':str(root),'token_env_file':str(token)}),\
                 patch.object(round_runner.sys,'platform','linux'),patch.object(round_runner,'run',side_effect=run),\
                 patch.object(round_runner.signal,'signal'),patch.object(round_runner.signal,'alarm'),patch.object(server_checks,'Live',FakeLive):
                code=round_runner.execute(1,'fixture','fixture',evidence)
            summary=json.loads((evidence/'round-summary.json').read_text())
            self.assertEqual(code,1)
            self.assertTrue(any(x[:3]==['sudo','shutdown','-h'] and x[-1]=='+150' for x in commands))
            self.assertTrue(any(x[:3]==['sudo','shutdown','-h'] and x[-1]=='now' for x in commands))
            return called,summary
    def test_N03_failure_continues_and_remains_failed(self):
        called,summary=self.execute_with_failure('N-03')
        self.assertEqual(called[called.index('N-03')+1],'N-01')
        self.assertEqual(called[-1],'N-05')  # N-06's one retry is scheduled in R2.
        item=next(x for x in summary['items'] if x['id']=='N-03')
        self.assertFalse(item['passed']);self.assertEqual(item['failure_rule'],'continue')
    def test_other_stop_rules_remain_effective(self):
        for ident in ['R-01','R-02','N-01','R-04']:
            with self.subTest(id=ident):
                called,_=self.execute_with_failure(ident)
                self.assertEqual(called[-1],ident)
                self.assertNotIn('R-05',called)
    def test_only_N03_plan_rule_changed(self):
        from plan import STEPS
        expected={'R-01':'stop','R-02':'stop','N-03':'continue','N-01':'stop','R-03':'continue',
            'R-04':'critical-auth','R-05':'continue','R-06':'continue','R-07':'continue','R-08':'continue',
            'R-09':'restore','R-10':'continue','N-02':'continue','N-04':'restore','N-05':'restore',
            'N-06':'restore','R-11':'restore','R-12':'restore','R-13':'continue','N-07':'restore'}
        self.assertEqual({x[0]:x[3] for x in STEPS},expected)

def check_revision_rules():
    suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(x)
                            for x in [BudgetRules,PrestartRules,RoundRules])
    output=io.StringIO();result=unittest.TextTestRunner(stream=output,verbosity=2).run(suite)
    if not result.wasSuccessful():raise AssertionError(output.getvalue())
    return {'tests':result.testsRun,'passed':result.testsRun,'AWS_commands':0,
            'actual_round_loop_failure_paths':5,'stdout':output.getvalue()}
if __name__=='__main__':print(json.dumps(check_revision_rules(),indent=2))
