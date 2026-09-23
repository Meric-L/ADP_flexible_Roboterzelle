"""Zeigt live (und als Zusammenfassung), welche Prozesse auf dem Pi wie viel
CPU und RAM verbrauchen -- fuer den Verdacht, dass die Verarbeitung der
12MP-Bilder (oder ein anderer Prozess) den Pi so auslastet, dass die
effektive Framerate weit unter 1 fps faellt.

Reine stdlib-Loesung (liest `/proc/[pid]/stat`, `/proc/[pid]/status`,
`/proc/loadavg`, `/proc/meminfo`) -- bewusst ohne `psutil` o.ae., damit auf
einem bereits ueberlasteten/haengenden Pi kein zusaetzliches `pip install`
noetig ist und der Profiler selbst so wenig Last wie moeglich erzeugt.

Liest zusaetzlich, falls vorhanden, CPU-Temperatur (`/sys/class/thermal`)
und den Raspberry-Pi-Throttling-Status (`vcgencmd get_throttled`) mit --
ein durch Unterspannung/Hitze gedrosselter Pi kann genau das Symptom
"CPU sieht nicht mal voll ausgelastet aus, aber alles ist langsam"
erzeugen, das eine reine CPU%-Prozessliste nicht zeigt.

Sampling-basiert (Standard: alle 2s) -- sehr kurze Spitzen (< 1 Interval)
zwischen zwei Messungen koennen dadurch unterschaetzt werden. Fuer den
Verdacht "welches Skript / welcher Dauerprozess frisst kontinuierlich CPU"
reicht das; fuer einzelne kurze Ausreisser ggf. --interval verkleinern.

Prozess-Liste allein ist nur das, was `top` auch zeigt: der `vision_server`
laeuft als EIN Python-Prozess, der Kamera-Capture, Detection und
OPC-UA/MJPEG in mehreren Threads erledigt (`vision-camera`,
`vision-<profile>`, `vision-calibration`, siehe `ThreadPoolExecutor(...,
thread_name_prefix=...)` in camera.py/detection/base.py). "python3 nimmt
90% CPU" sagt also noch nicht, *welcher* Teil davon das Problem ist.
Deshalb schluesselt dieses Tool zusaetzlich CPU% pro Thread innerhalb des
`vision_server`-Prozesses auf (`/proc/[pid]/task/[tid]/stat`) -- per
`--pid` explizit oder automatisch ueber den ersten Prozess, dessen
Kommandozeile "vision_server" enthaelt.

Fuer noch mehr Detail (welche Python-Funktion/welcher Call-Stack konkret
die Zeit frisst, nicht nur welcher Thread) waere `py-spy top --pid <PID>`
der naechste Schritt -- separat zu installieren, hier bewusst nicht
eingebaut, um dem Pi kein zusaetzliches Tool aufzudruecken ohne Rueckfrage.

Nutzung auf dem Pi, z. B. waehrend der Server unter Last laeuft:

    python3 src/vision_server/tools/pi_load_profiler.py --duration 120 --csv load.csv

Ctrl+C beendet vorzeitig und druckt trotzdem die Zusammenfassung.
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
    pid: int
    name: str
    cpu_percent: float
    rss_kb: int


@dataclass
class ProcStats:
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
    tid: int
    name: str
    cpu_percent: float


@dataclass
class ThreadStats:
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
    # Feld 2 (comm) kann Leerzeichen/Klammern enthalten -- ab der letzten
    # schliessenden Klammer zaehlen, dann utime (14.) + stime (15.) Feld.
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
    """Erster Python-Prozess, dessen Kommandozeile "vision_server" enthaelt
    -- fuer die automatische Thread-Aufschluesselung ohne manuelles --pid.
    Prueft zusaetzlich den Prozessnamen (nicht nur die Kommandozeile), sonst
    matcht z. B. auch eine Shell, in deren Historie/Argumenten der Pfad
    vorkommt."""
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
    """`vcgencmd get_throttled` -- Bit 0/1/2/3 = aktuell (unter Spannung,
    Frequenz gedrosselt, Throttling, Soft-Temp-Limit), Bit 16-19 = seit Boot
    schon mal aufgetreten. Ungleich "0x0" ist ein starkes Indiz dafuer, dass
    nicht die Bildverarbeitung selbst das Problem ist, sondern Netzteil/
    Kuehlung den Pi drosseln."""
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
            continue  # erster Sample fuer diesen PID -- noch kein Delta

        name = _read_proc_name(pid) or f"pid-{pid}"
        rss_kb = _read_proc_rss_kb(pid) or 0
        cpu_percent = max(0.0, (jiffies - previous) / tick_budget * 100)
        samples.append(ProcSample(pid=pid, name=name, cpu_percent=cpu_percent, rss_kb=rss_kb))

    return samples, cpu_jiffies


def _sample_threads(pid: int, prev_cpu_jiffies: dict, interval_s: float) -> tuple[list[ThreadSample], dict]:
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
            continue  # erster Sample fuer diesen TID -- noch kein Delta

        name = _read_thread_name(pid, tid) or f"tid-{tid}"
        cpu_percent = max(0.0, (jiffies - previous) / tick_budget * 100)
        samples.append(ThreadSample(tid=tid, name=name, cpu_percent=cpu_percent))

    return samples, cpu_jiffies


def _print_snapshot(samples: list[ProcSample], top_n: int, elapsed_s: float) -> None:
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
    print(f"--- Threads von PID {pid} (vision_server) ---")
    if not samples:
        print("  (keine Thread-Daten -- Prozess beendet?)")
        return
    top = sorted(samples, key=lambda s: s.cpu_percent, reverse=True)[:top_n]
    print(f"  {'TID':>7}  {'CPU%':>6}  NAME")
    for sample in top:
        print(f"  {sample.tid:>7}  {sample.cpu_percent:6.1f}  {sample.name}")


def run(args: argparse.Namespace) -> int:
    stats: dict[int, ProcStats] = {}
    thread_stats: dict[int, ThreadStats] = {}
    csv_writer = None
    csv_file = None
    if args.csv:
        csv_file = open(args.csv, "w", newline="")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(["elapsed_s", "scope", "pid_or_tid", "name", "cpu_percent", "rss_kb"])

    target_pid = args.pid
    if target_pid is None and not args.no_threads:
        target_pid = _find_vision_server_pid()
        if target_pid is not None:
            print(f"Thread-Aufschluesselung: automatisch erkannter vision_server-Prozess PID {target_pid}")
        else:
            print("Thread-Aufschluesselung: kein vision_server-Prozess gefunden (--pid setzen falls anders benannt)")

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
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
