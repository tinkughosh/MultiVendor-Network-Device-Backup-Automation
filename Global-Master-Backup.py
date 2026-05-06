import os
import csv
import json
import smtplib
import time
import urllib3
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from email.utils import formataddr
from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoTimeoutException, NetmikoAuthenticationException
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from boxsdk import JWTAuth, Client
from boxsdk.exception import BoxAPIException
from datetime import datetime
from orionsdk import SwisClient

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------- Load config.json ----------
_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
with open(_config_path) as _f:
    _cfg = json.load(_f)


def _validate_config(cfg):
    required = {
        'paths':       ['command_file_path', 'jwt_config_path', 'backup_output_dir', 'reports_dir'],
        'solarwinds':  ['server', 'username', 'password'],
        'Global-Backup-Automation-Mail-config': ['sender', 'sender_name', 'receiver', 'subject', 'smtp_server', 'smtp_port'],
        'box':         ['folder_id', 'failed_csv_folder_id'],
        'performance': ['max_workers'],
    }
    missing = []
    for section, keys in required.items():
        for key in keys:
            if not cfg.get(section, {}).get(key) and cfg.get(section, {}).get(key) != 0:
                missing.append(f"{section}.{key}")
    if not cfg.get('device_credentials'):
        missing.append('device_credentials')
    if missing:
        print("ERROR: The following required fields are missing or empty in config.json:")
        for m in missing:
            print(f"  {m}")
        raise SystemExit(1)

_validate_config(_cfg)

# ---------- Paths ----------
COMMAND_FILE_PATH = _cfg['paths']['command_file_path']
jwt_config_path   = _cfg['paths']['jwt_config_path']

# ---------- SolarWinds ----------
SOLARWINDS_SERVER     = _cfg['solarwinds']['server']
SOLARWINDS_USERNAME   = _cfg['solarwinds']['username']
SOLARWINDS_PASSWORD   = _cfg['solarwinds']['password']
SOLARWINDS_VERIFY_SSL = _cfg['solarwinds'].get('verify_ssl', False)

# ---------- Device credentials ----------
DEVICE_CREDENTIALS = _cfg['device_credentials']

# ---------- Email ----------
_MAIL_CFG = _cfg['Global-Backup-Automation-Mail-config']
EMAIL_SENDER      = _MAIL_CFG['sender']
EMAIL_SENDER_NAME = _MAIL_CFG['sender_name']
EMAIL_RECEIVER    = _MAIL_CFG['receiver']
EMAIL_CC          = _MAIL_CFG.get('cc', '')
EMAIL_BCC         = _MAIL_CFG.get('bcc', '')
EMAIL_SUBJECT     = _MAIL_CFG['subject']
SMTP_SERVER       = _MAIL_CFG['smtp_server']
SMTP_PORT         = int(_MAIL_CFG['smtp_port'])

# ---------- Per-driver netmiko tuning ----------
# Keyed on the resolved netmiko driver string (DeviceType + LoginMethod).
# `connect_kwargs` is merged into ConnectHandler kwargs.
# `read_timeout` is used for every send_command() call on devices using this driver.
NETMIKO_TUNING = {
    'cisco_ios_ssh':        {'connect_kwargs': {'fast_cli': False, 'global_delay_factor': 2, 'banner_timeout': 20}, 'read_timeout': 120},
    'cisco_ios_telnet':     {'connect_kwargs': {'fast_cli': False, 'global_delay_factor': 3, 'banner_timeout': 30}, 'read_timeout': 120},
    'cisco_wlc_ssh':        {'connect_kwargs': {'fast_cli': False, 'global_delay_factor': 3, 'banner_timeout': 30}, 'read_timeout': 180},
    'aruba_os_ssh':         {'connect_kwargs': {'fast_cli': False, 'global_delay_factor': 3, 'banner_timeout': 30}, 'read_timeout': 180},
    'hp_procurve_ssh':      {'connect_kwargs': {'fast_cli': False, 'global_delay_factor': 3, 'banner_timeout': 30}, 'read_timeout': 120},
    'hp_procurve_telnet':   {'connect_kwargs': {'fast_cli': False, 'global_delay_factor': 4, 'banner_timeout': 45}, 'read_timeout': 120},
    'juniper_junos_ssh':    {'connect_kwargs': {'fast_cli': False, 'global_delay_factor': 2, 'banner_timeout': 20}, 'read_timeout': 120},
    'juniper_junos_telnet': {'connect_kwargs': {'fast_cli': False, 'global_delay_factor': 3, 'banner_timeout': 30}, 'read_timeout': 120},
}

NETMIKO_TUNING_DEFAULT = {
    'connect_kwargs': {'fast_cli': False, 'global_delay_factor': 2, 'banner_timeout': 20},
    'read_timeout':   90,
}


def tuning_for(driver):
    """Return {'connect_kwargs': {...}, 'read_timeout': int} for a netmiko driver string.

    Falls back to NETMIKO_TUNING_DEFAULT for any driver not in the table.
    """
    return NETMIKO_TUNING.get(driver, NETMIKO_TUNING_DEFAULT)


def format_error(exc):
    """Build a CSV-safe error string with the real exception class name and message.

    Output examples:
        'error: exceptions.ReadTimeout: Pattern not detected: '#$' in output'
        'error: ssh_exception.SSHException: Error reading SSH protocol banner'
        'error: TimeoutError: timed out'
        'error: Exception'   (when the exception has no message)
    """
    cls = type(exc).__name__
    mod = type(exc).__module__ or ''
    if mod and mod != 'builtins':
        cls = f"{mod.rsplit('.', 1)[-1]}.{cls}"
    msg = str(exc).replace('\n', ' ').strip()[:200]
    return f"error: {cls}: {msg}" if msg else f"error: {cls}"


# ---------- SolarWinds inventory ----------
def resolve_netmiko_driver(device_type, login_method):
    """
    Combines DeviceType (vendor/OS base, e.g. cisco_ios) and LoginMethod
    (ssh / telnet) into the single netmiko driver string (e.g. cisco_ios_ssh).
    Strips any existing _ssh/_telnet suffix from DeviceType first to avoid
    double-suffixes if the full driver name was entered by mistake.
    Falls back to DeviceType as-is if LoginMethod is blank.
    """
    dt = (device_type or '').strip()
    lm = (login_method or '').strip().lower()

    if lm in ('ssh', 'telnet'):
        for suffix in ('_ssh', '_telnet'):
            if dt.lower().endswith(suffix):
                dt = dt[:-len(suffix)]
                break
        return f"{dt}_{lm}"

    return dt


def fetch_inventory_from_solarwinds():
    """
    Pull every SolarWinds node whose Backup_Enabled custom property is YES.
    Resolve credentials from DEVICE_CREDENTIALS. Return device dicts shaped
    for backup_single_device(). Non-network devices (no Backup_Enabled) are
    excluded automatically.

    SolarWinds custom properties required (9 total):
      Backup_Enabled, Disable_Reason, DeviceType, LoginMethod,
      BackupCommand, CommandFile, Creds,
      Prompt        — set to 'enable' if device needs enable mode (legacy / older gear)
      EnableCred — enable secret; required when Prompt = 'enable'
    """
    swis = SwisClient(
        SOLARWINDS_SERVER,
        SOLARWINDS_USERNAME,
        SOLARWINDS_PASSWORD,
        verify=SOLARWINDS_VERIFY_SSL,
    )

    swql = """
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
    """
    rows = swis.query(swql).get('results', [])

    devices, skipped = [], []
    for r in rows:
        # Common fields available regardless of whether the device is backupable
        # or skipped due to missing custom-property data. Both lists use the
        # same dict shape so the failed-reasons CSV writer can serialize either.
        base = {
            'LocationName':    r.get('LocationName') or '',
            'Hostname':        r.get('Hostname') or '',
            'IP':              r.get('IP') or '',
            'Vendor':          r.get('Vendor') or '',
            'MachineType':     r.get('MachineType') or '',
            'AlertStatus':     r.get('AlertStatus') or '',
            'DeviceType':      (r.get('DeviceType') or '').strip(),
            'LoginMethod':     (r.get('LoginMethod') or '').strip(),
        }

        creds_key = (r.get('Creds') or '').strip()
        if not creds_key:
            skipped.append({**base, 'Result': 'skipped: Creds custom property empty'})
            continue
        password = DEVICE_CREDENTIALS.get(creds_key)
        if password is None:
            skipped.append({**base,
                            'Result': f'skipped: Creds key "{creds_key}" not found in device_credentials (config.json)'})
            continue

        netmiko_driver = resolve_netmiko_driver(r.get('DeviceType'), r.get('LoginMethod'))
        if not netmiko_driver:
            skipped.append({**base, 'Result': 'skipped: DeviceType custom property empty'})
            continue

        devices.append({
            **base,
            'DeviceType':      netmiko_driver,
            'BackupCommand':   (r.get('BackupCommand') or '').strip(),
            'CommandFile':     (r.get('CommandFile') or '').strip(),
            'CommandFilePath': COMMAND_FILE_PATH,
            'Username':        creds_key,
            'Password':        password,
            'Status':          'enable',
            'Disable_Reason':  r.get('Disable_Reason') or '',
            'Prompt':          (r.get('Prompt') or '').strip().lower(),
            'EnableCred':      (r.get('EnableCred') or '').strip(),
        })
    return devices, skipped


# ---------- Email ----------
def send_email(total_devices, success_count, failure_count,
               backup_failure_count, skipped_count,
               start_time, end_time, modified_inventory_csv=None):
    total_time = end_time - start_time
    success_percentage = (success_count / total_devices) * 100 if total_devices > 0 else 0
    failure_percentage = (failure_count / total_devices) * 100 if total_devices > 0 else 0

    email_body = f"""
    <html>
    <body>
        <p>Dear Team,</p>
        <p>Here is the summary of the device backup process. Percentages are
        calculated against the number of devices marked
        <code>Backup_Enabled = true</code> in SolarWinds.</p>
        <h2>Backup Summary Report</h2>
        <table border="1" cellpadding="5" cellspacing="0">
            <tr>
                <th>Backup-Enabled in SolarWinds</th>
                <th>Successful Backups</th>
                <th>Failed (total)</th>
                <th>Failed during backup</th>
                <th>Skipped (custom-property issues)</th>
                <th>Success %</th>
                <th>Failure %</th>
                <th>Total Time (sec)</th>
            </tr>
            <tr>
                <td>{total_devices}</td>
                <td>{success_count}</td>
                <td>{failure_count}</td>
                <td>{backup_failure_count}</td>
                <td>{skipped_count}</td>
                <td>{success_percentage:.2f}%</td>
                <td>{failure_percentage:.2f}%</td>
                <td>{total_time:.2f}</td>
            </tr>
        </table>
        <p><b>Failed during backup</b> = device was attempted but the connection,
        authentication, or command run failed.<br>
        <b>Skipped</b> = device had <code>Backup_Enabled = true</code> in
        SolarWinds but a required custom property
        (<code>Creds</code>, <code>DeviceType</code>, or a credential key
        absent from <code>config.json</code>) prevented the script from
        attempting it.</p>
        <p>The attached CSV lists every non-successful device with its specific reason in the <code>Result</code> column.</p>
        <p>Best regards,<br>Backup Automation System</p>
    </body>
    </html>
    """

    msg = MIMEMultipart()
    msg['From'] = formataddr((EMAIL_SENDER_NAME, EMAIL_SENDER))
    msg['To'] = EMAIL_RECEIVER
    msg['Cc'] = EMAIL_CC
    msg['Bcc'] = EMAIL_BCC
    msg['Subject'] = f"{EMAIL_SUBJECT} at {datetime.now().strftime('%H/%M on %d/%m/%Y')}"
    msg.attach(MIMEText(email_body, 'html'))

    if modified_inventory_csv:
        with open(modified_inventory_csv, 'rb') as file:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(file.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename="{os.path.basename(modified_inventory_csv)}"')
            msg.attach(part)

    recipients = [r for r in [EMAIL_RECEIVER, EMAIL_CC, EMAIL_BCC] if r]

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.sendmail(EMAIL_SENDER, recipients, msg.as_string())
            print("Email sent successfully!")
    except Exception as e:
        print(f"Failed to send email: {e}")


# ---------- Box ----------
def authenticate_box():
    try:
        with open(jwt_config_path) as jwt_file:
            config = json.load(jwt_file)
        auth = JWTAuth.from_settings_dictionary(config)
        client = Client(auth)
        user = client.user().get()
        print(f'Successfully authenticated as {user.name}')
        return client
    except Exception as e:
        print(f"Failed to authenticate: {e}")
        return None


def create_box_folder(client, parent_folder_id, folder_name):
    try:
        items = client.folder(parent_folder_id).get_items()
        for item in items:
            if item.name == folder_name:
                print(f"Folder '{folder_name}' already exists with ID: {item.id}")
                return item.id
        folder = client.folder(parent_folder_id).create_subfolder(folder_name)
        print(f"Created folder '{folder.name}' with ID: {folder.id}")
        return folder.id
    except BoxAPIException as e:
        print(f"Error creating folder: {e}")
        return None


# ---------- Backup ----------
def backup_single_device(device, timestamp, state_backup_dir, config_backup_dir):
    """
    Connect to one device and capture config + state backups.

    Returns a result string:
      'success'              — all commands completed
      'success (N errors)'   — connected and ran commands but N had errors
      'skipped'              — Backup_Enabled was not 'enable' in the device dict
      'authentication failure'
      'error: <ExceptionType>: <message>'  — connection-level failure
    """
    connection = None
    try:
        ip             = device['IP']
        username       = device['Username']
        password       = device['Password']
        device_type    = device['DeviceType']
        command_file   = device['CommandFile']
        command_file_path = os.path.join(device['CommandFilePath'], command_file)
        backup_command = device['BackupCommand']
        status         = device['Status'].lower()
        prompt         = device['Prompt']          # 'enable' or ''
        enable_pass    = device['EnableCred']

        if status != 'enable':
            return 'skipped'

        # ---- Connect (per-driver tuning) ----
        tuning = tuning_for(device_type)
        read_timeout = tuning['read_timeout']
        connection_args = {
            'device_type':  device_type,
            'ip':           ip,
            'username':     username,
            'password':     password,
            'timeout':      120,
            **tuning['connect_kwargs'],
        }
        if prompt == 'enable' and enable_pass:
            connection_args['secret'] = enable_pass

        connection = ConnectHandler(**connection_args)

        if prompt == 'enable':
            connection.enable()

        # Strip all common prompt chars to get a clean hostname for filenames
        hostname = connection.find_prompt().strip('#>').strip()

        # ---- Config backup (single command, e.g. show running-config) ----
        if backup_command:
            try:
                config_output = connection.send_command(backup_command, read_timeout=read_timeout)
                backup_output_file = os.path.join(
                    config_backup_dir, f"ConfigBackup_{hostname}_{ip}_{timestamp}.txt"
                )
                with open(backup_output_file, 'w') as f:
                    f.write(config_output)
            except Exception as e:
                print(f"  WARN [{ip}] config backup command failed: {type(e).__name__}: {e}")

        # ---- State backup (multiple commands from file) ----
        with open(command_file_path, 'r') as cmd_file:
            commands = [line.strip() for line in cmd_file if line.strip()]

        command_errors = 0
        state_backup_file = os.path.join(
            state_backup_dir, f"StateBackup_{hostname}_{ip}_{timestamp}.txt"
        )
        with open(state_backup_file, 'w') as state_outfile:
            for command in commands:
                state_outfile.write(f"Input command: {command}\n")
                try:
                    output = connection.send_command(command, read_timeout=read_timeout)
                    state_outfile.write(f"Output:\n{output}\n")
                except Exception as cmd_err:
                    # Log the error in the file and keep going — don't abort the device
                    command_errors += 1
                    err_text = format_error(cmd_err)
                    state_outfile.write(f"Output:\n{err_text}\n")
                    print(f"  WARN [{ip}] command '{command}' failed: {err_text}")
                state_outfile.write(
                    "######################################################################################\n"
                )

        if command_errors:
            return f"success ({command_errors} command error{'s' if command_errors > 1 else ''})"
        return 'success'

    except NetmikoAuthenticationException:
        return 'authentication failure'
    except NetmikoTimeoutException:
        return 'error: Connection timeout'
    except Exception as e:
        return format_error(e)
    finally:
        # Always close the connection regardless of how we exit
        if connection:
            try:
                connection.disconnect()
            except Exception:
                pass


def backup_devices(backup_parent_dir, failed_csv_box_folder_id, failed_csv_local_path, box_folder_id, max_workers=10):
    print("Fetching inventory from SolarWinds...")
    devices, skipped = fetch_inventory_from_solarwinds()
    enabled_total = len(devices) + len(skipped)
    print(f"Loaded {enabled_total} devices with Backup_Enabled=true. "
          f"{len(devices)} backupable, {len(skipped)} skipped (custom-property issues).")
    for d in skipped:
        print(f"  SKIP: {d.get('Hostname','')} ({d.get('IP','')}) - {d['Result']}")

    if not devices and not skipped:
        print("No devices in scope. Exiting.")
        return

    current_date = datetime.now().strftime('%d-%m-%Y')
    timestamp    = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')

    dated_backup_parent_dir = os.path.join(backup_parent_dir, current_date)
    os.makedirs(dated_backup_parent_dir, exist_ok=True)
    os.makedirs(failed_csv_local_path, exist_ok=True)

    state_backup_dir  = os.path.join(dated_backup_parent_dir, f"StateBackup_{timestamp}")
    config_backup_dir = os.path.join(dated_backup_parent_dir, f"ConfigBackup_{timestamp}")
    os.makedirs(state_backup_dir, exist_ok=True)
    os.makedirs(config_backup_dir, exist_ok=True)

    start_time = time.time()
    for d in devices:
        d['Result'] = ''

    device_start_times = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for d in devices:
            t0 = time.time()
            fut = executor.submit(backup_single_device, d, timestamp, state_backup_dir, config_backup_dir)
            futures[fut] = d
            device_start_times[fut] = t0

        for future in tqdm(as_completed(futures), total=len(devices), desc='Backing up devices', unit='device'):
            d = futures[future]
            d['Result'] = future.result()
            elapsed = time.time() - device_start_times[future]
            tag = 'OK  ' if d['Result'].startswith('success') else 'FAIL'
            print(f"  [{tag}] {d.get('Hostname',''):<35.35} {d.get('IP',''):<16} {d.get('DeviceType',''):<22} {elapsed:6.1f}s")

    end_time = time.time()

    # All devices that SolarWinds reported as Backup_Enabled = true.
    # Skipped devices count as failures because the user expected them to be
    # backed up but the script could not process them.
    all_enabled = devices + skipped
    total_devices = len(all_enabled)
    success_count = sum(1 for d in all_enabled if d['Result'].startswith('success'))
    skipped_count = len(skipped)
    backup_failure_count = sum(
        1 for d in devices if not d['Result'].startswith('success')
    )
    failure_count = total_devices - success_count   # = backup_failure_count + skipped_count

    # ---- Failed-reasons CSV ----
    failed_csv = os.path.join(failed_csv_local_path, f"Global-backup-failed-reasons-{timestamp}.csv")
    failed_fieldnames = [
        'LocationName', 'Hostname', 'IP', 'Vendor', 'MachineType',
        'AlertStatus', 'DeviceType', 'LoginMethod', 'Result',
    ]
    with open(failed_csv, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=failed_fieldnames)
        writer.writeheader()
        for d in all_enabled:
            if not d['Result'].startswith('success'):
                writer.writerow({k: d.get(k, '') for k in failed_fieldnames})

    # ---- Box upload ----
    # All backups go to a single Box folder (box_folder_id from config.json).
    client = authenticate_box()
    if client:
        # Upload failed CSV
        failed_csv_box_folder = create_box_folder(client, failed_csv_box_folder_id, "Global-Failed-Backups-Reports")
        if failed_csv_box_folder:
            try:
                client.folder(failed_csv_box_folder).upload(failed_csv)
                print(f"Uploaded failed CSV to Box folder ID {failed_csv_box_folder}")
            except BoxAPIException as e:
                print(f"Failed to upload CSV: {e}")

        # Create dated subfolder structure and upload all backup files
        dated_box_folder = create_box_folder(client, box_folder_id, current_date)
        if dated_box_folder:
            state_box  = create_box_folder(client, dated_box_folder, f"StateBackup_{timestamp}")
            config_box = create_box_folder(client, dated_box_folder, f"ConfigBackup_{timestamp}")

            for box_subfolder_id, local_dir in [(state_box, state_backup_dir), (config_box, config_backup_dir)]:
                if not box_subfolder_id:
                    continue
                for file_name in os.listdir(local_dir):
                    file_path = os.path.join(local_dir, file_name)
                    try:
                        client.folder(box_subfolder_id).upload(file_path)
                        print(f"Uploaded {file_name} to Box folder ID {box_subfolder_id}")
                    except BoxAPIException as e:
                        print(f"Failed to upload {file_name}: {e}")

    send_email(
        total_devices, success_count, failure_count,
        backup_failure_count, skipped_count,
        start_time, end_time, failed_csv,
    )


if __name__ == "__main__":
    backup_devices(
        backup_parent_dir        = _cfg['paths']['backup_output_dir'],
        box_folder_id            = _cfg['box']['folder_id'],
        failed_csv_local_path    = _cfg['paths']['reports_dir'],
        failed_csv_box_folder_id = _cfg['box']['failed_csv_folder_id'],
        max_workers              = int(_cfg['performance']['max_workers']),
    )
