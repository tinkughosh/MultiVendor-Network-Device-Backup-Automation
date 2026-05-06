# 📨 Examples — what a run produces

Two example artifacts that match what `Global-Master-Backup.py` emits at the end of a real run. All hostnames, IPs, and locations below are RFC-5737 documentation values — nothing real.

---

## 📋 [`example-failed-reasons.csv`](example-failed-reasons.csv)

The CSV the script writes to `paths.reports_dir` and uploads to Box (`box.failed_csv_folder_id`) at the end of every run. Every row is a device that did **not** finish with `success` (or `success (N command errors)`).

### Schema

The script writes exactly these columns (see `backup_devices()` in `Global-Master-Backup.py`):

| Column | Source |
|---|---|
| `LocationName` | SolarWinds `Orion.Nodes.Location` |
| `Hostname` | SolarWinds `Orion.Nodes.Caption` |
| `IP` | SolarWinds `Orion.Nodes.IP_Address` |
| `Vendor` | SolarWinds `Orion.Nodes.Vendor` |
| `MachineType` | SolarWinds `Orion.Nodes.MachineType` |
| `AlertStatus` | SolarWinds `Orion.Nodes.StatusDescription` |
| `DeviceType` | resolved netmiko driver (e.g. `cisco_ios_ssh`) |
| `LoginMethod` | `ssh` or `telnet` from the custom property |
| `Result` | the per-device outcome string — see below |

### What you'll see in `Result`

| Result text | Meaning | What to do |
|---|---|---|
| `error: Connection timeout` | netmiko `NetmikoTimeoutException` — device unreachable on the chosen port (SSH 22 / Telnet 23) | Check device reachability, ACLs, mgmt VRF, port availability |
| `authentication failure` | netmiko `NetmikoAuthenticationException` — TCP connected but credentials rejected | Wrong `Creds` value in SolarWinds, wrong password in `device_credentials`, account locked, or enable mode misconfigured |
| `error: ssh_exception.SSHException: Error reading SSH protocol banner` | TCP connected but SSH banner exchange failed — usually old SSH algorithms or rate-limited login | Check device SSH version / kex / cipher support; retry; consider `disabled_algorithms` in netmiko |
| `error: exceptions.ReadTimeout: Pattern not detected: '#$' in output` | Connected and authenticated but a `send_command()` call's prompt detector hit the read timeout | Increase `read_timeout` in `NETMIKO_TUNING` for that driver; check for paginated output (`terminal length 0` not set) |
| `error: TimeoutError: timed out` | Bare socket timeout (lower in the stack than netmiko) | Same triage as Connection timeout |
| `error: socket.gaierror: ...` | DNS resolution failed for the IP/host | Almost always a stale SolarWinds inventory entry |
| `error: NetmikoTimeoutException: ...` | Verbose form of Connection timeout | Same triage as Connection timeout |
| `skipped: Creds custom property empty` | SolarWinds custom property `Creds` is blank for this device | Populate `Creds` in SolarWinds |
| `skipped: Creds key "X" not found in device_credentials (config.json)` | `Creds` is set, but no matching key in `config.json` → `device_credentials` | Add the credential key + password to `config.json`, or fix the typo in SolarWinds |
| `skipped: DeviceType custom property empty` | SolarWinds custom property `DeviceType` is blank | Populate `DeviceType` (e.g. `cisco_ios`) in SolarWinds |

> 💡 Devices with `success (N command errors)` are **not** in this CSV — they appear in the success count and the per-device state-backup file contains the per-command error details. The CSV is reserved for devices that did not produce a complete backup.

---

## 📧 [`example-email-body.html`](example-email-body.html)

The literal HTML body of the summary email the script sends after every run (see `send_email()` in `Global-Master-Backup.py`). Subject line format:

```
Network Backup Summary at HH/MM on DD/MM/YYYY
```

Open the HTML file directly in a browser to preview how the email renders.

### Reading the table

| Column | What it means |
|---|---|
| **Backup-Enabled in SolarWinds** | Total devices with `Backup_Enabled = true` returned by the SWQL query — the denominator for all percentages |
| **Successful Backups** | Devices that returned `success` or `success (N command errors)` |
| **Failed (total)** | `Failed during backup` + `Skipped (custom-property issues)` |
| **Failed during backup** | Device was attempted; connection, auth, or command run failed (rows in the CSV with `error:` or `authentication failure`) |
| **Skipped (custom-property issues)** | Device was never attempted — missing `Creds`, missing `DeviceType`, or unmapped credential key (rows in the CSV with `skipped:`) |
| **Success %** | `Successful Backups / Backup-Enabled in SolarWinds` |
| **Failure %** | `Failed (total) / Backup-Enabled in SolarWinds` |
| **Total Time (sec)** | Wall-clock duration of the run from the moment SolarWinds was queried to the moment the email was queued |

The example numbers in `example-email-body.html` (1008 / 994 / 14 / 11 / 3) match the failure pattern shown in `example-failed-reasons.csv`.

> 🎯 **Why "Failed (total)" includes skips**: skipped devices are invisible failures — the user marked them `Backup_Enabled = true` and the script could not back them up. Counting them as failures keeps the success rate honest. The breakdown lets triage tell whether to fix the network (failed during backup) or the data (skipped).
