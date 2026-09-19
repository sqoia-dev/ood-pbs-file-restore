#!/usr/bin/env python3
"""Root-only PBS browse/restore broker invoked by a forced SSH command."""

import base64
import datetime
import json
import logging
import logging.handlers
import os
import pwd
import re
import shlex
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

sys.path.insert(0, "/usr/local/lib/ood-pbs-file-restore")
from site_config import load as load_site_config


MAX_REQUEST = 65536
USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class BrokerError(Exception):
    pass


_CONFIG = None


def site_config():
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = load_site_config()
    return _CONFIG


def logger():
    log = logging.getLogger("pbs-restore-broker")
    if not log.handlers:
        handler = logging.handlers.SysLogHandler(address="/dev/log")
        handler.setFormatter(logging.Formatter("pbs-restore-broker: %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
    return log


def load_environment():
    values = {}
    path = site_config()["broker"]["secret_env_file"]
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise BrokerError("unable to open broker environment") from error
    details = os.fstat(descriptor)
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_nlink != 1
        or details.st_uid != 0
        or details.st_gid != 0
        or stat.S_IMODE(details.st_mode) != 0o600
    ):
        os.close(descriptor)
        raise BrokerError("broker environment must be a single-link root:root regular file mode 0600")
    with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("export "):
                continue
            key, separator, raw_value = line.partition("=")
            if not separator:
                continue
            parsed = shlex.split(raw_value, posix=True)
            if len(parsed) != 1:
                raise BrokerError("invalid broker environment")
            values[key] = parsed[0]
    required = (
        "PBS_PASSWORD",
        "PBS_API_ROOT",
        "PBS_AUTH_ID",
        "PBS_BACKUP_ID",
    )
    if any(not values.get(key) for key in required):
        raise BrokerError("incomplete broker environment")
    api_url = urllib.parse.urlparse(values["PBS_API_ROOT"])
    if (
        api_url.scheme != "https"
        or not api_url.netloc
        or api_url.username
        or api_url.password
        or api_url.query
        or api_url.fragment
        or values["PBS_API_ROOT"].endswith("/")
    ):
        raise BrokerError("invalid PBS API URL")
    return values


def identity(username):
    if not isinstance(username, str) or not USERNAME_RE.match(username):
        raise BrokerError("invalid authenticated username")
    try:
        account = pwd.getpwnam(username)
    except KeyError:
        raise BrokerError("authenticated account does not exist")
    expected_home = os.path.join(site_config()["broker"]["home_root"], username)
    if os.path.realpath(account.pw_dir) != expected_home:
        raise BrokerError("account home is outside the managed home root")
    return account


def decode_token(token):
    if token in (None, ""):
        return b""
    if not isinstance(token, str) or len(token) > 16384:
        raise BrokerError("invalid path token")
    try:
        relative = base64.b64decode(token.encode("ascii"), validate=True)
    except (ValueError, UnicodeError):
        raise BrokerError("invalid path token")
    if relative.startswith(b"/") or b"\x00" in relative:
        raise BrokerError("absolute paths are not allowed")
    components = relative.split(b"/")
    if any(component in (b"", b".", b"..") for component in components):
        raise BrokerError("unsafe path component")
    return relative


def encode_token(relative):
    if not relative:
        return ""
    return base64.b64encode(relative).decode("ascii")


def archive_layout(filenames):
    broker_config = site_config()["broker"]
    layouts = (
        (
            "legacy",
            broker_config["legacy_archive_name"],
            frozenset((broker_config["legacy_archive_name"], broker_config["legacy_catalog_name"])),
        ),
        (
            "split",
            broker_config["split_archive_name"],
            frozenset((broker_config["split_archive_name"], broker_config["split_payload_name"])),
        ),
    )
    for format_name, archive_name, required in layouts:
        if required.issubset(filenames):
            return {"format": format_name, "archive": archive_name}
    return None


def archive_prefix(username, snapshot):
    if snapshot["archive_format"] == "legacy":
        return ("/%s/%s" % (snapshot["archive_name"], username)).encode("utf-8")
    if snapshot["archive_format"] == "split":
        return ("/%s" % username).encode("utf-8")
    raise BrokerError("unsupported backup archive format")


def snapshot_api_params(snapshot, epoch, filepath):
    params = {
        "backup-type": site_config()["broker"]["backup_type"],
        "backup-id": snapshot["backup_id"],
        "backup-time": epoch,
        "filepath": base64.b64encode(filepath).decode("ascii"),
    }
    if snapshot["archive_format"] == "split":
        params["archive-name"] = snapshot["archive_name"]
    return params


def api_call(endpoint, params, environment):
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        environment["PBS_API_ROOT"] + "/" + endpoint + "?" + query,
        headers={
            "Authorization": "PBSAPIToken=" + environment["PBS_AUTH_ID"] + ":" + environment["PBS_PASSWORD"],
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(
            request, timeout=30, context=ssl.create_default_context()
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise BrokerError("PBS request failed with HTTP %s" % error.code)
    except (urllib.error.URLError, ValueError) as error:
        raise BrokerError("PBS request failed: %s" % error)
    if "data" not in payload:
        raise BrokerError("PBS returned an invalid response")
    return payload["data"]


def api_download(endpoint, params, environment, destination):
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        environment["PBS_API_ROOT"] + "/" + endpoint + "?" + query,
        headers={
            "Authorization": "PBSAPIToken=" + environment["PBS_AUTH_ID"] + ":" + environment["PBS_PASSWORD"],
            "Accept": "application/octet-stream",
        },
    )
    try:
        with urllib.request.urlopen(
            request, timeout=86400, context=ssl.create_default_context()
        ) as response:
            with open(destination, "wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
    except urllib.error.HTTPError as error:
        raise BrokerError("PBS restore request failed with HTTP %s" % error.code)
    except urllib.error.URLError as error:
        raise BrokerError("PBS restore request failed: %s" % error)


def available_snapshots(environment):
    broker_config = site_config()["broker"]
    entries = api_call(
        "snapshots",
        {"backup-type": broker_config["backup_type"], "backup-id": environment["PBS_BACKUP_ID"]},
        environment,
    )
    cutoff = int(
        (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=broker_config["snapshot_max_age_days"])
        ).timestamp()
    )
    snapshots = []
    for entry in entries:
        stamp = entry.get("backup-time")
        if not isinstance(stamp, int) or stamp < cutoff:
            continue
        files = entry.get("files")
        if not isinstance(files, list):
            continue
        filenames = {
            item.get("filename")
            for item in files
            if isinstance(item, dict) and isinstance(item.get("filename"), str)
        }
        layout = archive_layout(filenames)
        if layout is None:
            continue
        instant = datetime.datetime.fromtimestamp(
            stamp, tz=datetime.timezone.utc
        )
        snapshots.append(
            {
                "epoch": stamp,
                "timestamp": instant.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "date": instant.strftime("%Y-%m-%d"),
                "protected": bool(entry.get("protected", False)),
                "archive_format": layout["format"],
                "archive_name": layout["archive"],
                "backup_id": environment["PBS_BACKUP_ID"],
            }
        )
    snapshots.sort(key=lambda item: item["epoch"], reverse=True)
    return snapshots


def require_snapshot(epoch, environment):
    if isinstance(epoch, bool) or not isinstance(epoch, int):
        raise BrokerError("invalid backup snapshot")
    snapshots = available_snapshots(environment)
    for snapshot in snapshots:
        if snapshot["epoch"] == epoch:
            return snapshot
    raise BrokerError("backup snapshot is unavailable")


def list_directory(username, epoch, token, environment):
    snapshot = require_snapshot(epoch, environment)
    relative = decode_token(token)
    prefix = archive_prefix(username, snapshot)
    full_path = prefix + (b"/" + relative if relative else b"")
    entries = api_call(
        "catalog",
        snapshot_api_params(snapshot, epoch, full_path),
        environment,
    )
    safe_entries = []
    # PBS normalizes catalog response paths by removing the request's leading
    # slash, so validate against the normalized archive/user prefix.
    required_prefix = prefix.lstrip(b"/") + b"/"
    for entry in entries:
        try:
            decoded = base64.b64decode(entry["filepath"], validate=True)
        except (KeyError, ValueError):
            raise BrokerError("PBS returned an invalid catalog path")
        normalized = decoded[1:] if decoded.startswith(b"/") else decoded
        if normalized.startswith(b"/") or not normalized.startswith(required_prefix):
            raise BrokerError("PBS returned a path outside the user boundary")
        child_relative = normalized[len(required_prefix) :]
        decode_token(encode_token(child_relative))
        safe_entries.append(
            {
                "name": entry.get("text", ""),
                "token": encode_token(child_relative),
                "type": entry.get("type"),
                "leaf": bool(entry.get("leaf", True)),
                "size": entry.get("size"),
                "mtime": entry.get("mtime"),
            }
        )
    safe_entries.sort(key=lambda item: (item["leaf"], item["name"].lower()))
    return {"path": token or "", "entries": safe_entries}


def open_or_create_directory(parent_fd, name, mode, uid, gid):
    try:
        os.mkdir(name, mode=mode, dir_fd=parent_fd)
    except FileExistsError:
        pass
    try:
        descriptor = os.open(name, DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError:
        raise BrokerError("restore directory is missing, unsafe, or not a directory")
    details = os.fstat(descriptor)
    if not stat.S_ISDIR(details.st_mode):
        os.close(descriptor)
        raise BrokerError("restore path component is not a directory")
    os.fchown(descriptor, uid, gid)
    os.fchmod(descriptor, mode)
    return descriptor


def prepare_restore_job(account, date, job_id, relative):
    descriptors = []
    try:
        home_fd = os.open(account.pw_dir, DIRECTORY_FLAGS)
        descriptors.append(home_fd)
        home_details = os.fstat(home_fd)
        if home_details.st_uid != account.pw_uid:
            raise BrokerError("home directory ownership is invalid")

        restore_fd = open_or_create_directory(
            home_fd,
            site_config()["app"]["restore_directory_name"].encode("utf-8"),
            0o700, account.pw_uid, account.pw_gid
        )
        descriptors.append(restore_fd)
        date_fd = open_or_create_directory(
            restore_fd, date.encode("ascii"), 0o700, account.pw_uid, account.pw_gid
        )
        descriptors.append(date_fd)
        job_fd = open_or_create_directory(date_fd, job_id.encode("ascii"), 0o700, 0, 0)
        descriptors.append(job_fd)

        parent_fd = job_fd
        for component in relative.split(b"/")[:-1]:
            parent_fd = open_or_create_directory(
                parent_fd, component, 0o700, account.pw_uid, account.pw_gid
            )
            descriptors.append(parent_fd)

        return descriptors, job_fd, parent_fd, relative.split(b"/")[-1]
    except Exception:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise


def entry_metadata(username, epoch, relative, environment):
    parent, separator, name = relative.rpartition(b"/")
    listing = list_directory(
        username, epoch, encode_token(parent) if separator else "", environment
    )
    wanted = encode_token(relative)
    for entry in listing["entries"]:
        if entry["token"] == wanted:
            return entry
    raise BrokerError("selected path is unavailable in this snapshot")


def safe_extract_zip(archive, destination_parent, account):
    root = os.path.realpath(destination_parent)
    with zipfile.ZipFile(archive, "r") as bundle:
        entries = bundle.infolist()
        if not entries:
            raise BrokerError("PBS returned an empty directory archive")
        validated = []
        destinations = set()
        for entry in entries:
            name = entry.filename
            if "\x00" in name or name.startswith("/") or "\\" in name:
                raise BrokerError("PBS returned an unsafe archive path")
            components = [item for item in name.split("/") if item]
            if not components or any(item in (".", "..") for item in components):
                raise BrokerError("PBS returned an unsafe archive path")
            destination = os.path.join(destination_parent, *components)
            if not os.path.abspath(destination).startswith(root + os.sep):
                raise BrokerError("PBS archive escaped the restore root")
            mode = entry.external_attr >> 16
            if destination in destinations:
                raise BrokerError("PBS returned duplicate archive paths")
            destinations.add(destination)
            validated.append((entry, destination, mode))

        # Create links only after all directories and regular files. This keeps
        # a malicious archive from using an earlier symlink as a later parent.
        validated.sort(key=lambda item: stat.S_ISLNK(item[2]))
        for entry, destination, mode in validated:
            if entry.is_dir() or stat.S_ISDIR(mode):
                os.makedirs(destination, mode=(mode & 0o777) or 0o700, exist_ok=True)
                os.chown(destination, account.pw_uid, account.pw_gid)
                continue
            os.makedirs(os.path.dirname(destination), mode=0o700, exist_ok=True)
            if stat.S_ISLNK(mode):
                if os.path.lexists(destination):
                    raise BrokerError("PBS returned conflicting archive paths")
                target = bundle.read(entry).decode("utf-8", "surrogateescape")
                if not target or target.startswith("/") or "\x00" in target:
                    raise BrokerError("PBS archive symlink target is unsafe")
                resolved = os.path.normpath(os.path.join(os.path.dirname(entry.filename), target))
                if resolved == ".." or resolved.startswith("../"):
                    raise BrokerError("PBS archive symlink target escapes the restore root")
                os.symlink(target, destination)
                os.lchown(destination, account.pw_uid, account.pw_gid)
                continue
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(destination, flags, (mode & 0o777) or 0o600)
            try:
                with os.fdopen(descriptor, "wb") as output, bundle.open(entry) as source:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
            except Exception:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                raise
            os.chown(destination, account.pw_uid, account.pw_gid)
            os.chmod(destination, (mode & 0o777) or 0o600)


def chown_tree(path, uid, gid):
    os.lchown(path, uid, gid)
    for current, directories, files in os.walk(path, followlinks=False):
        for name in directories + files:
            os.lchown(os.path.join(current, name), uid, gid)


def restore_path(username, epoch, token, environment):
    account = identity(username)
    snapshot = require_snapshot(epoch, environment)
    relative = decode_token(token)
    if not relative:
        raise BrokerError("select a file or directory to restore")
    metadata = entry_metadata(username, epoch, relative, environment)

    date = snapshot["date"]
    job_id = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%H%M%SZ-"
    ) + uuid.uuid4().hex[:8]

    staging_root = site_config()["broker"]["staging_root"]
    os.makedirs(staging_root, mode=0o700, exist_ok=True)
    os.chown(staging_root, 0, 0)
    os.chmod(staging_root, 0o700)
    staging = tempfile.mkdtemp(prefix=username + "-", dir=staging_root)
    descriptors = []
    try:
        download = os.path.join(staging, "download")
        full_path = archive_prefix(username, snapshot) + b"/" + relative
        api_download(
            "pxar-file-download",
            snapshot_api_params(snapshot, epoch, full_path),
            environment,
            download,
        )

        source = download
        if metadata["type"] == "d":
            extract_parent = os.path.join(staging, "extracted")
            os.mkdir(extract_parent, 0o700)
            safe_extract_zip(download, extract_parent, account)
            source = os.path.join(extract_parent, os.fsdecode(relative.split(b"/")[-1]))
            if not os.path.lexists(source):
                raise BrokerError("PBS directory archive did not contain the selected path")
        elif metadata["type"] == "l":
            raise BrokerError(
                "individual symbolic links cannot be restored; restore their parent directory"
            )
        elif metadata["type"] not in ("f", "h"):
            raise BrokerError("this catalog entry type cannot be restored")

        descriptors, job_fd, parent_fd, leaf = prepare_restore_job(
            account, date, job_id, relative
        )
        if metadata["type"] == "d":
            chown_tree(source, account.pw_uid, account.pw_gid)
        os.rename(source, leaf, dst_dir_fd=parent_fd)
        if metadata["type"] in ("f", "h"):
            target_fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
            try:
                os.fchown(target_fd, account.pw_uid, account.pw_gid)
                os.fchmod(target_fd, 0o600)
                if metadata.get("mtime"):
                    os.utime(target_fd, (metadata["mtime"], metadata["mtime"]))
            finally:
                os.close(target_fd)
        os.fchown(job_fd, account.pw_uid, account.pw_gid)
        os.fchmod(job_fd, 0o700)
        visible_destination = os.path.join(
            account.pw_dir, site_config()["app"]["restore_directory_name"], date, job_id, os.fsdecode(relative)
        )
        logger().info(
            "user=%s action=restore snapshot=%s destination=%s",
            username,
            snapshot["timestamp"],
            visible_destination,
        )
        return {
            "restored_to": visible_destination,
            "snapshot": snapshot["timestamp"],
            "overwrite": False,
        }
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
        shutil.rmtree(staging, ignore_errors=True)


def dispatch(request):
    username = request.get("user")
    identity(username)
    environment = load_environment()
    action = request.get("action")
    if action == "snapshots":
        return {"snapshots": available_snapshots(environment)}
    if action == "list":
        return list_directory(
            username, request.get("snapshot"), request.get("path", ""), environment
        )
    if action == "restore":
        return restore_path(
            username, request.get("snapshot"), request.get("path", ""), environment
        )
    raise BrokerError("unsupported action")


def main():
    raw = sys.stdin.buffer.readline(MAX_REQUEST + 1)
    if not raw or len(raw) > MAX_REQUEST or sys.stdin.buffer.read(1):
        raise BrokerError("invalid request size")
    try:
        request = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise BrokerError("invalid JSON request")
    if not isinstance(request, dict):
        raise BrokerError("request must be an object")
    return dispatch(request)


if __name__ == "__main__":
    try:
        print(json.dumps({"ok": True, "data": main()}, sort_keys=True))
    except (BrokerError, subprocess.TimeoutExpired) as error:
        logger().warning("request denied or failed: %s", error)
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        raise SystemExit(1)
    except Exception:
        logger().exception("unexpected broker failure")
        print(json.dumps({"ok": False, "error": "internal broker error"}, sort_keys=True))
        raise SystemExit(1)
