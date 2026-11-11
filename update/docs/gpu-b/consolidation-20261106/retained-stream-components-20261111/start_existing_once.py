"""검토된 loader와 기존 driver.start를 정확1회 연결. source/model 실행 없음."""
from pathlib import Path
import argparse,datetime,hashlib,json,os,sys,types

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()

def start_once(driver, packet_sha, local_identity):
    # put의 x 생성이 실패하면 start까지 도달하지 않는다.
    driver.put('start-wrapper-once-intent.json',{'at':now(),'packet_sha256':packet_sha,
        'local_identity':local_identity,'scope':'existing selected instance normal start once; no source/model execution'})
    original=None
    try:
        driver.start()
    except BaseException as error:
        original=error
    terminal={'at':now(),'packet_sha256':packet_sha,'local_identity':local_identity,
        'returncode':0 if original is None else 1,
        'original_error':None if original is None else {'type':type(original).__name__,'message':str(original)},
        'resource_cleanup_complete_claimed':False,'remaining_resource_stop_verification_required':True}
    try:
        driver.put('start-wrapper-local-terminal.json',terminal)
    except BaseException as secondary:
        if original is None:raise
        original.add_note('Start local terminal preservation failed: '+repr(secondary))
    if original is not None:raise original.with_traceback(original.__traceback__)
    return terminal

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packet-sha256',required=True)
    args=parser.parse_args()
    root=Path(__file__).parent
    packet_raw=(root/'operator-freeze.json').read_bytes()
    assert hashlib.sha256(packet_raw).hexdigest()==args.packet_sha256
    packet=json.loads(packet_raw)
    assert packet['not_ready'] is False and packet['execution_ready'] is True
    assert packet['private_path']==str(root)
    pin=packet['start_entry']
    assert pin['filename']==Path(__file__).name
    actual=Path(__file__).read_bytes()
    assert len(actual)==pin['bytes'] and hashlib.sha256(actual).hexdigest()==pin['sha256']
    invoker=root/'invoke_recovery_once.py';raw=invoker.read_bytes()
    pin=packet['local_files'][invoker.name]
    assert len(raw)==pin['bytes'] and hashlib.sha256(raw).hexdigest()==pin['sha256']
    module=types.ModuleType('s2_verified_recovery_start_entry');module.__file__=str(invoker)
    sys.modules[module.__name__]=module
    exec(compile(raw,str(invoker),'exec'),module.__dict__)
    verified_raw,verified_packet=module.load_packet(args.packet_sha256)
    loader=module.loader_from_private(verified_packet)
    fields=(Path('/proc/self/stat').read_text().rsplit(')',1)[1].split())
    local={'PID':os.getpid(),'PGID':os.getpgid(0),'start_ticks':int(fields[19])}
    print(json.dumps({'start_entry_actual_local_identity':local,'packet_sha256':args.packet_sha256}),flush=True)
    with loader.load_private(root,verified_raw,args.packet_sha256) as runtime:
        result=start_once(runtime.driver,args.packet_sha256,local)
    print(json.dumps(result),flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
