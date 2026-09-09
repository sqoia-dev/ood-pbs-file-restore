#!/usr/bin/env python3
"""Fail-closed role installer with unprivileged dry-run and DESTDIR staging."""

import argparse
import os
from pathlib import Path
import stat
import sys
import tempfile

from site_config import ConfigError, load

SOURCE = Path(__file__).resolve().parent


def destination(path, destdir):
    return Path(destdir) / path.lstrip("/") if destdir else Path(path)


def same_bytes(source, target):
    if target.is_symlink():
        raise ConfigError("refusing symlink installer target %s" % target)
    if target.exists() and not target.is_file():
        raise ConfigError("refusing non-file installer target %s" % target)
    return target.is_file() and source.read_bytes() == target.read_bytes()


def _temporary(target):
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".%s." % target.name, dir=str(target.parent))
    return os.fdopen(descriptor, "wb"), Path(name)


def _prepared_bytes(data, target, mode):
    handle, temporary = _temporary(target)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        return temporary
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def planned_file(source, target, mode):
    action = "keep" if same_bytes(source, target) and target.stat().st_mode & 0o777 == mode else "install"
    return action, source.read_bytes() if action == "install" else None, target, mode


def planned_text(text, target, mode):
    current = target.read_text() if target.is_file() else None
    action = "keep" if current == text and target.stat().st_mode & 0o777 == mode else "install"
    return action, text.encode("utf-8") if action == "install" else None, target, mode


def commit_install(plans, dry_run):
    """Prepare every file before replacing any; restore all replacements on error."""
    for action, _, target, mode in plans:
        print("%s %s mode=%04o" % (action, target, mode))
    if dry_run:
        return
    prepared = []
    backups = []
    completed = False
    try:
        for action, data, target, mode in plans:
            if action == "install":
                prepared.append((target, _prepared_bytes(data, target, mode)))
        for target, temporary in prepared:
            backup = None
            if target.exists() or target.is_symlink():
                descriptor, backup_name = tempfile.mkstemp(prefix=".%s.rollback." % target.name, dir=str(target.parent))
                os.close(descriptor)
                backup = Path(backup_name)
                os.replace(target, backup)
            backups.append((target, backup))
            os.replace(temporary, target)
        completed = True
    except Exception as install_error:
        rollback_errors = []
        for target, backup in reversed(backups):
            if backup is not None and (backup.exists() or backup.is_symlink()):
                try:
                    # Replacing the target directly preserves both the current
                    # target and the sole prior-byte backup if restoration fails.
                    os.replace(backup, target)
                except OSError as rollback_error:
                    rollback_errors.append("%s: %s" % (target, rollback_error))
            elif backup is None:
                try:
                    if target.exists() or target.is_symlink():
                        target.unlink()
                except OSError as rollback_error:
                    rollback_errors.append("%s: %s" % (target, rollback_error))
        if rollback_errors:
            raise RuntimeError(
                "installation failed; prior bytes retained in rollback backup; "
                "manual recovery required: " + "; ".join(rollback_errors)
            ) from install_error
        raise
        raise
    finally:
        for _, temporary in prepared:
            temporary.unlink(missing_ok=True)
        if completed:
            for _, backup in backups:
                if backup is not None:
                    backup.unlink(missing_ok=True)


def remove_managed_file(target, dry_run):
    if not target.exists() and not target.is_symlink():
        print("absent %s" % target)
        return
    details = os.lstat(target)
    if not (stat.S_ISREG(details.st_mode) or stat.S_ISLNK(details.st_mode)):
        raise ConfigError("refusing to remove non-file managed target %s" % target)
    print("remove %s" % target)
    if not dry_run:
        target.unlink()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("portal", "broker"), required=True)
    parser.add_argument("--config", required=True, help="validated non-secret site JSON")
    parser.add_argument("--action", choices=("install", "uninstall"), default="install")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--destdir", default="", help="stage absolute paths below this directory")
    args = parser.parse_args()

    config_source = Path(args.config).resolve()
    config = load(str(config_source))
    if not args.dry_run and not args.destdir and os.geteuid() != 0:
        parser.error("a live install or uninstall requires root; use --dry-run or --destdir")
    config_target = destination("/etc/ood-pbs-file-restore/site.json", args.destdir)
    library_target = destination("/usr/local/lib/ood-pbs-file-restore/site_config.py", args.destdir)

    if args.role == "broker":
        executable_target = destination("/usr/local/sbin/pbs-restore-broker", args.destdir)
        if args.action == "uninstall":
            for target in (executable_target, library_target):
                remove_managed_file(target, args.dry_run)
            print("preserve %s and all credentials, logs, and restored data" % config_target)
            return
        commit_install([
            planned_file(SOURCE / "broker.py", executable_target, 0o755),
            planned_file(SOURCE / "site_config.py", library_target, 0o644),
            planned_file(config_source, config_target, 0o644),
        ], args.dry_run)
        staging = destination(config["broker"]["staging_root"], args.destdir)
        print("ensure %s mode=0700 owner=root:root" % staging)
        if not args.dry_run:
            staging.mkdir(parents=True, exist_ok=True)
            os.chmod(str(staging), 0o700)
            if not args.destdir:
                os.chown(str(staging), 0, 0)
        print("preserve secret file %s; installer never reads or writes it" % config["broker"]["secret_env_file"])
        return

    client_target = destination(config["portal"]["client_path"], args.destdir)
    sudoers_target = destination("/etc/sudoers.d/ood-pbs-file-restore", args.destdir)
    app_target = destination("/var/www/ood/apps/sys/pbs-file-restore", args.destdir)
    app_files = ("app.py", "passenger_wsgi.py", "manifest.yml", "site_config.py", "requirements.txt", "LICENSE", "NOTICE")
    if args.action == "uninstall":
        for target in (client_target, sudoers_target, library_target) + tuple(app_target / name for name in app_files):
            remove_managed_file(target, args.dry_run)
        print("preserve %s, SSH keys, known_hosts, restored data, and unmanaged app files" % config_target)
        return

    sudoers = "Defaults!%s !requiretty\nALL ALL=(root) NOPASSWD: %s\n" % (config["portal"]["client_path"], config["portal"]["client_path"])
    plans = [planned_file(SOURCE / "client.py", client_target, 0o755), planned_file(SOURCE / "site_config.py", library_target, 0o644), planned_file(config_source, config_target, 0o644), planned_text(sudoers, sudoers_target, 0o440)]
    plans.extend(planned_file(SOURCE / name, app_target / name, 0o644) for name in app_files)
    commit_install(plans, args.dry_run)
    print("validate %s with visudo -cf before enabling users" % sudoers_target)
    print("preserve SSH private keys and known_hosts; installer never reads or writes them")


if __name__ == "__main__":
    try:
        main()
    except ConfigError as error:
        print("configuration error: %s" % error, file=sys.stderr)
        raise SystemExit(2)
