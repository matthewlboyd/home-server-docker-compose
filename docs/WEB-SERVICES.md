# Local web services

Nginx Proxy Manager (NPM) provides the local names `npm.bigbiscuit.org`,
`pihole.bigbiscuit.org`, and `peanut.bigbiscuit.org`. Pi-hole resolves all three
to the server's LAN address. One Let's Encrypt certificate covers these names;
NPM uses Cloudflare DNS validation to issue and renew it automatically.
Clients must use Pi-hole DNS, either directly on the LAN or through the existing
Tailscale subnet route and DNS configuration. Public A records and router port
forwards are unnecessary.

## Deploy from this checkout

This assumes the existing Pi-hole, native NUT monitor, and restic backups work.
The deployment inspects the running Compose definition and preserves its
Pi-hole data directory and existing hostname aliases. For example, an existing
`etc-pihole` directory stays in use instead of switching to `data/pihole`.
The Pi-hole image version stays selected by the existing private `.env`.

Set `npm_admin_email` in the private `ansible/inventory/hosts.yml`. Optional
inventory settings are `homelab_domain`, `peanut_bridge_subnet`, and
`peanut_bridge_gateway`; defaults are selected in `ansible/web-services.yml`.
If the host uses an existing backup helper or timer with different names, set
`homelab_backup_command` and `homelab_backup_unit` as well. Bombadil uses
`/usr/local/sbin/backup-bombadil` and `bombadil-backup.service`.

Run from the `ansible` directory, inspect the preview, then apply:

```bash
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local ansible-playbook \
  -i inventory/hosts.yml web-services.yml --check --diff --ask-become-pass
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local ansible-playbook \
  -i inventory/hosts.yml web-services.yml --ask-become-pass
```

If this computer cannot reach the inventory's Tailscale address but is on the
home LAN, append `-e ansible_host=192.168.4.30` to each command. The SSH agent
must have the home-server key loaded. The sudo prompt asks for the server
account's password; do not put it into the inventory or shell history.

The apply run checks bridge overlap and port availability, takes a restic
snapshot, saves the prior Compose/environment files, prepares persistent data,
adds the bridge firewall rule, starts both containers, and applies local DNS.
It also creates the web accounts and proxy hosts, blocks PeaNUT's raw NUT
terminal, and checks actual UPS telemetry and local DNS.

## Accounts and addresses

| Service | Address | Login |
| --- | --- | --- |
| NPM administration | `https://npm.bigbiscuit.org` | Your `npm_admin_email` |
| Pi-hole | `https://pihole.bigbiscuit.org/admin/` | Existing Pi-hole password |
| PeaNUT | `https://peanut.bigbiscuit.org` | `admin` |

These HTTPS addresses work after the certificate step below. Before that,
the hostnames serve HTTP. NPM's direct `http://SERVER_LAN_IP:81` address remains
available for recovery if its hostname, proxy, or certificate needs repair.

The generated NPM and PeaNUT passwords are stored on the server in root-only
files. Retrieve them privately and save them in your password manager:

```bash
sudo cat /opt/homelab/pihole/secrets/npm_admin_password
sudo cat /opt/homelab/pihole/secrets/peanut_web_password
```

The NPM helper creates the initial account through its API; it does not use
NPM's startup password environment variable, which logs the initial password.
PeaNUT receives a temporary bootstrap environment file, persists its bcrypt
account in `data/peanut/auth.yaml`, then is recreated without that environment
file. This prevents the initial environment password remaining an alternate
API credential after a later password change.

Existing accounts are not reset. If an existing NPM account's password is no
longer the saved bootstrap password, update the root-only password file before
rerunning the helper. If you change PeaNUT's password, update its saved file as
well so deployment validation can authenticate.

## Trusted HTTPS with Cloudflare

Create a Cloudflare API token with **Zone / DNS / Edit** limited to
`bigbiscuit.org`. Save only the token value in a private file outside this
checkout; do not paste the token into inventory, command arguments, or Git.

For an existing deployment, run the focused `https.yml` playbook from the
`ansible` directory. Set `npm_cloudflare_token_source` to that local file. The
playbook copies it to a root-owned mode `0600` file on the server, adds NPM's
local hostname, and configures HTTPS for all three services:

```bash
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local ansible-playbook \
  -i inventory/hosts.yml https.yml --check --diff --ask-become-pass \
  -e npm_cloudflare_token_source=/absolute/private/path/cloudflare_dns_token
ANSIBLE_LOCAL_TEMP=/tmp/ansible-local ansible-playbook \
  -i inventory/hosts.yml https.yml --ask-become-pass \
  -e npm_cloudflare_token_source=/absolute/private/path/cloudflare_dns_token
```

The default server destination is
`/opt/homelab/pihole/secrets/cloudflare_dns_token`. If the token is already
stored there, omit `npm_cloudflare_token_source`. To use another server path,
set this private inventory variable:

```yaml
npm_cloudflare_token_file: /opt/homelab/pihole/secrets/cloudflare_dns_token
```

Once local DNS and the current helper are deployed, certificate and proxy
configuration can also be rerun directly on the server:

```bash
cd /opt/homelab/pihole
sudo configure-web-proxies --email YOUR_EMAIL \
  --cloudflare-token-file secrets/cloudflare_dns_token
```

NPM uses a DNS challenge to obtain one certificate with all three hostnames
listed as Subject Alternative Names (SANs), then redirects HTTP to HTTPS.
It proxies its own hostname to `http://127.0.0.1:81` inside the NPM container.
This does not publish the services. The helper reuses a matching
certificate on later runs; omitting the token preserves existing HTTPS settings.
Without a token, the initial setup serves HTTP and reports that HTTPS remains
to be configured. NPM stores the DNS credentials for automatic renewal, so its
data and certificate directories remain part of the encrypted restic backup.

## Network and data layout

NPM publishes only the selected LAN address on ports `80`, `443`, and recovery
admin port `81`. Its backend port `3000` stays inside Docker. Pi-hole's direct
`8080` and `8443` access remains available for recovery. Docker-published ports use Docker
firewall rules, so do not rely on UFW alone to restrict them; the LAN address
binding and absence of external port forwards are intentional.

NUT stays on `127.0.0.1:3493` and `[::1]:3493`. PeaNUT uses host networking to
read it without UPS control credentials, but binds its web listener only to
`172.29.20.1:8081` on the private Docker bridge `br-peanut`. NPM is the only
container attached to that link. The deployment checks that the `/29` subnet
does not overlap another LAN, VPN, or Docker network and refuses to silently
replace an existing bridge with different addresses.

The bridge UFW rule is:

```bash
sudo ufw allow in on br-peanut from 172.29.20.0/29 \
  to 172.29.20.1 port 8081 proto tcp
```

PeaNUT's dashboard does not need its raw NUT terminal. The proxy uses
`nginx/peanut-advanced.conf` to reject `/api/ws` and its trailing paths. Keep that
restriction when editing the proxy host. No NUT monitor password is provided to
PeaNUT.

The HTTPS playbook sets `PEANUT_AUTH_URL=https://peanut.bigbiscuit.org` in the
private Compose environment (using the configured homelab domain), and Compose
passes it to PeaNUT as `AUTH_URL`. This gives Auth.js the correct public origin
for login redirects and API credential verification. PeaNUT verifies Basic
credentials by calling its own `/api/auth/verify` endpoint, so the container
must resolve its HTTPS name to the local NPM address and trust the certificate.
Compose supplies that local hostname mapping inside PeaNUT, so authentication
also works when the server's own DNS resolver does not use Pi-hole.
Without the explicit origin, Next.js can combine forwarded HTTPS with PeaNUT's
internal HTTP listener and reject otherwise valid API credentials. NPM retains
the HTTPS forwarded protocol for every route and passes client authentication
through normally.

`data/nginx-proxy-manager`, `data/letsencrypt`, and `data/peanut` persist across
recreation. PeaNUT explicitly uses `/config/auth.yaml` so its login persists as
well. The backup script stops only the running Pi-hole/NPM/PeaNUT services and
restores them after success or failure. These directories are under the
existing `/opt/homelab` backup path. When a host has its own backup helper, a
systemd override wraps that helper with container stop/start handling. Its
original backup commands, credentials, retention policy, and timer schedule
are retained; the generic restic configuration is not substituted.

## Verify and roll back

Check all three container health states and the existing NUT listener:

```bash
cd /opt/homelab/pihole
sudo docker compose ps
ss -lnt 'sport = :3493'
upsc cyberpower@localhost
dig @192.168.4.30 npm.bigbiscuit.org +short
dig @192.168.4.30 pihole.bigbiscuit.org +short
dig @192.168.4.30 peanut.bigbiscuit.org +short
```

Open all three web names from a client using Pi-hole DNS. Check the PeaNUT page for
battery charge, runtime, and line-power status. With HTTPS configured, verify
the certificate is trusted and covers all three names. Confirm HTTP redirects
to HTTPS and PeaNUT's `/api/ws` and `/api/ws/` return HTTP `403`.
An authenticated HTTPS request to `/api/v1/devices/cyberpower` should return
`200` with actual UPS values; the same request without Basic credentials should
return `401`. To check interactively without putting the password in shell
history, let curl prompt for it:

```bash
curl --user admin --fail --show-error \
  https://peanut.bigbiscuit.org/api/v1/devices/cyberpower
```

If initial deployment must be rolled back, stop the new services and restore
the saved Compose/environment definitions. The data directories are retained:

```bash
cd /opt/homelab/pihole
sudo docker compose stop peanut nginx-proxy-manager
sudo cp --preserve=all compose.yaml.before-web-services compose.yaml
sudo cp --preserve=all .env.before-web-services .env
sudo docker compose up -d --no-deps pihole
```

The bridge firewall rule can then be removed using `ufw delete` with the same
rule arguments. If a backup service override was installed, remove only its
`web-services.conf` drop-in and run `systemctl daemon-reload` to return to the
original backup entry point. Restore application data from the pre-deployment
snapshot only if needed; do not delete or replace the existing Pi-hole data
directory.

## Upstream references

- [NPM setup](https://nginxproxymanager.com/setup/)
- [PeaNUT release and configuration](https://github.com/Brandawg93/PeaNUT/releases/tag/v6.0.0)
- [PeaNUT 6.0.0 API authentication](https://github.com/Brandawg93/PeaNUT/blob/v6.0.0/src/auth.config.ts)
- [Docker internal bridge access](https://docs.docker.com/engine/network/port-publishing/#gateway-modes)
- [Cloudflare DNS challenge credentials](https://certbot-dns-cloudflare.readthedocs.io/en/stable/)
