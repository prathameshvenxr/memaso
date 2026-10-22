"""python -m memory_core <migrate|create-tenant|create-key|serve|mcp>   (config: $MEMASO_CONFIG)"""
from __future__ import annotations

import argparse
import asyncio
import os

from .config import load_config


def main() -> None:
    ap = argparse.ArgumentParser(prog="memory_core")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    t = sub.add_parser("create-tenant")
    t.add_argument("tenant")
    t.add_argument("--budget", type=int, default=None, help="monthly token budget")
    k = sub.add_parser("create-key")
    k.add_argument("tenant")
    k.add_argument("--subject", default=None, help="bind the key to one subject")
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8080)
    sub.add_parser("mcp")
    a = ap.parse_args()
    cfg = load_config(os.environ.get("MEMASO_CONFIG"))
    admin = cfg.store.get("admin_dsn") or cfg.store["dsn"]

    from . import migrate as m

    if a.cmd == "migrate":
        asyncio.run(m.migrate(admin))
        print("migrated")
    elif a.cmd == "create-tenant":
        asyncio.run(m.create_tenant(admin, a.tenant, a.budget))
        print(f"tenant {a.tenant} ready")
    elif a.cmd == "create-key":
        print(asyncio.run(m.create_api_key(admin, a.tenant, a.subject)))  # shown once; only its hash is stored
    elif a.cmd == "serve":
        import uvicorn

        from .api.app import create_app
        from .bootstrap import build

        uvicorn.run(create_app(build(cfg)), host=a.host, port=a.port)
    else:
        from .mcp_server import main as mcp_main

        asyncio.run(mcp_main())


if __name__ == "__main__":
    main()
