# Production security checklist

Complete this checklist for each deployment. The broker is a privileged
security boundary, not an ordinary web application.

## Identity and filesystem

- [ ] Portal and broker resolve the same username, UID, primary GID, and home.
- [ ] Managed homes are exactly `/home/<username>` and owned by that user.
- [ ] Intended users have UID 1000 or greater and names matching
  `[a-z_][a-z0-9_-]{0,31}`.
- [ ] The broker has direct access to the real home filesystem; it is not
  restoring into an unrelated local `/home`.
- [ ] `/home/.pbs-restore-staging` is root-owned with mode `0700`.

## PBS

- [ ] A dedicated API token is used only by this service.
- [ ] The token has read access only to the required datastore or narrower
  supported scope.
- [ ] The token cannot modify, prune, delete, or administer backups.
- [ ] PBS TLS uses a trusted certificate or an installed private CA; certificate
  verification has not been disabled.
- [ ] The configured broker secret environment file is root-owned, mode `0600`, and excluded from
  configuration bundles and support captures.

## SSH broker boundary

- [ ] The portal uses a dedicated key, not an administrator's normal SSH key.
- [ ] The broker host key was verified out of band before it was pinned.
- [ ] The public key is restricted with both `restrict` and the exact forced
  command `/usr/local/sbin/pbs-restore-broker`.
- [ ] A shell, PTY, forwarding, agent forwarding, and arbitrary commands fail.
- [ ] The private key directory is root-only and the key is mode `0600`.

## Portal and sudo

- [ ] `client.py` points to the intended broker FQDN and pinned key files.
- [ ] `site.json` passed runtime validation; its deployment model remains
  `shared-home-v1`, and users cannot modify it.
- [ ] `/usr/local/sbin/pbs-restore-client` is root-owned and not group-writable.
- [ ] `/etc/sudoers.d/ood-pbs-restore` passes `visudo -cf`.
- [ ] Sudo grants only the exact client path; it does not grant Python, SSH,
  shells, editors, or wildcard arguments.
- [ ] The application checkout is root-owned and not writable by portal users.

## Acceptance and operations

- [ ] Snapshot listing and catalog browsing pass with a synthetic canary user.
- [ ] A request containing another `user` value cannot change effective identity.
- [ ] Absolute-path and `..` tokens are rejected.
- [ ] A small file restore lands only below the configured restore tree.
- [ ] Restored ownership, modes, and no-overwrite behavior were inspected.
- [ ] Rejected and successful operations appear in broker logs without secrets.
- [ ] Token rotation, key revocation, incident response, restore-capacity
  monitoring, and service ownership have named procedures.

Do not attach environment files, private keys, tokens, production usernames,
catalog responses, backup contents, or internal host inventories to public
issues.
