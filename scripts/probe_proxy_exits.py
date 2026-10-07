#!/usr/bin/env python3
"""Probe inline Mihomo nodes without mutating the running proxy instance."""

import argparse
import concurrent.futures
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.parse import urlparse

from platform_support import process_options, protect_private_path, write_private


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-config", type=Path, required=True)
    parser.add_argument("--mihomo-bin", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--country", type=str.upper, required=True)
    parser.add_argument("--interface", help="Physical egress interface; verify before use with TUN")
    parser.add_argument("--node", action="append", help="Exact inline node name; repeat to select several")
    parser.add_argument("--parallel", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=7)
    parser.add_argument("--geo-url", default="https://www.cloudflare.com/cdn-cgi/trace")
    parser.add_argument("--target-url", default="https://chatgpt.com/cdn-cgi/trace")
    args = parser.parse_args()
    if len(args.country) != 2 or not args.country.isascii() or not args.country.isalpha():
        parser.error("--country must be a two-letter country code")
    if not 1 <= args.parallel <= 8 or not 1 <= args.timeout <= 30:
        parser.error("--parallel must be 1..8 and --timeout must be 1..30 seconds")
    for url in (args.geo_url, args.target_url):
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            parser.error("trace URLs must be HTTPS URLs without embedded credentials")
    if not args.runtime_config.is_file() or not args.mihomo_bin.is_file():
        parser.error("runtime config and Mihomo binary must be existing files")
    if not shutil.which("curl.exe" if os.name == "nt" else "curl"):
        parser.error("curl is required")
    return args


def clean_env():
    return {key: value for key, value in os.environ.items()
            if key.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}}


def allocate_ports(count):
    # Reserve all ports together, then release immediately before Mihomo starts.
    sockets = []
    try:
        for _ in range(count):
            sock = socket.socket()
            sockets.append(sock)
            sock.bind(("127.0.0.1", 0))
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def trace(url, port, timeout, env):
    started = time.monotonic()
    command = ["curl.exe" if os.name == "nt" else "curl", "--disable", "--silent", "--show-error", "--fail",
               "--max-time", str(timeout), "--proxy", f"http://127.0.0.1:{port}",
               "--noproxy", "", url]
    try:
        result = subprocess.run(command, capture_output=True, text=True, env=env,
                                timeout=timeout + 2, **process_options())
    except subprocess.TimeoutExpired:
        return {"ok": False, "seconds": round(time.monotonic() - started, 3),
                "error": "request timeout"}
    elapsed = round(time.monotonic() - started, 3)
    if result.returncode:
        # Codes identify transport errors without reproducing URL/config details.
        return {"ok": False, "seconds": elapsed, "error": f"curl exit {result.returncode}"}
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    try:
        ip = str(ipaddress.ip_address(fields.get("ip", "")))
        country = fields.get("loc", "").upper()
        if len(country) != 2 or not country.isascii() or not country.isalpha():
            raise ValueError("invalid country")
    except ValueError:
        return {"ok": False, "seconds": elapsed, "error": "invalid trace response"}
    return {"ok": True, "seconds": elapsed, "ip": ip, "country": country}


def probe(node, port, args, env):
    geo = trace(args.geo_url, port, args.timeout, env)
    row = {"name": node["name"], "type": node.get("type"), "exit": geo,
           "target": {"tested": False, "ok": False}}
    if geo["ok"] and geo["country"] == args.country:
        target = trace(args.target_url, port, args.timeout, env)
        target["tested"] = True
        if target["ok"] and target["country"] != args.country:
            target["ok"] = False
            target["error"] = "target exit country differs from requested country"
        row["target"] = target
    return row


def write_report(path, report):
    write_private(path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def run(args):
    try:
        import yaml
    except ImportError:
        raise RuntimeError("PyYAML is required; use an existing runtime or a temporary PYTHONPATH") from None
    try:
        source = yaml.safe_load(args.runtime_config.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        raise RuntimeError("runtime YAML is invalid; parser details suppressed to protect credentials") from None
    if not isinstance(source, dict):
        raise RuntimeError("runtime config must be a YAML mapping")
    available = source.get("proxies", [])
    if not isinstance(available, list) or any(not isinstance(node, dict) for node in available):
        raise RuntimeError("runtime proxies must be an inline list of node mappings")
    nodes = [node for node in available if not args.node or node.get("name") in args.node]
    names = [node.get("name") for node in nodes]
    if not nodes or any(not isinstance(name, str) or not name for name in names):
        raise RuntimeError("no valid inline nodes selected")
    if len(set(names)) != len(names):
        raise RuntimeError("duplicate selected node names")
    if args.node and set(args.node) - set(names):
        raise RuntimeError("a requested node is absent from the inline runtime proxies")
    if any(node.get("dialer-proxy") for node in nodes):
        raise RuntimeError("selected nodes contain dialer-proxy dependencies; use a topology-aware probe")
    env = clean_env()
    ports = allocate_ports(len(nodes))
    with tempfile.TemporaryDirectory(prefix="codex-node-probe-") as temporary:
        directory = Path(temporary)
        protect_private_path(directory)
        config = {"mode": "rule", "log-level": "error", "ipv6": True,
                  "tun": {"enable": False}, "proxies": nodes,
                  "rules": ["MATCH,DIRECT"], "listeners": [
                      {"name": f"probe-{index}", "type": "http", "listen": "127.0.0.1",
                       "port": port, "proxy": node["name"]}
                      for index, (node, port) in enumerate(zip(nodes, ports))]}
        if args.interface:
            config["interface-name"] = args.interface
        # Do not import main-instance DNS group references or its DNS listener.
        dns = source.get("dns", {})
        resolvers = dns.get("proxy-server-nameserver", []) if isinstance(dns, dict) else []
        resolvers = [value for value in resolvers if isinstance(value, str) and "#" not in value]
        config["dns"] = {"enable": True, "ipv6": True,
                         "nameserver": resolvers or ["1.1.1.1", "8.8.8.8"]}
        config_path = directory / "config.yaml"
        config_path.touch(mode=0o600)
        protect_private_path(config_path)
        config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
        binary = str(args.mihomo_bin.resolve())
        log_path = directory / "core.log"
        log_path.touch(mode=0o600)
        protect_private_path(log_path)
        with log_path.open("w", encoding="utf-8") as log:
            valid = subprocess.run([binary, "-t", "-d", temporary, "-f", str(config_path)],
                                   stdout=log, stderr=log, env=env, timeout=20, **process_options())
            if valid.returncode:
                raise RuntimeError("isolated Mihomo config validation failed; no main config was changed")
            process = subprocess.Popen([binary, "-d", temporary, "-f", str(config_path)],
                                       stdout=log, stderr=log, env=env, **process_options())
            try:
                deadline = time.monotonic() + 10
                waiting = set(ports)
                while waiting and time.monotonic() < deadline and process.poll() is None:
                    for port in list(waiting):
                        try:
                            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                                waiting.remove(port)
                        except OSError:
                            pass
                    if waiting:
                        time.sleep(0.1)
                if waiting or process.poll() is not None:
                    raise RuntimeError("isolated proxy listeners did not start")
                with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as executor:
                    futures = [executor.submit(probe, node, port, args, env)
                               for node, port in zip(nodes, ports)]
                    rows = [future.result() for future in futures]
                write_report(args.report, {"requested_country": args.country, "nodes": rows})
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
    healthy = sum(row["target"]["ok"] for row in rows)
    print(f"Probed {len(rows)} nodes; {healthy} requested-country nodes passed target trace.")
    print(f"Report: {args.report.expanduser().resolve()}")


if __name__ == "__main__":
    try:
        run(parse_args())
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        raise SystemExit(f"Probe failed: {error}") from None
    except KeyboardInterrupt:
        raise SystemExit("Probe interrupted; isolated process and temporary config cleaned up.") from None
