# Home server

Configuration and recovery instructions for an Ubuntu home server running
Pi-hole, Nginx Proxy Manager, PeaNUT, Tailscale, UPS monitoring, and encrypted backups.
An optional Project Zomboid server runs in its own Compose project.

**Rebuilding a server? Start with [Recovery](docs/RECOVERY.md).** You need this
repository, your saved credentials, and a restic backup. Ansible handles part of
the setup; it does not install the operating system or restore application data.

## Choose a task

| I want to… | Open |
| --- | --- |
| Set up a new server | [Installation](docs/INSTALL.md) |
| Restore files or rebuild the server | [Recovery](docs/RECOVERY.md) |
| Configure or check backups | [Backups](docs/BACKUPS.md) |
| Update Pi-hole | [Upgrading](docs/UPGRADING.md) |
| Fix containers that are unreachable after reboot | [Troubleshooting](docs/TROUBLESHOOTING.md) |
| Manage dashboards, UPS, or the game server | [Web services](docs/WEB-SERVICES.md) · [UPS](docs/UPS.md) · [Project Zomboid](docs/ZOMBOID.md) |

## What runs where

| Location | Services |
| --- | --- |
| Docker Compose | Pi-hole, Nginx Proxy Manager, PeaNUT |
| Separate game Compose project | Project Zomboid, stable build, four players |
| Ubuntu host | Tailscale, Cockpit, UFW, Network UPS Tools (NUT), restic |
| Private object-storage bucket | Encrypted restic backups |

Pi-hole supplies local DNS. Nginx Proxy Manager supplies trusted HTTPS for
`npm.${HOMELAB_DOMAIN}`, `pihole.${HOMELAB_DOMAIN}`, `peanut.${HOMELAB_DOMAIN}`,
and `cockpit.${HOMELAB_DOMAIN}`.
Set `HOMELAB_DOMAIN` once in the server's private `.env`; Ansible uses that value.
Tailscale provides remote access. These services require no public port forwards.

Containers publish their web and DNS ports on the configured LAN address.
Docker manages its own firewall rules, so UFW alone does not restrict those
ports. Cockpit uses NPM for its trusted HTTPS hostname. Direct access remains
available through Tailscale at `https://TAILSCALE_IP:9090`; port 9090 stays closed
to the LAN.

## What Ansible does

Run Ansible from a separate, trusted computer. Each playbook needs the previous
stage to be working:

| Playbook | Requires | Configures |
| --- | --- | --- |
| [site.yml](ansible/site.yml) | Ubuntu 24.04+, SSH/sudo access, running Pi-hole and its password file, supported USB UPS | Host packages, firewall, security updates, Cockpit, NUT, Pi-hole blocklists |
| [web-services.yml](ansible/web-services.yml) | Pi-hole, working NUT telemetry, configured backup service | NPM, PeaNUT, local names, accounts, backup integration |
| [https.yml](ansible/https.yml) | Working web services and Cloudflare DNS token | Shared Let's Encrypt certificate, HTTPS redirects, PeaNUT's HTTPS login URL, Cockpit access |
| [zomboid.yml](ansible/zomboid.yml) | Working homelab and backups, x86_64 host with at least 8 GB RAM | Private game server, saved world, game credentials, backup integration |

Install Ubuntu, Docker, and Tailscale first. The [installation guide](docs/INSTALL.md)
puts the commands in dependency order and identifies which computer to use.

## What to keep for recovery

- **GitHub:** Compose, Ansible playbooks, scripts, and configuration examples.
- **1Password:** the private `.env`, application logins, Cloudflare token, SSH
  access, and backup credentials. The `bombadil` Environment is a saved copy of
  `.env`; Ansible does not read it automatically.
- **restic:** application data, NPM certificates and database, and host
  configuration. The [backup recovery credentials](docs/BACKUPS.md#1-recreate-the-backup-configuration)
  must be available separately from the server.

Keep real credentials, private inventory, `.env`, and application data out of Git.
