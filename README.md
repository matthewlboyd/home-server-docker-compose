# Docker Compose home server

A Docker Standalone stack with Traefik, Portainer CE, Pi-hole v6, nginx,
ddclient, and a Docker socket proxy. Deployment settings are supplied through
environment variables, so the same Compose file can be managed from Git in
Portainer or used with the Docker Compose CLI.

## Repository contents

- `docker-compose.yml`: service definitions, routing, mounts, and logging.
- `.env.example`: example settings and empty credential/image fields.
- `.gitignore`: excludes local credentials, application state, and backups.
- `capture-image-pins.sh`: prints image references from an existing installation.

Only these configuration templates and documentation belong in this repository.
Populated `.env` files, DNS credentials, password files, certificates, application
databases, and host-specific Traefik/ddclient configuration stay on the server.
Git ignore rules do not remove files that have already been committed.

## Configuration

Use `.env.example` as the variable reference. For a new local checkout, copy it
to `.env` and fill in the values. When reusing an existing `.env`, merge the
required settings without overwriting other application variables.

| Variable | Purpose |
| --- | --- |
| `DATA_DIR` | Absolute data/configuration directory on the Docker host, without a trailing slash. |
| `DOMAINNAME` | Cloudflare-managed DNS domain used for web routing. |
| `SERVER_IP` | IPv4 address assigned to the server's LAN interface for Pi-hole DNS. |
| `TZ` | IANA time zone, such as `Etc/UTC`. |
| `PUID`, `PGID` | Host user/group IDs for ddclient; default to `1000`. |
| `CLOUDFLARE_EMAIL` | ACME account email address. |
| `TRAEFIK_ACME_CASERVER` | ACME directory URL; use the directory matching the certificate environment. |
| `CLOUDFLARE_DNS_API_TOKEN` | Cloudflare token with Zone Read and DNS Edit for the configured domain. |
| `PIHOLE_PASSWORD` | Pi-hole web/API password. |
| `*_IMAGE` | Tested service image references; immutable digests are recommended. |

The domain supplies these routes: the apex for nginx and `traefik.`,
`portainer.`, and `pihole.` subdomains for their respective interfaces. DNS
records and network access to the reverse proxy must be configured separately.
The ddclient update target is configured in its private on-host configuration.

To reuse images from an existing installation, run this helper on the Docker
host while the named application containers still exist:

```bash
bash capture-image-pins.sh
```

Copy the printed assignments into the deployment environment. The helper reads
local image metadata for the five application containers and queries the
socket-proxy registry through Docker Buildx. For a fresh installation, populate
the image variables with tested references manually. The helper does not deploy
or update containers.

## Required host files

All application mounts use `DATA_DIR`, including website content. They point at
the Docker host filesystem, independently of the repository checkout location.
The following paths must already exist with suitable ownership and permissions:

```text
DATA_DIR/
├── ddclient/                 # Private ddclient configuration
├── pihole/
│   ├── pihole/               # Pi-hole configuration and databases
│   └── dnsmasq.d/            # Optional custom DNS configuration
├── portainer/data/           # Portainer state
├── shared/                  # Files referenced by Traefik middleware
├── traefik2/
│   ├── acme/acme.json        # ACME certificate storage, mode 600
│   └── rules/               # Traefik dynamic configuration
└── website/                 # nginx content
```

The `traefik2` directory name is a storage convention, not a Traefik version pin.
The rules directory must define the referenced `chain-basic-auth` and
`chain-no-auth` middlewares. These definitions and their authentication files
are not included. Preserve the contents of existing certificate and data files.
Binding the Docker socket requires a local Docker Engine at
`/var/run/docker.sock`.

Pi-hole uses v6 environment variables. Review custom dnsmasq files before
enabling the commented `FTLCONF_misc_etc_dnsmasq_d` setting. Remove `NET_ADMIN`
if DHCP and IPv6 Router Advertisements are unused. DNS is published on the
specified LAN IPv4 address; web interfaces use Traefik on ports 80 and 443.

## Portainer and Git

For a new stack, select **Stacks → Add stack → Git Repository**, choose the
repository and branch, and set the Compose path to `docker-compose.yml`.
Provide the deployment variables in Portainer, either individually or using
**Load variables from .env file**. The repository's `.env.example` is a reference,
not a populated environment file. See [Portainer's stack setup documentation](https://docs.portainer.io/user/docker/stacks/add).

Absolute host mounts avoid dependence on Portainer's Business Edition feature
for relative path volumes. Host files are not downloaded or provisioned by this
repository. [Portainer documents that distinction here](https://docs.portainer.io/user/docker/stacks/add#relative-path-volumes).

Moving an existing CLI-managed stack into Portainer requires a coordinated
handover; creating a second stack will conflict with the fixed container names.
Preserve the existing Compose project identity and point `DATA_DIR` at the
existing data root. This file includes Portainer itself, so retain SSH access
and a local Compose copy for bootstrap and recovery.

Start with manual deployments. After an initial deployment is working, edit
the Compose file in Git and use **Pull and redeploy** in Portainer to apply a
reviewed revision. [Portainer describes Git stack updates here](https://docs.portainer.io/user/docker/stacks/edit#pull-and-redeploy).

## Validation and backups

Validate a populated local environment without printing expanded credentials:

```bash
docker compose --env-file .env -f docker-compose.yml config --quiet
```

This checks Compose configuration, not image compatibility or application
startup. Verify the selected Traefik version, middleware, DNS resolution,
HTTPS, logins, and ddclient updates on the host before relying on the deployment.

Back up `DATA_DIR` and the private deployment environment to encrypted storage
on another device or off site. Capture consistent application databases using
application backups or a maintenance stop. Git stores the deployment recipe;
the separate backup stores the data and credentials needed for recovery.
