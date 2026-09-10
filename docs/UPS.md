# UPS monitoring

The host uses Network UPS Tools (NUT) to monitor a directly attached CyberPower
CP1000PFCLCD over USB. The device reports USB ID `0764:0501` and uses NUT's
`usbhid-ups` driver. Some versions of `lsusb` label this shared USB interface as
a CP1500 AVR; that label does not identify the actual UPS model.

The Ansible baseline installs and configures NUT in standalone mode. Its data
server listens only on localhost, and a generated monitor password is stored in
`/etc/nut/.monitor-password`. The password and NUT configuration are included in
the existing restic backup because `/etc` is backed up.

PeaNUT provides the web dashboard at `peanut.bigbiscuit.org` after the
[web-services setup](WEB-SERVICES.md). Its container uses the host network to
read NUT on `127.0.0.1:3493`, without the shutdown-monitor credentials. Its web
listener binds only to the private `br-peanut` bridge address on port `8081`;
Nginx Proxy Manager is its web entry point. Port `3493` stays localhost-only.

The playbook also reloads and applies Ubuntu's packaged NUT udev rule. This is
needed when the USB cable was connected before NUT was installed; without it,
the driver cannot open the otherwise supported device.

NUT initiates an orderly host shutdown when the UPS reports that it is on
battery and has reached its low-battery condition. The firmware setting **State
After G3: S0 State** then starts the host when utility power returns. No timed
early-shutdown rule is configured; this keeps the setup simple and uses the
UPS's own battery threshold.

After applying Ansible, verify the live data path without disconnecting utility
power:

```bash
sudo systemctl status nut-server nut-monitor --no-pager
sudo upsc cyberpower@localhost
```

The output should include `ups.status: OL` while the UPS is on utility power.
Also confirm that battery charge, runtime, input voltage, and load values are
present when supported by the device.

Do not use `upsmon -c fsd` for a routine test. It initiates the real forced
shutdown sequence. A later controlled outage test can unplug the UPS input from
the wall while leaving the NUC connected to the UPS, confirm `ups.status: OB`,
and reconnect utility power before the low-battery threshold is reached.
