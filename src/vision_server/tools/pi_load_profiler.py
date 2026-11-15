"""Show live (and as a summary) which processes on the Pi use how much CPU and RAM.

This checks whether processing the 12 MP images (or another process) loads the
Pi so much that the effective frame rate drops far below 1 fps.

It is a pure stdlib solution. It reads `/proc/[pid]/stat`, `/proc/[pid]/status`,
`/proc/loadavg` and `/proc/meminfo`. It deliberately avoids `psutil` and similar
packages, so that no extra `pip install` is needed on an already overloaded or
hung Pi, and so that the profiler itself adds as little load as possible.

If available, it also reads the CPU temperature (`/sys/class/thermal`) and the
Raspberry Pi throttling status (`vcgencmd get_throttled`). A Pi throttled by
undervoltage or heat can show exactly the symptom "the CPU does not even look
fully loaded, but everything is slow", which a pure CPU% process list does not
show.

Sampling is interval-based (default: every 2 s). Very short peaks (shorter than
one interval) between two measurements may therefore be underestimated. To find
"which script or long-running process uses CPU continuously", this is enough.
For single short outliers, reduce `--interval` if needed.

A process list alone shows only what `top` also shows. The `vision_server` runs
as ONE Python process that handles camera capture, detection and OPC UA/MJPEG in
several threads (`vision-camera`, `vision-<profile>`, `vision-calibration`; see
``ThreadPoolExecutor(..., thread_name_prefix=...)`` in camera.py and
detection/base.py). "python3 uses 90% CPU" therefore does not yet say *which*
part of it is the problem. This tool additionally breaks CPU% down per thread
inside the `vision_server` process (`/proc/[pid]/task/[tid]/stat`). The process
is chosen with `--pid`, or automatically as the first process whose command
line contains "vision_server".

The thread breakdown shows only TIDs and CPU%, not meaningful names. CPython
does not pass `threading.Thread(name=...)` or `thread_name_prefix` on to the
kernel, so `/proc/[pid]/task/[tid]/comm` shows only "python3" for every thread
(verified with a small test script). To still see which *code* (camera capture,
AprilTag detection, ...) uses the time, `--pyspy` runs `py-spy top` on the same
target process instead. It reads the real Python call stack per thread
(ptrace-based, without changing vision_server code). py-spy must be installed
separately (`pip install py-spy`, or on the Pi possibly via `pipx` or in the
venv). It usually needs root (`sudo py-spy ...`), because `ptrace_scope` on
Debian and Raspberry Pi OS protects foreign processes by default.

Usage on the Pi, e.g. while the server runs under load:

    python3 src/vision_server/tools/pi_load_profiler.py --duration 120 --csv load.csv

For the function call stack instead of pure CPU% numbers (possibly with sudo):

    python3 src/vision_server/tools/pi_load_profiler.py --pyspy

Ctrl+C ends the run early and still prints the summary.
"""

import argparse
import csv
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field

CLOCK_TICKS = os.sysconf("SC_CLK_TCK")


@dataclass
class ProcSample:
    """One CPU and RAM sample of a process."""

    pid: int
    name: str
    cpu_percent: float
    rss_kb: int


@dataclass
class ProcStats:
    """Collected CPU and RAM samples of one process."""

    name: str
    cpu_samples: list = field(default_factory=list)
    rss_samples: list = field(default_factory=list)

    @property
    def avg_cpu(self) -> float:
        return sum(self.cpu_samples) / len(self.cpu_samples) if self.cpu_samples else 0.0

    @property
    def max_cpu(self) -> float:
        return max(self.cpu_samples, default=0.0)

    @property
    def max_rss_kb(self) -> int:
        return max(self.rss_samples, default=0)


@dataclass
class ThreadSample:
    """One CPU sample of a thread."""

    tid: int
    name: str
    cpu_percent: float


@dataclass
class ThreadStats:
    """Collected CPU samples of one thread."""

    name: str
    cpu_samples: list = field(default_factory=list)

    @property
    def avg_cpu(self) -> float:
        return sum(self.cpu_samples) / len(self.cpu_samples) if self.cpu_samples else 0.0

    @property
    def max_cpu(self) -> float:
        return max(self.cpu_samples, default=0.0)


def _read_proc_name(pid: int) -> str | None:
    try:
        with open(f"/proc/{pid}/comm") as handle:
            return handle.read().strip()
    except OSError:
        return None


def _read_proc_cpu_jiffies(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/stat") as handle:
            raw = handle.read()
    except OSError:
        return None
    # Field 2 (comm) may contain spaces or parentheses, so count from the last
    # closing parenthesis, then take utime (14th) + stime (15th) field.
    fields = raw[raw.rfind(")") + 1:].split()
    try:
        return int(fields[11]) + int(fields[12])
    except (IndexError, ValueError):
        return None


def _read_proc_rss_kb(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/status") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        return None
    return None


def _list_pids() -> list[int]:
    return [int(entry) for entry in os.listdir("/proc") if entry.isdigit()]


def _read_thread_name(pid: int, tid: int) -> str | None:
    try:
        with open(f"/proc/{pid}/task/{tid}/comm") as handle:
            return handle.read().strip()
    except OSError:
        return None


def _read_thread_cpu_jiffies(pid: int, tid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/task/{tid}/stat") as handle:
            raw = handle.read()
    except OSError:
        return None
    fields = raw[raw.rfind(")") + 1:].split()
    try:
        return int(fields[11]) + int(fields[12])
    except (IndexError, ValueError):
        return None


def _list_tids(pid: int) -> list[int]:
    try:
        return [int(entry) for entry in os.listdir(f"/proc/{pid}/task") if entry.isdigit()]
    except OSError:
        return []


def _find_vision_server_pid() -> int | None:
    """Return the first Python process whose command line contains "vision_server", or None.

    Used for the automatic thread breakdown when no ``--pid`` is given. The
    process name is checked as well, not only the command line. Otherwise a
    shell whose arguments or history contain the path would also match.
    """
    for pid in _list_pids():
        name = _read_proc_name(pid)
        if not name or not name.startswith("python"):
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as handle:
                cmdline = handle.read().decode(errors="replace")
        except OSError:
            continue
        if "vision_server" in cmdline:
            return pid
    return None


def _read_loadavg() -> tuple[float, float, float]:
    return os.getloadavg()


def _read_mem_percent() -> float | None:
    total = available = None
    try:
        with open("/proc/meminfo") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    available = int(line.split()[1])
    except OSError:
        return None
    if total is None or available is None or total == 0:
        return None
    return (1 - available / total) * 100


def _read_cpu_temp_c() -> float | None:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as handle:
            return int(handle.read().strip()) / 1000
    except (OSError, ValueError):
        return None


def _read_throttled_flags() -> str | None:
    """Return the throttling flags from `vcgencmd get_throttled`, or None.

    Bits 0-3 show the current state (undervoltage, frequency capped, throttled,
    soft temperature limit). Bits 16-19 show whether it occurred since boot.
    Any value other than "0x0" is a strong hint that the image processing itself
    is not the problem, but that the power supply or cooling throttles the Pi.
    """
    try:
        result = subprocess.run(
            ["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip().removeprefix("throttled=")


def _sample_processes(prev_cpu_jiffies: dict, interval_s: float) -> tuple[list[ProcSample], dict]:
    """Sample the CPU use of all processes since the previous snapshot.

    Return the samples and the new jiffies snapshot. A process seen for the
    first time has no delta yet and is skipped.
    """
    cpu_jiffies: dict[int, int] = {}
    samples: list[ProcSample] = []
    tick_budget = CLOCK_TICKS * interval_s

    for pid in _list_pids():
        jiffies = _read_proc_cpu_jiffies(pid)
        if jiffies is None:
            continue
        cpu_jiffies[pid] = jiffies

        previous = prev_cpu_jiffies.get(pid)
        if previous is None:
            continue  # first sample for this PID -- no delta yet

        name = _read_proc_name(pid) or f"pid-{pid}"
        rss_kb = _read_proc_rss_kb(pid) or 0
        cpu_percent = max(0.0, (jiffies - previous) / tick_budget * 100)
        samples.append(ProcSample(pid=pid, name=name, cpu_percent=cpu_percent, rss_kb=rss_kb))

    return samples, cpu_jiffies


def _sample_threads(pid: int, prev_cpu_jiffies: dict, interval_s: float) -> tuple[list[ThreadSample], dict]:
    """Sample the CPU use of all threads of `pid` since the previous snapshot."""
    cpu_jiffies: dict[int, int] = {}
    samples: list[ThreadSample] = []
    tick_budget = CLOCK_TICKS * interval_s

    for tid in _list_tids(pid):
        jiffies = _read_thread_cpu_jiffies(pid, tid)
        if jiffies is None:
            continue
        cpu_jiffies[tid] = jiffies

        previous = prev_cpu_jiffies.get(tid)
        if previous is None:
            continue  # first sample for this TID -- no delta yet

        name = _read_thread_name(pid, tid) or f"tid-{tid}"
        cpu_percent = max(0.0, (jiffies - previous) / tick_budget * 100)
        samples.append(ThreadSample(tid=tid, name=name, cpu_percent=cpu_percent))

    return samples, cpu_jiffies


def _print_snapshot(samples: list[ProcSample], top_n: int, elapsed_s: float) -> None:
    """Print the system header line and the top processes of one sample."""
    load1, load5, load15 = _read_loadavg()
    mem_percent = _read_mem_percent()
    temp_c = _read_cpu_temp_c()
    throttled = _read_throttled_flags()

    header = f"\n=== t={elapsed_s:6.1f}s  load={load1:.2f}/{load5:.2f}/{load15:.2f}"
    if mem_percent is not None:
        header += f"  mem={mem_percent:.0f}%"
    if temp_c is not None:
        header += f"  temp={temp_c:.1f}C"
    if throttled is not None and throttled != "0x0":
        header += f"  THROTTLED={throttled}"
    print(header)

    top = sorted(samples, key=lambda s: s.cpu_percent, reverse=True)[:top_n]
    print(f"{'PID':>7}  {'CPU%':>6}  {'RSS_MB':>7}  NAME")
    for sample in top:
        print(f"{sample.pid:>7}  {sample.cpu_percent:6.1f}  {sample.rss_kb / 1024:7.1f}  {sample.name}")


def _print_thread_snapshot(pid: int, samples: list[ThreadSample], top_n: int) -> None:
    """Print the top threads of the target process."""
    print(f"--- Threads von PID {pid} (vision_server) ---")
    if not samples:
        print("  (keine Thread-Daten -- Prozess beendet?)")
        return
    top = sorted(samples, key=lambda s: s.cpu_percent, reverse=True)[:top_n]
    print(f"  {'TID':>7}  {'CPU%':>6}  NAME")
    for sample in top:
        print(f"  {sample.tid:>7}  {sample.cpu_percent:6.1f}  {sample.name}")


def _run_pyspy(pid: int) -> int:
    """Pass the PID on to `py-spy top`.

    Unlike our own thread table, it shows the real Python call stack per thread.
    py-spy reads the interpreter state directly via ptrace, instead of relying
    on OS thread names.
    """
    print(f"Starte 'py-spy top --pid {pid}' (Ctrl+C zum Beenden) ...")
    try:
        result = subprocess.run(["py-spy", "top", "--pid", str(pid)])
    except FileNotFoundError:
        print(
            "FEHLER: 'py-spy' ist nicht installiert bzw. nicht im PATH. "
            "Installieren z. B. mit 'pip install py-spy' im vision_server-venv.",
            file=sys.stderr,
        )
        return 1
    except PermissionError:
        print(
            "FEHLER: keine Berechtigung, den Prozess zu tracen -- mit 'sudo' erneut versuchen "
            "(ptrace_scope schuetzt auf Debian/Raspberry Pi OS standardmaessig fremde Prozesse).",
            file=sys.stderr,
        )
        return 1
    return result.returncode


def run(args: argparse.Namespace) -> int:
    """Run the profiler loop until Ctrl+C or the duration ends, then print summaries.

    With ``--pyspy`` it hands over to `py-spy top` instead.
    """
    stats: dict[int, ProcStats] = {}
    thread_stats: dict[int, ThreadStats] = {}
    csv_writer = None
    csv_file = None
    if args.csv:
        csv_file = open(args.csv, "w", newline="")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(["elapsed_s", "scope", "pid_or_tid", "name", "cpu_percent", "rss_kb"])

    target_pid = args.pid
    if target_pid is None and (not args.no_threads or args.pyspy):
        target_pid = _find_vision_server_pid()
        if target_pid is not None:
            print(f"Thread-Aufschluesselung: automatisch erkannter vision_server-Prozess PID {target_pid}")
        else:
            print("Thread-Aufschluesselung: kein vision_server-Prozess gefunden (--pid setzen falls anders benannt)")

    if args.pyspy:
        if target_pid is None:
            print("FEHLER: kein Ziel-Prozess fuer --pyspy gefunden -- --pid explizit setzen.", file=sys.stderr)
            return 1
        if csv_file:
            csv_file.close()
        return _run_pyspy(target_pid)

    stop = False

    def _handle_sigint(signum, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _handle_sigint)

    print(f"Sample-Intervall: {args.interval_s:.1f}s, Top {args.top}, Ctrl+C zum Beenden.")
    start = time.monotonic()
    prev_cpu_jiffies: dict[int, int] = {}
    _, prev_cpu_jiffies = _sample_processes(prev_cpu_jiffies, args.interval_s)
    prev_thread_jiffies: dict[int, int] = {}
    if target_pid is not None:
        _, prev_thread_jiffies = _sample_threads(target_pid, prev_thread_jiffies, args.interval_s)

    try:
        while not stop:
            time.sleep(args.interval_s)
            if stop:
                break
            elapsed = time.monotonic() - start
            samples, prev_cpu_jiffies = _sample_processes(prev_cpu_jiffies, args.interval_s)
            _print_snapshot(samples, args.top, elapsed)

            for sample in samples:
                entry = stats.setdefault(sample.pid, ProcStats(name=sample.name))
                entry.cpu_samples.append(sample.cpu_percent)
                entry.rss_samples.append(sample.rss_kb)
                if csv_writer:
                    csv_writer.writerow([f"{elapsed:.1f}", "process", sample.pid, sample.name,
                                          f"{sample.cpu_percent:.1f}", sample.rss_kb])

            if target_pid is not None:
                thread_samples, prev_thread_jiffies = _sample_threads(target_pid, prev_thread_jiffies, args.interval_s)
                _print_thread_snapshot(target_pid, thread_samples, args.top)
                for tsample in thread_samples:
                    tentry = thread_stats.setdefault(tsample.tid, ThreadStats(name=tsample.name))
                    tentry.cpu_samples.append(tsample.cpu_percent)
                    if csv_writer:
                        csv_writer.writerow([f"{elapsed:.1f}", "thread", tsample.tid, tsample.name,
                                              f"{tsample.cpu_percent:.1f}", ""])

            if args.duration_s and elapsed >= args.duration_s:
                break
    finally:
        if csv_file:
            csv_file.close()

    print("\n=== Zusammenfassung Prozesse (nach Durchschnitts-CPU%, absteigend) ===")
    print(f"{'PID':>7}  {'AVG_CPU%':>8}  {'MAX_CPU%':>8}  {'MAX_RSS_MB':>10}  NAME")
    ranked = sorted(stats.items(), key=lambda item: item[1].avg_cpu, reverse=True)
    for pid, entry in ranked[: args.top]:
        print(f"{pid:>7}  {entry.avg_cpu:8.1f}  {entry.max_cpu:8.1f}  {entry.max_rss_kb / 1024:10.1f}  {entry.name}")

    if target_pid is not None:
        print(f"\n=== Zusammenfassung Threads von PID {target_pid} (nach Durchschnitts-CPU%, absteigend) ===")
        print(f"{'TID':>7}  {'AVG_CPU%':>8}  {'MAX_CPU%':>8}  NAME")
        ranked_threads = sorted(thread_stats.items(), key=lambda item: item[1].avg_cpu, reverse=True)
        for tid, tentry in ranked_threads[: args.top]:
            print(f"{tid:>7}  {tentry.avg_cpu:8.1f}  {tentry.max_cpu:8.1f}  {tentry.name}")

    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse the command line and run the profiler."""
    parser = argparse.ArgumentParser(
        description="Zeigt live, welche Prozesse auf dem Pi wie viel CPU/RAM verbrauchen",
    )
    parser.add_argument("--interval", dest="interval_s", type=float, default=2.0,
                         help="Sekunden zwischen zwei Messungen (Standard: 2.0)")
    parser.add_argument("--top", type=int, default=10, help="Anzahl Prozesse in Liste/Zusammenfassung")
    parser.add_argument("--duration", dest="duration_s", type=float, default=None,
                         help="Nach dieser Zeit (s) automatisch beenden; ohne Angabe bis Ctrl+C")
    parser.add_argument("--csv", type=str, default=None, help="Pfad fuer eine CSV-Log-Datei aller Samples")
    parser.add_argument("--pid", type=int, default=None,
                         help="PID, dessen Threads zusaetzlich einzeln aufgeschluesselt werden "
                              "(Standard: automatisch der erste Prozess mit 'vision_server' in der Kommandozeile)")
    parser.add_argument("--no-threads", action="store_true",
                         help="Keine automatische Thread-Aufschluesselung versuchen")
    parser.add_argument("--pyspy", action="store_true",
                         help="Statt eigener Messung 'py-spy top' auf den Ziel-Prozess starten -- "
                              "zeigt den echten Python-Call-Stack pro Thread (muss installiert sein, "
                              "braucht meist sudo)")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
