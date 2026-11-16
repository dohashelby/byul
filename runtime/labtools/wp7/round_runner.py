"""Run on the server only after approval. Default is an offline plan."""
from __future__ import annotations
import argparse,json,os,signal,sys,time
from pathlib import Path
from common import BASELINE,approval,now,private_json,run,save
from plan import STEPS,ROUND_MINUTES,describe

def must_stop(rule,error):
    return rule=='stop' or error in {'RecoveryFailure','TimeoutExpired'} or error=='RoundTerminated'

def execute(number,config_path,receipt,evidence,timer_state=None):
    approval(receipt)
    if sys.platform!='linux':raise ValueError('SERVER_EXECUTION_REQUIRES_LINUX')
    c=private_json(config_path);root=Path(c['runtime_root'])
    code,head=run(['git','rev-parse','HEAD'],cwd=root,timeout=10)
    if code or head.strip()!=BASELINE:raise ValueError('BASELINE_MISMATCH')
    out=Path(evidence);out.mkdir(mode=0o700,parents=True,exist_ok=False)
    bootstrap=Path(__file__).resolve().parent/'bootstrap'
    if bootstrap.is_dir():
        import shutil
        shutil.copytree(bootstrap,out/'bootstrap')
    started=now();c['round_started_at']=started
    # The server role uses no AWS credentials.
    cap=ROUND_MINUTES[number]*60
    if timer_state:
        elapsed=time.time()-float(Path(timer_state).read_text().strip())
        if not 0<=elapsed<cap-600:raise ValueError('INITIAL_TIMER_BUDGET_EXHAUSTED_OR_CLOCK_INVALID')
        remaining=cap-elapsed  # Never re-arm or extend the first-SSH shutdown timer.
    else:
        code,_=run(['sudo','shutdown','-h','+'+str(ROUND_MINUTES[number])],timeout=15)
        if code:raise ValueError('SHUTDOWN_TIMER_NOT_ARMED')
        remaining=cap
    sys.path[:0]=[str(root),str(root/'src')]
    from server_checks import Live
    deadline=time.monotonic()+remaining-600
    rows=[];interrupted=False
    class RoundTerminated(Exception):pass
    def terminate(*_):raise RoundTerminated()
    signal.signal(signal.SIGTERM,terminate);signal.signal(signal.SIGINT,terminate)
    try:
        for ident,r,limit,rule,criteria in STEPS:
            if r!=number or (c.get('only_ids') and ident not in c['only_ids']):continue
            if time.monotonic()+limit>deadline:break
            item=out/ident;item.mkdir(mode=0o700)
            step_start=now();live=Live(c,item);error=None;exit_code=0;snapshot=None
            try:
                # Watchdog in each external command; alarm also bounds API loops.
                signal.signal(signal.SIGALRM,terminate);signal.alarm(limit)
                if ident in {'R-04','R-09','N-04','N-05','N-06','R-11','R-12','N-07'}:
                    snapshot=live.emergency_snapshot()
                if ident not in {'R-01','R-02','N-03','N-01'}:live.fresh_gateway()
                if ident=='R-05':live.normal()
                live.run(ident)
                if ident=='R-05':live.normal()
                live.services()
            except Exception as exc:
                signal.alarm(0)
                error=type(exc).__name__;exit_code=124 if error=='RoundTerminated' else 1
                # Only controlled error codes are recorded. Never arbitrary exception text.
                live.output_log.append('STEP_ERROR_CLASS='+error)
                import traceback
                live.output_log.append(traceback.format_exc())
                if error in {'CheckFailure','RecoveryFailure','JudgmentDeferred'}:
                    live.output_log.append('STEP_REASON='+str(exc))
                if snapshot:
                    try:
                        signal.alarm(540);live.emergency_restore(snapshot)
                    except Exception:
                        error='RecoveryFailure'
                        live.output_log.append('EMERGENCY_RESTORE_FAILED')
                    finally:signal.alarm(0)
            finally:
                signal.alarm(0);live.close()
                (item/'command.txt').write_text('\n'.join(live.command_log)+'\n')
                (item/'output.txt').write_text('\n'.join(live.output_log)+'\n')
                for p in item.glob('*.txt'):p.chmod(0o600)
            result={'id':ident,'started_at':step_start,'ended_at':now(),'exit_code':exit_code,
                    'passed':exit_code==0,'result':'passed' if exit_code==0 else ('held' if error=='JudgmentDeferred' else 'failed'),
                    'criteria':criteria,'failure_rule':rule,'error_class':error}
            save(item/'result.json',result);rows.append(result)
            continue_product_failure=c.get('customer_install_validation') and error not in {'RecoveryFailure','TimeoutExpired','RoundTerminated'}
            if exit_code and not continue_product_failure and (must_stop(rule,error) or rule in {'critical-auth','restore'}):
                # Conservative: restoration/normal-auth ambiguity ends this round.
                break
    except BaseException:
        interrupted=True
    finally:
        save(out/'round-summary.json',{'round':number,'started_at':started,'ended_at':now(),
             'interrupted':interrupted,'items':rows,
             'not_run':[x['id'] for x in describe(number) if x['id'] not in {y['id'] for y in rows}],
             'compute_shutdown':'timer_armed; export handshake pending'})
        (out/'round-summary.md').write_text('\n'.join(
            [f'# Round {number}',f'Start: {started}',f'End: {now()}']+
            [x['id']+' '+x['result'] for x in rows])+'\n')
    # Make an export copy on the server. Never scp original evidence.
    from export_evidence import export
    exported=out.with_name(out.name+'-export')
    try:
        tokens=[v for line in Path(c['token_env_file']).read_text().splitlines()
                if '=' in line and not line.startswith('#') for v in [line.split('=',1)[1]]]
        manifest=export(out,exported,tokens)
        save(exported.with_suffix('.ready.json'),manifest)
        # Laptop fetches archive, verifies hash, writes an ack without sensitive values.
        ack=exported.with_suffix('.ack')
        wait_until=min(deadline+480,time.monotonic()+300)
        while time.monotonic()<wait_until and not ack.exists():time.sleep(1)
    except Exception as exc:
        save(exported.with_suffix('.failed.json'),{'export_failed':True,'error_class':type(exc).__name__,'at':now()})
        raise
    finally:
        # Keep the original relative timer armed if immediate shutdown fails.
        run(['sudo','shutdown','-h','now'],timeout=15)
    return 0 if len(rows)==len(describe(number)) and all(x['passed'] for x in rows) else 1

def main():
    p=argparse.ArgumentParser();p.add_argument('--round',type=int,choices=(1,2),required=True)
    p.add_argument('--execute',action='store_true');p.add_argument('--dry-run',action='store_true')
    p.add_argument('--approval-file');p.add_argument('--config');p.add_argument('--evidence')
    p.add_argument('--timer-state')
    a=p.parse_args()
    if not a.execute:
        print(json.dumps({'round':a.round,'timer_minutes':ROUND_MINUTES[a.round],'steps':describe(a.round),
                         'external_commands_executed':0},ensure_ascii=False,indent=2));return
    if not all((a.approval_file,a.config,a.evidence)):p.error('approval/config/new evidence path required')
    raise SystemExit(execute(a.round,a.config,a.approval_file,a.evidence,a.timer_state))
if __name__=='__main__':main()
