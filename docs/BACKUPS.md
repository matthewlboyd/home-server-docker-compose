# Backups

**Already configured? Use [Run and check a backup](#run-and-check-a-backup).**
The setup below is for a new or rebuilt server. Keep an existing server's
working credentials, helper scripts, and timer settings.

Backups include `/opt/homelab`, `/etc` except `/etc/restic`, and the SSH
authorized-keys file selected in `backup-paths`. Application containers pause
during the snapshot and restart afterward. The host must resolve DNS without
Pi-hole. See [Installation](INSTALL.md#1-prepare-the-host) if it cannot.

When [Project Zomboid](ZOMBOID.md) is installed, the backup job saves and stops
the game first. It restores the game only if it was running before the backup.
Both the world and downloaded server files live under `/opt/homelab/zomboid`.
Players disconnect during the backup window; ordinary restarts do not update
the game build.

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
The generic script retains seven daily, four weekly, and twelve monthly
snapshots. Existing custom jobs retain their own schedule and retention rules.

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

Start the **service**, so its configured wrapper pauses all the relevant containers:

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
