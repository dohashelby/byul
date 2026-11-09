"""Local entry point using the same verified-current boundary and sync engine."""
import json
from pathlib import Path

from ..operations.agent import Agent, Config, ID, Rejected, locked
from .sync import SyncFailure, sync


def main(args):
    report = {"state": "failed", "code": "VALIDATION_FAILED", "dry_run": not args.execute}
    try:
        agent = Agent(None, Config("local", args.bundle_root, args.context, args.namespace,
                                  kubeconfig=args.kubeconfig, token_env_file=args.token_env_file,
                                  status_url=args.status_url, execute=args.execute))
        if args.existing and not ID.fullmatch(args.existing):
            raise ValueError()
        with locked(args.lock_file):
            current, intent = agent.current()
            source = Path(args.bundle_dir) if args.bundle_dir else Path(args.bundle_root) / args.existing
            bundle = agent.verified_bundle(source)
            agent.check_target(bundle)
            def guard():
                observed, _ = agent.current()
                live = agent.verified_bundle(Path(args.bundle_root) / observed["bundle_id"])
                if observed["bundle_id"] != bundle.bundle_id or live.sha256 != bundle.sha256:
                    raise SyncFailure("STATE_CHANGED")
            guard()
            def verify(inference):
                agent.sync_gateway(inference)
                guard()
            report = sync(intent, context=args.context, namespace=args.namespace, run=agent.run,
                          read_mode=agent.sync_mode, verify_gateway=verify,
                          components=args.components, cilium_apply=args.cilium_apply,
                          execute=args.execute, before_apply=guard)
    except SyncFailure as error:
        report["code"] = error.code
    except Rejected as error:
        report["code"] = str(error)
    except Exception:
        pass
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["state"] in {"checked", "succeeded"} else 1
