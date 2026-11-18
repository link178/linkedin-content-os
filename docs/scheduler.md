# Scheduled editorial cycle (Windows)

Headless weekday preparation uses **Windows Task Scheduler** → [`scripts/scheduled_cycle.py`](../scripts/scheduled_cycle.py) → existing [`scripts/cycle.py`](cycle-contract.md). The scheduler does not approve, publish, or open the Inbox HTTP server.

## Default schedule

Configured in [`config/scheduler.json`](../config/scheduler.json):

- Monday–Friday at **09:00** local time. The example `config/scheduler.json` expects Windows timezone id `UTC`. Set `timezone_id` to the machine zone before install.
- One run per calendar day (`max_runs_per_day`: 1).
- No catch-up after missed runs (`start_when_available`: false).
- No wake-from-sleep (`wake_to_run`: false).
- Task Scheduler **IgnoreNew** when a previous instance is still running.
- Runner wall clock: **5400s** (90 minutes); task hard limit **6000s**.

Change frequency by editing `weekdays` / `start_time_local` in that JSON, then re-run install.

## Install and control

From the repository root, with the same Python you use for `cycle.py`:

```text
python scripts/schedule_windows.py install
python scripts/schedule_windows.py status
python scripts/schedule_windows.py disable
python scripts/schedule_windows.py enable
python scripts/schedule_windows.py remove
python scripts/schedule_windows.py run-manual --test-mode
python scripts/schedule_windows.py verify-install
python scripts/schedule_windows.py remove-verify
```

`install --dry-run` prints Task Scheduler XML without registering.

If preflight fails (wrong timezone or missing `agent.cmd`), the task is registered **disabled** until you fix the environment and run `enable`.

## Logs and records

| Artifact | Purpose |
| --- | --- |
| `content/cycles/runs/RUN-*.log` | Redacted stdout/stderr from `cycle.py` for that scheduled attempt |
| `content/cycles/ops-runs.jsonl` | Append-only operational history (scheduled + metadata) |
| `content/cycles/cycle.lock` | Cross-process guard (not hand-edited) |

The Inbox dashboard **Scheduled runs** panel reads `ops-runs.jsonl` and shows the latest row for each `run_id`. Inbox need not be running during a scheduled run. The daily slot counts one execution per `run_id`. A reconcile `INTERRUPTED` row followed by a completion for the same id consumes one slot. `SKIPPED_ALREADY_RUNNING` does not consume a slot.

## Concurrency

[`scripts/cycle_guard.py`](../scripts/cycle_guard.py) provides one global lock for `run`, `resume`, and `discover`. Overlapping starts return `SKIPPED_ALREADY_RUNNING`. Inbox **Generate** and **Resume** probe the same lock.

## Cost and limits

The runtime does not measure token or currency cost. Enforceable limits: one scheduled run per weekday, one pipeline at a time, no automatic full-cycle retry, per-call agent timeout (180s) and discovery assessment timeout (600s) unchanged, and the 90-minute scheduled runner cap.

## Observed host limits

On 18 November 2026 the laptop was in Modern Standby at 09:00. The lid opened at 09:10:01. Task Scheduler recorded last run 09:10:04 and result `0x800710E0` ("the operator or administrator has refused the request"). Python did not start and `ops-runs.jsonl` has no row for that attempt. `StartWhenAvailable` is false, so a missed 09:00 is not caught up later. The same standby window covered 17 November 09:00 (lid at 09:07) with no ops record and no new candidate. Task Scheduler operational history was disabled, so that engine attempt is not in the event log.

A later `schtasks /Run` while the interactive session was available did start the registered command without `--test-mode`.

## Recovery

- Failed or interrupted runs remain in `ops-runs.jsonl`; partial candidates stay under `content/` like manual runs.
- Stale `running` rows are closed as `INTERRUPTED` when the pid is gone and the lock is free.
- Disable automation: `python scripts/schedule_windows.py disable`.
- Re-enable after fixing credentials or timezone: `python scripts/schedule_windows.py enable`.

## Inbox

```text
python scripts/inbox.py
```

Open `http://127.0.0.1:8765/`. Scheduled outcomes appear in **Scheduled runs**; manual **Generate** uses the same pipeline and lock.