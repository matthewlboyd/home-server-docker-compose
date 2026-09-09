# Recovery

Keep the S3 access key, secret key, and restic repository password in a password
manager. They are deliberately absent from Git and from the backup repository's
configuration examples.

## Restore a file

After recreating `/etc/restic` and installing `restic-server`, list snapshots and
restore into a temporary directory first:

```bash
sudo restic-server snapshots
restore_dir="$(mktemp -d /tmp/homelab-restore.XXXXXX)"
sudo restic-server restore latest --target "$restore_dir" --include /etc/hostname
sudo cmp /etc/hostname "$restore_dir/etc/hostname"
```

Inspect restored files before copying them into place. A full `/etc` restore onto
a different Ubuntu release can overwrite working network, SSH, account, and boot
configuration, so restore individual files unless rebuilding an identical host.

## Restore Pi-hole

Install Docker, place `compose.yaml` and the private `.env` in the stack directory,
then restore the saved `data/pihole` directory. Recreate `secrets/web_password`
from the password manager and start the service:

```bash
cd /opt/homelab/pihole
sudo docker compose up -d
sudo docker compose ps
```

Verify both normal and blocked lookups before changing router or Tailscale DNS:

```bash
dig @SERVER_LAN_IP example.com A +short
dig @SERVER_LAN_IP doubleclick.net A +short
```
