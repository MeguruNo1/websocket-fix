import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import diagnose_network as diagnostic
import platform_support
import probe_proxy_exits as nodes


class NetworkToolsTests(unittest.TestCase):
    def fixture(self):
        return {"network.websocket_reachability": {
                    "id": "network.websocket_reachability", "status": "ok",
                    "details": {"handshake result": "HTTP 101 Switching Protocols"}},
                "network.provider_reachability": {
                    "id": "network.provider_reachability", "status": "warning",
                    "details": {"desktop assets CDN": "optional timeout"}},
                "auth.credentials": {"id": "auth.credentials", "secret": "never-store"}}

    def test_doctor_schemas_and_http_do_not_substitute_for_101(self):
        for checks in (self.fixture(), list(self.fixture().values())):
            selected = diagnostic.network_checks({"checks": checks})
            self.assertTrue(diagnostic.handshake_passed(selected))
            self.assertNotIn("auth.credentials", selected)
            selected["network.websocket_reachability"]["details"]["handshake result"] = "HTTP 405"
            self.assertFalse(diagnostic.handshake_passed(selected))
        self.assertFalse(diagnostic.handshake_passed({}))

    def test_environment_isolation_does_not_mutate_parent(self):
        with patch.dict(os.environ, {"HTTP_PROXY": "http://old:123", "ALL_PROXY": "socks5://old:456",
                                     "KEEP_ME": "unchanged"}, clear=True):
            parent = dict(os.environ)
            clean = diagnostic.child_environment()
            explicit = diagnostic.child_environment("http://127.0.0.1:7897")
            self.assertEqual(dict(os.environ), parent)
            self.assertFalse(any(k.lower() in diagnostic.PROXY_KEYS for k in clean))
            self.assertEqual(explicit["KEEP_ME"], "unchanged")
            self.assertEqual(explicit["HTTPS_PROXY"], "http://127.0.0.1:7897")
            self.assertIn("::1", explicit["NO_PROXY"])

    def test_network_pass_ignores_unrelated_exit_but_requires_http(self):
        fixture = {"checks": self.fixture(), "codexVersion": "test"}
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 1, json.dumps(fixture), "private-config")) as run:
            row = diagnostic.run_doctor(Path("codex.exe"), "system", None, 10)
            self.assertTrue(row["websocket_101"])
            self.assertTrue(row["required_http_reachable"])
            self.assertEqual(run.call_args.args[0],
                             ["codex.exe", "--enable", "respect_system_proxy", "doctor", "--json"])
            self.assertNotIn("never-store", json.dumps(row))
        fixture["checks"]["network.provider_reachability"]["status"] = "fail"
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(fixture), "")):
            self.assertFalse(diagnostic.run_doctor(Path("codex.exe"), "clean", None, 10)["required_http_reachable"])

    def test_timeout_and_bad_json_do_not_expose_config(self):
        for outcome in (subprocess.TimeoutExpired("codex", 10),
                        subprocess.CompletedProcess([], 1, "private-config-token", "private-config-token")):
            options = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
            with patch.object(subprocess, "run", **options):
                row = diagnostic.run_doctor(Path("codex.exe"), "clean", None, 10)
                self.assertFalse(row["websocket_101"])
                self.assertNotIn("private-config-token", json.dumps(row))

    def test_private_file_acl_or_posix_mode_and_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            for contents in ("first", "second"):
                platform_support.write_private(path, contents)
                self.assertEqual(path.read_text(), contents)
            self.assertEqual([p.name for p in Path(directory).iterdir()], ["report.json"])
            if os.name == "nt":
                acl = subprocess.run(["icacls.exe", str(path)], capture_output=True, text=True,
                                     check=True, **platform_support.process_options()).stdout
                self.assertNotIn("(I)", acl)
            else:
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_node_exit_country_mismatch_is_not_healthy(self):
        from types import SimpleNamespace
        args = SimpleNamespace(geo_url="https://geo.example", target_url="https://target.example",
                               timeout=5, country="JP")
        with patch.object(nodes, "trace", side_effect=[{"ok": True, "country": "JP"},
                                                       {"ok": True, "country": "US"}]):
            row = nodes.probe({"name": "test", "type": "ss"}, 12345, args, {})
            self.assertFalse(row["target"]["ok"])
            self.assertTrue(row["target"]["tested"])


if __name__ == "__main__":
    unittest.main()
