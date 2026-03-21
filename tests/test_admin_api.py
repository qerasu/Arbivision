import argparse
import json
import os
import sys
from pathlib import Path
from urllib import error, parse, request

ENV_FILE_PATH = Path.home() / ".config" / "arbivision" / ".env"


def _load_env_file(path):
    if not path.exists():
        return

    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            if "=" not in line:
                continue

            key, val = line.split("=", 1)
            key = key.strip().removeprefix("export ").strip()
            val = val.strip()
            if not key:
                continue

            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]

            os.environ[key] = val


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("APP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("APP_PORT", "8000")))
    parser.add_argument("--scheme", default="http")
    parser.add_argument("--market-id", type=int, default=None)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--status", default="auto_approved")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def _base_url(args):
    return f"{args.scheme}://{args.host}:{args.port}"


def _request_json(url, headers=None):
    req = request.Request(url, headers=headers or {})
    with request.urlopen(req, timeout=10) as response:
        payload = response.read().decode("utf-8")
        return response.status, json.loads(payload)


def _print_json(title, payload):
    print(f"\n=== {title} ===")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _print_summary(name, payload):
    if name == "health":
        print(f"health: {payload.get('status')}")
        return

    if name == "status":
        markets = payload.get("market_counts", {})
        pairs = payload.get("pair_counts", {})
        opportunities = payload.get("opportunity_counts", {})
        alerts = payload.get("alert_counts", {})
        print(
            "status:"
            f" markets={markets.get('total', 0)}"
            f" pairs={pairs.get('total', 0)}"
            f" approved={pairs.get('approved', 0)}"
            f" opportunities={opportunities.get('total', 0)}"
            f" queued_alerts={alerts.get('queued', 0)}"
        )
        return

    if name == "admin pairs":
        print(f"admin pairs: {len(payload.get('data', []))}")
        return

    if name == "matcher debug":
        candidates = payload.get("data", [])
        if not candidates:
            print("matcher debug: 0 candidates")
            return

        top_candidate = candidates[0]
        print(
            "matcher debug:"
            f" candidates={len(candidates)}"
            f" top_market_id={top_candidate.get('market_id')}"
            f" matched={top_candidate.get('matched')}"
            f" reject_reason={top_candidate.get('reject_reason')}"
        )


def _admin_headers():
    token = os.environ.get("ADMIN_API_TOKEN", "").strip()
    if not token:
        print("ADMIN_API_TOKEN is empty")
        raise SystemExit(1)
    return {"X-Admin-Token": token}


def _run_check(name, url, headers=None, verbose=False):
    try:
        status_code, payload = _request_json(url, headers=headers)
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"failed: HTTP {exc.code}")
        print(body)
        raise SystemExit(1)
    except error.URLError as exc:
        print(f"failed: {exc.reason}")
        raise SystemExit(1)

    print(f"{name}: HTTP {status_code}")
    if verbose:
        _print_json(name, payload)
    else:
        _print_summary(name, payload)
    return payload


def main():
    _load_env_file(ENV_FILE_PATH)
    args = _parse_args()
    base_url = _base_url(args)
    admin_headers = _admin_headers()

    _run_check("health", f"{base_url}/api/health", verbose=args.verbose)
    _run_check("status", f"{base_url}/api/status", verbose=args.verbose)

    pairs_query = parse.urlencode({"status": args.status})
    pairs_payload = _run_check(
        "admin pairs",
        f"{base_url}/api/admin/pairs?{pairs_query}",
        headers=admin_headers,
        verbose=args.verbose,
    )

    market_id = args.market_id
    if market_id is None and pairs_payload.get("data"):
        market_id = pairs_payload["data"][0]["market_id_a"]
        print(f"\nusing first pair market_id_a for matcher debug: {market_id}")

    if market_id is None:
        print("\nmatcher debug skipped: no --market-id and no pairs found")
        return

    debug_query = parse.urlencode({"market_id": market_id, "limit": args.limit})
    _run_check(
        "matcher debug",
        f"{base_url}/api/admin/matcher/debug?{debug_query}",
        headers=admin_headers,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\ninterrupted by user")
        sys.exit(130)