# Portability and Community Release Notes

## Existing-project preflight

As of 2026-07-24, a GitHub repository search found no existing project
combining Open OnDemand with a self-service Proxmox Backup Server file restore
application.

Open OnDemand's official documentation says community Passenger apps should be
hosted in a well-commented GitHub repository, document dependencies and
cluster-specific instructions, and then be announced on the Open OnDemand
Discourse forum:

- [Install Passenger Apps](https://osc.github.io/ood-documentation/latest/install-passenger-apps.html)
- [Passenger app tutorials](https://osc.github.io/ood-documentation/latest/tutorials/tutorials-passenger-apps.html)
- [Open OnDemand Discourse](https://discourse.openondemand.org/)

PBS permission semantics are documented in:

- [Proxmox Backup Server user management](https://pbs.proxmox.com/docs/user-management.html)

## Recommendation

Maintain this as an independent project with deployment-specific configuration
kept outside the application source.

A suitable repository name is:

```text
ood-pbs-file-restore
```

Recommended repository structure:

```text
ood-pbs-file-restore/
  app/
    manifest.yml
    passenger_wsgi.py
    app.py
  broker/
    broker.py
    client.py
    sudoers
  ansible/
    roles/ood_pbs_restore/
    playbooks/example.yml
    inventory/example/
  config/
    broker.example.json
  tests/
    unit/
    integration/
  docs/
    architecture.md
    backup-layouts.md
    security.md
    operations.md
    migration.md
  CHANGELOG.md
  CONTRIBUTING.md
  LICENSE
  README.md
  SECURITY.md
```

## Deployment configuration

Keep these values in root-owned deployment configuration or environment files:

| Setting | Typical value/type | Portable form |
| --- | --- | --- |
| PBS API host and port | institution hostname | `pbs_api_url` |
| PBS datastore | one datastore | `pbs_datastore` |
| API token identifier | current owner token | `pbs_auth_id` |
| Snapshot type | `host` | `pbs_backup_type` |
| Snapshot ID | storage host name | `pbs_backup_id` |
| Archive names | `root.pxar.didx` or `root.mpxar.didx` plus `root.ppxar.didx` | `pbs_archive_layouts` |
| Archive user prefix | first component under `/home` | `archive_user_path_template` |
| Retention shown | 31 days | `snapshot_max_age_days` |
| Home root | `/home` | `home_root` |
| Restore directory | `.pbs-restores` | `restore_directory_name` |
| Broker host | storage host | inventory group/variable |
| Broker SSH account | root forced command | configurable service design |
| Portal hosts | three OOD nodes | inventory group |
| OOD system app root | `/var/www/ood/apps/sys` | `ood_apps_root` |
| Brand logo/navigation | institution-specific | local OOD dashboard assets |
| Canary user | site operator | `restore_canary_user` |

The broker should read a root-owned JSON/TOML configuration file rather than
compile institutional values into Python.

## Backup-layout adapters

Institutions commonly organize PBS backups differently. A public release
should define adapters rather than assume one layout.

### Shared-home archive

One snapshot group contains `/home`, with users as first-level paths:

```text
host/storage-server -> root.pxar.didx/<username>/...
                    -> root.mpxar.didx + root.ppxar.didx
```

The broker detects the format independently for each snapshot, allowing legacy
and split snapshots to coexist through a retention transition. Split requests
send the metadata archive as PBS's `archive-name` parameter while preserving
the same authenticated-user path boundary.

### Per-user snapshot groups

Each user has a separate group or namespace:

```text
host/<username> -> home.pxar.didx/...
```

This model can reduce catalog scope, but group mapping must be derived from the
authenticated identity and validated against an administrator-defined
template.

### Multiple home roots

Some clusters use project homes, hashed paths, or multiple filesystems. Support
should use an administrator-defined identity-to-archive mapping function, not
arbitrary user input.

## Security requirements for a public release

A release should not weaken these invariants:

1. The browser cannot select an effective username.
2. The Passenger process cannot read PBS credentials.
3. The broker independently resolves and validates the Unix account.
4. Every catalog and download path is derived beneath that identity.
5. Every returned PBS path is revalidated.
6. Restore destination traversal uses directory-relative descriptors and
   refuses symlinks.
7. The broker SSH key has a forced command and no forwarding.
8. PBS TLS is validated with a hostname or explicit CA trust.
9. Existing files are never overwritten by default.
10. Audit records contain identity, snapshot, action, result, and destination,
    but never token secrets or restored content.

Before public release, add:

- unit tests for tokens, path components, ZIP traversal, symlinks, NSS homes,
  and snapshot validation;
- integration tests against a disposable PBS datastore;
- concurrent multi-user restore tests;
- size, time, and request limits;
- a documented token-rotation procedure;
- a security contact and private vulnerability-reporting process; and
- a threat-model review by another HPC institution.

## Credentials

Do not publish:

- Ansible Vault files;
- API token identifiers tied to production;
- API token secrets;
- SSH private keys;
- production inventory or internal IP addresses;
- PBS certificates or internal CA material; or
- real user names, restored paths, screenshots, or logs.

Publish example values and a script or Ansible task that creates local
credentials during deployment.

The recommended PBS credential is a dedicated token with `Datastore.Read`
limited to the required datastore or namespace. This permission can read
arbitrary contents in that scope, so the broker remains a sensitive service.

## Branding

The portable application should default to the site's installed Open OnDemand
dashboard assets and public logo. It should not ship institution-specific logos.

Provide optional variables for:

- application name and description;
- icon;
- support URL;
- restore-location help text; and
- additional navigation links.

## Licensing and ownership

The public project is copyright Sqoia Labs and is licensed under
AGPL-3.0-or-later. Organizations that need different terms may contact Sqoia
Labs LLC regarding a separate commercial license.

## Release stages

### Stage 1: Internal portable package

- Extract configuration.
- Remove deployment-specific values and canary identity from application code.
- Convert deployment into an Ansible role.
- Add unit tests and example inventory.
- Test a clean installation in a disposable OOD/PBS environment.

### Stage 2: Trusted inter-institution pilot

- Share a private repository with one or two HPC institutions.
- Ask each site to test a different backup layout.
- Conduct a joint threat-model and usability review.
- Collect compatibility information for OOD, Passenger, Python, PBS, and
  operating systems.

### Stage 3: Public release

- Confirm the release contains no production credentials or user data.
- Publish tagged version `v0.1.0`.
- Provide checksums or signed releases.
- Publish screenshots containing only synthetic users and files.
- Announce the project on Open OnDemand Discourse.
- Request addition to the official community Passenger-app documentation.

### Stage 4: Maintained project

- Use semantic versioning and a changelog.
- Publish a support policy and compatibility matrix.
- Add CI, linting, security scanning, and release artifacts.
- Track PBS and OOD API changes.

## Proposed collaboration discussion

When approaching another institution, ask:

1. How are home backups grouped and named in PBS?
2. Is the archive encrypted client-side?
3. Where can a privileged broker run?
4. How is Unix identity synchronized between OOD and storage?
5. What restore destination and retention policy do users expect?
6. What quotas and audit retention are required?
7. Which OOD and PBS versions must be supported?
8. Can the site provide a disposable PBS snapshot for integration testing?

The best first partner is an institution already using Open OnDemand,
shared Unix identities, and unencrypted pxar backups, but with a backup layout
different enough to force a useful configuration boundary.
