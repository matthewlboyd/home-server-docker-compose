# Installation outline

These steps assume a current Ubuntu Server installation, a fixed or reserved LAN
address, and no public port forwards to the server.

## Host services

Install Docker Engine from Docker's official Ubuntu repository. Install Tailscale
and authenticate the server before removing any previous remote-access route.
Cockpit can be installed from Ubuntu packages for a small host-management UI.

Configure the server as a Tailscale subnet router and optional exit node:

```bash
sudo install -m 600 host/99-tailscale.conf /etc/sysctl.d/99-tailscale.conf
sudo sysctl --system
sudo tailscale set \
  --advertise-routes=192.168.0.0/24 \
  --advertise-exit-node
```

Approve the advertised route and exit node in the Tailscale admin console. Replace
the example subnet with the actual LAN CIDR.

Configure UFW with default-deny incoming and routed policies. Allow Tailscale,
LAN SSH, and Tailscale's direct UDP port:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw default deny routed
sudo ufw allow in on tailscale0 comment 'Tailscale'
sudo ufw allow from 192.168.0.0/24 to any port 22 proto tcp comment 'SSH from LAN'
sudo ufw allow 41641/udp comment 'Tailscale direct connections'
sudo ufw enable
```

## Pi-hole

Create a deployment directory and copy the public configuration:

```bash
sudo install -d -o "$USER" -g "$USER" /opt/homelab/pihole
cp compose.yaml .env.example /opt/homelab/pihole/
mkdir -p /opt/homelab/pihole/secrets
cd /opt/homelab/pihole
cp .env.example .env
```

Edit `.env`, then write the Pi-hole password without placing it in shell history:

```bash
read -r -s -p 'Pi-hole password: ' pihole_password; printf '\n'
umask 077
printf '%s\n' "$pihole_password" > secrets/web_password
unset pihole_password
docker compose config --quiet
sudo docker compose up -d
cd - >/dev/null
```

Set the router's custom DNS server to the host's LAN address. Do not configure an
external secondary DNS server if every client must use Pi-hole filtering.

In the Tailscale DNS console, add the Pi-hole LAN address as a global nameserver
and enable DNS override. On the Pi-hole host itself, disable tailnet DNS and use
independent upstream resolvers so maintenance can stop Pi-hole safely:

```bash
sudo install -m 600 host/90-server-dns.yaml.example \
  /etc/netplan/90-server-dns.yaml
sudo netplan generate
sudo netplan apply
sudo tailscale set --accept-dns=false
```

Edit the installed Netplan file for the actual interface before applying it.

## Encrypted off-site backups

Create a private S3-compatible bucket and a key limited to read, write, and delete
on that bucket. Save the access key, secret key, and an independent restic
repository password in a password manager.

Install restic and create `/etc/restic` with mode `700`. Store the access key in
`aws-access-key-id`, the secret key in `aws-secret-access-key`, and the repository
password in `password`. Each file must contain only its value followed by a
newline and must be owned by root with mode `600`. Copy and edit the non-secret
examples:

```bash
sudo apt install restic
sudo install -d -m 700 /etc/restic
sudo install -m 600 restic/repository.example /etc/restic/repository
sudo install -m 600 restic/region.example /etc/restic/region
sudo install -m 600 restic/backup-paths.example /etc/restic/backup-paths
sudo install -m 600 restic/excludes.example /etc/restic/excludes
sudo install -m 750 scripts/restic-server /usr/local/sbin/restic-server
sudo install -m 750 scripts/backup-server /usr/local/sbin/backup-server
```

Initialize and test the repository before enabling the timer:

```bash
sudo restic-server init
sudo backup-server
sudo restic-server check
sudo install -m 644 systemd/homelab-backup.service \
  /etc/systemd/system/homelab-backup.service
sudo install -m 644 systemd/homelab-backup.timer \
  /etc/systemd/system/homelab-backup.timer
sudo systemctl daemon-reload
sudo systemctl enable --now homelab-backup.timer
```
