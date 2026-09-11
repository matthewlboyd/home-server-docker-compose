# Backups

**Already configured? Use [Run and check a backup](#run-and-check-a-backup).**
The setup below is for a new or rebuilt server. Keep an existing server's
working storage credentials and timer settings. Older jobs using
`BASE_BACKUP_COMMAND` need the [one-time migration](#migrate-an-older-backup-job)
before installing the current script.

Backups include `/opt/homelab`, `/etc` except `/etc/restic`, and the selected SSH
authorized-keys file. Pi-hole stops while restic reads and uploads the snapshot,
then restarts. Clients can lose DNS during that pause; a longer upload means a
longer pause. The host must resolve DNS without Pi-hole. See
[Installation](INSTALL.md#1-prepare-the-host) if it cannot.

NPM and PeaNUT also pause for consistency. One script, `backup-server`, manages
these containers and restarts them before retention and pruning. Run manual
backups when this interruption is acceptable.

When [Project Zomboid](ZOMBOID.md) is installed, the backup job saves and stops
the game first. It restores the game only if it was running before the backup.
Both the world and downloaded server files live under `/opt/homelab/zomboid`.
Players disconnect during the backup window; ordinary restarts do not update
the game build.

Before stopping anything, the script checks its paths file, storage helper,
Docker's inventory, and each existing web container's state. Missing optional
containers are skipped; inspection failures abort the backup. Only services that were running are
restarted, and a restart failure makes the backup service report failure.

## 1. Recreate the backup configuration

Save these in a **1Password backup recovery item**, available without the server:

| Value | Purpose |
| --- | --- |
| Storage access key ID | Identify the account allowed to use the bucket |
| Storage secret access key | Authenticate to the bucket |
| restic repository password | Decrypt the backups; separate from the storage keys |
| Repository URL | Exact endpoint, bucket, and repository prefix |
| Region | Storage provider's region identifier |

For a new setup, create a private S3-compatible bucket and a key limited to
that bucket. For recovery, use the **existing** values. `/etc/restic` is excluded
from the backup, so do not rely on the server as the only copy of these values.

**Server — repository root:** install the wrapper and configuration examples.
Do this only on a host without an existing backup configuration.

```bash
cd ~/home-server-docker-compose
sudo apt install restic
sudo install -d -m 700 /etc/restic
sudo install -m 600 restic/repository.example /etc/restic/repository
sudo install -m 600 restic/region.example /etc/restic/region
sudo install -m 600 restic/backup-paths.example /etc/restic/backup-paths
sudo install -m 600 restic/excludes.example /etc/restic/excludes
sudo install -m 750 scripts/restic-server /usr/local/sbin/restic-server
```

Edit the three configuration files before running restic:

```bash
sudoedit /etc/restic/repository /etc/restic/region /etc/restic/backup-paths
```

Replace the repository and region examples with real values. In `backup-paths`,
replace `USERNAME` with the server account; remove that line if the file does
not exist. Include any application data stored outside `/opt/homelab`.

Enter the three secrets at hidden prompts. Run this whole block in the same
server **Bash** session; values do not become part of the command history.

```bash
sudo -v
for credential_file in aws-access-key-id aws-secret-access-key password; do
  read -r -s -p "Value for ${credential_file}: " backup_credential
  printf '\n'
  printf '%s\n' "$backup_credential" | sudo tee "/etc/restic/${credential_file}" >/dev/null
  sudo chmod 600 "/etc/restic/${credential_file}"
  unset backup_credential
done
```

Each file contains only its value followed by a newline. Keep `/etc/restic`
owned by root with mode `0700`, and its files root-owned with mode `0600`.

## 2. Test an existing repository

**New repository:** complete the first-setup box below before running these
checks. **Existing repository:** skip initialization and run the checks directly.

**Server:** verify access and repository structure.

```bash
sudo restic-server snapshots
sudo restic-server check
```

The snapshots should belong to the expected server. If access fails, check the
saved credentials and exact repository URL. **Do not run `init` to fix a failed
connection to an existing repository.**

<details>
<summary>First setup only: create a new, empty repository</summary>

After verifying that the configured URL points to the intended new repository,
run this once on the server:

```bash
sudo restic-server init
```

Skip this during recovery. It does not restore or reconnect existing backups.

</details>

## 3. Start nightly backups

**Server — repository root:** install the generic service and timer. This is
the setup for a new or rebuilt host, not a replacement for Bombadil's existing job.

```bash
cd ~/home-server-docker-compose
sudo install -m 750 scripts/backup-server /usr/local/sbin/backup-server
sudo install -m 644 systemd/homelab-backup.service /etc/systemd/system/homelab-backup.service
sudo install -m 644 systemd/homelab-backup.timer /etc/systemd/system/homelab-backup.timer
sudo systemctl daemon-reload
```

The service is now available to Ansible. **During recovery, leave the timer
disabled until the data and services are restored.** Return to
[Recovery](RECOVERY.md) before taking a full backup.

For a new installation, run the backup and file-restore checks below. Once they
pass, enable the timer:

```bash
sudo systemctl enable --now homelab-backup.timer
systemctl list-timers homelab-backup.timer --no-pager
```

The supplied timer runs nightly at 03:30 with up to 15 minutes of random delay.
The script retains seven daily, four weekly, and twelve monthly snapshots.
Bombadil keeps its existing timer and the same retention policy.

[Daily retention](https://restic.readthedocs.io/en/stable/060_forget.html#removing-snapshots-according-to-a-policy)
keeps the latest snapshot for each day, so an earlier snapshot
from that day can be pruned. Recording a rollback snapshot ID does not protect
it; retain a separate restored copy of needed rollback data before another
backup runs.

## Migrate an older backup job

The script does not automatically convert an older helper's hard-coded paths
or storage settings. Complete and review the migration before replacing a
working job:

1. Record the existing service, timer, backup paths, exclusions, and retention.
   Keep its helper and service configuration for rollback.
2. Put the same absolute paths into `/etc/restic/backup-paths`, one per line,
   and the same exclusions into `/etc/restic/excludes`. Keep both root-owned
   with mode `0600`. Preserve the existing storage helper and credentials.
3. Compare a direct restic `backup --dry-run` using the old arguments with one
   using `--files-from` and `--exclude-file`. These commands leave containers
   running and create no snapshot. Confirm the paths and selected parent match.
4. Install the current `backup-server` and configure the service to run it directly,
   with its existing storage helper selected by `RESTIC_COMMAND`. Clear `BASE_BACKUP_COMMAND`,
   preserve game enrollment, and reload systemd. Keep the existing timer.

For Bombadil, the private inventory selects:

```yaml
homelab_backup_unit: bombadil-backup.service
homelab_restic_command: /usr/local/sbin/restic-bombadil
```

The stored paths remain `/opt/homelab`, `/etc`, and the server account's SSH
authorized-keys file; `/etc/restic` stays excluded. `restic-bombadil` keeps the
same repository, credentials, and region. The old `backup-bombadil` file can
remain for rollback but is no longer part of the active job.

Configuration deployment does not prove a complete backup has succeeded.
Check the next scheduled result, or run a manual backup when the DNS pause is
acceptable. A nonempty `BASE_BACKUP_COMMAND` makes the new script fail before
it stops any container.

## Run and check a backup

**Server:** choose the names installed on this host. Keep the same Bash session
for the following commands.

| Host setup | `backup_unit` | `restic_command` |
| --- | --- | --- |
| New setup from this guide | `homelab-backup.service` | `restic-server` |
| Existing Bombadil setup | `bombadil-backup.service` | `restic-bombadil` |

```bash
backup_unit=homelab-backup.service
restic_command=restic-server
```

For existing Bombadil, replace those assignments with:

```bash
backup_unit=bombadil-backup.service
restic_command=restic-bombadil
```

Both service names run `backup-server`. It reads `/etc/restic/backup-paths`
and `/etc/restic/excludes`, then calls the selected restic storage helper.
Bombadil uses `restic-bombadil`; a new installation uses `restic-server`.

Start the **service**, so it uses the configured paths, backend, and game setting:

```bash
sudo systemctl start "$backup_unit"
sudo systemctl show "$backup_unit" -p Result -p ExecMainStatus
sudo "$restic_command" snapshots
```

Expect `Result=success`, `ExecMainStatus=0`, and a new snapshot. If the service
fails, inspect `sudo journalctl -u "$backup_unit" -n 80 --no-pager` before retrying.

Choose that snapshot's ID and restore one file to a private temporary directory:

```bash
snapshot_id=REPLACE_WITH_SNAPSHOT_ID
restore_dir=$(mktemp -d /tmp/homelab-restore.XXXXXX)
sudo "$restic_command" restore "$snapshot_id" --target "$restore_dir" --include /etc/hostname
sudo cmp /etc/hostname "$restore_dir/etc/hostname"
cd /opt/homelab/pihole
sudo docker compose ps
```

No output from `cmp` means the files match. Confirm the services that were running
before the backup are running again. [Recovery](RECOVERY.md) covers larger restores.
