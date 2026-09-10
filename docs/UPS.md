# Check the UPS

**Run `upsc cyberpower@localhost` on the server.** During normal utility power,
look for `ups.status: OL` and current battery readings.

The baseline uses Network UPS Tools (NUT) with a USB-connected CyberPower
CP1000PFCLCD, USB ID `0764:0501`, and the `usbhid-ups` driver. Some `lsusb`
versions show a different CyberPower model for this shared USB ID.

## 1. Check normal operation

**Server:** these commands read status without interrupting power.

```bash
lsusb -d 0764:0501
sudo systemctl status nut-driver@cyberpower nut-server nut-monitor --no-pager
upsc cyberpower@localhost
```

Confirm:

- The USB device is present and the NUT services are running.
- `ups.status` is `OL` while utility power is available.
- Battery charge, runtime, input voltage, and load are present where supported.

Active services alone do not prove that NUT can read the UPS. If telemetry is
missing, inspect the driver log:

```bash
sudo journalctl -u nut-driver@cyberpower -n 50 --no-pager
```

The [baseline playbook](../ansible/site.yml) configures NUT and reapplies Ubuntu's
USB permissions. This handles a UPS connected before the NUT packages were
installed. For different hardware, update `ansible/group_vars/all.yml` and
review `ansible/tasks/nut.yml` before running the baseline.

## 2. Check the dashboard

After [web-services setup](WEB-SERVICES.md), open
`https://peanut.bigbiscuit.org` and confirm that its battery and line-power
readings match `upsc`.

NUT listens only on localhost, port `3493`. PeaNUT reads that local service;
Nginx Proxy Manager provides access to its web dashboard. PeaNUT does not
receive NUT's shutdown-monitor password.

## 3. Understand shutdown and restart

NUT requests an orderly shutdown when the UPS reports both **on battery** and
**low battery**. There is no separate early-shutdown timer.

Check the server's BIOS setting for power restoration. On this NUC, the setting
is named **State After G3: S0 State**. Ansible does not configure the BIOS, and
normal telemetry checks do not verify a complete outage/shutdown/restart cycle.

The generated NUT monitor password lives in `/etc/nut/.monitor-password`.
The backup includes it with `/etc`; a new installation can generate a new one.

## Optional: briefly test battery operation

Use this only when you can reconnect power promptly and the battery is charged.
This tests the transition to battery, not low-battery shutdown or automatic restart.

1. Leave the server connected to the UPS and unplug the UPS's input from the wall.
2. Run `upsc cyberpower@localhost` and confirm `ups.status: OB`.
3. Reconnect utility power before the low-battery threshold and confirm `OL` returns.

Do not run `upsmon -c fsd` as a status check: it starts a real forced shutdown.
