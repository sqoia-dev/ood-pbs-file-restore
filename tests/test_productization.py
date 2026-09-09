import base64
import copy
import io
import json
import jsonschema
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile
import stat
import importlib.util
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
import broker
import client
import app
from site_config import ConfigError, load, validate

_INSTALL_SPEC = importlib.util.spec_from_file_location("product_install", ROOT / "install.py")
installer = importlib.util.module_from_spec(_INSTALL_SPEC)
_INSTALL_SPEC.loader.exec_module(installer)


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

    def test_schema_and_runtime_reject_the_same_security_variants(self):
        schema = json.loads((ROOT / "config/site.schema.json").read_text())
        variants = (
            ("broker", "home_root", "/"),
            ("broker", "staging_root", "/var//tmp"),
            ("broker", "secret_env_file", "/etc/broker.env/"),
            ("portal", "broker_ssh_target", "root@a"),
            ("app", "support_url", "https://support.example.test/help"),
            ("app", "support_url", "http://support.example.test/help"),
            ("app", "support_url", "https://user:pass@support.example.test/help"),
            ("app", "support_url", "https://support.example.test/help?unsafe=1"),
        )
        for section, key, value in variants:
            with self.subTest(section=section, key=key, value=value):
                changed = copy.deepcopy(self.config)
                changed[section][key] = value
                try:
                    validate(changed)
                    runtime_valid = True
                except ConfigError:
                    runtime_valid = False
                try:
                    jsonschema.validate(changed, schema)
                    schema_valid = True
                except jsonschema.ValidationError:
                    schema_valid = False
                self.assertEqual(schema_valid, runtime_valid)


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
            root_secret = os.stat_result((stat.S_IFREG | 0o600, 0, 0, 1, 0, 0, 0, 0, 0, 0))
            with mock.patch.object(broker.os, "fstat", return_value=root_secret), self.assertRaisesRegex(
                broker.BrokerError, "invalid PBS API URL"
            ) as caught:
                    broker.load_environment()
            self.assertNotIn("do-not-print-this", str(caught.exception))

    def test_broker_rejects_world_readable_or_non_root_secret_file(self):
        changed = copy.deepcopy(load(str(ROOT / "config/site.example.json")))
        with tempfile.NamedTemporaryFile() as environment:
            changed["broker"]["secret_env_file"] = environment.name
            broker._CONFIG = changed
            with self.assertRaisesRegex(broker.BrokerError, "root-owned mode 0600"):
                broker.load_environment()

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

    def test_zip_absolute_and_escaping_symlink_targets_are_rejected(self):
        account = type("Account", (), {"pw_uid": os.getuid(), "pw_gid": os.getgid()})()
        for target in ("/etc/passwd", "../../outside"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temp:
                archive = Path(temp) / "bad-link.zip"
                destination = Path(temp) / "out"
                destination.mkdir()
                link = zipfile.ZipInfo("nested/link")
                link.create_system = 3
                link.external_attr = (stat.S_IFLNK | 0o777) << 16
                with zipfile.ZipFile(archive, "w") as bundle:
                    bundle.writestr(link, target)
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

    def test_installer_preparation_failure_leaves_all_existing_files_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "first"
            second = Path(temp) / "second"
            first.write_text("old-first")
            second.write_text("old-second")
            plans = [("install", b"new-first", first, 0o600), ("install", b"new-second", second, 0o600)]
            original = installer._prepared_bytes
            def fail_second(data, target, mode):
                if target == second:
                    raise OSError("simulated sudoers staging failure")
                return original(data, target, mode)
            with mock.patch.object(installer, "_prepared_bytes", side_effect=fail_second):
                with self.assertRaises(OSError):
                    installer.commit_install(plans, False)
            self.assertEqual(first.read_text(), "old-first")
            self.assertEqual(second.read_text(), "old-second")

    def test_installer_replacement_failure_rolls_back_prior_replacements(self):
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "first"
            second = Path(temp) / "second"
            first.write_text("old-first")
            second.write_text("old-second")
            plans = [("install", b"new-first", first, 0o600), ("install", b"new-second", second, 0o600)]
            real_replace = installer.os.replace
            failed = False
            def fail_second(source, target):
                nonlocal failed
                if not failed and Path(target) == second and Path(source).name.startswith("."):
                    failed = True
                    raise OSError("simulated replacement failure")
                return real_replace(source, target)
            with mock.patch.object(installer.os, "replace", side_effect=fail_second):
                with self.assertRaises(OSError):
                    installer.commit_install(plans, False)
            self.assertEqual(first.read_text(), "old-first")
            self.assertEqual(second.read_text(), "old-second")

    def test_installer_refuses_a_symlink_target(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            victim = Path(temp) / "victim"
            target = Path(temp) / "target"
            source.write_text("safe")
            victim.write_text("victim")
            target.symlink_to(victim)
            with self.assertRaisesRegex(ConfigError, "symlink installer target"):
                installer.planned_file(source, target, 0o600)
            self.assertEqual(victim.read_text(), "victim")

    def test_uninstall_preserves_unmanaged_app_files(self):
        with tempfile.TemporaryDirectory() as temp:
            config = str(ROOT / "config/site.example.json")
            args = ("--role", "portal", "--config", config, "--destdir", temp)
            self.assertEqual(self.run_installer(*args).returncode, 0)
            unmanaged = Path(temp) / "var/www/ood/apps/sys/pbs-file-restore/local-admin-file"
            unmanaged.write_text("preserve")
            result = self.run_installer(*args, "--action", "uninstall")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(unmanaged.exists())


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
