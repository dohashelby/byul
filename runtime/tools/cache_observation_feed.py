"""Root host service: publish cache metadata and actual Pod binding snapshots."""
import argparse
import math
import time

from serving_runtime.mvp.cache_observation import CacheObservationProducer, atomic_snapshot


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('settings', 'settings-sha256', 'kubeconfig', 'output'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--reader-gid', type=int, required=True)
    parser.add_argument('--kubectl', default='/usr/local/bin/kubectl')
    parser.add_argument('--interval', type=float, default=5)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args(argv)
    if not math.isfinite(args.interval) or not 1 <= args.interval <= 300:
        raise ValueError('CACHE_OBSERVER_INTERVAL_INVALID')
    producer = CacheObservationProducer(args.settings, settings_sha256=args.settings_sha256,
        reader_gid=args.reader_gid, kubeconfig=args.kubeconfig, kubectl=args.kubectl)
    while True:
        atomic_snapshot(args.output, producer.collect(), args.reader_gid)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == '__main__':
    raise SystemExit(main())
