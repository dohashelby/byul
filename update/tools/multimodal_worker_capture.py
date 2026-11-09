"""Capture a real vLLM MM CPU worker with locally generated random micro weights."""
import argparse
import json

from guard.collectors import multimodal_worker_cpu as producer
from tools.consumption_links_capture import capture


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('context', 'namespace', 'pod', 'container', 'out'):
        parser.add_argument('--' + option, required=True)
    args = parser.parse_args(argv)
    result = capture(context=args.context, namespace=args.namespace, pod=args.pod, container=args.container,
                     model_dir='/models', output=args.out, producer_module=producer)
    print(json.dumps({key: result[key] for key in ('scenario_complete', 'selected_request_chains_complete',
                                                  'selected_multimodal_links_complete', 'diagnostic_codes')}))
    return 0 if result['source_ended'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
