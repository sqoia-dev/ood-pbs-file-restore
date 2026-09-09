#!/usr/bin/env python3
"""Sudo-confined OOD client that binds requests to the invoking Unix user."""

import json
import os
import pwd
import subprocess
import sys

sys.path.insert(0, "/usr/local/lib/ood-pbs-file-restore")
from site_config import load as load_site_config

MAX_REQUEST = 65536


def ssh_command(config=None):
    portal = (config or load_site_config())["portal"]
    return [
        "/usr/bin/ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "UserKnownHostsFile=" + portal["known_hosts"],
        "-i",
        portal["ssh_private_key"],
        portal["broker_ssh_target"],
    ]


def fail(message):
    print(json.dumps({"ok": False, "error": message}))
    raise SystemExit(1)


def main():
    sudo_user = os.environ.get("SUDO_USER")
    sudo_uid = os.environ.get("SUDO_UID")
    if not sudo_user or not sudo_uid:
        fail("broker client must be invoked through sudo")
    try:
        account = pwd.getpwnam(sudo_user)
    except KeyError:
        fail("invoking account does not exist")
    if str(account.pw_uid) != sudo_uid or account.pw_uid < 1000:
        fail("invalid invoking identity")

    raw = sys.stdin.buffer.readline(MAX_REQUEST + 1)
    if not raw or len(raw) > MAX_REQUEST or sys.stdin.buffer.read(1):
        fail("invalid request size")
    try:
        request = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        fail("invalid JSON request")
    if not isinstance(request, dict):
        fail("request must be an object")
    request["user"] = sudo_user
    encoded = (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8")
    completed = subprocess.run(
        ssh_command(),
        input=encoded,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=86500,
    )
    if completed.stdout:
        sys.stdout.buffer.write(completed.stdout)
    else:
        fail("restore broker was unavailable")
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    try:
        main()
    except subprocess.TimeoutExpired:
        fail("restore broker timed out")
