# Known Issues — Patterns and Pitfalls

Engineering patterns that bit this project and might bite a similar one. Each entry is the pattern, not the incident — focused on what to look for and how to avoid it.

---

## 1. Exception class equality vs. inheritance

- **Symptom:** Failure-reporting collapses to a single generic message regardless of the actual error class.
- **Pattern:** A dict keyed on exception types with `dict.get(type(e), default)` for routing — Python always raises a *subclass*, so a base-class key never matches and the default wins for everything.
- **Fix:** Use an explicit `except` chain ordered specific → general, or `isinstance()`. For the catch-all, surface `f"{type(e).__module__.rsplit('.',1)[-1]}.{type(e).__name__}: {str(e)[:200]}"`.
- **Status:** Resolved.

## 2. Per-device exception handler hides per-command failures

- **Symptom:** Output file ends mid-run; result reported as a hard failure even though the connection was fine.
- **Pattern:** A single try/except wraps the whole device's command loop. One slow / unsupported / paginated command throws and aborts the rest.
- **Fix:** Per-command try/except inside the loop. Log the exception into the output file and continue. Result becomes `success (N command errors)` instead of a hard fail.
- **Status:** Resolved.

## 3. Missing enable-mode escalation on legacy gear

- **Symptom:** A subset of older devices fail with auth or empty output despite valid credentials.
- **Pattern:** Privileged `show` commands need enable-mode. The driver requires both `secret=<enable_pw>` in `ConnectHandler` kwargs **and** an explicit `connection.enable()` call before sending commands. Forgetting either leaks as an authentication-shaped failure.
- **Fix:** Two SolarWinds custom properties (`Prompt`, `EnableCred`) drive the per-device escalation; both halves of the netmiko contract are honored.
- **Status:** Resolved.

## 4. Brittle prompt-stripping for filenames

- **Symptom:** Output filenames contain stray characters (`(config)`, trailing whitespace) or come out empty.
- **Pattern:** `connection.find_prompt()[:-1]` only chops one trailing character. Configuration mode, banner wraps, and trailing whitespace all break it.
- **Fix:** `connection.find_prompt().strip('#>$').strip()`.
- **Status:** Resolved.

## 5. `read_timeout` default too short for verbose commands

- **Symptom:** Big-chassis `show running-config` truncates or times out with a "pattern not detected" error.
- **Pattern:** netmiko's `send_command()` defaults to a 10-second read timeout. Large configs / `show tech` / full BGP tables routinely take 30–120 seconds.
- **Fix:** Per-driver `read_timeout` override (e.g. 120s for IOS-XE, 180s for WLCs). Pass it to every `send_command()` call.
- **Status:** Resolved.

## 6. Empty lines in command list = false positives

- **Symptom:** Output files contain command blocks with empty input and the prompt as "output". Command counter inflated.
- **Pattern:** `cmd_file.readlines()` yields blank lines; `connection.send_command('')` returns the prompt and looks like a successful command.
- **Fix:** `commands = [line.strip() for line in cmd_file if line.strip()]`.
- **Status:** Resolved.

## 7. Connection leak on exception

- **Symptom:** TCP `CLOSE_WAIT` accumulates over a long run; eventually the process exhausts file descriptors.
- **Pattern:** `connection.disconnect()` only on the happy path. Any exception above it leaks the socket.
- **Fix:** `disconnect()` in a `finally` block, or use the context-manager form (`with ConnectHandler(...) as conn:`).
- **Status:** Resolved.

## 8. Loader fallbacks make the config file optional

- **Symptom:** Script appears to work with a missing or stale config file because it silently falls back to hardcoded defaults — sometimes pointing at dev infra in production.
- **Pattern:** `os.getenv('KEY', 'hardcoded-default')` or `cfg.get('key', 'default')` at every read site means the config is decorative, not authoritative.
- **Fix:** Required keys have no defaults. Validate at startup; exit with an explicit list of missing keys before any work begins.
- **Status:** Resolved.

## 9. Silent skips on missing reference data

- **Symptom:** Devices that should be in scope are absent from both success and failure reports.
- **Pattern:** A required field (credential key, device type) is empty or unmapped; the worker drops the device with only a debug log message.
- **Fix:** Skipped devices land in the failed-reasons CSV with the reason as the `Result` value. Counts and percentages include them as failures.
- **Status:** Resolved.

## 10. Source-of-truth query returns zero rows

- **Symptom:** Inventory comes back empty against a healthy SoT.
- **Pattern:** Most-likely causes (in rough order): the API service account lacks read permission on a custom-property table; an SSL trust chain isn't trusted by the script's host; the WHERE clause uses a column type or value the SoT doesn't actually store (e.g. boolean column compared with `'YES'`); a query function name doesn't exist in this SoT version (`UPPER` vs. `ToUpper` in SWQL).
- **Fix:** Run the same query interactively in the SoT's query studio to isolate whether it's the script or the SoT.
- **Status:** Open.

## 11. JWT / OAuth token expiry on long runs

- **Symptom:** First N hours of operations succeed; everything after a token-lifetime boundary fails with 401.
- **Pattern:** SDK is initialized once at script start. The access token has a 60-minute lifetime; the SDK doesn't transparently refresh for every operation.
- **Fix candidates:** Re-authenticate immediately before each upload phase; catch 401, refresh, retry once; keep batches inside the token window.
- **Status:** Open.

## 12. Mail relay accepts then drops

- **Symptom:** SMTP transaction succeeds (script reports "email sent") but no message arrives; relay log shows the sender wasn't on the allowed list.
- **Pattern:** Restricted relays check the sender after accepting the SMTP envelope. The script can't tell that the message was dropped.
- **Fix:** Pin the sender to a whitelisted address in config. Don't change it without coordinating with messaging team. Optionally, send a self-test message at startup and verify delivery.
- **Status:** Open.

## 13. First-run on a fresh host fails on missing directory

- **Symptom:** `FileNotFoundError` writing the failed-reasons CSV before any device is contacted.
- **Pattern:** Output directories created on demand for backups but the reports directory was assumed to exist.
- **Fix:** `os.makedirs(path, exist_ok=True)` for *every* path in config.
- **Status:** Resolved.

## 14. Filename collision when runs overlap

- **Symptom:** Output files truncated or contain interleaved content.
- **Pattern:** Filenames embed `strftime('%Y-%m-%d_%H-%M-%S')`. Two runs starting in the same second share filenames.
- **Mitigations:** Don't run concurrently; add microseconds to the timestamp; or use a `flock` lockfile.
- **Status:** Mitigated (operational discipline).

## 15. Legacy telnet device hangs the worker

- **Symptom:** A handful of old devices block the worker thread for the full timeout before failing.
- **Pattern:** Some legacy gear sends a banner that doesn't terminate cleanly, or waits on a CR/LF before its login prompt. netmiko's prompt detector misses and waits out the read_timeout.
- **Fix:** Per-driver `fast_cli=False`, bumped `global_delay_factor`, and `banner_timeout`. Prefer SSH where firmware allows. Worst offenders may need explicit `expect_string=` on commands.
- **Status:** Mitigated.

---

## How to add a new entry

When a similar pattern bites in production, add a new numbered section using this template:

```markdown
## N. <Short pattern name>

- **Symptom:** <what an operator sees>
- **Pattern:** <the underlying engineering pattern>
- **Fix:** <approach, not necessarily code>
- **Status:** Resolved / Mitigated / Open
```

Bump the index at the top.
