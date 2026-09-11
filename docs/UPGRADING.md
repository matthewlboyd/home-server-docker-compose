# Upgrade Pi-hole

Take a successful backup before changing `PIHOLE_IMAGE`. The upgrade replaces
the container and keeps the data directory selected by `.env`.

Run the server commands in one Bash session so the rollback variables remain
available. Keep the snapshot ID, data path, and saved environment filename if
you close that terminal.

## 1. Select the installed backup service

**On the server:** select the commands for your installation. A host installed
from the repository's generic backup setup uses:

```bash
backup_unit=homelab-backup.service
restic_command=restic-server
```

The existing Bombadil installation uses this pair instead:

```bash
backup_unit=bombadil-backup.service
restic_command=restic-bombadil
```

Run backups through the selected systemd service so the shared backup script
handles the DNS, dashboards, and game consistently.

## 2. Save a recovery point

**On the server:** run the service and confirm success before continuing:

```bash
sudo systemctl start "$backup_unit"
sudo systemctl show "$backup_unit" -p Result -p ExecMainStatus
sudo "$restic_command" snapshots
```

Expect `Result=success` and `ExecMainStatus=0`. Choose the new snapshot for this
host and record its ID. Record the actual Pi-hole mount from Docker too:

```bash
read -r -p 'Pre-upgrade snapshot ID: ' snapshot_id
pihole_data_dir=$(sudo docker inspect --format \
  '{{range .Mounts}}{{if eq .Destination "/etc/pihole"}}{{.Source}}{{end}}{{end}}' pihole)
printf 'Pi-hole data directory: %s\n' "$pihole_data_dir"
```

Check that the printed path matches `PIHOLE_DATA_DIR` in the stack's `.env`
(relative paths are relative to `/opt/homelab/pihole`).
An existing installation may use `etc-pihole`; do not assume `data/pihole`.

## 3. Change the image and recreate Pi-hole

Read the [Pi-hole Docker release notes](https://github.com/pi-hole/docker-pi-hole/releases)
for the version you want. Check for migration or rollback restrictions.

**On the server:** save the current environment, then edit only `PIHOLE_IMAGE`
to the desired dated release tag:

```bash
cd /opt/homelab/pihole
env_backup=".env.before-upgrade.$(date +%Y%m%d-%H%M%S)"
sudo cp --preserve=all .env "$env_backup"
printf 'Environment rollback file: %s\n' "$env_backup"
sudoedit .env
sudo docker compose config --quiet
sudo docker compose pull pihole
sudo docker compose up -d --no-deps --wait --wait-timeout 180 pihole
```

## 4. Check health, DNS, and blocking

**On the server:** confirm the expected image and healthy state:

```bash
sudo docker inspect \
  --format '{{.Config.Image}} {{.State.Status}} {{.State.Health.Status}}' pihole
sudo docker exec pihole pihole -v
```

**From a client:** replace the example address if your server uses another IP:

```bash
dig @192.168.4.30 example.com A +short
dig @192.168.4.30 doubleclick.net A +short
```

Confirm the normal lookup resolves. Check that a domain on your active blocklist
appears blocked in Pi-hole's query log. Open
`https://pihole.${HOMELAB_DOMAIN}/admin/`, using `HOMELAB_DOMAIN` from `.env`, and
confirm your settings and lists are intact.

## 5. Keep the rollback files

Keep the pre-upgrade snapshot, saved `.env`, and previous Docker image until the
new version has passed your checks. Update the saved `.env` in 1Password and the
repository's `.env.example` if this is the version you intend to keep.

## Roll back if validation fails

### 1. Try the previous image

If the release notes describe an incompatible data migration, go straight to
step 2 below. Restore the pre-upgrade data before starting the old image.

**On the server, in the same Bash session:** restore the saved environment and
recreate Pi-hole:

```bash
cd /opt/homelab/pihole
sudo cp --preserve=all "$env_backup" .env
sudo docker compose up -d --no-deps --wait --wait-timeout 180 pihole
sudo docker compose ps
```

The old image normally remains in Docker's cache. Repeat the health and client
DNS checks from step 4.

### 2. Restore pre-upgrade data when necessary

**On the server:** use the exact snapshot ID and actual data path recorded in
step 2. Restore into a temporary directory first:

```bash
cd /opt/homelab/pihole
restore_dir=$(mktemp -d /tmp/pihole-rollback.XXXXXX)
sudo "$restic_command" restore "$snapshot_id" --target "$restore_dir" \
  --include "$pihole_data_dir"
sudo ls -ld "$restore_dir$pihole_data_dir"
```

Confirm the restored directory exists. Then stop Pi-hole, move the upgraded
data aside, and copy the snapshot data into place with its permissions intact:

```bash
sudo docker compose stop pihole
saved_upgraded_data="$pihole_data_dir.after-upgrade.$(date +%Y%m%d-%H%M%S)"
sudo mv "$pihole_data_dir" "$saved_upgraded_data"
sudo cp -a "$restore_dir$pihole_data_dir" "$pihole_data_dir"
sudo cp --preserve=all "$env_backup" .env
sudo docker compose up -d --no-deps --wait --wait-timeout 180 pihole
```

Repeat the health and client DNS checks. Keep `$saved_upgraded_data` until the
rollback is verified; it preserves any changes made after the upgrade.
