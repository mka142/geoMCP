"""`geomcp-admin keys create|list|revoke`: manage API keys for HTTP mode."""

import argparse
import sys

from geomcp_server import auth, metrics
from geomcp_server.db import migrate
from geomcp_server.settings import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="geomcp-admin")
    sub = parser.add_subparsers(dest="group", required=True)
    keys = sub.add_parser("keys", help="manage API keys").add_subparsers(dest="action", required=True)
    create = keys.add_parser("create", help="create a key and print it once")
    create.add_argument("--name", required=True, help="who or what uses the key, e.g. 'ceo-laptop'")
    keys.add_parser("list", help="list keys (never shows secrets)")
    revoke = keys.add_parser("revoke", help="revoke a key by id")
    revoke.add_argument("key_id")
    stats = sub.add_parser("stats", help="tool calls per person and tool, with errors, speed and quota spent")
    stats.add_argument("--days", type=int, default=30)
    args = parser.parse_args(argv)

    url = get_settings().database_url
    migrate(url)
    if args.group == "stats":
        print(metrics.stats_table(url, args.days))
        return 0
    if args.action == "create":
        print(auth.create_key(url, args.name))
        print("Store this key now; it cannot be shown again.", file=sys.stderr)
    elif args.action == "list":
        for k in auth.list_keys(url):
            state = f"revoked {k.revoked_at:%Y-%m-%d}" if k.revoked_at else "active"
            print(f"{k.id}  {k.name:<30}  created {k.created_at:%Y-%m-%d}  {state}")
    elif not auth.revoke_key(url, args.key_id):
        print(f"No active key {args.key_id!r}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
