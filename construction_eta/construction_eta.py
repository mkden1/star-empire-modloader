"""construction_eta.py - an estimated time to complete for each project in a station's Construction Bay.

What the server does (read off real sessions, see construction_diag.py): every ~10 s it pushes the whole
queue. Each project carries ``manhours_total`` and ``manhours_done`` (so the work left is exact) and its
blueprint's ``resource_costs`` (per unit, with ``manhours`` per unit). Each tick the projects are served in
QUEUE ORDER, each taking what it can use of every resource it needs - and a project needs ALL its resources at
once: one that is short of titanium or oil does not hold back the ones behind it (on a station with eight
projects the Beam Cannon, far down the queue, took all the metal while the Shield, Energy and Sensor waited for
titanium and oil that almost never came). A paused project is skipped. Progress per tick followed the supply of
the scarce resource (metal) exactly - 18,171 metal made 13,628 man-hours for a project costing 100M metal per
75M man-hours - so the speed of a project is bursty, never a steady number.

So the estimate is "work left / recent speed", made resource-aware:

  * the speed of the LEADING project is its own man-hours per second over its last WINDOW_INTERVALS updates,
    counting only updates in which it was running (a pause must not dilute it) - about 6 minutes, which stayed
    within about +-20% of the truth on a recorded run. The leader is the first project that is not paused,
    BLOCKED or STARVED: those are skipped, they use nothing and hold nothing back;
  * what the station SUPPLIES of each resource is the larger of what its projects used up and what ARRIVED in
    its cargo stream (``Flow``: any rise of a stock between two cargo messages), both over the last
    FLOW_WINDOW_S (6 h) - a resource delivered once an hour looks like a steady stream if only the minutes around
    a delivery are averaged. Each view is only a floor on the truth, "nothing arrives" is only believed when both
    have watched long enough to agree, and until then the supply is unknown;
  * a project is BLOCKED ("waiting for Titanium") when the supply of something it still needs more of than the
    station holds would take longer than BLOCK_HORIZON_S (a week) to bring in; the label holds until it would
    take under half of that. A leader that has not moved at all for its whole window is STARVED and skipped the
    same way, and named when every one of its shortages is known. A project's time is never less than what it is
    short of needs to arrive (its "floor"); when that is much longer than its work (TRICKLE_RATIO) it trickles
    along: the estimate says what limits it ("~3d (Titanium)") and it neither delays the projects behind it
    nor counts as ahead of them;
  * a project BEHIND the leader waits for it: its time is the time of everything ahead plus its own work at
    the speed the scarcest resource it still needs allows (supply / cost per man-hour; one with enough in stock
    for the rest of the project sets nothing). This assumes the projects ahead use all of it, so it is an
    upper bound if the leader leaves resources over; a follower seen to progress on its own is timed on its own
    speed;
  * not enough history yet shows "estimating...", a paused project "paused", one with nothing known "queued".

Known limit: with only minutes of history a single delivery of a rare resource is taken at face value (30
titanium in four minutes looks like a plausible steady rate); after an hour or two of watching it is not.

Display only: ``install(SolarSystemWindow, render_module)`` (called once from Client.py) wraps
``set_ps_construction_bay`` and ``set_ps_station_cargo`` (to feed the estimate; the originals run first and
unchanged) and ``_draw_station_overlay`` (to draw the text in the free space left of each progress bar, from the row
rectangles the game's own drawing publishes in ``_ps_cb_proj_rects``), so vanilla's drawing code stays as
the developer wrote it. A project that finishes is logged with its first estimate and the real time
(``construction_eta:`` lines in the client log), so the accuracy can be judged. An error anywhere switches
the estimate off for the session; ``STAR_EMPIRE_NO_CONSTRUCTION_ETA=1`` turns it off.
"""

from __future__ import annotations

import logging
import math
import os
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from construction_stock import num as _num, stock_from_cargo   # core: shared with construction_tooltip

logger = logging.getLogger(__name__)

ENV_OFF = "STAR_EMPIRE_NO_CONSTRUCTION_ETA"
WINDOW_INTERVALS = 36        # updates (~10 s each) the speed is averaged over: about 6 minutes
MIN_INTERVALS = 6            # fewer than this and the answer is "estimating..."
ROUGH_BELOW = 18             # fewer updates than this (~3 min) and the estimate is marked "?": early bursts mislead
MAX_INTERVAL_S = 30.0        # a longer gap between two updates is a gap in the data, not an interval
STALE_S = 60.0               # an estimate this old is not shown (the updates stopped)
MAX_STATIONS = 6
FLOW_WINDOW_S = 6 * 3600     # how far back what ARRIVES at the station is averaged: deliveries can be hours apart
FLOW_BUCKET_S = 60.0         # the inflow history is kept in buckets this long (a few hundred, not a message each)
MIN_FLOW_S = 120.0           # seconds of cargo stream watched before an inflow rate - or its absence - is believed
MIN_USED_S = 60.0            # seconds of running projects watched before their consumption is believed as a supply
BLOCK_HORIZON_S = 7 * 86400  # a resource that would take longer than this to arrive blocks the project: "waiting for" it
TRICKLE_RATIO = 1.5          # what a project waits for takes this much longer than its work: it trickles along
FINISHED_PROGRESS = 0.99     # a project that leaves the queue at or above this is counted as finished

_NOT_RESOURCE_COSTS = {"credits", "item_amount", "item_class", "item_type"}


@dataclass(frozen=True)
class Eta:
    kind: str                       # eta | estimating | starved | blocked | queued | paused | done
    seconds: "float | None" = None
    ahead: int = 0                  # unpaused projects ahead of it in the queue
    parallel: bool = False          # timed on its own speed because it is progressing behind the leader
    made_at: float = 0.0
    rough: bool = False             # built on little history: shown with a "?"
    resource: str = ""              # blocked: the name of the resource it is waiting for
    limited_by: str = ""            # eta: the resource whose arrival, not the work, sets the time

    @property
    def deadline(self) -> "float | None":
        return None if self.seconds is None else self.made_at + self.seconds


@dataclass
class _Row:
    pid: object
    name: str
    done: float
    total: float
    paused: bool
    ratios: dict
    progress: float
    labels: dict = field(default_factory=dict)
    role: str = ""                  # leader | follower | blocked | paused
    blocked: str = ""               # the name of the resource a blocked project waits for

    @property
    def remaining(self) -> float:
        return max(0.0, self.total - self.done)

    def label(self, resource: str) -> str:
        return self.labels.get(resource) or _pretty(resource)


def project_ratios(row: dict) -> "dict[str, float]":
    """Resource units each man-hour of this project uses up, per resource (per-unit cost / per-unit man-hours).
    Only the costs taken as the work goes ("periodic"); up-front ones do not set the speed."""
    hours = _num(row.get("manhours"))
    if hours <= 0:
        return {}
    ratios: "dict[str, float]" = {}
    costs = row.get("resource_costs")
    if isinstance(costs, list) and costs:
        for cost in costs:
            if not isinstance(cost, dict) or cost.get("timing", "periodic") == "initial":
                continue
            amount = _num(cost.get("amount"))
            if cost.get("resource") and amount > 0:
                key = str(cost["resource"])
                ratios[key] = ratios.get(key, 0.0) + amount / hours
    else:                           # an older server: the fixed cost_* fields
        for key, value in row.items():
            if key.startswith("cost_") and key[5:] not in _NOT_RESOURCE_COSTS and _num(value) > 0:
                ratios[key[5:]] = _num(value) / hours
    return ratios


def _pretty(key: str) -> str:
    return str(key).replace("_", " ").title()


def resource_labels(row: dict) -> "dict[str, str]":
    """The server's display name of each resource a project costs (Metal, Titanium...)."""
    names = {}
    costs = row.get("resource_costs")
    if isinstance(costs, list):
        for cost in costs:
            if isinstance(cost, dict) and cost.get("resource"):
                names[str(cost["resource"])] = cost.get("display_name") or _pretty(cost["resource"])
    return names


class Tally:
    """Amounts of resources added over time, kept in minute buckets over the last FLOW_WINDOW_S, with the time
    actually watched: rate(resource) is units per watched second. Used for what arrives at the station and for
    what its projects use up - both over the LONG window, because a resource that arrives once an hour looks like
    a steady stream if only the minutes around a delivery are averaged."""

    def __init__(self, minimum: float):
        self.minimum = minimum
        self.buckets: deque = deque()              # [minute, seconds watched, {resource: amount}]
        self.watched = 0.0
        self.totals: "dict[str, float]" = {}

    def add(self, now: float, seconds: float, amounts: dict) -> None:
        minute = int(now // FLOW_BUCKET_S)
        if not self.buckets or self.buckets[-1][0] != minute:
            self.buckets.append([minute, 0.0, {}])
        bucket = self.buckets[-1]
        bucket[1] += seconds
        self.watched += seconds
        for resource, amount in amounts.items():
            bucket[2][resource] = bucket[2].get(resource, 0.0) + amount
            self.totals[resource] = self.totals.get(resource, 0.0) + amount
        oldest = minute - int(FLOW_WINDOW_S // FLOW_BUCKET_S)
        while self.buckets and self.buckets[0][0] < oldest:
            _minute, old_seconds, old_amounts = self.buckets.popleft()
            self.watched = max(0.0, self.watched - old_seconds)
            for resource, amount in old_amounts.items():
                self.totals[resource] = self.totals.get(resource, 0.0) - amount

    def rate(self, resource: str) -> "float | None":
        """Units per watched second; None until `minimum` seconds have been watched."""
        if self.watched < self.minimum:
            return None
        return max(0.0, self.totals.get(resource, 0.0)) / self.watched


class Flow:
    """What ARRIVES at one station, read off its cargo stream (a cargo message about every second while docked).

    A resource's arrival is any rise of its stock between two messages. Production, deliveries and refining
    all show up that way, and a project's use of it (which happens at the bay's own tick) shows up as a fall,
    which is ignored - so this is a supply measure that does not depend on any project running. It is only
    ever a floor: where a rise and a fall land inside one gap they cancel. Only time actually watched counts
    (a gap in the stream is not time watched)."""

    def __init__(self):
        self.stock: "dict[str, float] | None" = None   # the latest cargo seen
        self.last_t: "float | None" = None
        self.arrivals = Tally(MIN_FLOW_S)

    def update(self, stock: dict, now: float) -> None:
        if self.stock is not None and self.last_t is not None and 0 < now - self.last_t <= MAX_INTERVAL_S:
            gains = {r: amount - self.stock.get(r, 0.0) for r, amount in stock.items()
                     if amount > self.stock.get(r, 0.0)}
            self.arrivals.add(now, now - self.last_t, gains)
        self.stock, self.last_t = dict(stock), now

    def rate(self, resource: str) -> "float | None":
        return self.arrivals.rate(resource)


class StationState:
    """The history of one station's bay and the estimates it gives. No I/O except log lines."""

    def __init__(self):
        self.prev_t: "float | None" = None
        self.prev: "dict[object, _Row]" = {}
        self.history: "dict[tuple, deque]" = {}        # (pid, role) -> deque of (dt, man-hours)
        self.supply: deque = deque(maxlen=WINDOW_INTERVALS)   # (dt, {resource: units consumed})
        self.first: "dict[object, tuple]" = {}         # pid -> (made_at, seconds, progress, ahead)
        self.flow = Flow()                             # what arrives at the station (from its cargo stream)
        self.used = Tally(MIN_USED_S)                  # what the projects used up, over the long window
        self.was_blocked: set = set()                  # projects that were blocked at the last update

    # -- reading one update ---------------------------------------------------------------------

    def update(self, queue, now: float, stock=None) -> "dict[object, Eta]":
        rows = self._rows(queue)
        if stock is None:
            stock = self.flow.stock                    # the latest cargo seen (None until one has arrived)
        self._classify(rows, stock)
        if self.prev_t is not None and 0 < now - self.prev_t <= MAX_INTERVAL_S:
            self._learn(rows, now - self.prev_t, now)
        self._departures(rows, now)
        self.prev_t, self.prev = now, {row.pid: row for row in rows}
        return self._estimate(rows, now, stock)

    @staticmethod
    def _rows(queue) -> "list[_Row]":
        raw = [r for r in queue if isinstance(r, dict) and r.get("project_id") is not None] \
            if isinstance(queue, (list, tuple)) else []
        order = sorted(range(len(raw)), key=lambda i: (_num(raw[i].get("queue_order"), i), i))
        rows = [_Row(pid=raw[i]["project_id"], name=str(raw[i].get("name") or raw[i].get("display_name") or "?"),
                     done=_num(raw[i].get("manhours_done")), total=_num(raw[i].get("manhours_total")),
                     paused=bool(raw[i].get("paused") or raw[i].get("is_paused")),
                     ratios=project_ratios(raw[i]), progress=_num(raw[i].get("progress")),
                     labels=resource_labels(raw[i]))
                for i in order]
        return rows

    def _classify(self, rows, stock) -> None:
        """Roles: paused projects are skipped; a project that cannot get a resource it needs is BLOCKED, and a
        leader that has not moved for its whole window is STARVED - both skipped too, they use nothing so they
        hold nothing back; the first of the rest leads, the others follow."""
        running = 0
        for row in rows:
            row.blocked = ""
            if row.paused:
                row.role = "paused"
                continue
            if row.remaining > 0:
                resource = self.blocked_on(row, stock, row.pid in self.was_blocked)
                if resource:
                    row.role, row.blocked = "blocked", row.label(resource)
                    continue
            if running == 0 and row.remaining > 0:
                before = self.prev.get(row.pid)
                if before is not None and before.role in ("leader", "starved") and row.done <= before.done \
                        and self.own_rate(row.pid, "leader") == 0:
                    shortage = self.worst_shortage(row, stock, require_known=True)
                    row.role, row.blocked = "starved", (row.label(shortage[0]) if shortage else "")
                    continue
            row.role = "leader" if running == 0 else "follower"
            running += 1
        self.was_blocked = {row.pid for row in rows if row.role == "blocked"}

    def _learn(self, rows, dt: float, now: float) -> None:
        consumed: "dict[str, float]" = {}
        active = False
        # The station's consumption can only be added up over an update in which nothing changed shape: the
        # same projects, none paused or resumed (what a project that paused used up in that gap is unknown).
        steady = set(self.prev) == {row.pid for row in rows}
        for row in rows:
            before = self.prev.get(row.pid)
            if before is None or before.paused != row.paused:
                steady = False
            if before is None or before.paused or row.paused:
                continue                # a pause is not slow work: it is left out of every speed
            moved = row.done - before.done
            if moved < 0:
                continue
            active = True
            for resource, per_hour in row.ratios.items():
                consumed[resource] = consumed.get(resource, 0.0) + moved * per_hour
            kind_before = "leader" if before.role == "starved" else before.role
            kind_now = "leader" if row.role == "starved" else row.role
            if kind_before == kind_now and kind_now in ("leader", "follower"):
                self.history.setdefault((row.pid, kind_now), deque(maxlen=WINDOW_INTERVALS)).append((dt, moved))
        if active and steady:
            self.supply.append((dt, consumed))
            self.used.add(now, dt, consumed)

    def _departures(self, rows, now: float) -> None:
        here = {row.pid for row in rows}
        for pid, before in self.prev.items():
            if pid in here:
                continue
            for role in ("leader", "follower"):
                self.history.pop((pid, role), None)
            first = self.first.pop(pid, None)
            if first is None:
                continue
            made_at, seconds, progress, ahead = first
            actual = now - made_at
            if before.progress >= FINISHED_PROGRESS:
                logger.info("construction_eta: %s (project %s) finished: first estimate ~%.0fs made at %.1f%% with "
                            "%d ahead, took %.0fs (%+.0f%%)", before.name, pid, seconds, progress * 100, ahead,
                            actual, (seconds / actual - 1) * 100 if actual > 0 else 0.0)
            else:
                logger.info("construction_eta: %s (project %s) left the queue at %.1f%% (cancelled?) after %.0fs "
                            "tracked", before.name, pid, before.progress * 100, actual)

    # -- the speeds -----------------------------------------------------------------------------

    def own_rate(self, pid, role: str) -> "float | None":
        window = self.history.get((pid, role))
        if window is None or len(window) < MIN_INTERVALS:
            return None
        seconds = sum(dt for dt, _ in window)
        return sum(moved for _, moved in window) / seconds if seconds > 0 else None

    def own_count(self, pid, role: str) -> int:
        return len(self.history.get((pid, role), ()))

    def own_speed(self, pid, role: str) -> "tuple[float | None, int]":
        """(man-hours per second, updates it rests on). A project that has just moved up to lead - because the
        ones ahead of it are blocked - has no history as the leader yet: it falls back on what it did as a
        follower (if that was progress; a follower that was merely starved says nothing about how fast it will go)."""
        rate = self.own_rate(pid, role)
        if rate is not None:
            return rate, self.own_count(pid, role)
        if role == "leader":
            alt = self.own_rate(pid, "follower")
            if alt is not None and alt > 0:
                return alt, self.own_count(pid, "follower")
        return None, self.own_count(pid, role)

    def supply_rate(self, resource: str) -> "float | None":
        """What the station supplies of a resource, units per second: the larger of what the projects used up and
        what arrived in the cargo, both over the long window (each is a floor on the truth, and a resource that
        arrives in deliveries hours apart needs the long view). "Nothing arrives" is only believed when both
        views have been watching long enough to agree; otherwise it is unknown (None)."""
        known = [v for v in (self.used.rate(resource), self.flow.rate(resource)) if v is not None]
        if not known:
            return None
        best = max(known)
        if best <= 0 and len(known) < 2:
            return None
        return best

    def projected_rate(self, row: _Row, stock) -> "tuple[float | None, bool]":
        """(man-hours per second this project would get from the station's supply, supply data exists): what the
        scarcest resource it still needs lets through. A resource with enough in stock for the rest of the
        project does not set the speed."""
        limits = []
        for resource, per_hour in row.ratios.items():
            if isinstance(stock, dict) and stock.get(resource, 0.0) >= per_hour * row.remaining:
                continue
            supply = self.supply_rate(resource)
            if supply is None:
                return None, False
            limits.append(supply / per_hour)
        return (min(limits), True) if limits else (None, True)

    def shortage_times(self, row: _Row, stock) -> "dict[str, float | None]":
        """For each resource the project still needs more of than the station holds: how long its supply would take
        to bring the difference (inf: nothing arrives, None: not known yet). Empty while the cargo is unknown."""
        if not isinstance(stock, dict):
            return {}
        out: "dict[str, float | None]" = {}
        for resource, per_hour in row.ratios.items():
            need, have = per_hour * row.remaining, stock.get(resource, 0.0)
            if have >= need:
                continue
            supply = self.supply_rate(resource)
            out[resource] = None if supply is None else (math.inf if supply <= 0 else (need - have) / supply)
        return out

    def worst_shortage(self, row: _Row, stock, require_known: bool = False) -> "tuple[str, float] | None":
        """The resource the project is short of that would take longest to arrive: (resource, seconds; inf = never).
        With require_known, nothing is named while any of its shortages is still unknown: the real culprit might
        be the one not measured yet."""
        times = self.shortage_times(row, stock)
        if require_known and any(seconds is None for seconds in times.values()):
            return None
        worst = None
        for resource, seconds in times.items():
            if seconds is not None and (worst is None or seconds > worst[1]):
                worst = (resource, seconds)
        return worst

    def blocked_on(self, row: _Row, stock, was_blocked: bool = False) -> "str | None":
        """The resource a project cannot get within BLOCK_HORIZON_S at the rate it arrives, if any. A project that
        was blocked stays so until it would take under half of that, so the label does not flicker."""
        worst = self.worst_shortage(row, stock)
        if worst is not None and worst[1] > BLOCK_HORIZON_S * (0.5 if was_blocked else 1.0):
            return worst[0]
        return None

    def floor(self, row: _Row, stock) -> "tuple[float, str | None]":
        """(the least time the project can take however fast it has been going, the resource that sets it): what
        it is short of has to arrive first."""
        worst = self.worst_shortage(row, stock)
        if worst is None or not math.isfinite(worst[1]):
            return 0.0, None
        return worst[1], worst[0]

    # -- the estimates --------------------------------------------------------------------------

    @staticmethod
    def _eta(seconds, resource_sets_it: bool, row: _Row, resource, ahead, parallel, now, rough) -> Eta:
        return Eta("eta", seconds, ahead, parallel, now, rough,
                   limited_by=row.label(resource) if resource_sets_it and resource else "")

    def _estimate(self, rows, now: float, stock) -> "dict[object, Eta]":
        results: "dict[object, Eta]" = {}
        cumulative: "float | None" = 0.0          # seconds until everything ahead is done (None: unknown)
        cumulative_rough = False
        unknown_kind = "estimating"
        blockers = 0     # projects ahead that really hold the ones behind them back (see "trickling" below)
        for row in rows:
            if row.role == "paused":
                results[row.pid] = Eta("paused", made_at=now)
                continue
            if row.role == "blocked":
                results[row.pid] = Eta("blocked", made_at=now, resource=row.blocked)
                continue
            if row.role == "starved":
                results[row.pid] = (Eta("blocked", made_at=now, resource=row.blocked) if row.blocked
                                    else Eta("starved", made_at=now))
                continue
            if row.remaining <= 0:
                results[row.pid] = Eta("done", made_at=now)
                continue
            floor, floor_resource = self.floor(row, stock)     # never sooner than what it is short of can arrive
            own, count = self.own_speed(row.pid, row.role)
            if row.role == "leader":
                rate = own if own is not None else self.projected_rate(row, stock)[0]
                rough = (count if own is not None else len(self.supply)) < ROUGH_BELOW
                if rate is None:
                    results[row.pid] = Eta("estimating", made_at=now)
                    cumulative, unknown_kind = None, "estimating"
                elif rate <= 0:
                    shortage = self.worst_shortage(row, stock, require_known=True)    # not moving: what is it waiting for?
                    results[row.pid] = (Eta("blocked", made_at=now, resource=row.label(shortage[0])) if shortage
                                        else Eta("starved", made_at=now))
                    cumulative, unknown_kind = None, "queued"
                else:
                    seconds = row.remaining / rate
                    trickling = floor > seconds * TRICKLE_RATIO     # what it waits for arrives much slower than it could work
                    results[row.pid] = self._eta(max(seconds, floor), trickling, row, floor_resource, blockers, False,
                                                 now, rough)
                    if not trickling:                # a trickling project uses little of what the others share
                        cumulative, cumulative_rough = seconds, rough
                        blockers += 1
            elif own is not None and own > 0:
                seconds = row.remaining / own
                results[row.pid] = self._eta(max(seconds, floor), floor > seconds * TRICKLE_RATIO, row, floor_resource, blockers, True,
                                             now, count < ROUGH_BELOW)
            elif cumulative is None:
                results[row.pid] = Eta(unknown_kind, made_at=now)
            else:
                rate, have_supply = self.projected_rate(row, stock)
                if rate is None or rate <= 0:
                    results[row.pid] = Eta("queued" if have_supply else "estimating", made_at=now)
                    cumulative, unknown_kind = None, ("queued" if have_supply else "estimating")
                else:
                    # (its floor can never exceed this: the rate already comes from the same supplies)
                    cumulative += row.remaining / rate
                    cumulative_rough = cumulative_rough or len(self.supply) < ROUGH_BELOW
                    results[row.pid] = self._eta(max(cumulative, floor), False, row, floor_resource, blockers, False,
                                                 now, cumulative_rough)
                    blockers += 1
            eta = results[row.pid]
            if eta.kind == "eta" and row.pid not in self.first:
                self.first[row.pid] = (now, eta.seconds, row.progress, eta.ahead)
        return results


# -- text ---------------------------------------------------------------------------------------

def format_duration(seconds: float) -> str:
    """"<15s", "40s", "1m 30s", "14m", "2h 05m", "3d 4h" - coarse on purpose: the estimate moves by that much."""
    seconds = max(0.0, float(seconds))
    if seconds < 15:
        return "<15s"
    step = 5 if seconds < 60 else 10
    total = int(round(seconds / step) * step)
    if total < 60:
        return f"{total}s"
    if total < 120:
        return f"1m {total - 60:02d}s"
    minutes = int(round(seconds / 60.0))
    if minutes < 90:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}h {minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def eta_text(eta: "Eta | None", now: float, paused: bool = False) -> str:
    """What to write next to a project; "" for nothing."""
    if paused or (eta is not None and eta.kind == "paused"):
        return "paused"
    if eta is None or eta.kind == "done":
        return ""
    if eta.kind == "estimating":
        return "estimating..."
    if eta.kind == "starved":
        return "waiting for resources"
    if eta.kind == "blocked":
        return f"waiting for {eta.resource}" if eta.resource else "waiting for resources"
    if eta.kind == "queued":
        return "queued"
    if now - eta.made_at > STALE_S:
        return ""
    left = max(0.0, eta.deadline - now)
    text = "~" + format_duration(left) + ("?" if eta.rough else "")
    notes = ([f"after {eta.ahead}"] if eta.ahead and not eta.parallel else []) + ([eta.limited_by] if eta.limited_by else [])
    return text + (f" ({', '.join(notes)})" if notes else "")


# -- the station registry and the hooks ---------------------------------------------------------

_states: "OrderedDict[object, StationState]" = OrderedDict()
_results: "dict[object, Eta]" = {}
_failed = False


def _enabled() -> bool:
    return not _failed and not os.environ.get(ENV_OFF)


def _state_for(station_id) -> StationState:
    state = _states.get(station_id)
    if state is None:
        state = _states[station_id] = StationState()
        while len(_states) > MAX_STATIONS:
            _states.popitem(last=False)
    else:
        _states.move_to_end(station_id)
    return state


def observe(station_id, queue, now: float, stock=None) -> "dict[object, Eta]":
    """Feed one PS_CONSTRUCTION_BAY update; returns (and publishes) the estimate of every project."""
    global _results
    results = _state_for(station_id).update(queue, now, stock)
    _results = {**_results, **results}
    return results


def observe_cargo(station_id, cargo, now: float) -> None:
    """Feed one PS_STATION_CARGO message: what arrives at the station, and what it holds now."""
    stock = stock_from_cargo(cargo)
    if stock is not None and cargo:                   # an empty list is "not loaded yet", not "nothing in stock"
        _state_for(station_id).flow.update(stock, now)


def eta_for(pid) -> "Eta | None":
    return _results.get(pid)


def _feed(window, data) -> None:
    if not _enabled() or not isinstance(data, dict):
        return
    try:
        cargo = getattr(window, "_ps_station_cargo", None)
        observe(data.get("station_id"), data.get("queue"), time.monotonic(),
                stock_from_cargo(cargo) if cargo else None)      # no cargo yet: the stream's own latest, if any
    except Exception:
        _switch_off("while reading a construction update")


def _feed_cargo(window, data) -> None:
    if not _enabled() or not isinstance(data, dict):
        return
    try:
        observe_cargo(data.get("station_id"), data.get("cargo"), time.monotonic())
    except Exception:
        _switch_off("while reading the station cargo")


def _switch_off(where: str) -> None:
    global _failed
    _failed = True
    logger.exception("construction_eta: switched off for this session after an error %s", where)


def _palette(render_module) -> dict:
    return {
        "text": getattr(render_module, "_PS_TEXT", (150, 190, 215)),
        "caption": getattr(render_module, "_PS_CAPTION", (120, 140, 170)),
        "paused": getattr(render_module, "_PS_ENERGY", (230, 170, 60)),
        "warn": getattr(render_module, "_PS_WARN", (230, 110, 90)),
    }


def _draw(window, ctx, palette: dict) -> None:
    """The estimate text, right-aligned against the left edge of each row's progress bar."""
    overlay = getattr(window, "_station_overlay", None)
    if (not overlay or overlay.get("kind") != "player_station"
            or getattr(window, "_ps_tab", None) != "Construction Bay"
            or getattr(window, "_ps_cb_cancel_confirm", None) is not None):
        return
    rects = getattr(window, "_ps_cb_proj_rects", None)
    if not rects:
        return
    screen = ctx.screen
    scale = window._s
    font = window._instrument_font(11)
    now = time.monotonic()
    for pid, rect, project in rects:
        paused = bool(project.get("paused"))
        text = eta_text(eta_for(pid), now, paused)
        if not text:
            continue
        # the game's own column maths (render_mixin, Construction Bay rows): number column, product column,
        # then the progress column whose RIGHT half is the bar - the left half is free for this text
        column_left = rect.x + scale(30) + scale(220) + scale(6)
        column_width = rect.width - (scale(30) + scale(220)) - scale(20)
        bar_left = column_left + (column_width - column_width // 2)
        # the product name is not clipped by the game, so a long one can reach into this space
        name = str(project.get("name", "Unknown")) + ("  [PAUSED]" if paused else "")
        name_right = rect.x + scale(30) + scale(6) + window._F(12).size(name)[0]
        room = bar_left - scale(8) - max(column_left, name_right + scale(8))
        colour = (palette["paused"] if paused else palette["warn"] if text.startswith("waiting")
                  else palette["text"] if text.startswith("~") else palette["caption"])
        label = window._cached_label(text, colour, font)
        if label.get_width() > room and " (" in text:
            label = window._cached_label(text.split(" (")[0], colour, font)          # drop the suffix first
        if label.get_width() > room:
            continue                                                             # no room: say nothing
        screen.blit(label, (bar_left - scale(8) - label.get_width(),
                            rect.y + (rect.height - label.get_height()) // 2))


def install(window_cls, render_module=None) -> bool:
    """Hook ``window_cls`` (Client.py's SolarSystemWindow). Idempotent; True if it hooked this call."""
    if window_cls.__dict__.get("_construction_eta_installed"):
        return False
    feed_original = window_cls.set_ps_construction_bay
    draw_original = window_cls._draw_station_overlay
    cargo_original = getattr(window_cls, "set_ps_station_cargo", None)
    palette = _palette(render_module)

    def set_ps_construction_bay(self, data, *args, **kwargs):
        result = feed_original(self, data, *args, **kwargs)
        _feed(self, data)
        return result

    def _draw_station_overlay(self, ctx, *args, **kwargs):
        result = draw_original(self, ctx, *args, **kwargs)
        if _enabled():
            try:
                _draw(self, ctx, palette)
            except Exception:
                _switch_off("while drawing")
        return result

    def set_ps_station_cargo(self, data, *args, **kwargs):
        result = cargo_original(self, data, *args, **kwargs)
        _feed_cargo(self, data)
        return result

    wrapped = [(set_ps_construction_bay, feed_original), (_draw_station_overlay, draw_original)]
    if cargo_original is not None:
        wrapped.append((set_ps_station_cargo, cargo_original))
        window_cls.set_ps_station_cargo = set_ps_station_cargo
    for wrapper, original in wrapped:
        wrapper.__wrapped__ = original
        wrapper.__doc__ = original.__doc__
    window_cls.set_ps_construction_bay = set_ps_construction_bay
    window_cls._draw_station_overlay = _draw_station_overlay
    window_cls._construction_eta_installed = True
    return True


def reset_for_tests() -> None:
    global _results, _failed
    _states.clear()
    _results = {}
    _failed = False
