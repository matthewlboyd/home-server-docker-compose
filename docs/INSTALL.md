# Install on a new server

**Have an existing backup? Use [Recovery](RECOVERY.md) instead.** This guide
creates a new installation. It does not restore previous application data.

Use Ubuntu Server 24.04 or newer, a reserved LAN address, SSH access, and a sudo
account. The baseline expects a supported CyberPower UPS connected by USB.
Keep router port forwarding disabled for these services.

Commands marked **server** run in Bash on Ubuntu. Commands marked **control
computer** run on the Mac or Linux computer that runs Ansible. Replace uppercase
placeholders before running a command.

## 1. Prepare the host

**Server — home directory:** install the setup tools, then clone this repository.

```bash
sudo apt update
sudo apt install git usbutils
git clone https://github.com/matthewlboyd/home-server-docker-compose.git
cd ~/home-server-docker-compose
```

Install [Docker Engine and the Compose plugin](https://docs.docker.com/engine/install/ubuntu/#install-using-the-apt-repository)
from Docker's Ubuntu repository. Install and sign in to
[Tailscale](https://tailscale.com/docs/install/linux). Confirm both work:

```bash
sudo docker compose version
tailscale status
```

Give the server independent DNS so it can reach its backups while Pi-hole is
stopped. Inspect the existing Netplan files and the interface name first:

```bash
ip -brief address
sudo netplan get
sudo install -m 600 host/90-server-dns.yaml.example /etc/netplan/90-server-dns.yaml
sudoedit /etc/netplan/90-server-dns.yaml
```

In the new file, replace `enp1s0` with the real interface. Check it against the
existing configuration before applying it. Keep console access available while
changing networking.

```bash
sudo netplan generate
sudo netplan try
sudo tailscale set --accept-dns=false
getent hosts example.com
```

Confirm `netplan try` only while connectivity works. If it times out, verify
the effective network state before retrying; see [Netplan's rollback notes](https://netplan.readthedocs.io/en/stable/netplan-try/).

## 2. Start Pi-hole

**Server — repository root:** create the stack directory and private files.

```bash
cd ~/home-server-docker-compose
sudo install -d -m 755 /opt/homelab/pihole
sudo install -d -m 700 /opt/homelab/pihole/secrets
sudo install -m 644 compose.yaml /opt/homelab/pihole/compose.yaml
sudo install -m 600 .env.example /opt/homelab/pihole/.env
sudoedit /opt/homelab/pihole/.env
```

Set `LAN_IP`, `TZ`, and `PIHOLE_HOST_RECORDS` to your actual address, timezone,
and local aliases. Review `HOMELAB_DOMAIN`; keep the pinned image tags and
`PIHOLE_DATA_DIR=./data/pihole` for a new installation.

Choose a Pi-hole password, save it in 1Password, and enter it at this hidden
prompt. Run the whole block in the same server Bash session:

```bash
sudo -v
read -r -s -p 'Pi-hole password: ' pihole_password
printf '\n'
printf '%s\n' "$pihole_password" | sudo tee /opt/homelab/pihole/secrets/web_password >/dev/null
unset pihole_password
sudo chmod 600 /opt/homelab/pihole/secrets/web_password
cd /opt/homelab/pihole
sudo docker compose config --quiet
sudo docker compose up -d pihole
sudo docker compose ps
```

Wait for Pi-hole to report **healthy**. Test DNS from a LAN client before
changing the router's DNS settings:

```bash
dig @SERVER_LAN_IP example.com A +short
```

Open `http://SERVER_LAN_IP:8080/admin/` and verify the saved password works.

## 3. Configure the host with Ansible

**Control computer — one-time setup:** install Python 3 with `venv` support if
needed. Create an isolated Ansible installation, including `netaddr` for the
playbooks' IP-address checks:

```bash
python3 -m venv ~/.venvs/homelab-ansible
source ~/.venvs/homelab-ansible/bin/activate
python -m pip install ansible netaddr
```

Clone the repository here too if needed. From its `ansible` directory, prepare
the private inventory:

```bash
cd ~/home-server-docker-compose/ansible
cp inventory/hosts.yml.example inventory/hosts.yml
chmod 600 inventory/hosts.yml
ansible-galaxy collection install -r requirements.yml
```

Edit `inventory/hosts.yml`: set the SSH address, user, key path, timezone, LAN
subnet, and Pi-hole API URL. Use an address this computer can reach. On Ubuntu
26.04 the example selects `/usr/bin/sudo.ws`; on a host without that binary,
set `ansible_become_exe` to its installed sudo path.

Verify ordinary SSH access once so the host key is known and your SSH key is
unlocked. Keep the inventory private. Pi-hole must already be running with
`secrets/web_password` present, even for the preview.

**Server:** connect the UPS and confirm that `lsusb -d 0764:0501` finds it.
For different hardware, review [UPS setup](UPS.md) before applying the baseline.

**Control computer — `ansible` directory:** test access and preview the changes.

```bash
ansible homelab -m ping
ansible-playbook site.yml --check --diff --ask-become-pass
```

Review the preview, then apply:

```bash
ansible-playbook site.yml --ask-become-pass
```

The sudo prompt asks for the **server account's password**. The playbook
configures host packages, firewall rules, security updates, Cockpit, NUT, and
the Pi-hole blocklists in `ansible/group_vars/all.yml`. It reconciles lists
marked `Managed by Ansible` and preserves other lists.

**Server:** run the [UPS checks](UPS.md#1-check-normal-operation). Confirm a
second SSH session works after the firewall changes.

## 4. Add backups and the web dashboards

For dashboard checks, temporarily set one LAN client's DNS to the server's
address. Leave the router and Tailscale DNS changes for step 5.

Complete these in order:

1. Follow [Backups](BACKUPS.md), including a successful backup and file restore.
2. Follow [Web services](WEB-SERVICES.md) to add NPM and PeaNUT.
3. Complete that guide's HTTPS step using the Cloudflare DNS token.

The web-services playbook requires working UPS telemetry and an installed
backup service. It checks both before making changes.

## 5. Enable client DNS and remote access

**Server:** advertise the LAN route through Tailscale. Replace `LAN_SUBNET`
with the real CIDR, such as `192.168.4.0/22`.

```bash
sudo tailscale set --advertise-routes=LAN_SUBNET
```

Approve the route in the Tailscale admin console. If you also want an exit node,
run `sudo tailscale set --advertise-exit-node` and approve that separately.
Ansible has already enabled IP forwarding.

**LAN client:** verify normal resolution and blocking:

```bash
dig @SERVER_LAN_IP example.com A +short
dig @SERVER_LAN_IP doubleclick.net A +short
```

The first lookup should resolve. Confirm the second is blocked in Pi-hole's
query log; the exact DNS response depends on the blocking settings.

Set the router's DNS server to `SERVER_LAN_IP`. An external secondary DNS server
lets clients bypass Pi-hole. In the Tailscale DNS console, add the same LAN
address as a global nameserver and enable DNS override for clients.

Verify the [four HTTPS dashboards](WEB-SERVICES.md) from both a LAN client and
a Tailscale client. Keep the server's own `--accept-dns=false` setting.

## Optional: add Project Zomboid

After the homelab and backups work, follow [Project Zomboid](ZOMBOID.md).
It uses a separate Compose project and the existing LAN/Tailscale connection.
