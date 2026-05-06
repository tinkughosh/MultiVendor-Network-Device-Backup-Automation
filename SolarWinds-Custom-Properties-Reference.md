# SolarWinds Custom Properties — Reference for Backup Automation

A populated reference for the SolarWinds team to use when creating and filling
in the nine custom properties consumed by `Global-Master-Backup.py`.

Last updated: 2026-05-03

---

## 1. Property catalog

All nine properties live on `Orion.Nodes` (joined via `Orion.NodesCustomProperties`).
Names must NOT contain spaces (SolarWinds platform constraint).

| # | Property | Type | Required | Allowed / sample values | Behavior when blank |
|---|---|---|---|---|---|
| 1 | `Backup_Enabled` | Boolean OR Text | Yes | Boolean `True` / `False`, OR text `YES` / `NO` (the script accepts both) | Device is **excluded** from the backup run (master filter) |
| 2 | `Disable_Reason` | Text | No | `Decommissioned 2026-04-01`, `Vendor RMA in progress`, `Pending replacement` | Empty — informational only |
| 3 | `DeviceType` | Text | Yes | `cisco_ios`, `cisco_nxos`, `cisco_wlc`, `cisco_xr`, `arista_eos`, `juniper_junos`, `hp_procurve`, `hp_comware`, `aruba_aoscx`, `paloalto_panos`, `fortinet`, `f5_tmsh` | Device is skipped with reason `DeviceType custom property empty` |
| 4 | `LoginMethod` | Text | Yes | `ssh`, `telnet` | Falls back to using `DeviceType` raw — usually fails |
| 5 | `BackupCommand` | Text | No | `show running-config`, `show configuration`, `show config`, *(blank for state-only)* | No config-backup file is written; state backup still runs |
| 6 | `CommandFile` | Text | Yes | `Cisco-State-Backup-Commands.txt`, `Hpe-State-Backup-Commands.txt`, `WLC-State-Backup-Commands.txt`, `Juniper-State-Backup-Commands.txt` | Device fails — script tries to open a path with no filename |
| 7 | `Creds` | Text | Yes | One of the credential keys defined in `config.json` → `device_credentials`. Currently: `svc-network-automation`, `svc-network-readwrite`, `svc-network-legacy`, `svc-vendor-readonly`, `root`, `svc-personal-account`, `svc-acquired-region`, `admin_region_a`, `admin_region_b`, `admin_region_legacy_site`. See **Creds migration plan** below for which devices use which key. | Device skipped with reason `Creds custom property empty` |
| 8 | `Prompt` | Text | No | `enable`, *(blank)* | No enable-mode escalation attempted |
| 9 | `EnableCred` | Text | Conditional — required when `Prompt = enable` | the secret string | If `Prompt = enable` but this is blank, escalation will fail at runtime |

### Field-naming rules
- No spaces in custom property names (replace with underscore — `Backup_Enabled`, not `Backup Enabled`).
- The script does case-insensitive comparison only on `Backup_Enabled` (treats `yes`, `Yes`, `YES` identically). All other fields are used verbatim, so be consistent (e.g. always `cisco_ios`, never `Cisco_IOS`).

---

## 1.5. Creds migration plan (SolarWinds retag work)

The legacy regional inventories used `Creds` values that were either ambiguous (one username with multiple passwords across sites) or duplicated. The new credential keys consolidate each unique username:password combination into a single key. The SolarWinds team must update each device's `Creds` custom property as follows:

| Current `Creds` value | Devices | Region(s) | New `Creds` value | Notes |
|---|---:|---|---|---|
| `svc-network-automation` | 519 | All | `svc-network-automation` | unchanged |
| `svc-network-readwrite` | 102 | REGION_A | `svc-network-readwrite` | unchanged |
| `svc-network-legacy` | 67 | (mixed) | `svc-network-legacy` | unchanged |
| `svc-vendor-readonly` | 12 | REGION_A | `svc-vendor-readonly` | unchanged |
| `root` | 26 | (mixed) | `root` | unchanged |
| `svc-personal-account` | 7 | (mixed) | `svc-personal-account` | unchanged |
| `svc-acquired-region` | 2 | (mixed) | `svc-acquired-region` | unchanged |
| `localkb` | 158 | REGION_C | **`svc-network-readwrite`** | retag — `svc-network-readwrite` creds also work on these devices |
| `admin` (variant 1) | 216 | REGION_B | **`admin_region_a`** | broad REGION_B standard admin password |
| `admin` (variant 2) | 48 | REGION_A | **`admin_region_b`** | REGION_A default admin password |
| `admin` (variant 3) | 13 | REGION_B LK-WSAR site | **`admin_region_legacy_site`** | older WSAR-site admin password |
| `admin` (variant 4) | 1 | REGION_B (`NZ-TAUR-SW06`) | **fix data first** | empty password in source — set a real value before tagging |

After retagging, every backup-enabled device's `Creds` value matches a key in `config.json` → `device_credentials`. New devices added later inherit the same scheme.

---

## 2. The `Creds` ↔ `config.json` mapping

`Creds` is **just the username**. The script looks it up in
`config.json` → `device_credentials` to get the password.

`config.json` excerpt:

```json
"device_credentials": {
    "svc-network-automation": "thisisthepassword"
}
```

To rotate a password, edit `config.json`. To use a different account on a
specific device, set `Creds = the.other.username` on that node in SolarWinds
and add a matching entry in `device_credentials`.

If `Creds` references a username that isn't in `device_credentials`, the
script logs `Creds key "X" not found in device_credentials` and skips the
device — it will not crash the run.

---

## 3. `DeviceType` + `LoginMethod` → netmiko driver

The script combines them at runtime as `f"{DeviceType}_{LoginMethod}"`.

| `DeviceType`    | `LoginMethod` | Netmiko driver used      |
|-----------------|---------------|--------------------------|
| `cisco_ios`     | `ssh`         | `cisco_ios_ssh`          |
| `cisco_ios`     | `telnet`      | `cisco_ios_telnet`       |
| `cisco_wlc`     | `ssh`         | `cisco_wlc_ssh`          |
| `cisco_nxos`    | `ssh`         | `cisco_nxos_ssh`         |
| `cisco_xr`      | `ssh`         | `cisco_xr_ssh`           |
| `arista_eos`    | `ssh`         | `arista_eos_ssh`         |
| `juniper_junos` | `ssh`         | `juniper_junos_ssh`      |
| `hp_procurve`   | `ssh`         | `hp_procurve_ssh`        |
| `hp_procurve`   | `telnet`      | `hp_procurve_telnet`     |
| `hp_comware`    | `ssh`         | `hp_comware_ssh`         |
| `aruba_aoscx`   | `ssh`         | `aruba_aoscx_ssh`        |
| `paloalto_panos`| `ssh`         | `paloalto_panos_ssh`     |
| `fortinet`      | `ssh`         | `fortinet_ssh`           |
| `f5_tmsh`       | `ssh`         | `f5_tmsh_ssh`            |

Canonical netmiko driver list:
https://github.com/ktbyers/netmiko/blob/develop/PLATFORMS.md

---

## 4. Sample device rows (use as a fill-in pattern)

### Sample A — Cisco IOS over SSH (the default 75% of inventory)

| Field | Value |
|---|---|
| Caption (hostname) | `EXAMPLE-CORE-SW01` |
| IP_Address        | `192.0.2.11` |
| `Backup_Enabled`  | `YES` |
| `Disable_Reason`  | *(blank)* |
| `DeviceType`      | `cisco_ios` |
| `LoginMethod`     | `ssh` |
| `BackupCommand`   | `show running-config` |
| `CommandFile`     | `Cisco-State-Backup-Commands.txt` |
| `Creds`           | `svc-network-automation` |
| `Prompt`          | *(blank)* |
| `EnableCred`  | *(blank)* |

### Sample B — Legacy Cisco IOS over Telnet, requires enable mode

| Field | Value |
|---|---|
| Caption | `EXAMPLE-LEGACY-SW02` |
| IP_Address | `192.0.2.12` |
| `Backup_Enabled` | `YES` |
| `DeviceType` | `cisco_ios` |
| `LoginMethod` | `telnet` |
| `BackupCommand` | `show running-config` |
| `CommandFile` | `Cisco-State-Backup-Commands.txt` |
| `Creds` | `svc-network-automation` |
| `Prompt` | `enable` |
| `EnableCred` | `<the enable secret>` |

### Sample C — HP ProCurve over SSH

| Field | Value |
|---|---|
| Caption | `EXAMPLE-PROC-01` |
| IP_Address | `192.0.2.13` |
| `Backup_Enabled` | `YES` |
| `DeviceType` | `hp_procurve` |
| `LoginMethod` | `ssh` |
| `BackupCommand` | `show running-config` |
| `CommandFile` | `Hpe-State-Backup-Commands.txt` |
| `Creds` | `svc-network-automation` |
| `Prompt` | *(blank)* |
| `EnableCred` | *(blank)* |

### Sample D — Cisco WLC

| Field | Value |
|---|---|
| Caption | `EXAMPLE-WLC-01` |
| IP_Address | `192.0.2.14` |
| `Backup_Enabled` | `YES` |
| `DeviceType` | `cisco_wlc` |
| `LoginMethod` | `ssh` |
| `BackupCommand` | `show running-config` |
| `CommandFile` | `WLC-State-Backup-Commands.txt` |
| `Creds` | `svc-network-automation` |

### Sample E — Decommissioned device (excluded from backup runs)

| Field | Value |
|---|---|
| Caption | `EXAMPLE-DECOMMISSIONED-SW99` |
| IP_Address | `192.0.2.99` |
| `Backup_Enabled` | `NO` |
| `Disable_Reason` | `Decommissioned 2026-04-01 — physical removal pending` |
| `DeviceType` | `cisco_ios` *(can stay populated)* |
| `LoginMethod` | `ssh` |
| `Creds` | `svc-network-automation` |
| *(other fields can stay populated for historical reference)* |

### Sample F — Server / UPS / non-network device

Leave **all nine** custom properties blank. The SWQL filter `WHERE
Backup_Enabled = true` excludes the row entirely, so non-network nodes
in SolarWinds do not need to be touched.

---

## 5. Validation checklist before first run

For every node you mark `Backup_Enabled = YES`:

- [ ] `DeviceType` and `LoginMethod` together resolve to a valid netmiko driver (see the table in §3)
- [ ] `Creds` is a key that exists in `config.json` → `device_credentials`
- [ ] `CommandFile` is a filename that exists at the script's `paths.command_file_path` directory on the EC2 host
- [ ] If the device requires enable mode, **both** `Prompt = enable` AND `EnableCred` are populated
- [ ] `BackupCommand` is the platform-correct command (e.g. `show configuration` for Junos, not `show running-config`)

After the first run, the script writes a CSV at
`paths.reports_dir/Global-backup-failed-reasons-<timestamp>.csv` listing
every device that did not succeed and the reason. Use that as the punch list
for fixing custom-property values.

---

## 6. Adding a new property in SolarWinds (UI steps)

1. **Settings → All Settings → Manage Custom Properties** (under "Node & Group Management").
2. **Add Custom Property** → choose `Nodes` as the object type.
3. Fill in:
   - **Name** — exactly as listed in §1 (no spaces, case-sensitive).
   - **Description** — copy the "Allowed / sample values" column for context.
   - **Format** — `Text` for all ten properties.
   - **Restrict values** (optional) — for `Backup_Enabled` and `LoginMethod` you can add a fixed list (`YES`/`NO`, `ssh`/`telnet`) for a clean UI dropdown.
4. **Submit**, then **Select Nodes** → assign and populate values per the samples above.

---

## 7. SWQL the script runs (for reference)

```sql
SELECT
    n.Caption           AS Hostname,
    n.IP_Address        AS IP,
    n.Vendor            AS Vendor,
    n.MachineType       AS MachineType,
    n.Location          AS LocationName,
    n.StatusDescription AS AlertStatus,
    cp.Backup_Enabled   AS Backup_Enabled,
    cp.Disable_Reason   AS Disable_Reason,
    cp.DeviceType       AS DeviceType,
    cp.LoginMethod      AS LoginMethod,
    cp.BackupCommand    AS BackupCommand,
    cp.CommandFile      AS CommandFile,
    cp.Creds            AS Creds,
    cp.Prompt           AS Prompt,
    cp.EnableCred   AS EnableCred
FROM Orion.Nodes n
INNER JOIN Orion.NodesCustomProperties cp ON n.NodeID = cp.NodeID
WHERE cp.Backup_Enabled = true
```

You can paste this into **Settings → SolarWinds Orion → SWQL Studio** to
preview exactly what the script will see at runtime, before running it.
