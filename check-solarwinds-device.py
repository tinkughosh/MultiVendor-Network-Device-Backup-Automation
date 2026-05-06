#!/usr/bin/env python3
"""
check-solarwinds-device.py

Pre-flight diagnostic: fetch and display a single network device's standard
fields and ALL custom properties currently defined on it in SolarWinds.

Use this to:
  - Verify your SWIS API account works (any successful query proves it).
  - See which custom properties already exist on the device.
  - Cross-check against the nine backup-automation properties consumed by
    Global-Master-Backup.py — flagged as [PRESENT] or [NOT YET CREATED].

All inputs come from interactive prompts. Only dependency: orionsdk.

Usage:
    python3 check-solarwinds-device.py
"""

import sys
import getpass
import urllib3
from orionsdk import SwisClient

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# Properties Global-Master-Backup.py expects. The script flags any of these
# that are not yet defined as columns in Orion.NodesCustomProperties.
BACKUP_PROPERTIES = [
    'Backup_Enabled',
    'Disable_Reason',
    'DeviceType',
    'LoginMethod',
    'BackupCommand',
    'CommandFile',
    'Creds',
    'Prompt',
    'EnableCred',
]

# Standard Orion.Nodes columns to display. These are the well-known fields
# that exist on every SolarWinds installation.
NODE_FIELDS = [
    'Caption',
    'IP_Address',
    'NodeID',
    'Vendor',
    'MachineType',
    'Location',
    'StatusDescription',
    'DNS',
    'SysName',
]

# Columns that exist on Orion.NodesCustomProperties but are NOT user-defined
# custom properties — they are system metadata and should be filtered out
# when we list "the device's custom properties".
SYSTEM_CP_COLUMNS = {
    'NodeID',
    'InstanceType',
    'InstanceSiteId',
    'DisplayName',
    'Description',
    'Uri',
    'DetailsUrl',
    'Image',
    'Name',
}


def prompt(label, default=None, hidden=False):
    suffix = f" [{default}]" if default else ""
    fn = getpass.getpass if hidden else input
    val = fn(f"{label}{suffix}: ").strip()
    return val if val else (default or '')


def discover_cp_columns(swis):
    """
    Return the sorted list of user-defined custom-property column names on
    Orion.NodesCustomProperties.

    SWQL does NOT support `SELECT *`, so we have to enumerate columns
    explicitly. Metadata.Property is SolarWinds' schema-introspection table.
    """
    swql = """
    SELECT Name
    FROM Metadata.Property
    WHERE EntityName = 'Orion.NodesCustomProperties'
      AND IsNavigable = false
    """
    rows = swis.query(swql).get('results', [])
    cols = [r['Name'] for r in rows if r.get('Name') and r['Name'] not in SYSTEM_CP_COLUMNS]
    return sorted(cols)


def fetch_device_details(swis, search_field, search_value, cp_columns):
    """Return a list of (node_dict, custom_props_dict) tuples."""
    if search_field == 'ip':
        where = "WHERE IP_Address = @search"
        params = {'search': search_value}
    else:
        where = "WHERE ToUpper(Caption) LIKE ToUpper(@search)"
        params = {'search': f'%{search_value}%'}

    select_cols = ", ".join(NODE_FIELDS)
    swql_node = f"SELECT {select_cols} FROM Orion.Nodes {where}"
    nodes = swis.query(swql_node, **params).get('results', [])

    # Build an explicit SELECT for custom properties (SWQL has no SELECT *).
    if cp_columns:
        cp_select = ", ".join(cp_columns)
        cp_swql = f"SELECT {cp_select} FROM Orion.NodesCustomProperties WHERE NodeID = @nid"
    else:
        cp_swql = None

    out = []
    for node in nodes:
        nid = node.get('NodeID')
        cp = {}
        if cp_swql is not None:
            cp_rows = swis.query(cp_swql, nid=nid).get('results', [])
            if cp_rows:
                cp = cp_rows[0]
        out.append((node, cp))
    return out


def display_node(node, cp):
    print()
    print("=" * 78)
    print(f"  {node.get('Caption') or '(no hostname)'}  ({node.get('IP_Address') or 'no IP'})")
    print("=" * 78)

    # ---- Device details (Orion.Nodes) ----
    print("\n-- Device details (Orion.Nodes) --")
    for k in NODE_FIELDS:
        v = node.get(k)
        print(f"  {k:<22} {v if v not in (None, '') else '(blank)'}")

    # ---- All custom properties currently defined on this node ----
    cp_columns = {k: v for k, v in cp.items() if k != 'NodeID'}

    print(f"\n-- All custom properties on this device ({len(cp_columns)} columns) --")
    if not cp_columns:
        print("  (no user-defined custom property columns exist on Orion.NodesCustomProperties)")
    else:
        for k in sorted(cp_columns.keys()):
            v = cp_columns[k]
            print(f"  {k:<22} {v if v not in (None, '') else '(blank value)'}")

    # ---- Cross-check against Global-Master-Backup.py expectations ----
    cp_lookup_lower = {k.lower(): k for k in cp_columns.keys()}
    present, missing = [], []
    for expected in BACKUP_PROPERTIES:
        actual = cp_lookup_lower.get(expected.lower())
        if actual is not None:
            present.append((expected, actual, cp_columns[actual]))
        else:
            missing.append(expected)

    print(
        f"\n-- Backup-automation property check "
        f"({len(present)} of {len(BACKUP_PROPERTIES)} columns exist) --"
    )
    for expected, actual, v in present:
        case_note = "" if expected == actual else f"  (case in SolarWinds: '{actual}')"
        val_display = v if v not in (None, '') else '(blank value)'
        annotation = ""
        if expected == 'Backup_Enabled':
            # The backup script accepts boolean True or text 'YES'/'TRUE'/'1'
            truthy = (v is True) or (str(v).strip().upper() in ('YES', 'TRUE', '1'))
            annotation = "  [will be backed up]" if truthy else "  [will be SKIPPED]"
        print(f"  [PRESENT]          {expected:<18}{case_note}  -> {val_display}{annotation}")
    for prop in missing:
        print(f"  [NOT YET CREATED]  {prop}")

    if missing:
        print(f"\n  Still to create: {', '.join(missing)}")
    else:
        print("\n  All nine backup-automation properties are defined as columns.")


def main():
    print("SolarWinds device + custom-property check")
    print("-" * 78)
    server   = prompt("SolarWinds server hostname", default="solarwinds.example.com")
    username = prompt("SolarWinds username")
    password = prompt("SolarWinds password", hidden=True)
    if not (server and username and password):
        print("ERROR: server, username, and password are all required.")
        return 2

    verify_ssl = prompt("Verify SSL? [y/N]", default="N").lower().startswith('y')

    print("\nSearch by:")
    print("  1) IP address")
    print("  2) Hostname (case-insensitive contains)")
    choice = prompt("Pick 1 or 2", default="1")
    search_field = 'caption' if choice.startswith('2') else 'ip'
    search_value = prompt("IP / hostname")
    if not search_value:
        print("ERROR: search value is required.")
        return 2

    print(f"\nQuerying {server} ...")
    try:
        swis = SwisClient(server, username, password, verify=verify_ssl)
        cp_columns = discover_cp_columns(swis)
        results = fetch_device_details(swis, search_field, search_value, cp_columns)
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        return 1

    print(f"Connected. {len(cp_columns)} user-defined custom-property column(s) found in the schema.")

    if not results:
        print("No matching devices found.")
        return 1

    print(f"{len(results)} device(s) matched.")
    for node, cp in results:
        display_node(node, cp)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
