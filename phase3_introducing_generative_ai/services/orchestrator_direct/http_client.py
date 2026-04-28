"""
orchestrator/http_client.py
───────────────────────────
Thin HTTP helpers that log every outbound request and inbound response.
All orchestrator modules import http_post_json / http_get from here.
"""

import time
from typing import Any, Dict, Optional

import requests

import log_utils as L


def http_post_json(
    name:    str,
    url:     str,
    payload: dict,
    timeout: int = 60,
    headers: Optional[Dict[str, str]] = None,
) -> requests.Response:
    h = headers or {}
    L.log_http_out(name, "POST", url, payload, h)
    t0   = time.monotonic()
    resp = requests.post(url, json=payload, headers=h, timeout=timeout)
    L.log_http_in(name, resp, time.monotonic() - t0)
    resp.raise_for_status()
    return resp


def http_get(
    name:    str,
    url:     str,
    timeout: int = 10,
    headers: Optional[Dict[str, str]] = None,
) -> requests.Response:
    h = headers or {}
    L.log_http_out(name, "GET", url, headers=h)
    t0   = time.monotonic()
    resp = requests.get(url, headers=h, timeout=timeout)
    L.log_http_in(name, resp, time.monotonic() - t0)
    resp.raise_for_status()
    return resp
