# Minimal Ubuntu home server

This repository documents a small, reproducible home-server setup built around
Ubuntu Server, native Tailscale, Docker Compose, Pi-hole, Nginx Proxy Manager,
PeaNUT, Cockpit, UFW, Network UPS Tools, and encrypted restic backups to
S3-compatible object storage.

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
Docker   ── Pi-hole, Nginx Proxy Manager, PeaNUT
NPM      ── LAN HTTPS hostnames for its admin UI, Pi-hole, and the UPS dashboard
restic   ── encrypted, retained backups to a private S3-compatible bucket
NUT      ── graceful shutdown during an extended utility outage
```

The DNS server host uses independent upstream DNS rather than Pi-hole. This
avoids a circular dependency when Pi-hole is stopped for a consistent backup.
All other clients can continue using Pi-hole through router and Tailscale DNS
configuration.

## Repository contents

- `compose.yaml`: Pi-hole v6, Nginx Proxy Manager, and the private PeaNUT dashboard.
- `.env.example`: non-secret deployment variables.
- `host/`: Netplan and forwarding examples.
- `ansible/`: repeatable host-baseline configuration from a separate computer.
- `scripts/`: restic wrapper and consistent backup script.
- `restic/`: non-secret configuration examples.
- `systemd/`: nightly backup service and timer.
- `docs/INSTALL.md`: installation and validation outline.
- `docs/UPGRADING.md`: backup-first Pi-hole container upgrade procedure.
- `docs/RECOVERY.md`: tested restore workflow.
- `docs/UPS.md`: UPS monitoring, shutdown behavior, and validation.
- `docs/WEB-SERVICES.md`: deployment, local names, HTTPS, and dashboard setup.

Application data, `.env`, Pi-hole passwords, object-storage credentials, restic
passwords, Tailscale keys, SSH private keys, and restored files must never be
committed.

## Pi-hole deployment

Copy `.env.example` to `.env`, replace all example values, and create
`secrets/web_password` with mode `600`. Validate before starting:

```bash
docker compose config --quiet
sudo docker compose up -d pihole
sudo docker compose ps
```

The defaults publish DNS on the selected LAN address and the Pi-hole web UI on
ports `8080` and `8443`. UFW and Tailscale determine which networks can reach
those ports.

For a new Pi-hole-only installation, start `docker compose up -d pihole` first.
Use [the web-services deployment](docs/WEB-SERVICES.md) to prepare PeaNUT storage,
check the private bridge, and add Nginx Proxy Manager. The local names default to
`npm.bigbiscuit.org`, `pihole.bigbiscuit.org`, and `peanut.bigbiscuit.org`;
`HOMELAB_DOMAIN` changes the suffix. For an existing deployment,
`ansible/https.yml` adds a shared Let's Encrypt certificate using a Cloudflare
DNS token. NPM renews it automatically without public port forwards. NPM
publishes LAN ports `80` and `443`; administrative port `81` remains available
for recovery.

## Backup behavior

`backup-server` records a consistent snapshot by stopping the running Pi-hole,
Nginx Proxy Manager, and PeaNUT containers, running restic, and restarting only
the services that were running. It attempts restoration even when backup fails.
The server's host DNS must resolve independently of Pi-hole. Retention keeps
seven daily, four weekly, and twelve monthly snapshots.

The repository password is required for every restore. Store it separately from
the server and test a restore after initial setup and periodically afterward.

See [installation](docs/INSTALL.md), [upgrading](docs/UPGRADING.md), and
[recovery](docs/RECOVERY.md) for the full workflow.

## Host management with Ansible

Install Ansible on a trusted control computer, copy the example inventory, and
replace its documentation addresses and account names. Keep the real inventory
out of Git. Preview every run before applying it:

```bash
cd ansible
cp inventory/hosts.yml.example inventory/hosts.yml
ansible-galaxy collection install -r requirements.yml
ansible homelab -m ping
ansible-playbook site.yml --check --diff --ask-become-pass
ansible-playbook site.yml --ask-become-pass
```

The example inventory uses `/usr/bin/sudo.ws` for privilege escalation on
Ubuntu 26.04. This avoids the current Ansible prompt-detection problem with
Ubuntu's default `sudo-rs` while leaving `sudo-rs` as the host default.

The baseline playbook manages packages, timezone, forwarding, unattended
upgrades, Cockpit, UFW, UPS monitoring, and the Pi-hole blocklists declared in
`ansible/group_vars/all.yml`. Pi-hole API credentials are read from the existing
Docker secret on the server and are never stored in Git. Lists tagged `Managed by
Ansible` are reconciled and gravity is refreshed only when their configuration
changes. Other lists remain untouched.

The playbook deliberately does not modify Netplan, authenticate Tailscale, move
Pi-hole data, or replace backup credentials and timers.
