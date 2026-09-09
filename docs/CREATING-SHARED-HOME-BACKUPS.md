# Creating shared-home backups

This page is optional. The File Restore application itself does **not** require `proxmox-backup-client`; the broker reads existing snapshots through the PBS HTTPS API.

Use this procedure only when the site also needs to produce the shared-home backup layout expected by the application.

## Supported producer layout

The example creates a deterministic PBS group and archive:

    host/storage-server
      root.pxar.didx/
        alice/...
        bob/...
      catalog.pcat1.didx

The corresponding restore-broker values are:

    PBS_BACKUP_ID='storage-server'
    ARCHIVE_NAME = 'root.pxar.didx'

The source must be `/home` with user directories immediately below it. If the backup source, archive structure, or group model differs, the restore broker needs a reviewed mapping change.

## Runtime dependency boundary

| Component | Needs the static client? |
| --- | --- |
| Open OnDemand Passenger app | No |
| Portal-side sudo client | No |
| Restore broker | No |
| Host creating PBS snapshots | Yes |
| PBS server | Provides the server APIs |

The backup writer and restore broker must use separate PBS tokens and separate environment files:

| Purpose | Suggested file | Permission |
| --- | --- | --- |
| Backup writer | `/etc/pbs-home-backup-writer.env` | Datastore backup/write only where required |
| Restore broker | configured `broker.secret_env_file` | Read-only access to the required datastore |

Never reuse the writer token for user-facing restores.

## Live-tested compatibility

The reference workflow was checked live on 2026-08-07 with:

| Role | Live-tested software |
| --- | --- |
| Open OnDemand portal | Rocky Linux 9.8; Open OnDemand 4.2.3; Python 3.9.25; PyYAML 5.4.1; OpenSSH 9.9p1 |
| Broker and backup source | Red Hat Enterprise Linux 8.10; Python 3.6.8; OpenSSH 8.0p1 |
| Static client canary | Red Hat Enterprise Linux 8.9/8.10 and Rocky Linux 9.8, x86_64; `proxmox-backup-client` 4.2.3 |
| PBS server | Debian 13; PBS runtime 4.2.2 with server package 4.2.5-1 installed |

The public application recommends a maintained Python 3.9-or-newer runtime even though the broker path is currently live on Python 3.6.8. The official x86_64 client is statically linked. RHEL 8.9/8.10 and Rocky Linux 9.8 were tested directly; other Rocky Linux and AlmaLinux 8/9 combinations remain compatibility candidates and require a canary before production.

## Install the official static client on Enterprise Linux

Proxmox publishes `proxmox-backup-client-static` as a Debian package, not an RPM. Do not install the Debian package database on Rocky/RHEL. Instead, extract the static binary from the official package and verify both package and binary SHA-256 values.

The repository includes `examples/install-proxmox-backup-client-static.sh`. It:

- requires x86_64 and root;
- refuses an unverified package or extracted binary;
- installs a versioned root-owned executable;
- uses `/usr/local/sbin/proxmox-backup-client` as a symlink;
- refuses to overwrite an existing non-symlink canonical path; and
- does not install credentials or a schedule.

Install prerequisites on a Rocky/RHEL-family host:

    dnf install -y binutils coreutils curl tar xz zstd

### Tested 4.2.3 pin

The live-tested client came from the official Proxmox trixie client repository:

    Version: 4.2.3
    Package: proxmox-backup-client-static_4.2.3-1_amd64.deb
    Package SHA-256: 05ac991e89a6e899d3f236c15d13ba736221a44f138314f42cac22867ae46d55
    Extracted binary SHA-256: d2d7b8454ebddea8ebfb73531301d644f6214d2e7e22424fe16b4729d0e42573

Before using these values, independently verify the package checksum against the current Proxmox GPG-signed repository metadata. A copied checksum from this document is not a substitute for repository-signature verification.

Run the installer from a reviewed checkout:

    sudo examples/install-proxmox-backup-client-static.sh \
      4.2.3 \
      'http://download.proxmox.com/debian/pbs-client/dists/trixie/main/binary-amd64/proxmox-backup-client-static_4.2.3-1_amd64.deb' \
      '05ac991e89a6e899d3f236c15d13ba736221a44f138314f42cac22867ae46d55' \
      'd2d7b8454ebddea8ebfb73531301d644f6214d2e7e22424fe16b4729d0e42573'

The public download endpoint used for this tested package is HTTP. Integrity depends on validating the SHA-256 from GPG-signed Proxmox metadata before installation. If Proxmox provides a correctly authenticated HTTPS endpoint for a newer release, prefer it.

Verify:

    readlink -f /usr/local/sbin/proxmox-backup-client
    /usr/local/sbin/proxmox-backup-client version
    sha256sum /usr/local/sbin/proxmox-backup-client.official-4.2.3

Do not silently replace a running client. Confirm no backup process is active, test on one canary host, preserve the previous versioned binary, and validate a full backup before wider rollout.

## Create the backup-writer token

Create a dedicated PBS token permitted to create snapshots in only the intended datastore or namespace. It needs backup/write rights and is therefore more privileged than the restore token.

Follow the user and ACL documentation for the installed PBS release:

- [PBS user management](https://pbs.proxmox.com/docs/user-management.html)
- [PBS access control](https://pbs.proxmox.com/docs/user-management.html#access-control)

Confirm that the writer token cannot administer PBS or access unrelated datastores. Store its secret only on the backup-source host.

## Install the backup command

Install the sanitized examples:

    install -o root -g root -m 0700 \
      examples/pbs-backup-home /usr/local/sbin/pbs-backup-home
    install -o root -g root -m 0600 \
      examples/pbs-home-backup-writer.env.example \
      /etc/pbs-home-backup-writer.env
    editor /etc/pbs-home-backup-writer.env

Set:

- the backup-writer token secret;
- the independently verified PBS TLS fingerprint;
- the PBS repository in `user@realm!token@server:datastore` form;
- archive `root.pxar`;
- source `/home`; and
- a stable backup ID that exactly matches the restore broker's `PBS_BACKUP_ID`.

The wrapper uses `flock` to reject overlapping runs and writes `/var/log/pbs-home-backup.log`.

## Run a canary backup

Check source and destination capacity, ensure no backup is already running, then:

    /usr/local/sbin/pbs-backup-home
    tail -n 50 /var/log/pbs-home-backup.log

On PBS, verify that the completed `host/<backup-id>` snapshot includes both `root.pxar.didx` and `catalog.pcat1.didx`. Confirm that catalog browsing shows synthetic canary paths before connecting the restore application.

## Schedule

After a successful manual canary, create a root cron entry using a locally approved time. For example:

    MAILTO=storage-operations@example.edu
    30 3 * * * root /usr/local/sbin/pbs-backup-home

Install cron files as `root:root` mode `0644`. Monitor the log and PBS snapshots after the first scheduled run. Use a systemd timer instead if that is the site's standard.

## Upgrade the client

1. Read Proxmox release notes and obtain the new official static package.
2. Verify repository signatures and record the new package SHA-256.
3. Extract the candidate separately and record its binary SHA-256.
4. Confirm no `/home` backup is active.
5. Run the installer on one canary host with the new explicit values.
6. Verify `version`, run a backup, inspect logs, and verify the snapshot.
7. Retain the previous versioned binary for rollback.

## Uninstall or roll back

Stop the schedule first. Point the canonical symlink back to a preserved versioned binary and re-run `version`. To retire backup production, revoke the writer token and remove the schedule, writer environment, and wrapper according to local retention policy.

Do not remove the read-only restore token merely because backup production moves to another host. Do not delete PBS snapshots or users' restored data as part of a client rollback.
