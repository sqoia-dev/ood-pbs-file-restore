# Deployment guide

This guide is written for an administrator deploying the application from this repository alone. All hostnames, accounts, datastore names, and keys below are synthetic placeholders.

## 1. Confirm that the supported model fits

The current implementation supports this topology:

    Open OnDemand portal(s)
      |  sudo to the fixed portal client
      |  SSH with a pinned host key and forced command
      v
    broker/storage host with root access to /home
      |  HTTPS API with a read-only token
      v
    Proxmox Backup Server

The broker and portals must resolve the same Unix identities. For each participating account, the canonical NSS home must be exactly `/home/<username>`. The broker must see the same home filesystem where restored content belongs.

The PBS layout must be one shared `host` backup group whose backup ID is configured by `PBS_BACKUP_ID`. Every completed snapshot must contain:

- `root.pxar.didx`, with user homes stored as `/<archive>/<username>/...`
- `catalog.pcat1.didx`

PBS namespaces, per-user backup groups, alternate home roots, and alternate archive layouts are not implemented by this release. Do not deploy it unchanged for those layouts.

## 2. Prepare and validate non-secret site configuration

Copy `config/site.example.json` to a protected change-controlled working file. Set the portal broker target, installed paths, supported shared-home archive names, home/staging roots, retention window, application text, and support URL there rather than editing Python source:

    cp config/site.example.json site.json
    python3 tools/validate_config.py site.json
    python3 install.py --role broker --config site.json --dry-run
    python3 install.py --role portal --config site.json --dry-run

`config/site.schema.json` documents the format. Runtime validation is stricter than syntax alone: unknown fields, an unsupported deployment model, SSH option injection, unsafe path components, and out-of-range values fail closed.

This release accepts only `shared-home-v1` with PBS backup type `host`. Changing a field does not add support for another backup layout. Archive names, the home root, restore directory, and broker target remain security-sensitive and require the confinement checks in section 8.

Pin the source before installation. For the upstream release shown by the existing live evidence:

    git clone https://github.com/NessieCanCode/ood-pbs-file-restore.git \
      /root/ood-pbs-file-restore-src
    cd /root/ood-pbs-file-restore-src
    git checkout v1.0.2

For a reviewed Sqoia Labs product commit, substitute its exact immutable commit. Verify the commit/tree and source according to local supply-chain policy; do not assume the recommended `sqoia-dev/ood-pbs-file-restore` path exists until it has been separately published.

## 3. Prepare a dedicated PBS token

Create a service user and a separate API token in PBS. Grant the token only the read privilege required to list snapshots, read catalogs, and download pxar content from the selected datastore. Do not reuse a backup-writer or administrator token.

PBS command syntax and token privilege-separation behavior vary by release, so use the user-management procedure for the installed PBS version:

- [PBS user management](https://pbs.proxmox.com/docs/user-management.html)
- [PBS access control](https://pbs.proxmox.com/docs/user-management.html#access-control)

At minimum, verify in the PBS UI or CLI that the token:

1. Can audit/read the selected datastore.
2. Cannot modify, prune, delete, or administer backups.
3. Cannot read unrelated datastores unless that access is intentionally required.

Retain the token secret only long enough to install the broker environment file. Never place it on an Open OnDemand portal.

## 4. Install and configure the broker

Perform this section as root on the broker/storage host after reviewing the dry run.

    python3 install.py --role broker --config site.json

The idempotent installer places the broker and shared validator in protected paths, installs the non-secret site JSON as `/etc/ood-pbs-file-restore/site.json`, and enforces mode `0700` on the configured staging directory. It never reads or writes credentials. A `DESTDIR` may be used to stage the exact file layout for packaging review without host mutation.

Create the broker-only secret file at the exact `broker.secret_env_file` path from `site.json`, root-owned mode `0600`:

    install -d -o root -g root -m 0755 /etc/ood-pbs-file-restore
    install -o root -g root -m 0600 \
      examples/ood-pbs-file-restore.env.example \
      /etc/ood-pbs-file-restore/broker.env
    editor /etc/ood-pbs-file-restore/broker.env

Set the four required `PBS_*` values. The broker requires an HTTPS API root with no credentials, query, fragment, or trailing slash. Do not prefix lines with `export`. Confirm file protection without printing contents:

    stat -c '%U:%G %a %n' /etc/ood-pbs-file-restore/broker.env

The expected result is `root:root 600`. Use a dedicated read-only PBS token scoped to the intended datastore or narrower supported scope.

Install any private CA into the operating-system trust store and verify TLS/DNS normally. Never disable certificate validation. Prepare a canary whose canonical NSS home is exactly `<broker.home_root>/<username>`, with matching identity across portal and broker. The broker must directly see that filesystem.

## 5. Create the forced-command SSH boundary

### 5.1 Generate one dedicated key on each portal

Perform this as root on each Open OnDemand portal:

    install -d -o root -g root -m 0700 /etc/ood-pbs-restore
    ssh-keygen -t ed25519 \
      -f /etc/ood-pbs-restore/broker_ed25519 \
      -N '' \
      -C 'ood-pbs-restore@ood.example.edu'
    chmod 0600 /etc/ood-pbs-restore/broker_ed25519
    chmod 0644 /etc/ood-pbs-restore/broker_ed25519.pub

Do not reuse a cluster administrator key. For multiple portals, distinct keys make revocation and auditing clearer.

### 5.2 Pin the broker host key

Obtain the broker's SSH host-key fingerprint through a trusted administrative channel. A network scan alone does not establish trust.

Collect the candidate key:

    ssh-keyscan -t ed25519 storage.example.edu > /tmp/storage.example.edu.known_hosts
    ssh-keygen -lf /tmp/storage.example.edu.known_hosts

Compare the displayed fingerprint with the independently verified value. Only after they match:

    install -o root -g root -m 0644 \
      /tmp/storage.example.edu.known_hosts \
      /etc/ood-pbs-restore/known_hosts

### 5.3 Authorize only the broker command

On `storage.example.edu`, append the portal public key to root's authorized keys as one physical line:

    restrict,command="/usr/local/sbin/pbs-restore-broker" ssh-ed25519 REPLACE_WITH_PUBLIC_KEY ood-pbs-restore@ood.example.edu

`restrict` disables PTY allocation and forwarding on supported OpenSSH releases. The explicit forced command prevents a client-supplied command or shell from running. Keep both controls.

The broker currently assumes `root@storage.example.edu`. The SSH server must therefore permit public-key root login for this restricted key while still prohibiting password-based root login. If site policy prohibits root SSH entirely, redesign and review the privileged boundary before deployment; changing only the username is not sufficient.

### 5.4 Prove that the key is constrained

From the portal, an interactive attempt must not produce a shell:

    ssh -i /etc/ood-pbs-restore/broker_ed25519 \
      -o UserKnownHostsFile=/etc/ood-pbs-restore/known_hosts \
      -o StrictHostKeyChecking=yes root@storage.example.edu

The forced broker may return an invalid-request JSON response because no request was supplied. That is acceptable. Receiving a shell is a deployment failure.

## 6. Install the portal client, application, and sudo rule

Perform this on every portal after independently provisioning the dedicated SSH private key and pinned `known_hosts` at the paths in `site.json`:

    python3 install.py --role portal --config site.json --dry-run
    python3 install.py --role portal --config site.json
    visudo -cf /etc/sudoers.d/ood-pbs-file-restore

The installer derives the exact sudo command from the validated `portal.client_path`; it does not use wildcards or grant Python, SSH, shells, or editors. It installs the app under `/var/www/ood/apps/sys/pbs-file-restore` and never reads or writes the SSH key or host-key pin.

Confirm ownership/modes and prove the dedicated key receives only the forced broker command. Receiving a shell, PTY, forwarding, or an arbitrary command is a deployment failure. If portal access is broader than restore eligibility, replace the sudoers subject with a reviewed local group/user alias while preserving the exact command.

As the non-privileged canary:

    printf '%s\n' '{"action":"snapshots"}' | \
      sudo -n /usr/local/sbin/pbs-restore-client | python3 -m json.tool

Never print the broker environment while troubleshooting.

## 7. Activate the Open OnDemand application

The portal requires Python 3.9 or later and PyYAML 5.4 or later. Prefer the system package or a site-managed environment explicitly selected by Passenger:

    python3 -c 'import sys, yaml; print(sys.version); print(yaml.__version__)'

The application reads the active dashboard Sprockets manifest. Confirm the configured Open OnDemand release provides one:

    ls /var/www/ood/apps/sys/dashboard/public/assets/.sprockets-manifest-*.json

If the dashboard layout differs, treat that platform as unproven and review an adapter before enabling users. To refresh Passenger, create/touch `tmp/restart.txt` in the installed app and restart only the canary PUN through the site's supported procedure.

## 8. Acceptance testing

### 8.1 Static checks

From the checkout:

    python3 -m py_compile app.py passenger_wsgi.py client.py broker.py site_config.py validate.py install.py tools/validate_config.py
    python3 tools/validate_config.py site.json
    python3 -m unittest discover -s tests -v
    python3 -c 'import yaml; yaml.safe_load(open("manifest.yml"))'
    visudo -cf /etc/sudoers.d/ood-pbs-restore

### 8.2 Browser checks

Sign in as the canary and open `/pun/sys/pbs-file-restore`. Verify:

1. The page loads without a Python or Passenger error.
2. Available snapshot dates appear.
3. Browsing never shows another user's top-level archive.
4. A small file restores below the configured home and restore directory.
5. The original live file is not overwritten.
6. A second restore creates a new job directory.

### 8.3 Confinement checks

The included `validate.py` performs a real small-file restore. It refuses to run unless explicitly enabled. Review it first, ensure the canary has a backed-up file of 1 MiB or less, then run as the canary:

    PBS_RESTORE_ENABLE_LIVE_TEST=YES python3 validate.py

To include a known directory restore:

    PBS_RESTORE_ENABLE_LIVE_TEST=YES \
    PBS_RESTORE_TEST_DIRECTORY='synthetic-test-directory' \
      python3 validate.py

Inspect ownership and confinement after the test:

    find /home/canary/.pbs-restores -xdev -printf '%u:%g %m %p\n'

Do not run the live validator as root or against a real user's account.

## 9. Logs and troubleshooting

The broker writes to the local syslog socket with identifier `pbs-restore-broker`. Depending on the distribution, inspect:

    journalctl -t pbs-restore-broker

Common failures:

| Symptom | Check |
| --- | --- |
| App reports restore service unavailable | Sudo rule, installed client, SSH key modes, pinned host key, forced command |
| Broker reports incomplete environment | Four required `PBS_*` values, file syntax, no `export`, mode `0600` |
| PBS request fails | DNS, TCP 8007, TLS trust, API URL, token ID/secret, datastore ACL |
| No snapshots appear | Backup type/ID, configured window, and presence of both required archive files |
| Authenticated account rejected | Matching NSS, UID at least 1000, allowed username syntax, exact `/home/<username>` |
| Directory restore fails | PBS returned ZIP structure, free staging space, symlink/traversal rejection |
| Page cannot find dashboard assets | Open OnDemand version or nonstandard dashboard asset location |

Logs intentionally avoid token secrets and restored contents. Preserve that property when adding diagnostics.

## 10. Upgrades

1. Record exact current commit/tree, installed file checksums, and validated configuration; preserve protected copies outside installer targets.
2. Review `CHANGELOG.md`, configuration schema changes, and every security-sensitive diff.
3. Validate and dry-run both roles from one exact new commit.
4. Test on a staging portal or one synthetic canary when available.
5. Install broker, client, and app from that same commit; never mix versions.
6. Validate sudoers, refresh only the canary PUN, and repeat snapshot, browse, small-restore, confinement, ownership, no-overwrite, and log checks.
7. Deploy serially to remaining portals only after the canary passes.

## 11. Disable, uninstall, or roll back

To disable access, remove/disable the system app, remove the sudoers rule and run `visudo -c`, remove the portal key line from broker `authorized_keys`, and revoke the dedicated PBS token. Preserve logs and user restores according to site policy.

The bounded uninstaller removes only managed runtime files:

    python3 install.py --role portal --config site.json --action uninstall --dry-run
    python3 install.py --role broker --config site.json --action uninstall --dry-run

Review, then rerun without `--dry-run`. It deliberately preserves site configuration, credentials, SSH trust, logs, and restored data for separate reviewed disposition.

For rollback, reinstall all runtime files from the exact preserved previous commit and its compatible validated configuration. Revalidate sudoers, restart the canary PUN, and repeat acceptance testing. Do not automatically delete staging evidence or users' restore trees.

## 12. Information safe to include in support requests

Generally safe after review:

- Application version and Open OnDemand/Python/PBS versions
- Sanitized error text
- Whether failure occurs at web app, sudo client, SSH, broker, or PBS stage
- Synthetic host roles such as `portal`, `broker`, and `PBS`
- File ownership and modes without real usernames or internal paths

Never publish:

- PBS token IDs or secrets
- Private/public SSH key material or full host keys
- Internal hostnames, IP addresses, inventories, or certificates
- Production usernames, UIDs, backup paths, catalog responses, or contents
- The configured broker secret environment file

Before production enablement, complete [SECURITY-CHECKLIST.md](SECURITY-CHECKLIST.md).
