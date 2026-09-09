#!/usr/bin/env python3
"""Live canary checks for identity confinement, browse, and one small restore."""

import base64
import json
import os
import pwd
import subprocess

from site_config import load as load_site_config

ACCOUNT = pwd.getpwuid(os.getuid())
CONFIG = load_site_config()
CLIENT = ["/usr/bin/sudo", "-n", CONFIG["portal"]["client_path"]]
EXPECTED_ROOT = os.path.join(
    ACCOUNT.pw_dir, CONFIG["app"]["restore_directory_name"]
) + os.sep

if os.environ.get("PBS_RESTORE_ENABLE_LIVE_TEST") != "YES":
    raise SystemExit(
        "Refusing live restores without PBS_RESTORE_ENABLE_LIVE_TEST=YES"
    )


def request(payload):
    completed = subprocess.run(
        CLIENT,
        input=(json.dumps(payload) + "\n").encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=300,
    )
    response = json.loads(completed.stdout.decode("utf-8"))
    return completed.returncode, response


code, snapshots = request({"action": "snapshots", "user": "root"})
assert code == 0 and snapshots["ok"]
latest = snapshots["data"]["snapshots"][0]["epoch"]

code, listing = request({"action": "list", "snapshot": latest, "path": ""})
assert code == 0 and listing["ok"] and listing["data"]["entries"]
for entry in listing["data"]["entries"]:
    decoded = base64.b64decode(entry["token"], validate=True)
    assert not decoded.startswith(b"/")
    assert b".." not in decoded.split(b"/")

escape = base64.b64encode(b"/etc").decode("ascii")
code, denied = request({"action": "list", "snapshot": latest, "path": escape})
assert code != 0 and not denied["ok"]

candidate = next(
    entry
    for entry in listing["data"]["entries"]
    if entry["type"] == "f" and (entry.get("size") or 0) <= 1024 * 1024
)
code, restored = request(
    {"action": "restore", "snapshot": latest, "path": candidate["token"]}
)
assert code == 0 and restored["ok"], restored
destination = restored["data"]["restored_to"]
assert destination.startswith(EXPECTED_ROOT)
assert os.path.lexists(destination)
assert os.path.realpath(destination).startswith(EXPECTED_ROOT)

directory_name = os.environ.get("PBS_RESTORE_TEST_DIRECTORY")
if directory_name:
    directory = next(
        entry
        for entry in listing["data"]["entries"]
        if entry["type"] == "d" and entry["name"] == directory_name
    )
    code, restored_directory = request(
        {"action": "restore", "snapshot": latest, "path": directory["token"]}
    )
    assert code == 0 and restored_directory["ok"], restored_directory
    directory_destination = restored_directory["data"]["restored_to"]
    assert os.path.isdir(directory_destination)
    assert os.path.realpath(directory_destination).startswith(EXPECTED_ROOT)

print(
    "snapshots=%d entries=%d escape_denied=true file_restore_verified=true directory_restore_verified=%s"
    % (
        len(snapshots["data"]["snapshots"]),
        len(listing["data"]["entries"]),
        str(bool(directory_name)).lower(),
    )
)
