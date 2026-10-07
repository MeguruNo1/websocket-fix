#!/usr/bin/env python3
"""Run isolated Codex doctor network checks; never edit config or restart the app."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.parse import urlsplit

from platform_support import process_options, write_private

PROXY_KEYS = {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}


def child_environment(proxy=None):
    env = {key: value for key, value in os.environ.items() if key.lower() not in PROXY_KEYS}
    if proxy:
        settings = {"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy, "ALL_PROXY": proxy,
                    "NO_PROXY": "localhost,127.0.0.1,::1"}
        env.update(settings)
        if os.name != "nt":
            env.update({key.lower(): value for key, value in settings.items()})
    return env


def network_checks(report):
    checks = report.get("checks", {})
    if isinstance(checks, list):
        checks = {row["id"]: row for row in checks if isinstance(row, dict) and "id" in row}
    if not isinstance(checks, dict):
        raise ValueError("Unsupported doctor checks schema")
    return {key: value for key, value in checks.items()
            if key.startswith("network.") and isinstance(value, dict)}


def handshake_passed(checks):
    row = checks.get("network.websocket_reachability", {})
    return (row.get("status") == "ok" and
            row.get("details", {}).get("handshake result") == "HTTP 101 Switching Protocols")


def run_doctor(binary, mode, proxy, timeout):
    command = [str(binary)]
    if mode == "system":
        command += ["--enable", "respect_system_proxy"]
    command += ["doctor", "--json"]
    started = time.monotonic()
    try:
        result = subprocess.run(command, env=child_environment(proxy if mode == "explicit" else None),
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=timeout, **process_options())
        report = json.loads(result.stdout)
        checks = network_checks(report)
    except subprocess.TimeoutExpired:
        return {"error": "doctor timeout", "websocket_101": False}
    except (ValueError, AttributeError):
        # Do not echo stderr/raw stdout; old clients may fail while loading private config.
        return {"error": "doctor did not return supported JSON; inspect version/help/config privately",
                "websocket_101": False}
    provider = checks.get("network.provider_reachability", {})
    return {"elapsed_seconds": round(time.monotonic() - started, 3),
            "doctor_exit_code": result.returncode, "codex_version": report.get("codexVersion"),
            "websocket_101": handshake_passed(checks),
            "required_http_reachable": provider.get("status") in ("ok", "warning"),
            "network_checks": checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-bin", required=True, type=Path, help="Actual running backend binary")
    parser.add_argument("--mode", choices=("clean", "system", "explicit"), default="clean",
                        help="clean: saved config without proxy env; system: temporary feature; explicit: proxy env")
    parser.add_argument("--proxy", help="Confirmed HTTP proxy URL, required for explicit mode")
    parser.add_argument("--report", required=True, type=Path, help="Private output outside the skill repository")
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    if not args.codex_bin.is_file() or not 1 <= args.rounds <= 5 or not 5 <= args.timeout <= 120:
        parser.error("Existing binary, rounds 1..5 and timeout 5..120 seconds required")
    if args.mode == "explicit":
        try:
            parsed = urlsplit(args.proxy or "")
            valid = (parsed.scheme == "http" and parsed.hostname and parsed.port
                     and not parsed.username and not parsed.password
                     and parsed.path in ("", "/") and not parsed.query and not parsed.fragment)
        except ValueError:
            valid = False
        if not valid:
            parser.error("Explicit mode requires http://host:port without embedded credentials")
    elif args.proxy:
        parser.error("--proxy is only used in explicit mode")
    if args.mode == "system":
        supported = subprocess.run([str(args.codex_bin), "features", "list"],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   env=child_environment(), timeout=15, **process_options())
        if supported.returncode or not any(line.split()[:1] == ["respect_system_proxy"]
                                           for line in supported.stdout.splitlines()):
            parser.error("Cannot confirm respect_system_proxy support; inspect current binary/config")
    rows = []
    for index in range(args.rounds):
        row = run_doctor(args.codex_bin, args.mode, args.proxy, args.timeout)
        rows.append(row)
        write_private(args.report, json.dumps({"generated_at_utc": datetime.now(timezone.utc).isoformat(),
                                              "mode": args.mode, "rounds": rows}, ensure_ascii=False, indent=2) + "\n")
        print(f"Round {index + 1}: WebSocket 101={row['websocket_101']}; "
              f"required HTTP={row.get('required_http_reachable', False)}", flush=True)
    return 0 if all(row["websocket_101"] and row.get("required_http_reachable") for row in rows) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        raise SystemExit("Diagnostic process/file operation failed; inspect local permissions and binary") from None
