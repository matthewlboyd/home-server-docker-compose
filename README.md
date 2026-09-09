# Minimal Ubuntu home server

This repository documents a small, reproducible home-server setup built around
Ubuntu Server, native Tailscale, Docker Compose, Pi-hole, Cockpit, UFW, and
encrypted restic backups to S3-compatible object storage.

The server exposes no services directly to the internet. Tailscale provides
remote SSH, access to the home subnet, and an optional exit node. Pi-hole filters
DNS for home-network clients and Tailscale clients. A static public website can
run separately on a managed host such as Cloudflare Pages.

## Architecture

```text
Home clients ───────────────┐
                           ├── Pi-hole on the LAN ── public DNS resolvers
Remote Tailscale clients ──┘

Tailscale ── SSH / subnet routing / optional exit node
Cockpit  ── host status and routine administration over LAN or Tailscale
Docker   ── Pi-hole only
restic   ── encrypted, retained backups to a private S3-compatible bucket
```

The DNS server host uses independent upstream DNS rather than Pi-hole. This
avoids a circular dependency when Pi-hole is stopped for a consistent backup.
All other clients can continue using Pi-hole through router and Tailscale DNS
configuration.

## Repository contents

- `compose.yaml`: Pi-hole v6 deployment with LAN-bound ports and Docker secrets.
- `.env.example`: non-secret deployment variables.
- `host/`: Netplan and forwarding examples.
- `scripts/`: restic wrapper and consistent backup script.
- `restic/`: non-secret configuration examples.
- `systemd/`: nightly backup service and timer.
- `docs/INSTALL.md`: installation and validation outline.
- `docs/RECOVERY.md`: tested restore workflow.

Application data, `.env`, Pi-hole passwords, object-storage credentials, restic
passwords, Tailscale keys, SSH private keys, and restored files must never be
committed.

## Pi-hole deployment

Copy `.env.example` to `.env`, replace all example values, and create
`secrets/web_password` with mode `600`. Validate before starting:

```bash
docker compose config --quiet
sudo docker compose up -d
sudo docker compose ps
```

The defaults publish DNS on the selected LAN address and the Pi-hole web UI on
ports `8080` and `8443`. UFW and Tailscale determine which networks can reach
those ports.

## Backup behavior

`backup-server` records a consistent Pi-hole snapshot by stopping the container,
running restic, and restarting Pi-hole even when the backup fails. The server's
host DNS must therefore resolve independently of Pi-hole. Retention keeps seven
daily, four weekly, and twelve monthly snapshots.

The repository password is required for every restore. Store it separately from
the server and test a restore after initial setup and periodically afterward.

See [installation](docs/INSTALL.md) and [recovery](docs/RECOVERY.md) for the full
workflow.
