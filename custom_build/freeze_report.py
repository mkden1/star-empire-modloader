"""Summarise what the freeze watchdog (_internal/freeze_watchdog.py) wrote to the client log.

    custom_build\\python.exe custom_build\\freeze_report.py [path\\to\\star_empire_client.log]

Prints, for the whole log: how many stalls each thread had and how long, and which code the
message ("app") thread was sitting in when the watchdog dumped every thread's stack -- grouped,
most common first. After a change meant to remove stalls, compare this report before and after:
the stack that used to head the list should be gone.

Reading the groups:
  * a stack inside our own code (a logger, a file call) is a stall we can remove;
  * "Client.py ... start" is the app thread's idle wait (clock.tick) -- both threads sitting at
    their normal waits means the whole process was held up (page-ins, CPU starvation);
  * the "OTHER threads" list shows what the writer / loader threads were doing at the same moment,
    e.g. several threads inside os.makedirs at once means the drive itself stalled.
"""
import os
import re
import sys
from collections import Counter

DEFAULT_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "star_empire_client.log")

THREAD_RE = re.compile(r"^Thread 0x[0-9a-f]+(?: \[([^\]]*)\])? \(most recent call first\):")
FRAME_RE = re.compile(r'^\s+File "([^"]+)", line (\d+) in (\S+)')
LOG_LINE_RE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d \[")
STALL_RE = re.compile(r"(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d) \[WARNING\] freeze_watchdog: "
                      r"freeze_watchdog: the (app|render) thread was stalled for ([0-9.]+)s")
IDLE_FILES = ("threading.py", "queue.py", "concurrent\\futures\\thread.py")


def short(path):
    for marker in ("\\_internal\\", "\\custom_build\\"):
        if marker in path:
            return path.split(marker, 1)[1]
    return path


def read_dumps(lines):
    """Every 'Timeout (...)!' faulthandler dump, as a list of threads (name, [(file, line, fn)])."""
    dumps, cur = [], None
    for ln in lines:
        if ln.startswith("Timeout ("):
            cur = []
            dumps.append(cur)
            continue
        if cur is None:
            continue
        m = THREAD_RE.match(ln)
        if m:
            cur.append((m.group(1) or "", []))
            continue
        f = FRAME_RE.match(ln)
        if f and cur:
            cur[-1][1].append((short(f.group(1)), int(f.group(2)), f.group(3)))
        elif LOG_LINE_RE.match(ln):
            cur = None                       # the dump is over; ordinary log lines resume
    return dumps


def describe(frames, depth):
    if not frames:
        return "(no Python frame)"
    return " <- ".join("%s:%d %s" % f for f in frames[:depth])


def main(argv):
    path = argv[1] if len(argv) > 1 else DEFAULT_LOG
    # faulthandler writes through its own handle, so a dump can split a log line in two; the
    # one splice that matters (a timestamp cut by a dump) is repaired by matching loosely below.
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    stalls = [(d, t, who, float(s)) for d, t, who, s in STALL_RE.findall(text)]
    dumps = read_dumps(text.splitlines())

    print("log: %s" % path)
    print("stall warnings: %d   stack dumps: %d" % (len(stalls), len(dumps)))
    days = sorted({d for d, _, _, _ in stalls})
    for who in ("app", "render"):
        xs = [s for _, _, w, s in stalls if w == who]
        if xs:
            print("  %-6s n=%-4d >=0.5s:%-3d >=1s:%-3d >=2s:%-3d max=%.2fs total=%.0fs" % (
                who, len(xs), sum(x >= .5 for x in xs), sum(x >= 1 for x in xs),
                sum(x >= 2 for x in xs), max(xs), sum(xs)))
    for day in days:
        n = sum(1 for d, _, _, _ in stalls if d == day)
        print("  %s: %d stalls" % (day, n))

    app, render, others = Counter(), Counter(), Counter()
    for threads in dumps:
        main_t = next((t for t in threads
                       if any(fn == "<module>" and "run_client" in p for p, _, fn in t[1])), None)
        render_t = next((t for t in threads if t[0] == "SolarSystemWindow"), None)
        if main_t is None:
            continue
        app[describe(main_t[1], 4)] += 1
        if render_t is not None:
            render[describe(render_t[1], 3)] += 1
        for t in threads:
            if t is main_t or t is render_t or not t[1]:
                continue
            top = t[1][0]
            if top[0] in IDLE_FILES or (top[0] == "arena_connection.py" and top[2] == "run"):
                continue
            others[(t[0] or "?") + " :: " + describe(t[1], 3)] += 1

    def show(title, counter, n):
        print("\n=== %s ===" % title)
        for stack, count in counter.most_common(n):
            print("%4d  %s" % (count, stack[:260]))

    show("APP (message) thread: where it was stuck", app, 15)
    show("RENDER thread: where it was", render, 8)
    show("OTHER threads caught doing something", others, 12)


if __name__ == "__main__":
    main(sys.argv)
