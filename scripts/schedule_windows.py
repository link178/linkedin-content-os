"""Install and manage the Windows Task Scheduler entry for scheduled_cycle.py. Stdlib only."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
DEFAULT_CONFIG = ROOT / "config" / "scheduler.json"


def load_config(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("scheduler config must be a JSON object")
    return data


def local_timezone_id() -> str:
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", "[TimeZoneInfo]::Local.Id"],
        capture_output=True,
        text=True,
        check=False,
    )
    return (completed.stdout or "").strip()


def agent_available() -> bool:
    import cursor_runtime as cursor_runtime_mod

    return cursor_runtime_mod.default_agent_path().is_file()


def preflight(config: dict) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    expected_tz = str(config.get("timezone_id") or "UTC")
    actual_tz = local_timezone_id()
    if actual_tz != expected_tz:
        reasons.append(f"local timezone is {actual_tz!r}, expected {expected_tz!r}")
    if not Path(sys.executable).is_file():
        reasons.append(f"python executable missing: {sys.executable}")
    script = ROOT / "scripts" / "scheduled_cycle.py"
    if not script.is_file():
        reasons.append(f"runner missing: {script}")
    if not agent_available():
        reasons.append("Cursor agent.cmd was not found; log in after install if needed")
    return len(reasons) == 0, reasons


def _iso_duration(seconds: int) -> str:
    if seconds <= 0:
        return "PT0S"
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    parts = "PT"
    if hours:
        parts += f"{hours}H"
    if minutes:
        parts += f"{minutes}M"
    if secs or parts == "PT":
        parts += f"{secs}S"
    return parts


def _weekday_tags(weekdays: list[str]) -> str:
    tags = []
    for day in weekdays:
        name = day.strip()
        if not name:
            continue
        tags.append(f"<{name} />")
    return "\n          ".join(tags)


def build_task_xml(
    config: dict,
    *,
    python_exe: Path,
    runner_args: str,
    enabled: bool,
    one_shot: bool = False,
) -> str:
    task_name = str(config.get("task_name") or "LinkedInContentOS-EditorialCycle")
    start_time = str(config.get("start_time_local") or "09:00")
    weekdays = list(config.get("weekdays") or ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"])
    limit_sec = int(config.get("task_time_limit_sec") or 6000)
    wake = "true" if config.get("wake_to_run") else "false"
    start_avail = "true" if config.get("start_when_available") else "false"
    # StartBoundary date portion is refreshed on install; Task Scheduler uses time-of-day from trigger.
    boundary = f"2026-01-01T{start_time}:00"
    enabled_text = "true" if enabled else "false"
    weekday_xml = _weekday_tags(weekdays)
    if one_shot:
        trigger_xml = f"""
    <TimeTrigger>
      <StartBoundary>{boundary}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>"""
    else:
        trigger_xml = f"""
    <CalendarTrigger>
      <StartBoundary>{boundary}</StartBoundary>
      <ScheduleByWeek>
        <DaysOfWeek>
          {weekday_xml}
        </DaysOfWeek>
        <WeeksInterval>1</WeeksInterval>
      </ScheduleByWeek>
      <Enabled>true</Enabled>
    </CalendarTrigger>"""
    runner = ROOT / "scripts" / "scheduled_cycle.py"
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>LinkedIn Content OS headless editorial cycle ({task_name})</Description>
  </RegistrationInfo>
  <Triggers>{trigger_xml}
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>{start_avail}</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <WakeToRun>{wake}</WakeToRun>
    <Enabled>{enabled_text}</Enabled>
    <ExecutionTimeLimit>{_iso_duration(limit_sec)}</ExecutionTimeLimit>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{python_exe}</Command>
      <Arguments>{runner_args}</Arguments>
      <WorkingDirectory>{ROOT}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _write_xml(text: str) -> Path:
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-16", suffix=".xml", delete=False)
    try:
        handle.write(text)
        handle.flush()
        return Path(handle.name)
    finally:
        handle.close()


def schtasks(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True, check=False)


def task_exists(name: str) -> bool:
    completed = schtasks(["/Query", "/TN", name])
    return completed.returncode == 0


def cmd_install(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    task_name = str(config.get("task_name"))
    ok, reasons = preflight(config)
    enabled = ok and not args.disabled
    runner_args = f"\"{ROOT / 'scripts' / 'scheduled_cycle.py'}\" --repo-root \"{ROOT}\" --content-root \"{ROOT / 'content'}\" --config \"{config_path}\""
    if args.test_mode_runner:
        runner_args += " --test-mode"
    xml = build_task_xml(config, python_exe=Path(sys.executable).resolve(), runner_args=runner_args, enabled=enabled)
    if args.dry_run:
        print(xml)
        if reasons:
            print("preflight notes:", "; ".join(reasons))
        return 0
    xml_path = _write_xml(xml)
    try:
        completed = schtasks(["/Create", "/TN", task_name, "/XML", str(xml_path), "/F"])
    finally:
        xml_path.unlink(missing_ok=True)
    if completed.returncode != 0:
        print(completed.stderr or completed.stdout, file=sys.stderr)
        return completed.returncode
    state = "enabled" if enabled else "disabled"
    print(f"registered task {task_name} ({state})")
    if reasons:
        print("preflight:", "; ".join(reasons))
    return 0


def cmd_verify_install(args: argparse.Namespace) -> int:
    """One-shot verify task that runs scheduled_cycle --test-mode."""
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    verify_name = str(config.get("verify_task_name") or "LinkedInContentOS-EditorialCycle-Verify")
    runner_args = (
        f"\"{ROOT / 'scripts' / 'scheduled_cycle.py'}\" --repo-root \"{ROOT}\" "
        f"--content-root \"{ROOT / 'content'}\" --config \"{config_path}\" --test-mode"
    )
    xml = build_task_xml(
        config,
        python_exe=Path(sys.executable).resolve(),
        runner_args=runner_args,
        enabled=True,
        one_shot=False,
    )
    if args.dry_run:
        print(xml)
        return 0
    xml_path = _write_xml(xml)
    try:
        completed = schtasks(["/Create", "/TN", verify_name, "/XML", str(xml_path), "/F"])
    finally:
        xml_path.unlink(missing_ok=True)
    if completed.returncode != 0:
        print(completed.stderr or completed.stdout, file=sys.stderr)
        return completed.returncode
    run = schtasks(["/Run", "/TN", verify_name])
    if run.returncode != 0:
        print(run.stderr or run.stdout, file=sys.stderr)
        return run.returncode
    print(f"started verify task {verify_name}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config).resolve())
    task_name = str(config.get("task_name"))
    completed = schtasks(["/Query", "/TN", task_name, "/V", "/FO", "LIST"])
    if completed.returncode != 0:
        print(f"task {task_name} is not registered")
        return 1
    print(completed.stdout)
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config).resolve())
    task_name = str(config.get("task_name"))
    completed = schtasks(["/Change", "/TN", task_name, "/DISABLE"])
    print(completed.stdout or completed.stderr)
    return completed.returncode


def cmd_enable(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config).resolve())
    task_name = str(config.get("task_name"))
    completed = schtasks(["/Change", "/TN", task_name, "/ENABLE"])
    print(completed.stdout or completed.stderr)
    return completed.returncode


def cmd_remove(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config).resolve())
    task_name = str(config.get("task_name"))
    completed = schtasks(["/Delete", "/TN", task_name, "/F"])
    print(completed.stdout or completed.stderr)
    return completed.returncode


def cmd_remove_verify(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config).resolve())
    verify_name = str(config.get("verify_task_name"))
    completed = schtasks(["/Delete", "/TN", verify_name, "/F"])
    print(completed.stdout or completed.stderr)
    return completed.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Windows Task Scheduler controls for the editorial cycle.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    sub = parser.add_subparsers(dest="command", required=True)

    install = sub.add_parser("install", help="register the production weekday task")
    install.add_argument("--dry-run", action="store_true")
    install.add_argument("--disabled", action="store_true", help="register but leave disabled")
    install.add_argument("--test-mode-runner", action="store_true", help="register runner with --test-mode (manual only)")
    install.set_defaults(func=cmd_install)

    verify = sub.add_parser("verify-install", help="register and immediately run a --test-mode verify task")
    verify.add_argument("--dry-run", action="store_true")
    verify.set_defaults(func=cmd_verify_install)

    status = sub.add_parser("status", help="show schtasks query output")
    status.set_defaults(func=cmd_status)

    disable = sub.add_parser("disable")
    disable.set_defaults(func=cmd_disable)

    enable = sub.add_parser("enable")
    enable.set_defaults(func=cmd_enable)

    remove = sub.add_parser("remove")
    remove.set_defaults(func=cmd_remove)

    remove_verify = sub.add_parser("remove-verify")
    remove_verify.set_defaults(func=cmd_remove_verify)

    manual = sub.add_parser("run-manual", help="invoke scheduled_cycle directly (not via Task Scheduler)")
    manual.add_argument("--test-mode", action="store_true")
    def _run_manual(a: argparse.Namespace) -> int:
        cmd = [
            sys.executable,
            str(SCRIPTS / "scheduled_cycle.py"),
            "--repo-root",
            str(ROOT),
            "--content-root",
            str(ROOT / "content"),
            "--config",
            str(Path(a.config).resolve()),
        ]
        if a.test_mode:
            cmd.append("--test-mode")
        return subprocess.call(cmd)

    manual.set_defaults(func=_run_manual)

    return parser


def main(argv: list[str] | None = None) -> int:
    if sys.platform != "win32":
        print("schedule_windows.py is for Windows Task Scheduler only", file=sys.stderr)
        return 2
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())