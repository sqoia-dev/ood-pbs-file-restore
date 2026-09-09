#!/usr/bin/env python3
"""Idempotent role installer with an unprivileged dry-run and DESTDIR staging."""

import argparse
import os
from pathlib import Path
import shutil
import sys

from site_config import ConfigError, load

SOURCE = Path(__file__).resolve().parent


def destination(path, destdir):
    return Path(destdir) / path.lstrip("/") if destdir else Path(path)


def same_bytes(source, target):
    return target.is_file() and source.read_bytes() == target.read_bytes()


def install_file(source, target, mode, dry_run):
    action = "keep" if same_bytes(source, target) and target.stat().st_mode & 0o777 == mode else "install"
    print("%s %s mode=%04o" % (action, target, mode))
    if dry_run or action == "keep":
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    shutil.copyfile(str(source), str(temporary))
    os.chmod(str(temporary), mode)
    os.replace(str(temporary), str(target))


def install_text(text, target, mode, dry_run):
    current = target.read_text() if target.is_file() else None
    action = "keep" if current == text and target.stat().st_mode & 0o777 == mode else "install"
    print("%s %s mode=%04o" % (action, target, mode))
    if dry_run or action == "keep":
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(text)
    os.chmod(str(temporary), mode)
    os.replace(str(temporary), str(target))


def remove_path(target, dry_run):
    if not target.exists() and not target.is_symlink():
        print("absent %s" % target)
        return
    print("remove %s" % target)
    if dry_run:
        return
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(str(target))
    else:
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
        managed = (executable_target, library_target)
        if args.action == "uninstall":
            for target in managed:
                remove_path(target, args.dry_run)
            print("preserve %s and all credentials, logs, and restored data" % config_target)
            return
        install_file(SOURCE / "broker.py", executable_target, 0o755, args.dry_run)
        install_file(SOURCE / "site_config.py", library_target, 0o644, args.dry_run)
        install_file(config_source, config_target, 0o644, args.dry_run)
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
    if args.action == "uninstall":
        for target in (client_target, sudoers_target, library_target, app_target):
            remove_path(target, args.dry_run)
        print("preserve %s, SSH keys, known_hosts, and restored data" % config_target)
        return

    install_file(SOURCE / "client.py", client_target, 0o755, args.dry_run)
    install_file(SOURCE / "site_config.py", library_target, 0o644, args.dry_run)
    install_file(config_source, config_target, 0o644, args.dry_run)
    sudoers = (
        "Defaults!%s !requiretty\n" % config["portal"]["client_path"]
        + "ALL ALL=(root) NOPASSWD: %s\n" % config["portal"]["client_path"]
    )
    install_text(sudoers, sudoers_target, 0o440, args.dry_run)
    for relative, mode in (
        ("app.py", 0o644),
        ("passenger_wsgi.py", 0o644),
        ("manifest.yml", 0o644),
        ("site_config.py", 0o644),
        ("requirements.txt", 0o644),
        ("LICENSE", 0o644),
        ("NOTICE", 0o644),
    ):
        install_file(SOURCE / relative, app_target / relative, mode, args.dry_run)
    print("validate %s with visudo -cf before enabling users" % sudoers_target)
    print("preserve SSH private keys and known_hosts; installer never reads or writes them")


if __name__ == "__main__":
    try:
        main()
    except ConfigError as error:
        print("configuration error: %s" % error, file=sys.stderr)
        raise SystemExit(2)
