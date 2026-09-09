# Upgrading Pi-hole

Pi-hole Docker upgrades replace the container while retaining configuration in
the bind-mounted `data/pihole` directory. Read the release notes before changing
the pinned image tag.

## Create a recovery point

Run the configured restic backup and confirm that it saves a new snapshot:

```bash
sudo /usr/local/sbin/backup-server
```

## Update the image

In the Pi-hole deployment directory, save the current environment file and edit
`PIHOLE_IMAGE` to the desired dated release tag:

```bash
cd /opt/homelab/pihole
sudo cp --preserve=all .env .env.before-upgrade
sudoedit .env
sudo docker compose pull pihole
sudo docker compose up -d --no-deps pihole
```

Wait for the health check and inspect the installed component versions:

```bash
sudo timeout 180 sh -c \
  'until [ "$(docker inspect --format "{{.State.Health.Status}}" pihole)" = healthy ]; do sleep 2; done'
sudo docker inspect \
  --format '{{.Config.Image}} {{.State.Status}} {{.State.Health.Status}}' \
  pihole
sudo docker exec pihole pihole -v
```

Verify both normal resolution and blocking from a client, replacing the example
address with the server's LAN address:

```bash
dig @192.0.2.10 example.com A +short
dig @192.0.2.10 doubleclick.net A +short
```

## Roll back

If validation fails, restore the previous image setting and recreate the
container. The previous image normally remains in the local Docker cache:

```bash
cd /opt/homelab/pihole
sudo cp --preserve=all .env.before-upgrade .env
sudo docker compose up -d --no-deps pihole
sudo docker compose ps
```

If the persistent data was changed incompatibly, stop Pi-hole and restore
`/opt/homelab/pihole/data/pihole` from the pre-upgrade restic snapshot before
starting the previous image.
