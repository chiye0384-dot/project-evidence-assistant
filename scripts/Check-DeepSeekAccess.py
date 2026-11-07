"""Opt-in official model-list metadata check. Never dispatches a completion."""
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import re
import sys
from time import monotonic
import warnings

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from project_evidence.deepseek_network import checked_proxy_port, client_options, public_route
ENDPOINT = "https://api.deepseek.com/models"
MODEL = "deepseek-flash"


def check(key, *, transport=None, proxy_port=0):
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "endpoint": ENDPOINT,
              "method": "GET", "model": MODEL, "model_requests_sent": 0,
              "configured_model_listed": False, "status": "local_key_format_invalid"}
    try:
        checked_proxy_port(proxy_port)
    except ValueError:
        result['status'] = 'local_proxy_invalid'
        return result
    route = public_route(proxy_port)
    if route is not None:
        result['network_route'] = route
    if not isinstance(key, str) or re.fullmatch(r"[A-Za-z0-9_-]{8,200}", key) is None:
        return result
    deadline = monotonic() + 8.0
    try:
        with httpx.Client(transport=transport, trust_env=False, follow_redirects=False,
                          timeout=httpx.Timeout(8.0, connect=5.0), **client_options(proxy_port)) as client:
            with client.stream("GET", ENDPOINT, headers={"Authorization": "Bearer " + key}) as response:
                result["http_status"] = response.status_code
                if response.status_code != 200:
                    result["status"] = {401: "authentication_rejected", 402: "account_access_rejected",
                                        403: "access_forbidden", 429: "rate_limited",
                                        500: "provider_error", 503: "provider_busy"}.get(response.status_code, "http_rejected")
                    return result
                raw = bytearray()
                for chunk in response.iter_bytes():
                    if monotonic() >= deadline or len(raw) + len(chunk) > 65536:
                        result["status"] = "metadata_limit"; return result
                    raw.extend(chunk)
        data = json.loads(raw)
        if (not isinstance(data, dict) or data.get("object") != "list"
                or not isinstance(data.get("data"), list) or len(data["data"]) > 100):
            result["status"] = "invalid_metadata"; return result
        listed = any(isinstance(model, dict) and model.get("id") == MODEL for model in data["data"])
        result["configured_model_listed"] = listed
        result["status"] = "authorized_model_list" if listed else "configured_model_not_listed"
    except httpx.TimeoutException as error:
        result["status"] = "transport_timeout"
        result['transport_kind'] = 'connect_timeout' if isinstance(error, httpx.ConnectTimeout) else 'other_timeout'
    except httpx.HTTPError as error:
        result["status"] = "transport_error"
        result['transport_kind'] = 'connect_error' if isinstance(error, httpx.ConnectError) else 'other_http_error'
    except Exception:
        result["status"] = "invalid_metadata"
    return result


def main(argv=()):
    arguments = list(argv)
    prompt = arguments[:1] == ['--prompt-key']
    remaining = arguments[1:] if prompt else arguments
    proxy_port = 0
    if remaining:
        if (len(remaining) != 2 or remaining[0] != '--proxy-port'
                or re.fullmatch(r'[0-9]{1,5}', remaining[1]) is None or int(remaining[1]) > 65535):
            print(json.dumps({"status": "local_arguments_invalid", "model_requests_sent": 0})); return 2
        proxy_port = int(remaining[1])
    if prompt:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                key = getpass.getpass("DeepSeek API key (hidden): ")
        except (EOFError, KeyboardInterrupt, OSError, getpass.GetPassWarning):
            print(json.dumps({"status": "local_key_input_unavailable", "model_requests_sent": 0})); return 2
    else:
        key = os.environ.get("DEEPSEEK_API_KEY", "")
    result = check(key, proxy_port=proxy_port) if proxy_port else check(key)
    del key
    directory = ROOT / "evidence"
    if directory.is_symlink() or directory.resolve().parent != ROOT.resolve():
        print(json.dumps({"status": "local_evidence_path_not_permitted", "model_requests_sent": 0})); return 2
    directory.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = json.dumps(result, ensure_ascii=False, indent=2)
    (directory / ("deepseek-access-" + stamp + ".json")).write_text(output, encoding="utf-8")
    latest = directory / "deepseek-access-latest.json"
    if latest.is_symlink():
        print(json.dumps({"status": "local_evidence_path_not_permitted", "model_requests_sent": 0})); return 2
    latest.write_text(output, encoding="utf-8")
    print(output)
    return 0 if result["status"] == "authorized_model_list" else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
