"""Prepare and inspect passive input-source candidates without starting engines."""
import argparse
import json
from pathlib import Path

from guard.collectors import native_input


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    prepare=commands.add_parser('prepare');prepare.add_argument('--output',type=Path,required=True)
    prepare.add_argument('--image',required=True)
    verify=commands.add_parser('verify');verify.add_argument('--prepared',type=Path,required=True)
    verify.add_argument('--capture',type=Path,required=True)
    verify.add_argument('--expected-uid',type=int)
    qualification=commands.add_parser('qualification')
    for name in('engine','version','image'):qualification.add_argument('--'+name,required=True)
    args=parser.parse_args()
    if args.command=='prepare':
        result=native_input.prepare(args.output,image=args.image,repository=Path(__file__).resolve().parents[2])
    elif args.command=='verify':
        result=native_input.verify_capture(args.prepared,args.capture,expected_uid=args.expected_uid)
        result.pop('rows');result.pop('requests')
    else:
        from guard.collectors.native_input_qualification import status
        result=status(args.engine,args.version,args.image)
    print(json.dumps(result,ensure_ascii=True))


if __name__=='__main__':main()
