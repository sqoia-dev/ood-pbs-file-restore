# PBS File Restore for Open OnDemand by Sqoia Labs

A security-bound Open OnDemand companion application for authenticated users to browse recent Proxmox Backup Server (PBS) shared-home snapshots and restore files or directories into a new, non-overwriting destination in their own home.

## What it provides

- Date-based snapshot selection and catalog browsing in Open OnDemand.
- Effective-user confinement at the Passenger, sudo client, SSH, broker, archive, and filesystem boundaries.
- Root-only PBS credentials on the broker; the portal and user Passenger process never receive them.
- Non-overwriting restores beneath a configurable directory (default `~/.pbs-restores`).
- Revalidation of returned PBS paths, descriptor-relative destination traversal, and audit logging.
- One validated non-secret site configuration shared by portal, application, and broker roles.
- Idempotent role installation with dry-run and `DESTDIR` staging support.

```text
Browser -> user Passenger app -> constrained sudo client
        -> pinned forced-command SSH -> privileged storage broker
        -> PBS HTTPS API -> /home/<user>/.pbs-restores/<date>/<job-id>/
```

## Evidence boundary

The reference workflow was checked live on **2026-08-07** with:

| Role | Live-tested versions |
| --- | --- |
| Open OnDemand portal | Rocky Linux 9.8, Open OnDemand 4.2.3, Python 3.9.25, PyYAML 5.4.1, OpenSSH 9.9p1 |
| Broker and backup source | Red Hat Enterprise Linux 8.10, Python 3.6.8, OpenSSH 8.0p1 |
| Static backup client (optional producer only) | `proxmox-backup-client` 4.2.3 on RHEL 8.9/8.10 and Rocky Linux 9.8 x86_64 |
| PBS server | Debian 13, PBS runtime 4.2.2 with server package 4.2.5-1 installed |

Python 3.6.8 describes the live broker evidence but is end-of-life; new deployments should use Python 3.9 or later. Comparable platforms are candidates, not supported claims, until a site canary passes.

Only the `shared-home-v1` model is implemented: one `host` backup group, a pxar archive containing one top-level directory per username, matching Unix identities, and a broker with direct access to the managed home filesystem. PBS namespaces, per-user backup groups, alternate mappings, and arbitrary home layouts are not implemented.

## Safe configuration

Copy and review the non-secret example:

```bash
cp config/site.example.json site.json
python3 tools/validate_config.py site.json
```

`config/site.schema.json` documents the format. Runtime validation rejects unknown settings, unsupported deployment models, non-HTTPS PBS transport, unsafe paths, SSH option injection, and invalid restore directory names.

The PBS token secret remains separate in the broker-only file named by `broker.secret_env_file` (default `/etc/ood-pbs-file-restore/broker.env`, root-owned mode `0600`):

```bash
PBS_API_ROOT='https://pbs.example.edu:8007/api2/json/admin/datastore/DATASTORE'
PBS_AUTH_ID='restore@pbs!openondemand'
PBS_PASSWORD='REPLACE_WITH_TOKEN_SECRET'
PBS_BACKUP_ID='storage-server'
```

Never commit that file, pass secrets to `install.py`, place the token on a portal, or reuse a backup-writer/admin token.

## Install and validate

Read [the deployment guide](docs/DEPLOYMENT.md) and complete the [security checklist](docs/SECURITY-CHECKLIST.md). Preview both roles without root or host mutation:

```bash
python3 install.py --role broker --config site.json --dry-run
python3 install.py --role portal --config site.json --dry-run
```

Stage a clean package tree for inspection or packaging tests:

```bash
python3 install.py --role broker --config site.json --destdir "$PWD/stage-broker"
python3 install.py --role portal --config site.json --destdir "$PWD/stage-portal"
```

A real install requires root-owned protected source, independently provisioned credentials/SSH trust, `visudo` validation, canary acceptance tests, and local change control. The installer never reads or writes the PBS secret, SSH private key, or `known_hosts`.

Local checks:

```bash
python3 -m py_compile app.py passenger_wsgi.py client.py broker.py site_config.py validate.py install.py tools/validate_config.py
python3 tools/validate_config.py config/site.example.json
python3 -m unittest discover -s tests -v
python3 install.py --role portal --config config/site.example.json --dry-run
python3 install.py --role broker --config config/site.example.json --dry-run
```

The live validator is intentionally separate and refuses to run unless explicitly enabled against a non-privileged synthetic canary. Do not run it with production credentials from a development checkout.

## Operations

- **Upgrade:** validate the new config and source, preserve the exact previous release/config, install broker/client/app from one reviewed commit, restart only the canary PUN, and rerun confinement and restore checks before serial rollout.
- **Rollback:** reinstall all three runtime components from the exact preserved previous release and repeat acceptance testing. Do not mix versions.
- **Uninstall/disable:** use `install.py --action uninstall` for each role, then separately remove the forced-key authorization, validate sudoers, and revoke the dedicated PBS token. Configuration, credentials, logs, and user restore trees are deliberately preserved for reviewed disposition.
- **Secrets:** rotate the broker token and portal key through site-managed protected workflows; never include values in logs or support captures.

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for exact security gates and [docs/CREATING-SHARED-HOME-BACKUPS.md](docs/CREATING-SHARED-HOME-BACKUPS.md) for the optional, separately privileged backup producer.

## Limitations

- Shared-home archive layout only.
- Synchronous restores; no built-in per-user byte quotas or rate limits.
- Individual symbolic links must be restored through their parent directory.
- Standalone restored files are reduced to mode `0600`.
- Every deployment requires an institution-specific privileged-boundary and capacity review.
- Integration, concurrent multi-user, and disposable-PBS tests are not included in this local productization; live canary validation remains required.

## Support, source, and license

Official source and issue tracker: <https://github.com/sqoia-dev/ood-pbs-file-restore>.

Copyright © 2026 Sqoia Labs. Licensed under the [GNU Affero General Public License v3.0 or later](LICENSE), with the notices in [NOTICE](NOTICE). Network operators must meet the AGPL source-availability obligations for deployed modifications. The software is provided without warranty as described in the license.
