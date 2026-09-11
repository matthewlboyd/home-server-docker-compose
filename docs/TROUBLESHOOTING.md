# Containers are healthy but unreachable after a reboot

A container's health check runs inside the container. It can pass even when
Docker has lost a network attachment or failed to publish its ports.

## 1. Check the address and container networks

**On the server:**

```bash
ip -brief -4 address
sudo docker compose --project-directory /opt/homelab/pihole ps
sudo docker inspect --format '{{.Name}} {{json .NetworkSettings.Networks}}' \
  pihole nginx-proxy-manager
sudo journalctl -b -u systemd-networkd-wait-online.service -u docker.service --no-pager
```

Confirm that the `LAN_IP` saved in the stack's `.env` is assigned to the LAN
interface. Pi-hole needs its default Compose network; NPM needs both the
default network and `peanut_link`. An empty network map or missing attachment
is a problem even when the status says **healthy**.

If Docker logged a port-binding failure before the LAN address appeared,
`network-online.target` completed too early. It can complete after its wait
service times out; ordering Docker after that target does not guarantee that
the required address exists.

## 2. Install the startup guard

**On the control computer, in this checkout's `ansible` directory:** verify
that the private inventory's `pihole_api_url` uses the same IPv4 address as
`LAN_IP` in the server's `.env`. Preview, review, then apply:

```bash
ansible-playbook docker-startup.yml --check --diff --ask-become-pass
ansible-playbook docker-startup.yml --ask-become-pass
```

The normal host, web-services, and HTTPS playbooks also install this guard.
It adds `/etc/systemd/system/docker.service.d/homelab-lan-address.conf` and the
root-owned `/usr/local/sbin/wait-for-homelab-address` helper. Applying it reloads
systemd without restarting Docker or the containers.

On future Docker starts, the helper waits up to 60 seconds for that exact IPv4
address. If it is still absent, the check fails and Docker's existing restart
policy retries. The full wait keeps retries outside Docker's startup rate
limit. Ansible checks the supported vendor policy before installing anything.
If the reserved address changes, update `.env` and the inventory, then rerun
the guard task before the next Docker restart.

## 3. Recover affected containers and verify access

The guard prevents the next startup race; it does not repair a container that
already lost its network. Once the LAN address is present, verify the existing
Compose configuration and persistent mounts. Recreate only the affected
services with their existing data; do not remove volumes or run `compose down`.
For example, if Pi-hole and NPM lost their attachments:

```bash
cd /opt/homelab/pihole
sudo docker compose config --quiet
sudo docker compose up -d --no-deps --force-recreate --no-build --pull never \
  --wait --wait-timeout 180 pihole nginx-proxy-manager
```

For an affected game server, follow its [safe stop and restart procedure](ZOMBOID.md)
before recreation so a running world is saved. Check DNS over UDP and TCP
from a LAN client, then open the HTTPS dashboards. Inspect network attachments
again; container health alone is not enough.

**On the server:** confirm the guard is loaded without restarting Docker:

```bash
sudo systemctl show docker.service --property=ExecStartPre
sudo systemctl cat docker.service
```

If the helper reports that the address is still absent, fix the LAN assignment
instead of removing the guard. After a failed boot, inspect the current boot's
Docker journal for the helper's result.
