# Docker Compose home server

A Docker Standalone setup with a small CLI-managed Portainer bootstrap stack and
a Git-managed application stack containing Traefik, Pi-hole v6, nginx, ddclient,
and a Docker socket proxy.

Portainer manages the same Docker host on which it runs. It is deliberately kept
out of the application stack it manages. If Portainer stops or an application
deployment fails, it can still be recovered through SSH and Docker Compose.

## Repository contents

- `portainer-compose.yml`: Portainer bootstrap and recovery deployment. Run it
  only from the Docker host.
- `docker-compose.yml`: application stack deployed from Git through Portainer.
- `.env.example`: variable reference with empty credential and image fields.
- `.gitignore`: excludes credentials, application state, and backups.
- `capture-image-pins.sh`: prints image references from an existing installation.

Populated `.env` files, DNS credentials, passwords, certificates, application
databases, and private Traefik/ddclient configuration stay on the server. Git
ignore rules do not remove secrets that have already been committed.

## Architecture and trust boundary

Portainer connects to the local Docker socket and therefore has effective
root-level control over the Docker host. The socket is mounted read-only to
protect the socket filesystem entry, but this does not make Docker API requests
read-only. Restrict Portainer administrator access and keep the host reachable
over SSH for recovery.

The stacks share the external `home-server-proxy` Docker network:

```text
SSH / Docker Compose
        │
        └── portainer-compose.yml
              └── Portainer ───────┐
                                   │ home-server-proxy
Portainer Git deployment           │
        └── docker-compose.yml      │
              ├── Traefik ─────────┘
              ├── Pi-hole
              ├── nginx
              ├── ddclient
              └── socket-proxy
```

Portainer remains accessible directly at `https://SERVER_IP:9443` on the LAN.
This path does not depend on DNS, Pi-hole, or Traefik. Portainer's generated
certificate may produce a browser warning until a trusted certificate is
configured. The optional `portainer.DOMAINNAME` Traefik route uses the existing
`chain-basic-auth@file` middleware for an additional authentication layer.
Prefer LAN or VPN access; if the hostname is internet-accessible, add a strong
identity-aware access layer such as Cloudflare Access and restrict origin access.

## Configuration

Copy `.env.example` to `.env` on the Docker host and fill in real values. Keep
that file outside Git and restrict its permissions. When deploying the
application stack from Git, enter the corresponding values in Portainer's stack
environment variables or upload the private `.env` through Portainer.

| Variable | Used by | Purpose |
| --- | --- | --- |
| `DATA_DIR` | Both | Absolute host directory containing configuration and application data. |
| `DOMAINNAME` | Both | Cloudflare-managed domain used for HTTPS routes. |
| `SERVER_IP` | Both | IPv4 address assigned to the server's LAN interface. |
| `TZ` | Both | IANA time zone, such as `Etc/UTC`. |
| `PUID`, `PGID` | Applications | Host IDs for ddclient; default to `1000`. |
| `CLOUDFLARE_EMAIL` | Applications | ACME account email address. |
| `TRAEFIK_ACME_CASERVER` | Applications | ACME directory URL. |
| `CLOUDFLARE_DNS_API_TOKEN` | Applications | Token with Zone Read and DNS Edit for the domain. |
| `PIHOLE_PASSWORD` | Applications | Pi-hole web/API password. |
| `PORTAINER_IMAGE` | Bootstrap | Tested Portainer image reference. |
| Other `*_IMAGE` variables | Applications | Tested application image references. |

The domain routes its apex to nginx and uses `traefik.`, `portainer.`, and
`pihole.` subdomains for their interfaces. DNS records and network access are
configured separately. The ddclient target remains in its private host-mounted
configuration.

To capture immutable references from existing containers, run this on the
Docker host while the containers still exist:

```bash
bash capture-image-pins.sh
```

The helper reads image metadata for the five existing containers and resolves
the socket-proxy manifest with Docker Buildx. It prints environment assignments
and does not deploy or update containers.

## Required host files

All application mounts use `DATA_DIR`, so they are independent of Portainer's
repository checkout location. Create and protect these paths before deployment:

```text
DATA_DIR/
├── ddclient/                 # Private ddclient configuration
├── pihole/
│   ├── pihole/               # Pi-hole configuration and databases
│   └── dnsmasq.d/            # Optional custom DNS configuration
├── portainer/data/           # Portainer state and stack metadata
├── shared/                   # Files referenced by Traefik middleware
├── traefik2/
│   ├── acme/acme.json        # ACME certificate storage, mode 600
│   └── rules/                # Traefik dynamic configuration
└── website/                  # nginx content
```

The rules directory must provide `chain-basic-auth` and `chain-no-auth`. Their
password files are intentionally excluded. Review custom dnsmasq files for
Pi-hole v6 before enabling `FTLCONF_misc_etc_dnsmasq_d`. Remove `NET_ADMIN` from
Pi-hole when DHCP and IPv6 Router Advertisements are unused.

## Bootstrap Portainer

Create the shared network once, validate the bootstrap configuration, and start
Portainer from SSH:

```bash
docker network inspect home-server-proxy >/dev/null 2>&1 || \
  docker network create home-server-proxy

docker compose --env-file .env \
  -p portainer \
  -f portainer-compose.yml \
  config --quiet

docker compose --env-file .env \
  -p portainer \
  -f portainer-compose.yml \
  up -d
```

Verify the container and direct recovery endpoint:

```bash
docker compose -p portainer -f portainer-compose.yml ps
docker compose -p portainer -f portainer-compose.yml logs --tail=100 portainer
```

Open `https://SERVER_IP:9443` from the LAN and confirm login before moving the
application stack into Portainer.

## Deploy applications from Git

In Portainer, select **Stacks → Add stack → Git Repository**. Use this repository,
select the intended branch, set the Compose path to `docker-compose.yml`, and
provide the application variables. Because the repository is public, Git
credentials are not required. Start with manual **Pull and redeploy** updates;
enable automatic GitOps polling only after recovery and backups have been tested.

The external `home-server-proxy` network must exist first. The Compose file uses
absolute host mounts, avoiding Portainer Business Edition's relative-volume
feature. Portainer clones the configuration but does not populate `DATA_DIR`.

Resources created earlier with the Compose CLI appear as external resources in
Portainer and have limited stack control. Portainer does not automatically adopt
an existing CLI stack as a Git-managed stack. Moving an installation therefore
requires a maintenance window:

1. Back up `.env`, `DATA_DIR/portainer/data`, and consistent application data.
2. Bootstrap Portainer separately and verify `https://SERVER_IP:9443`.
3. Validate `docker-compose.yml` with the real variables.
4. Stop and remove only the old application containers. Do not remove bind-mounted
   data, use `docker compose down -v`, or remove the Portainer bootstrap container.
5. From the direct LAN Portainer endpoint, deploy the Git application stack.
6. Verify Pi-hole DNS, public DNS, HTTPS, authentication, certificates, ddclient,
   and container health before ending the maintenance window.

Fixed container names will conflict if the old and new application stacks run at
the same time. Stopping Traefik and Pi-hole also interrupts HTTPS and LAN DNS, so
keep the direct Portainer endpoint open and ensure the host has working upstream
DNS during the handover.

## Upgrades, backup, and recovery

Upgrade applications by changing tested image references in Portainer's stack
variables or Git configuration, then using **Pull and redeploy**. Do not use the
Portainer interface to stop, remove, or redeploy the Portainer container itself;
the management connection can disappear partway through the operation.

Before a Portainer upgrade, back up `DATA_DIR/portainer/data`. Then update
`PORTAINER_IMAGE` in the private `.env` and run from SSH:

```bash
docker compose --env-file .env \
  -p portainer \
  -f portainer-compose.yml \
  pull

docker compose --env-file .env \
  -p portainer \
  -f portainer-compose.yml \
  up -d
```

If Portainer is unavailable, recover it with the same `up -d` command and inspect
its logs. Its state is restored from `DATA_DIR/portainer/data`. The application
containers continue running when Portainer is stopped because Docker manages
their lifecycle and restart policies.

Back up all of `DATA_DIR` and the private deployment environment to encrypted
storage on another device or off site. Use application exports or a maintenance
stop for consistent Pi-hole and Portainer databases. Git contains the deployment
recipe; it is not a backup of state, credentials, or certificates.

## Validation

Validate both files without printing expanded credentials:

```bash
docker compose --env-file .env -f portainer-compose.yml config --quiet
docker compose --env-file .env -f docker-compose.yml config --quiet
```

Compose validation does not test image compatibility, middleware contents,
credentials, firewall rules, or application startup.

## References

- [Portainer local Docker environment](https://docs.portainer.io/admin/environments/add/local)
- [Portainer Git stack deployment](https://docs.portainer.io/user/docker/stacks/add#option-3-git-repository)
- [Portainer external resource limitations](https://docs.portainer.io/advanced/access-control#resources-deployed-outside-of-portainer)
- [Portainer Docker Standalone upgrades](https://docs.portainer.io/start/upgrade/docker)
