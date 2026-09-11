# Recover the server

Choose one snapshot before restoring application data. Use that same snapshot
for Pi-hole, Nginx Proxy Manager, PeaNUT, and their configuration files.

For one missing file, use [Restore one file](#restore-one-file). A replacement
server needs Ubuntu, host services, private credentials, and backed-up data;
the Git checkout supplies the public configuration.

## 1. Prepare the replacement server

**On the replacement server:** follow the host setup in [INSTALL.md](INSTALL.md)
to install Ubuntu, Docker Engine with Compose, Tailscale, and this checkout.
Confirm SSH works before changing network settings. Keep the host's own DNS
independent of Pi-hole. Do not start fresh application containers yet.

Recreate `/etc/restic` and install `restic-server` using
[backup setup, step 1](BACKUPS.md#1-recreate-the-backup-configuration).
Use the saved repository address, region, storage credentials, and repository
password. Follow [step 2](BACKUPS.md#2-test-an-existing-repository) to check access.
**Do not run `restic init` during recovery.**

## 2. Restore one snapshot into a staging directory

**On the replacement server, in Bash:** keep this terminal open for the remaining
steps. List snapshots and enter the exact ID you want to recover:

```bash
sudo restic-server snapshots
read -r -p 'Snapshot ID to restore: ' snapshot_id
restore_dir=$(mktemp -d /tmp/homelab-recovery.XXXXXX)
sudo restic-server restore "$snapshot_id" --target "$restore_dir" \
  --include /opt/homelab/pihole
```

The restored stack should include these files from that one snapshot:

- `compose.yaml`, `.env`, `nginx/`, and `secrets/`.
- The Pi-hole directory selected by `PIHOLE_DATA_DIR` in `.env`.
- `data/nginx-proxy-manager`, `data/letsencrypt`, and `data/peanut`.

Inspect the staged `.env` with
`sudoedit "$restore_dir/opt/homelab/pihole/.env"`. Pi-hole may use `etc-pihole`
instead of `data/pihole`. If its configured directory is outside
`/opt/homelab/pihole`, restore that path separately using **the same
`$snapshot_id`** before continuing.

Recover missing private files from the password manager. The `.env` copy alone
does not contain all application passwords or data.

## 3. Put the recovered files in place

**On the replacement server:** copy the staged stack only after confirming
`/opt/homelab/pihole` does not already exist. If a failed installation exists,
stop its containers and move its stack directory aside first; keep it until
recovery is verified.

```bash
sudo install -d /opt/homelab
sudo cp -a "$restore_dir/opt/homelab/pihole" /opt/homelab/
cd /opt/homelab/pihole
sudoedit .env
sudo docker compose config --quiet
```

Update `LAN_IP` and the final address in `PIHOLE_HOST_RECORDS` if the server's
address changed. Preserve the selected image versions and Pi-hole data directory.
Put separately restored data back at the path selected by `PIHOLE_DATA_DIR`,
updating that value only if the restored path changed.

`cp -a` preserves ownership and permissions. Keep `.env` and secret files private.
PeaNUT needs `data/peanut` owned by UID/GID `1000:1000`, directory mode `0700`,
and `auth.yaml` and `settings.yml` mode `0600`. NPM's data includes its account
database and renewal credentials; restore it together with `data/letsencrypt`.

Restore host configuration selectively. Copying all of `/etc` over a new Ubuntu
installation can replace working network, SSH, account, and boot configuration.

## 4. Bring up Pi-hole, the host baseline, and backups

**On the replacement server:** start Pi-hole with its restored data:

```bash
cd /opt/homelab/pihole
sudo docker compose up -d --no-deps --wait --wait-timeout 180 pihole
sudo docker compose ps
```

**On the control computer:** update the private Ansible inventory for the
replacement host. Complete the baseline in [INSTALL.md](INSTALL.md) and the
telemetry check in [UPS.md](UPS.md). The UPS must report actual values before
deploying PeaNUT.

The web playbooks read `HOMELAB_DOMAIN` from the restored `.env`; do not set a
second domain in inventory. Match these network settings to the restored `.env`
before running the web playbook; it writes its inventory values back into `.env`:

| Restored `.env` value | Private inventory variable |
| --- | --- |
| `PEANUT_BRIDGE_SUBNET` | `peanut_bridge_subnet` |
| `PEANUT_BRIDGE_GATEWAY` | `peanut_bridge_gateway` |

Set `pihole_api_url` to the replacement host's reachable Pi-hole API address,
and `npm_admin_email` to the restored NPM account's email.

**On the replacement server:** install the backup helper and systemd service
using [backup setup, step 3](BACKUPS.md#3-start-nightly-backups). Leave the timer
disabled while recovery is incomplete. The web-services playbook requires a
working backup service before it can run.

For a rebuilt host using the repository's backup setup, use these values
in the private inventory:

```yaml
homelab_backup_unit: homelab-backup.service
homelab_restic_command: /usr/local/sbin/restic-server
```

Use an existing service or restic helper name only if you also restored it and
its configuration. All setups use the repository's `backup-server` entry point.

## 5. Restore the web entry points and verify recovery

**On the control computer:** follow the preview and apply commands in
[WEB-SERVICES.md](WEB-SERVICES.md) for `web-services.yml`.

That playbook checks bridge overlap, restores the private bridge firewall rule,
and starts NPM before PeaNUT. This creates PeaNUT's bridge address before its web
listener binds to it. Restored accounts and NPM certificate settings are retained.

If `web-services.yml` stops on an expired or untrusted certificate after the
containers have started, run `https.yml` using
[Enable trusted HTTPS](WEB-SERVICES.md#4-enable-trusted-https) with a valid
recovered or newly supplied Cloudflare token. Then rerun `web-services.yml`
to complete its HTTPS and UPS validation before continuing.

**From a client using Pi-hole DNS:** replace `${HOMELAB_DOMAIN}` below with its
restored `.env` value. Confirm these results before changing router or Tailscale
DNS to a replacement address:

- Normal DNS lookups succeed and a known blocked domain appears blocked in
  Pi-hole's query log.
- `https://npm.${HOMELAB_DOMAIN}`,
  `https://pihole.${HOMELAB_DOMAIN}/admin/`,
  `https://peanut.${HOMELAB_DOMAIN}`, and `https://cockpit.${HOMELAB_DOMAIN}`
  open with trusted HTTPS and valid logins.
- PeaNUT shows actual UPS readings; `/api/ws` and `/api/ws/` return `403`.

**On the replacement server:** run a full backup through its systemd service:

```bash
sudo systemctl start homelab-backup.service
sudo systemctl show homelab-backup.service -p Result -p ExecMainStatus
sudo restic-server snapshots
cd /opt/homelab/pihole
sudo docker compose ps
```

Expect `Result=success`, `ExecMainStatus=0`, a new snapshot, and healthy containers.
Then enable the timer using [BACKUPS.md](BACKUPS.md#3-start-nightly-backups).
Keep the recovery snapshot and any moved-aside data until these checks pass.

If NPM's HTTPS entry point needs repair, use `http://SERVER_LAN_IP:81` and the
HTTPS procedure in [WEB-SERVICES.md](WEB-SERVICES.md).

## Restore the optional game server

If the snapshot includes Project Zomboid, follow
[Restore the game](ZOMBOID.md#restore-the-game). Restore its whole directory
from the same snapshot before running `ansible/zomboid.yml`; this keeps the
world, configuration, credentials, and installed game build together.

## Restore one file

**On the server:** restore access to the existing repository using
[BACKUPS.md](BACKUPS.md#1-recreate-the-backup-configuration), if needed. An
existing Bombadil installation uses `restic-bombadil` in place of `restic-server`.

1. Run `sudo restic-server snapshots` and choose an exact snapshot ID.
2. Restore into a temporary directory, replacing the example path as needed:

   ```bash
   read -r -p 'Snapshot ID to restore: ' snapshot_id
   restore_dir=$(mktemp -d /tmp/homelab-file-restore.XXXXXX)
   sudo restic-server restore "$snapshot_id" --target "$restore_dir" \
     --include /etc/hostname
   sudo cmp /etc/hostname "$restore_dir/etc/hostname"
   ```

3. Inspect the staged file before copying it into place. Preserve the current
   file first if you need a way to undo the replacement.
