# Project Zomboid

**Join `zomboid.${HOMELAB_DOMAIN}` on port `16261`.** Replace `${HOMELAB_DOMAIN}`
with the value in the homelab's private `.env`. The server uses the current
stable Steam branch and allows four players over the LAN or Tailscale.

The game runs separately from Pi-hole and the web dashboards. It needs no NPM
proxy or HTTPS certificate. Only UDP ports `16261` and `16262` bind to the
server's LAN address; RCON stays inside Docker.

## 1. Check the prerequisites

Complete [Installation](INSTALL.md), [Web services](WEB-SERVICES.md), and
[Backups](BACKUPS.md) first. The game setup expects an x86_64 host with at least
8 GB RAM and 15 GB of free disk space. Defaults give Java 4 GB of heap inside
a container limited to 6 GB.

The root Compose file adds the `zomboid` DNS name using `HOMELAB_DOMAIN`.
Apply the current web-services playbook first if that DNS name is missing.
Your clients need the same stable game version as the server.

## 2. Deploy the game

**Control computer — repository's `ansible` directory:** use your existing
private inventory and preview the game deployment.

```bash
ansible-playbook -i inventory/hosts.yml zomboid.yml --check --diff --ask-become-pass
```

Review the preview, then apply:

```bash
ansible-playbook -i inventory/hosts.yml zomboid.yml --ask-become-pass
```

The first run downloads the server from Steam. Allow up to 15 minutes for
initial startup. The playbook creates private passwords, prepares persistent
storage, and adds the game to the existing backup job. Existing worlds and
passwords are preserved on later runs.

## 3. Join and check the server

**Server:** open a root shell to access the private game directory, then check
that the container is healthy and responds to RCON.

```bash
sudo -i
cd /opt/homelab/zomboid
docker compose ps
docker compose exec -T zomboid rcon-cli -c /home/steam/server/rcon.yml -T 10s players
exit
```

**Game client:** use **Join → Favorites → Add Server**:

| Field | Value |
| --- | --- |
| Address | `zomboid.${HOMELAB_DOMAIN}`, or the server's LAN IP |
| Port | `16261` |
| Server password | `SERVER_PASSWORD` from the game's private `.env` |
| Username/password | Your player account; use a separate account from `admin` |

The server is not listed publicly and does not enable router UPnP. For remote
play, connect to Tailscale and use the existing subnet route and Pi-hole DNS.

Use `sudoedit /opt/homelab/zomboid/.env` privately to retrieve the server,
administrator, and RCON passwords, then save them in 1Password. Do not paste
that file into Git, shared logs, or support messages.

## 4. Change settings or update the game

The game's private `.env` controls its image, stable branch, admin/RCON
credentials, memory, and player limit. The join password, public-listing
setting, and UPnP setting are stored in
`data/server-data/Server/homelab.ini`. Stop the game before editing its files.
If you change the join password, update both `SERVER_PASSWORD` in `.env` and
`Password` in the INI to the same value. Keep all three saved passwords as long
hexadecimal strings, and keep `Public=false` and `UPnP=false`.

**Server — game directory:**

```bash
sudo -i
cd /opt/homelab/zomboid
docker compose stop zomboid
sudoedit .env
docker compose up -d --wait --wait-timeout 900 zomboid
exit
```

`UPDATE_ON_START=false` prevents routine starts and nightly backups from
upgrading the downloaded game. To update intentionally:

1. Run and verify a [backup](BACKUPS.md#run-and-check-a-backup).
2. Check the [official release notes](https://projectzomboid.com/blog/news/)
   for save compatibility and the required client version.
3. Set `UPDATE_ON_START=true` in the game's `.env`, then recreate the container.
4. Verify the server and a real client connection. Set it back to `false` and
   recreate once more before the next scheduled backup.

The pinned Docker image and downloaded Steam game build are separate things.
Changing the image pin does not by itself choose a specific game version.
Keep the pre-update snapshot until players can connect to the existing world.

## Restore the game

**Replacement server:** recover the homelab and backup access first. Restore
the entire game directory from the chosen snapshot into a staging directory:

```bash
read -r -p 'Snapshot ID: ' snapshot_id
restore_dir=$(mktemp -d /tmp/zomboid-restore.XXXXXX)
sudo restic-server restore "$snapshot_id" --target "$restore_dir" --include /opt/homelab/zomboid
```

Use `restic-bombadil` on an existing host that uses that wrapper. Stop any game
container, then use `sudo mv` to move an existing `/opt/homelab/zomboid` aside.
Copy the restored directory into place with `sudo cp -a`.

Keep `.env` root-owned with mode `0600`, and the game data owned by UID/GID
`1000:1000`. Update `LAN_IP` if necessary and keep `UPDATE_ON_START=false`.
Restore the world and `data/server-files` together so the recovered server uses
the matching installed build.

Preview and run `zomboid.yml` to restore backup integration and start the game.
Verify a real player can load the world before discarding the recovery copy.

## Image reference

This setup uses the [indifferentbroccoli image](https://github.com/indifferentbroccoli/projectzomboid-server-docker),
pinned to a version and digest. Its entrypoint runs as container root to prepare
the Steam user and data ownership; no privileged mode, host networking, or
Docker socket is granted. It supports password environment variables rather
than Docker secret files, so the game's `.env` stays private.
