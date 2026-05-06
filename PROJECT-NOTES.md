# Network Backup Automation — SolarWinds Integration

Project notes and design decisions for moving the network device backup
automation from a static CSV inventory to a SolarWinds-driven inventory.

Last updated: 2026-05-03 (session 2)

---

## Project goal

Replace the static `inventory-region-a.csv` inventory with a live pull from
SolarWinds so devices added/removed in SolarWinds are picked up automatically
on the next backup run. Single global script (no region split) that backs up
every SolarWinds node flagged as enabled.

**Script renamed:** `region-a-MasterBackup.py` → `Global-Master-Backup.py`

---

## Architecture

```
SolarWinds (source of truth)
   |
   |  SWQL query at script startup
   |  Filter: Backup_Enabled = true   (boolean column)
   |
   v
Global-Master-Backup.py  (reads all config from config.json)
   |
   |  - Resolves Creds → password via device_credentials in config.json
   |  - Builds netmiko driver from DeviceType + LoginMethod
   |  - Handles enable mode if Prompt = 'enable' (REGION_B / legacy gear)
   |  - ThreadPoolExecutor backs up devices in parallel
   |  - Per-command error handling — one failing command won't abort the device
   |
   v
   - State + config backups → backup_output_dir/<date>/
   - Failed-reasons CSV   → reports_dir/
   - Backups uploaded to Box grouped by device BoxFolderID (per-region routing)
   - HTML summary email sent
```

Devices not in scope (servers, UPS, etc.) are excluded automatically —
they won't have `Backup_Enabled` populated, so the SWQL filter drops them.

---

## Files in this project

| File | Purpose |
|---|---|
| `Global-Master-Backup.py` | The active backup script. Reads everything from `config.json`. |
| `config.json` | Single config file — all paths, credentials, email, Box IDs, API key. |
| `requirements.txt` | Python dependencies. Rebuild venv with `pip install -r requirements.txt`. |
| `PROJECT-NOTES.md` | This file — design decisions and project context. |
| `inventory-region-a.csv` | Original static CSV. Retained as reference for SolarWinds migration. |

---

## config.json structure

Deploy path (recommended): `<APP_INSTALL_ROOT>/config.json` — see the **Filesystem layout & naming conventions** section in `README.md` for the full recommended directory tree. The example values use `/opt/network-backup/` as a placeholder install root; substitute whatever path you adopt on your Linux host (typically an EC2 instance running Amazon Linux / Ubuntu).
Permissions: `chmod 600`

```json
{
    "paths": {
        "command_file_path": "...",
        "jwt_config_path": "...",
        "backup_output_dir": "...",
        "reports_dir": "..."
    },
    "solarwinds": {
        "server": "...",
        "username": "...",
        "password": "...",
        "verify_ssl": false
    },
    "device_credentials": {
        "svc-network-automation": "password",
        "another.user": "anotherpassword"
    },
    "Global-Backup-Automation-Mail-config": {
        "sender": "...", "sender_name": "...", "receiver": "...",
        "cc": "", "bcc": "",
        "subject": "Global Backup Summary",
        "smtp_server": "...", "smtp_port": 25
    },
    "box": {
        "folder_id": "...",
        "failed_csv_folder_id": "..."
    },
    "performance": { "max_workers": 10 },
    "api_key": ""
}
```

`device_credentials` is a plain JSON object — key is the username (must match the
`Creds` SolarWinds custom property), value is the password. Add accounts by
adding more key/value pairs. No special format or parsing needed.

The script validates all required fields at startup and exits with a list of
what's missing before attempting any connections.

---

## SolarWinds custom properties (nine, on Orion.Nodes)

| Property | Type | Purpose |
|---|---|---|
| `Backup_Enabled` | Text | `YES` = include. Missing or anything else = skip. Master filter. |
| `Disable_Reason` | Text | Free text when `Backup_Enabled` is `NO`. |
| `DeviceType` | Text | Vendor/OS base, e.g. `cisco_ios`, `hp_procurve`. |
| `LoginMethod` | Text | `ssh` or `telnet`. Combined with DeviceType to build the netmiko driver. |
| `BackupCommand` | Text | Optional config dump command, e.g. `show running-config`. Leave blank if not needed. |
| `CommandFile` | Text | Filename of the state-backup command list (e.g. `Cisco-State-Backup-Commands.txt`). |
| `Creds` | Text | The device username — also the lookup key into `device_credentials` in config.json. |
| `Prompt` | Text | Set to `enable` if the device requires enable mode (REGION_B gear, legacy IOS). Blank otherwise. |
| `EnableCred` | Text | The enable-mode secret password. Required when `Prompt = enable`. Blank otherwise. |

Names must not contain spaces (SolarWinds constraint).

**Box folders (current):**

| Purpose | Box Folder ID |
|---|---|
| All backups (state + config) | `<BACKUP_BOX_FOLDER_ID>` |
| Failed-backup reports CSV    | `<REPORTS_BOX_FOLDER_ID>` |

Previously the project used per-region folders (REGION_A `<LEGACY_REGION_A_BOX_FOLDER_ID>`, REGION_B `<LEGACY_REGION_B_BOX_FOLDER_ID>`, REGION_C `<LEGACY_REGION_C_BOX_FOLDER_ID>`, failed CSVs `<LEGACY_REPORTS_BOX_FOLDER_ID>`) routed via a `BoxFolderID` per-device custom property. Per-region routing was removed 2026-05-03 along with the `BoxFolderID` property; all backups now land in a single folder, with the failed-reasons report CSV going to a separate sibling folder.

---

## DeviceType + LoginMethod → netmiko driver

Script concatenates `f"{DeviceType}_{LoginMethod}"` at runtime.
If DeviceType already contains a transport suffix it is stripped first to
avoid double-suffixes (e.g. `cisco_ios_ssh` entered by mistake → still
produces `cisco_ios_ssh`, not `cisco_ios_ssh_ssh`).

**Current inventory combinations (from inventory-region-a.csv):**

| DeviceType | LoginMethod | Netmiko driver | Count |
|---|---|---|---:|
| `cisco_ios` | `ssh` | `cisco_ios_ssh` | 370 |
| `cisco_ios` | `telnet` | `cisco_ios_telnet` | 28 |
| `cisco_wlc` | `ssh` | `cisco_wlc_ssh` | 39 |
| `hp_procurve` | `ssh` | `hp_procurve_ssh` | 51 |
| `hp_procurve` | `telnet` | `hp_procurve_telnet` | 1 |

No Juniper devices in the current inventory.

**Forward-looking reference:**

| Real device | DeviceType | LoginMethod |
|---|---|---|
| Juniper Junos | `juniper_junos` | `ssh` / `telnet` |
| Cisco NX-OS | `cisco_nxos` | `ssh` |
| Cisco IOS-XR | `cisco_xr` | `ssh` / `telnet` |
| Arista EOS | `arista_eos` | `ssh` / `telnet` |
| HPE Comware | `hp_comware` | `ssh` / `telnet` |
| HPE Aruba CX | `aruba_aoscx` | `ssh` |
| Palo Alto | `paloalto_panos` | `ssh` |
| Fortinet | `fortinet` | `ssh` |
| F5 BIG-IP | `f5_tmsh` | `ssh` |

Canonical driver list: https://github.com/ktbyers/netmiko/blob/develop/PLATFORMS.md

---

## Deployment steps

1. **SolarWinds:** create the nine custom properties on `Orion.Nodes` and
   populate them for every network device that should be backed up.
   Set `Prompt = enable` and `EnableCred` only for devices that require
   enable mode (most REGION_B gear, older IOS devices).

2. **Linux host (EC2 or similar) — set up venv:**
   ```bash
   # Substitute <APP_INSTALL_ROOT> for your install path (e.g. /opt/network-backup)
   cd <APP_INSTALL_ROOT>
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Linux host — deploy and secure the config:**
   ```bash
   cp config.json <APP_INSTALL_ROOT>/config.json
   chmod 600 <APP_INSTALL_ROOT>/config.json
   ```
   Fill in all placeholder values — SolarWinds server/credentials,
   `device_credentials`, email settings.

4. **Linux host — deploy the script:**
   ```bash
   cp Global-Master-Backup.py <APP_INSTALL_ROOT>/
   ```

5. **Ensure command files exist** at `COMMAND_FILE_PATH`
   (e.g. `Cisco-State-Backup-Commands.txt`). These are the per-vendor
   state-backup command lists already used by the original script.

6. **Ensure the Box JWT file exists** at `JWT_CONFIG_PATH`.

7. **First test run:**
   ```bash
   source venv/bin/activate
   python3 Global-Master-Backup.py
   ```
   Output will print:
   - `Loaded N devices with Backup_Enabled=YES. M skipped.`
   - Per-device skip reasons (missing Creds, unresolved credential key, empty DeviceType)

8. **Update cron/scheduler** to call `Global-Master-Backup.py` from `<APP_INSTALL_ROOT>` on the schedule you want (typically once nightly).

---

## Design decisions

1. **SolarWinds is the sole inventory source.** No local CSV or profile file.
   The network team manages devices in SolarWinds; the script picks them up
   automatically on the next run.

2. **Single `config.json` for all config.** Consolidates paths, SolarWinds
   credentials, device credentials, email, and Box IDs in one place.
   JSON chosen over .env for native types (bool, int, dict) and because
   `device_credentials` maps naturally to a JSON object with no parsing.
   No defaults embedded in the script so the file is never optional.

3. **Passwords stay out of SolarWinds.** The `Creds` property holds only
   the username. Passwords are in `device_credentials` in `config.json`.
   To rotate a password, edit one line in `config.json`.

4. **No region filter.** `Backup_Enabled = YES` is the only gate.
   Servers/UPS are excluded because they won't have the property set.

5. **DeviceType + LoginMethod are separate fields.** DeviceType is the
   vendor/OS base; LoginMethod is the transport. The script combines them
   at runtime. Forgiving of full driver names entered by mistake.

6. **`CommandFilePath` is in `config.json`** as `paths.command_file_path`.
   It is the same for all devices — no per-device override needed.

7. **All output directories are auto-created** by the script including
   `REPORTS_DIR`, so a fresh Linux deployment needs no manual folder setup.

---

## Bug fixes applied (session 2)

The original REGION_A/REGION_B/REGION_C scripts had three bugs carried into the early Global script drafts. All fixed:

1. **"Unknown error" in results** — The `failure_reasons` dict used `type(e) == Exception` for the catch-all. Python never raises bare `Exception`; it always raises a subclass, so the key never matched. Fixed by removing the dict entirely and using `f"error: {type(e).__name__}: {str(e)[:150]}"` directly in the `except` clause.

2. **Incomplete state backups** — A single failing `send_command()` call threw an exception that aborted the whole device, leaving a partial output file. Fixed with per-command `try/except` inside the command loop: errors are written into the output file as `ERROR: ExceptionType: message` and the loop continues. Result becomes `success (N command errors)` instead of a hard failure.

3. **Missing enable mode (REGION_B)** — The REGION_B script had `Prompt`/`EnableCred` fields and `connection.enable()` logic. The Global script lacked this. Fixed by adding `Prompt` and `EnableCred` as SolarWinds custom properties and implementing the same enable mode logic.

Additional improvements:
- `read_timeout=60` on all `send_command()` calls (handles slow/verbose commands)
- Empty lines in command files are filtered out before iteration
- `connection.disconnect()` moved to a `finally` block — always runs even on exception
- Hostname extraction uses `.strip('#>').strip()` (more robust, handles all prompt styles)
- ~~BoxFolderID custom property + per-region Box upload routing~~ — removed 2026-05-03 when all backups consolidated into a single Box folder

