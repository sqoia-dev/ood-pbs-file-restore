#!/usr/bin/env python3
"""Validated, non-secret site configuration shared by portal and broker roles."""

import json
import os
import re
from urllib.parse import urlparse

DEFAULT_CONFIG_PATH = "/etc/ood-pbs-file-restore/site.json"
_SIMPLE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_SSH_TARGET = re.compile(r"^root@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_DEFAULT_SPLIT_ARCHIVE_NAME = "root.mpxar.didx"
_DEFAULT_SPLIT_PAYLOAD_NAME = "root.ppxar.didx"


class ConfigError(ValueError):
    pass


def _object(value, label):
    if not isinstance(value, dict):
        raise ConfigError("%s must be an object" % label)
    return value


def _exact_keys(value, expected, label):
    unknown = set(value) - set(expected)
    missing = set(expected) - set(value)
    if unknown:
        raise ConfigError("%s has unknown settings: %s" % (label, ", ".join(sorted(unknown))))
    if missing:
        raise ConfigError("%s is missing settings: %s" % (label, ", ".join(sorted(missing))))


def _string(value, label, maximum=512):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ConfigError("%s must be a non-empty string" % label)
    return value


def _absolute_path(value, label):
    value = _string(value, label)
    if not value.startswith("/") or value == "/" or os.path.normpath(value) != value:
        raise ConfigError("%s must be a normalized absolute path" % label)
    return value


def validate(config):
    config = _object(config, "configuration")
    _exact_keys(config, ("schema_version", "deployment_model", "broker", "portal", "app"), "configuration")
    if config["schema_version"] != 1:
        raise ConfigError("schema_version must be 1")
    if config["deployment_model"] != "shared-home-v1":
        raise ConfigError("only the live-tested shared-home-v1 deployment model is implemented")

    broker = dict(_object(config["broker"], "broker"))
    common_broker_keys = (
        "backup_type", "home_root", "staging_root", "snapshot_max_age_days",
        "secret_env_file",
    )
    legacy_v1_keys = ("archive_name", "catalog_name")
    current_v1_keys = (
        "legacy_archive_name", "legacy_catalog_name", "split_archive_name",
        "split_payload_name",
    )
    if any(key in broker for key in legacy_v1_keys):
        _exact_keys(broker, common_broker_keys + legacy_v1_keys, "broker")
        broker["legacy_archive_name"] = broker.pop("archive_name")
        broker["legacy_catalog_name"] = broker.pop("catalog_name")
        broker["split_archive_name"] = _DEFAULT_SPLIT_ARCHIVE_NAME
        broker["split_payload_name"] = _DEFAULT_SPLIT_PAYLOAD_NAME
    else:
        _exact_keys(broker, common_broker_keys + current_v1_keys, "broker")
    config = dict(config)
    config["broker"] = broker
    if broker["backup_type"] != "host":
        raise ConfigError("broker.backup_type must remain host for shared-home-v1")
    for key in ("legacy_archive_name", "legacy_catalog_name", "split_archive_name", "split_payload_name"):
        value = _string(broker[key], "broker." + key, 128)
        if not _SIMPLE_NAME.match(value):
            raise ConfigError("broker.%s contains unsafe characters" % key)
    for key in ("home_root", "staging_root", "secret_env_file"):
        _absolute_path(broker[key], "broker." + key)
    days = broker["snapshot_max_age_days"]
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 366:
        raise ConfigError("broker.snapshot_max_age_days must be an integer from 1 through 366")

    portal = _object(config["portal"], "portal")
    _exact_keys(portal, ("broker_ssh_target", "ssh_private_key", "known_hosts", "client_path"), "portal")
    target = _string(portal["broker_ssh_target"], "portal.broker_ssh_target", 255)
    if not _SSH_TARGET.match(target) or ".." in target:
        raise ConfigError("portal.broker_ssh_target must be root@ followed by a DNS hostname")
    for key in ("ssh_private_key", "known_hosts", "client_path"):
        _absolute_path(portal[key], "portal." + key)

    app = _object(config["app"], "app")
    _exact_keys(app, ("name", "description", "support_url", "restore_directory_name"), "app")
    _string(app["name"], "app.name", 120)
    _string(app["description"], "app.description", 300)
    support = _string(app["support_url"], "app.support_url", 500)
    parsed = urlparse(support)
    if not support.startswith("https://") or parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigError("app.support_url must be an HTTPS URL without credentials, query, or fragment")
    restore_name = _string(app["restore_directory_name"], "app.restore_directory_name", 128)
    if restore_name in (".", "..") or "/" in restore_name or not _PATH_COMPONENT.match(restore_name):
        raise ConfigError("app.restore_directory_name must be one safe path component")
    return config


def load(path=None):
    path = path or DEFAULT_CONFIG_PATH
    try:
        with open(path, "r", encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, ValueError) as error:
        raise ConfigError("unable to load site configuration: %s" % error)
    return validate(config)
