import base64
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile
import stat
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
import broker
import client
import app
from site_config import ConfigError, load, validate


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.config = load(str(ROOT / "config/site.example.json"))
        broker._CONFIG = self.config

    def test_example_is_valid_and_contains_no_secret_value(self):
        self.assertEqual(self.config["deployment_model"], "shared-home-v1")
        text = (ROOT / "config/site.example.json").read_text()
        self.assertNotIn("PBS_PASSWORD", text)
        self.assertNotIn("PRIVATE KEY", text)

    def test_unknown_setting_fails_closed(self):
        changed = copy.deepcopy(self.config)
        changed["broker"]["allow_arbitrary_path"] = True
        with self.assertRaises(ConfigError):
            validate(changed)

    def test_unsupported_layout_fails_closed(self):
        changed = copy.deepcopy(self.config)
        changed["deployment_model"] = "per-user"
        with self.assertRaises(ConfigError):
            validate(changed)

    def test_ssh_option_injection_fails_closed(self):
        changed = copy.deepcopy(self.config)
        changed["portal"]["broker_ssh_target"] = "-oProxyCommand=evil"
        with self.assertRaises(ConfigError):
            validate(changed)

    def test_traversal_restore_directory_fails_closed(self):
        changed = copy.deepcopy(self.config)
        changed["app"]["restore_directory_name"] = "../escape"
        with self.assertRaises(ConfigError):
            validate(changed)


class SecurityBoundaryTests(unittest.TestCase):
    def test_path_tokens_reject_absolute_and_traversal(self):
        for raw in (
            b"/etc",
            b"../alice",
            b"alice/../bob",
            b"alice//file",
            b"alice/./file",
            b"alice\x00file",
        ):
            with self.subTest(raw=raw), self.assertRaises(broker.BrokerError):
                broker.decode_token(base64.b64encode(raw).decode("ascii"))

    def test_archive_prefix_is_identity_derived(self):
        self.assertEqual(broker.archive_prefix("alice"), b"/root.pxar.didx/alice")

    def test_broker_requires_https_without_disclosing_secret(self):
        changed = copy.deepcopy(load(str(ROOT / "config/site.example.json")))
        with tempfile.TemporaryDirectory() as temp:
            environment = Path(temp) / "broker.env"
            environment.write_text(
                "PBS_API_ROOT='http://pbs.example.test/api2/json/admin/datastore/home'\n"
                "PBS_AUTH_ID='restore@pbs!ood'\n"
                "PBS_PASSWORD='do-not-print-this'\n"
                "PBS_BACKUP_ID='storage'\n"
            )
            changed["broker"]["secret_env_file"] = str(environment)
            broker._CONFIG = changed
            with self.assertRaisesRegex(broker.BrokerError, "invalid PBS API URL") as caught:
                broker.load_environment()
            self.assertNotIn("do-not-print-this", str(caught.exception))

    def test_client_ssh_command_keeps_pinning_and_forced_identity(self):
        command = client.ssh_command(load(str(ROOT / "config/site.example.json")))
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("IdentitiesOnly=yes", command)
        self.assertEqual(command[-1], "root@storage.example.edu")
        self.assertNotIn("-oProxyCommand", " ".join(command))

    def test_zip_traversal_is_rejected(self):
        account = type("Account", (), {"pw_uid": os.getuid(), "pw_gid": os.getgid()})()
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "bad.zip"
            destination = Path(temp) / "out"
            destination.mkdir()
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../escape", "unsafe")
            with self.assertRaises(broker.BrokerError):
                broker.safe_extract_zip(str(archive), str(destination), account)
            self.assertFalse((Path(temp) / "escape").exists())

    def test_zip_symlink_cannot_become_a_later_parent(self):
        account = type("Account", (), {"pw_uid": os.getuid(), "pw_gid": os.getgid()})()
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "symlink-parent.zip"
            destination = Path(temp) / "out"
            destination.mkdir()
            link = zipfile.ZipInfo("pivot")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr(link, "../outside")
                bundle.writestr("pivot/file", "unsafe")
            with self.assertRaises((broker.BrokerError, OSError)):
                broker.safe_extract_zip(str(archive), str(destination), account)
            self.assertFalse((Path(temp) / "outside/file").exists())

    def test_zip_duplicate_path_is_rejected(self):
        account = type("Account", (), {"pw_uid": os.getuid(), "pw_gid": os.getgid()})()
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "duplicate.zip"
            destination = Path(temp) / "out"
            destination.mkdir()
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("same", "one")
                bundle.writestr("same", "two")
            with self.assertRaises(broker.BrokerError):
                broker.safe_extract_zip(str(archive), str(destination), account)


class InstallerTests(unittest.TestCase):
    def run_installer(self, *arguments):
        return subprocess.run(
            ["python3", str(ROOT / "install.py"), *arguments],
            cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False,
        )

    def test_both_role_dry_runs_are_unprivileged_and_secret_safe(self):
        config = str(ROOT / "config/site.example.json")
        for role in ("portal", "broker"):
            result = self.run_installer("--role", role, "--config", config, "--dry-run")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("REPLACE_WITH_TOKEN_SECRET", result.stdout)
            self.assertIn("installer never reads or writes", result.stdout)

    def test_staged_portal_install_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            args = ("--role", "portal", "--config", str(ROOT / "config/site.example.json"), "--destdir", temp)
            first = self.run_installer(*args)
            second = self.run_installer(*args)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("keep", second.stdout)
            sudoers = Path(temp) / "etc/sudoers.d/ood-pbs-file-restore"
            self.assertEqual(sudoers.stat().st_mode & 0o777, 0o440)


class ApplicationTests(unittest.TestCase):
    def test_product_page_uses_validated_site_copy(self):
        config = load(str(ROOT / "config/site.example.json"))
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / ".sprockets-manifest-test.json"
            manifest.write_text(json.dumps({"assets": {"application.css": "app.css", "application.js": "app.js"}}))
            with mock.patch.object(app, "load_site_config", return_value=config), mock.patch.object(
                app.glob, "glob", return_value=[str(manifest)]
            ), mock.patch.object(app, "interactive_apps_menu", return_value=""):
                page = app.render_page("alice")
        self.assertIn("PBS File Restore for Open OnDemand by Sqoia Labs", page)
        self.assertIn("AGPL-3.0-or-later", page)
        self.assertNotIn("__APP_", page)


if __name__ == "__main__":
    unittest.main()
