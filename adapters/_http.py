"""
Shared Supabase REST helper for adapters/ modules that talk to the
VendorHub Supabase project directly (vendorhub.py's tables,
live_treaty_lookup.py's rate-limit ledger). Always uses the service_role
key -- never the public anon key, which cannot do any of what these
modules need.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Optional


def credentials(supabase_url: Optional[str], service_role_key: Optional[str]) -> tuple[str, str]:
    supabase_url = supabase_url or os.environ.get("SUPABASE_URL")
    service_role_key = service_role_key or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not service_role_key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must both be set "
            "(env vars, or a local .env — see .env.example). The service_role "
            "key is a real secret: never commit it or pass it on the command line."
        )
    return supabase_url.rstrip("/"), service_role_key


def supabase_request(
    method: str,
    path_and_query: str,
    supabase_url: Optional[str] = None,
    service_role_key: Optional[str] = None,
    body: Optional[Any] = None,
    prefer: Optional[str] = None,
) -> Any:
    base_url, service_role_key = credentials(supabase_url, service_role_key)
    headers = {
        "apikey": service_role_key,
        "Authorization": f"Bearer {service_role_key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{base_url}/rest/v1/{path_and_query}",
        method=method,
        headers=headers,
        data=data,
    )
    with urllib.request.urlopen(req) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None
