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
sudo docker compose up -d pihole
sudo docker compose ps
```

Verify both normal and blocked lookups before changing router or Tailscale DNS:

```bash
dig @SERVER_LAN_IP example.com A +short
dig @SERVER_LAN_IP doubleclick.net A +short
```

## Restore the web services

Restore `data/nginx-proxy-manager`, `data/letsencrypt`, and `data/peanut` from the
same snapshot before starting those services. The NPM directories contain its
SQLite database, account data, certificates, and DNS API credentials. PeaNUT's
directory contains `settings.yml` and its persistent `auth.yaml` account file;
keep it owned by UID/GID `1000:1000` with directory mode `0700`.

Run the network check and restore the bridge firewall rule described in
[WEB-SERVICES.md](WEB-SERVICES.md), then start both containers. Verify HTTPS,
local DNS, actual UPS readings, and the blocked PeaNUT terminal endpoint.
Check `npm.bigbiscuit.org`, `pihole.bigbiscuit.org`, and `peanut.bigbiscuit.org`
using the restored shared Let's Encrypt certificate. NPM retains the DNS
credentials it needs for automatic renewal. If NPM's HTTPS address needs
repair, use its direct LAN address, `http://SERVER_LAN_IP:81`.
