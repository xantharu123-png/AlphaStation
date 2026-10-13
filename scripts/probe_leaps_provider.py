"""Offline-first capability entry point; credentials are never auto-discovered."""
import argparse
import json


def main(argv=None, *, provider=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--execute', action='store_true')
    parser.add_argument('--max-requests', type=int)
    parser.add_argument('--underlying')
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({'mode':'dry_run','requests':0,'capabilities':'unverified','display_rights':'unverified'}))
        return 0
    if args.max_requests != 3 or args.underlying != 'SOFI':
        parser.error('execute requires --max-requests 3 --underlying SOFI')
    if provider is None:
        print(json.dumps({'mode':'blocked','requests':0,'reason':'configured_provider_required'}))
        return 2
    from modules.leaps.provider import probe_capabilities
    result = probe_capabilities(provider, max_requests=3)
    print(json.dumps({'mode':'execute', 'reference':result.reference,
                      'option_quotes':result.option_quotes, 'stock_quotes':result.stock_quotes,
                      'display_rights':'unverified'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
