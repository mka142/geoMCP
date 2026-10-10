"""`geomcp serve --stdio | --http`, `geomcp check`, `geomcp setup`."""

import argparse
import getpass
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from dotenv import load_dotenv

from geomcp_core.models import GeomcpError

CREDENTIALS = [
    ("GEOMCP_CDSE_CLIENT_ID", "Copernicus Data Space (Sentinel Hub) OAuth client id", False),
    ("GEOMCP_CDSE_CLIENT_SECRET", "Copernicus Data Space (Sentinel Hub) OAuth client secret", True),
    ("GEOMCP_CDS_KEY", "Climate Data Store personal access token", True),
]


def _check_cdse() -> str:
    from geomcp_core.adapters import cdse

    if not cdse.configured():
        raise GeomcpError("source_unconfigured", "not configured", cdse.unconfigured_hint())
    today = datetime.now(UTC).date()
    since = today - timedelta(days=20)
    found = cdse.catalog_search("sentinel-2-l2a", (17.0, 51.09, 17.07, 51.13), since, today)
    return f"token OK, catalogue OK ({len(found)} Sentinel-2 scenes over Wrocław in the last 20 days)"


def _check_cds() -> str:
    from geomcp_core.adapters.cds import CDS_VARIABLES, Era5LandAdapter

    day = datetime.now(UTC).date() - timedelta(days=30)
    raw = Era5LandAdapter()._job(  # pyright: ignore[reportPrivateUsage]
        {
            "variable": list(CDS_VARIABLES)[:1],
            "location": {"latitude": 51.1, "longitude": 17.0},
            "date": [f"{day}/{day}"],
            "data_format": "csv",
        }
    )
    return f"token and licence OK (one-day job returned {len(raw)} bytes)"


def check() -> int:
    """One live call per credentialed source; prints OK or the exact problem."""
    failures = 0
    checks: list[tuple[str, Callable[[], str]]] = [
        ("Copernicus Data Space (Sentinel Hub)", _check_cdse),
        ("Climate Data Store (ERA5-Land)", _check_cds),
    ]
    for name, fn in checks:
        t0 = time.monotonic()
        try:
            print(f"OK    {name}: {fn()} [{time.monotonic() - t0:.1f}s]")
        except GeomcpError as e:
            failures += 1
            print(f"FAIL  {name}: {e.message}" + (f"\n      Fix: {e.hint}" if e.hint else ""))
        except Exception as e:  # network trouble and the like: report it, do not crash the check
            failures += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    return 1 if failures else 0


def _write_env(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in values:
            out.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in values.items() if k not in seen]
    path.write_text("\n".join(out) + "\n")
    path.chmod(0o600)


def setup(env_path: Path) -> int:
    """Ask for each credential (hidden input), write them to .env, then run the live check."""
    print(f"Credentials are stored in {env_path.resolve()} (mode 600). Press Enter to keep a current value.\n")
    updates: dict[str, str] = {}
    for var, label, secret in CREDENTIALS:
        state = "set" if os.environ.get(var) else "not set"
        prompt = f"{label} [{var}, {state}]: "
        value = (getpass.getpass(prompt) if secret else input(prompt)).strip()
        if value:
            updates[var] = value
            os.environ[var] = value
    if updates:
        _write_env(env_path, updates)
        print(f"\nSaved {', '.join(updates)}.")
    print("\nChecking the sources:\n")
    return check()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="geomcp")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the MCP server")
    mode = serve.add_mutually_exclusive_group()
    mode.add_argument("--stdio", action="store_true", help="local mode over stdin/stdout (default)")
    mode.add_argument(
        "--http", action="store_true", help="remote mode over Streamable HTTP at /mcp: API keys, plus Google sign-in"
    )
    serve.add_argument("--host", help="HTTP bind address (default GEOMCP_HOST or 127.0.0.1)")
    serve.add_argument("--port", type=int, help="HTTP port (default GEOMCP_PORT or 8000)")
    sub.add_parser("check", help="test the configured data-source credentials with one live call each")
    setup_p = sub.add_parser("setup", help="enter data-source credentials into .env, then check them")
    setup_p.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args(argv)

    load_dotenv(override=False)  # credentials for the core adapters; real environment variables win
    if args.command == "check":
        return check()
    if args.command == "setup":
        return setup(args.env_file)

    from geomcp_server import auth
    from geomcp_server.app import build_server
    from geomcp_server.log import configure
    from geomcp_server.settings import get_settings

    settings = get_settings()
    configure(settings.log_level)
    log = structlog.get_logger()

    if not args.http:
        log.info("starting", transport="stdio")
        build_server().run(transport="stdio", show_banner=False)
        return 0

    url = settings.database_url
    host, port = args.host or settings.host, args.port or settings.port
    public = settings.public_url or f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"
    if settings.google_client_id:
        import fastmcp

        fastmcp.settings.home = settings.data_dir / "fastmcp"  # OAuth client registrations, kept with the data
    provider = auth.http_auth(
        url, public, settings.google_client_id, settings.google_client_secret, settings.allowed_emails
    )
    server = build_server(auth=provider, public_url=public)  # also applies migrations
    if not any(k.revoked_at is None for k in auth.list_keys(url)):
        log.warning("no_active_api_keys", hint="create one with: geomcp-admin keys create --name <who>")
    log.info(
        "starting",
        transport="http",
        host=host,
        port=port,
        path="/mcp",
        public_url=public,
        google_sign_in=bool(settings.google_client_id),
    )
    server.run(
        transport="http",
        host=host,
        port=port,
        path="/mcp",
        show_banner=False,
        # FastMCP ignores allowed_hosts unless its Host guard is on; "auto" guards only loopback binds
        host_origin_protection=True if settings.allowed_hosts else "auto",
        allowed_hosts=settings.allowed_hosts,
        uvicorn_config={"log_config": None},  # keep our JSON logging
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
