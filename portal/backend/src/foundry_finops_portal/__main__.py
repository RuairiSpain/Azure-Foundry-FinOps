"""``foundry-finops-portal``: run the portal with uvicorn."""

from __future__ import annotations

import argparse
import os

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser(description="Foundry FinOps portal")
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", os.environ.get("WEBSITES_PORT", "8000"))))
    args = ap.parse_args()
    uvicorn.run("foundry_finops_portal.app:create_app_from_env", factory=True, host=args.host, port=args.port,
                proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
