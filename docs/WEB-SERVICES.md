# Add Nginx Proxy Manager and PeaNUT

Start with step 1. If both containers already work and only HTTPS is missing,
check your inventory in step 2, then go to step 4.

Allow about 30 minutes, plus time for image downloads and backups.

## 1. Check the prerequisites

The server needs working [Pi-hole](INSTALL.md), [UPS monitoring](UPS.md), and
[backups](BACKUPS.md). The playbooks keep the existing Pi-hole data directory,
image selection, and DNS aliases; do not move its data to match an example.

Use a **control computer** with this checkout, Ansible, and SSH access to the
server. Load the server's SSH key into its agent before continuing. See
[installation](INSTALL.md) if the control computer is not ready.

**Ready:** Pi-hole answers DNS queries, NUT reports UPS readings, and the
server's backup job succeeds.

## 2. Set the domain and private inventory

Set `HOMELAB_DOMAIN` in the server's `/opt/homelab/pihole/.env`. This is the
single source for the domain: Ansible reads it as `homelab_domain` and does not
write it back. Remove a legacy `homelab_domain` inventory setting if it conflicts.
In the addresses below, `${HOMELAB_DOMAIN}` means that saved value.

**On the control computer, from this checkout's root:**

```bash
cd ansible
export ANSIBLE_LOCAL_TEMP=/tmp/ansible-local
test -f inventory/hosts.yml || cp inventory/hosts.yml.example inventory/hosts.yml
chmod 600 inventory/hosts.yml
ansible-galaxy collection install -r requirements.yml
```

Edit `inventory/hosts.yml`. Keep your existing settings. Replace any example
SSH address, username, key path, LAN subnet, and Pi-hole API address with the
real values. Use a LAN or Tailscale address that this computer can reach.

Add these entries under the existing `all.children.homelab.vars` block:

```yaml
npm_admin_email: you@example.com
homelab_backup_command: /usr/local/sbin/backup-server
homelab_backup_unit: homelab-backup.service
```

Replace the email with your NPM administrator address. The backup names above
match a new installation from this repository. For an existing server, use its
actual backup job. **On Bombadil, replace those two backup entries with:**

```yaml
homelab_backup_command: /usr/local/sbin/backup-bombadil
homelab_backup_unit: bombadil-backup.service
```

Keep this inventory private.

```bash
ansible -i inventory/hosts.yml homelab -m ping
```

**Ready:** Ansible reports `SUCCESS` and `ping: pong`.

## 3. Preview and install the containers

**On the control computer, still in `ansible/`:**

```bash
ansible-playbook -i inventory/hosts.yml web-services.yml \
  --check --diff --ask-become-pass
```

The password prompt is for the **server's sudo password**. Review the preview.
If it has no failures and the changes match this setup, apply it:

```bash
ansible-playbook -i inventory/hosts.yml web-services.yml --ask-become-pass
```

The apply run backs up first, installs the services, creates the web accounts
and local DNS names, checks UPS telemetry, and verifies another backup. The
backup job briefly pauses the running containers.

**Done:** Pi-hole, NPM, and PeaNUT are running and healthy. Before step 4, those
dashboards use HTTP. Complete the HTTPS step before logging in to
Cockpit through its new hostname.

## 4. Enable trusted HTTPS

Create a Cloudflare API token with **Zone / DNS / Edit**, restricted to
the zone named by `HOMELAB_DOMAIN`. Save only the token value as one
line in a mode `0600` file **on the control computer, outside this checkout**.
Do not put the token value in the inventory, command arguments, or Git.

**On the control computer, in `ansible/`:** replace the example source path
below with your private token file.

```bash
ansible-playbook -i inventory/hosts.yml https.yml \
  --check --diff --ask-become-pass \
  -e npm_cloudflare_token_source=/absolute/private/path/cloudflare_dns_token
```

After reviewing the preview, apply it:

```bash
ansible-playbook -i inventory/hosts.yml https.yml --ask-become-pass \
  -e npm_cloudflare_token_source=/absolute/private/path/cloudflare_dns_token
```

The playbook stores the token as a root-owned `0600` file at
`/opt/homelab/pihole/secrets/cloudflare_dns_token`. If the token is already
there, omit the `-e npm_cloudflare_token_source=...` argument on both commands.

**Done:** one Let's Encrypt certificate covers all four dashboard names, HTTP redirects
to HTTPS, and NPM retains the DNS credentials for automatic renewal. No public
A records or router port forwarding are needed.

## 5. Open the services and verify them

**On a client using Pi-hole DNS**, open these addresses. For remote access,
use the configured Tailscale subnet route and DNS settings.

| Service | Address | Login |
| --- | --- | --- |
| NPM | `https://npm.${HOMELAB_DOMAIN}` | Your `npm_admin_email` |
| PeaNUT | `https://peanut.${HOMELAB_DOMAIN}` | `admin` |
| Pi-hole | `https://pihole.${HOMELAB_DOMAIN}/admin/` | Existing Pi-hole password |
| Cockpit | `https://cockpit.${HOMELAB_DOMAIN}` | Your Ubuntu username and password |

Retrieve the generated passwords **on the server**, then save them in your
password manager:

```bash
sudo cat /opt/homelab/pihole/secrets/npm_admin_password
sudo cat /opt/homelab/pihole/secrets/peanut_web_password
```

**Done:** all four pages have trusted certificates, logins work, and PeaNUT
shows battery charge, runtime, and line-power status.

<details>
<summary>Optional: verify DNS, container health, and API access</summary>

**On the server:**

```bash
cd /opt/homelab/pihole
sudo docker compose ps
upsc cyberpower@localhost
ss -lnt 'sport = :3493'
```

All three containers should be healthy. NUT should listen only on
`127.0.0.1:3493` and `[::1]:3493`.

**On a client:** enter the `HOMELAB_DOMAIN` value from the server's `.env`.
Replace the example LAN address if needed.

```bash
printf 'HOMELAB_DOMAIN: '
read -r HOMELAB_DOMAIN
dig @192.168.4.30 npm.${HOMELAB_DOMAIN} +short
dig @192.168.4.30 peanut.${HOMELAB_DOMAIN} +short
dig @192.168.4.30 pihole.${HOMELAB_DOMAIN} +short
dig @192.168.4.30 cockpit.${HOMELAB_DOMAIN} +short
curl --user admin --fail --show-error \
  https://peanut.${HOMELAB_DOMAIN}/api/v1/devices/cyberpower
```

All DNS answers should be the server's LAN address. Curl prompts for the PeaNUT
password and should return UPS data. That API request without credentials must
return `401`; `/api/ws` and `/api/ws/` must return `403`. HTTP service URLs
should redirect to HTTPS.

</details>

<details>
<summary>Reference: passwords and persistent data</summary>

Existing accounts are not reset by these playbooks. After changing an NPM or
PeaNUT password in its UI, update the corresponding root-only saved password
file before rerunning deployment validation. Do not store sudo passwords in
the inventory or shell history.

PeaNUT's account persists in `data/peanut/auth.yaml` as a bcrypt hash. Its
temporary bootstrap password environment file is removed after account
creation. NPM's account is created through its API, avoiding startup password
logging.

The actual Pi-hole mount and `data/nginx-proxy-manager`, `data/letsencrypt`, and
`data/peanut` must survive container recreation. They are included in the
existing `/opt/homelab` backup. The deployment wraps an existing backup helper
without replacing its credentials, paths, retention, or timer. See
[backups](BACKUPS.md) for checks and [recovery](RECOVERY.md) for restoration.

</details>

<details>
<summary>Reference: HTTPS and PeaNUT authentication</summary>

The helper reuses an unexpired Let's Encrypt certificate only when it covers
exactly the four configured dashboard names and uses Cloudflare DNS validation. It
confirms the saved certificate through NPM's API before updating proxy hosts.
When called without a token, it preserves existing TLS settings. To use a
different token path **on the server**, set this in the private inventory:

```yaml
npm_cloudflare_token_file: /absolute/server/path/cloudflare_dns_token
```

That file must be root-owned with mode `0600`. Keep NPM's saved renewal
credentials and certificate data in the encrypted backup.

The HTTPS playbook sets `PEANUT_AUTH_URL=https://peanut.${HOMELAB_DOMAIN}` in the
server's `.env`, using the configured domain. Compose passes this as
`AUTH_URL` and maps that hostname to NPM's LAN address inside PeaNUT. This
ensures login redirects and API credential checks use the trusted HTTPS
origin, even when the server's own DNS resolver does not use Pi-hole.

Keep this setting after HTTPS is enabled. PeaNUT checks API passwords through
its own `/api/auth/verify` endpoint; without the correct origin, it can try TLS
on its internal HTTP port and return `401` for a valid password. NPM passes
client authentication and the HTTPS forwarded protocol through normally.

</details>

<details>
<summary>Reference: Cockpit login and direct access</summary>

Cockpit uses your existing Ubuntu username and password; the playbook does not
create another account. NPM forwards its HTTPS hostname to Cockpit on the
private Docker bridge, including the WebSocket connection used by the dashboard
and terminal. Only that bridge gains access to port 9090; direct LAN access to
that port stays closed.

The playbook preserves existing Cockpit settings and direct Tailscale access,
adds the allowed HTTPS origin, and restarts Cockpit only when its configuration
changes. The trusted certificate is for `cockpit.${HOMELAB_DOMAIN}`. The direct
`https://TAILSCALE_IP:9090` fallback keeps Cockpit's own certificate.

After deployment, open Cockpit and confirm the dashboard and terminal load.
The HTTPS playbook also checks the login page, WebSocket upgrades, allowed
origins, and forwarded protocol handling without using a password.

</details>

<details>
<summary>Reference: ports, optional settings, and rollback</summary>

NPM binds only the selected LAN address on ports `80`, `443`, and recovery
admin port `81`. Its HTTPS admin hostname proxies to `127.0.0.1:81` inside its
container. Direct `http://SERVER_LAN_IP:81` access remains available if DNS or
HTTPS needs repair. Pi-hole retains direct ports `8080` and `8443` for recovery.

Docker-published ports use Docker firewall rules. Preserve the LAN address
bindings; UFW alone does not restrict these published ports.

PeaNUT reads the localhost NUT service through host networking, while its web
listener binds to `172.29.20.1:8081` on the private `br-peanut` bridge. NPM is
the only container on that bridge. The playbook adds the UFW rule for this
connection and rejects overlapping LAN, VPN, or Docker subnets. Optional
private inventory settings are:

| Setting | Default |
| --- | --- |
| `homelab_stack_dir` | `/opt/homelab/pihole` |
| `peanut_bridge_subnet` | `172.29.20.0/29` |
| `peanut_bridge_gateway` | `172.29.20.1` |

Do not change an existing bridge's addresses without planning that migration.
Keep the `/api/ws` restrictions in `nginx/peanut-advanced.conf`; PeaNUT is not
given NUT monitor credentials or UPS shutdown permissions.

### Undo the initial web-services installation

**On the server:** confirm that `compose.yaml.before-web-services` and
`.env.before-web-services` are the matching pair from before NPM and PeaNUT
were added. These files are saved only once. This rollback returns to Pi-hole
alone and keeps application data.

```bash
cd /opt/homelab/pihole
sudo docker compose stop peanut nginx-proxy-manager
sudo cp --preserve=all compose.yaml.before-web-services compose.yaml
sudo cp --preserve=all .env.before-web-services .env
sudo docker compose up -d --no-deps pihole
sudo docker compose ps
```

**If Project Zomboid is enrolled in backups, keep `web-services.conf` and
`zomboid.conf`.** The wrapper is still needed to save and stop the game safely.

Otherwise, if the deployment added `web-services.conf` to an existing backup
job, remove only that file and reload systemd. No override is added for the
repository's default `backup-server` job. **On Bombadil, without the game:**

```bash
sudo rm /etc/systemd/system/bombadil-backup.service.d/web-services.conf
sudo systemctl daemon-reload
sudo systemctl cat bombadil-backup.service
```

For another existing backup job, substitute its `homelab_backup_unit` in the
path and final command. Check that the service again runs its original backup
helper. Keep the service, timer, and other overrides.

After NPM and PeaNUT are stopped, the bridge firewall rule can be removed.
For the default bridge addresses:

```bash
sudo ufw delete allow in on br-peanut from 172.29.20.0/29 \
  to 172.29.20.1 port 8081 proto tcp
sudo ufw delete allow in on br-peanut from 172.29.20.0/29 \
  to 172.29.20.1 port 9090 proto tcp
```

To reverse only a later HTTPS change, use its matching timestamped Compose and
environment backups instead of the initial-installation pair. Configuration
copies alone do not undo certificate or proxy changes stored in NPM's database.
Use [recovery](RECOVERY.md) if that application data needs restoration.
Never delete or replace the existing Pi-hole data directory to match an
example layout.

</details>

<details>
<summary>Upstream documentation</summary>

- [NPM setup](https://nginxproxymanager.com/setup/)
- PeaNUT [configuration](https://github.com/Brandawg93/PeaNUT/releases/tag/v6.0.0) and [API authentication](https://github.com/Brandawg93/PeaNUT/blob/v6.0.0/src/auth.config.ts)
- Cockpit [configuration](https://docs.cockpit-project.org/cockpit-guide/360/man/cockpit.conf.5.html) and [NGINX proxy setup](https://cockpit-project.org/external/wiki/Proxying-Cockpit-over-NGINX)
- [Docker port publishing](https://docs.docker.com/engine/network/port-publishing/)
- [Cloudflare certificate validation](https://certbot-dns-cloudflare.readthedocs.io/en/stable/)

</details>
