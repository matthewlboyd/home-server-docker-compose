# Automatic image updates

Pi-hole, Nginx Proxy Manager, and PeaNUT update automatically. **Project Zomboid
stays manual**, including its Docker image and game files.

## How it works

1. Renovate proposes stable image versions and immutable digests in
   [compose.yaml](../compose.yaml). Major releases are included.
2. GitHub runs the tests and checks that the images exist. Passing Renovate PRs
   merge automatically; failed checks leave the PR open.
3. The server applies merged updates once a day, between **4:15 and 4:20 a.m.**
   in the server's timezone (Pacific on Bombadil), after the nightly backup.
   It updates one service at a time. Renovate can prepare PRs throughout the day.

Before changing a service, the updater pulls the image and checks the running
stack. It briefly stops that service, copies its data, and starts the new image.
It then checks DNS, trusted HTTPS, NPM login, PeaNUT login, the UPS dashboard,
and live UPS data. NPM upgrades also test certificate renewal against Let's
Encrypt's staging service. Expect a short DNS interruption when Pi-hole updates.

If a check fails, it restores **both the previous image and its data**. That
candidate is blocked until a different image is merged or you explicitly retry.
Interrupted updates recover when the service exits or after a reboot, without
waiting for the next daily window or installing another release. If recovery fails, the
pending transaction remains for repair and further updates stop.

Only the three image references are read from GitHub. The server never downloads
and executes new scripts, playbooks, or arbitrary Compose settings. Ansible is
still required to deploy those changes. The updater cannot restart Zomboid and
shares a lock with the nightly backup to prevent overlapping maintenance.

## Set up or rebuild

First complete the web services, HTTPS, and backup setup. From your trusted
computer:

```bash
ansible-playbook -i ansible/inventory/hosts.yml ansible/image-updates.yml --ask-become-pass
```

The playbook pins the images already running, without restarting them, and
enables the timer. It preserves the actual Pi-hole data directory. If the server
was off during its daily window, the missed check runs when it returns.

In GitHub, install the [Renovate app](https://github.com/apps/renovate) for this
repository. Keep automatic merging enabled, and require the `Validate` check on
`main` before merging. The policy is in [renovate.json](../renovate.json).

Image versions belong in Compose. Old `PIHOLE_IMAGE`, `NPM_IMAGE`, and
`PEANUT_IMAGE` entries in private `.env` copies are ignored and can be removed;
credentials and host settings stay in `.env`/1Password. The game keeps its own
separate configuration.

## Check status

On the server:

```bash
systemctl list-timers homelab-image-updates.timer
sudo journalctl -u homelab-image-updates.service -n 50 --no-pager
sudo update-homelab-images --check
sudo check-homelab-health
```

`--check` previews changes. To apply merged image updates immediately:

```bash
sudo systemctl start homelab-image-updates.service
```

GitHub reports failed repository checks. Server rollout failures appear in the
systemd journal and failed service status; this setup does not send email or
push alerts. Tests reduce risk but cannot prove every new application version
works. The live checks cover this homelab's main functions.

## Pause, roll back, or retry

Pause future updates before investigating:

```bash
sudo systemctl disable --now homelab-image-updates.timer
```

This does not interrupt an update already in progress. Let it finish, then use
the retained recovery copy if needed:

```bash
sudo update-homelab-images --rollback peanut
```

Replace `peanut` with `pihole` or `nginx-proxy-manager`. Rollback restores data
from **before the last update**, so changes made afterward will not be active.
The replaced data is kept beside its original directory with `.image-failed`
appended. One recovery copy per service is kept under
`/var/lib/homelab-image-updates/backups/`; each next update replaces that copy.
These are local copies, so keep the independent nightly restic backups too.

To retry a blocked image after resolving the problem, remove only its entry
from the root-owned failure list and run the service again:

```bash
sudoedit /var/lib/homelab-image-updates/failed.json
sudo systemctl start homelab-image-updates.service
```

Do not delete `pending.json` to bypass a failed recovery. Fix the reported
problem and run `sudo update-homelab-images --recover` to resume recovery first.

Resume future updates when ready:

```bash
sudo systemctl enable --now homelab-image-updates.timer
```

For game updates, follow [Project Zomboid](ZOMBOID.md).
