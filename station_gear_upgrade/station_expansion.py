"""station_expansion.py - an ADD 50 GAMMA + 50 WORKERS button in the station prefab dialog: move Station Expander
Gammas and workers from your hold into the station and fit the expanders.

The button sits beside UPGRADE GEAR (station_gear_upgrade.py draws it, feeds it the clicks, the cargo pushes and the
equip answers; nothing is added to Client.py). Each gamma "adds 5000 hull space ... requires 1 worker to operate", so
the run is what you would do by hand in the Storage tab, in this order, each step waiting for the station's own cargo
push to show that it happened (or for PS_EQUIP_MODULE_RESULT to refuse the fit), and giving up after STEP_WAIT_S:

  1. PS_TRANSFER_TO_STATION   ``workers``, up to WORKER_COUNT, from your hold (the worker store grows)
  2. PS_TRANSFER_TO_STATION   ``item:station:station_expander_gamma``, up to EXPANDER_COUNT, from your hold
  3. PS_EQUIP_MODULE          fit up to EXPANDER_COUNT of the expanders that are in the station's storage; the server
                              takes only as many as there are free workers and answers once with how many it fitted

A hold with less than 50 gives what it has (the button says the numbers before you press it). Bound expanders cannot go
into a station and pinned workers are not moved. Expanders already sitting unfitted in the station's storage (left by an
earlier run that stopped) are fitted too, up to the same 50, so pressing the button again finishes the job. Nothing is
sent until you press the button, and a failure stops the run and says where everything is.

Kill switch: STAR_EMPIRE_NO_STATION_EXPANSION=1. Any error switches only this button off for the session.
"""

from __future__ import annotations

import logging
import os
import time

import station_gear_upgrade as sgu
from inventory_sections import inventory_stack_quantity

logger = logging.getLogger(__name__)

EXPANDER_TYPE = "station_expander_gamma"
EXPANDER_KEY = "item:station:" + EXPANDER_TYPE
EXPANDER_NAME = "Station Expander Gamma"
WORKER_KEY = "workers"
EXPANDER_COUNT = 50
WORKER_COUNT = 50
STEP_WAIT_S = 10.0
MAX_LINES = 4
TITLE = "EXPAND STATION"

STEPS = ("workers", "expanders", "equip")
STEP_TEXT = {
    "workers": "Moving workers in...",
    "expanders": "Moving expanders in...",
    "equip": "Fitting expanders...",
}
TIMEOUT_TEXT = {
    "workers": "the station did not take the workers (is its store full?)",
    "expanders": "the station did not take the expanders (is its storage full?)",
    "equip": "the station did not fit them (the Log tab has the server's reason)",
}

_failed = False


def _switch_off(where: str) -> None:
    global _failed
    if not _failed:
        logger.exception("station_expansion: %s failed; the expansion button is switched off for this session", where)
    _failed = True


def enabled() -> bool:
    return not _failed and os.environ.get("STAR_EMPIRE_NO_STATION_EXPANSION") != "1"


def reset_for_tests() -> None:
    global _failed
    _failed = False


def running(win) -> bool:
    return bool(win.__dict__.get("_sgx"))


def _say(win, text: str, warn: bool = False) -> None:
    sgu._say(win, "Station expansion: " + text, warn)


# -- what the hold and the station have -------------------------------------------------------------------------------

def hold_counts(inventory) -> dict:
    """What the hold has to give: ``expanders`` / ``workers`` that can move, ``bound`` expanders and ``pinned``
    workers that cannot."""
    counts = {"expanders": 0, "bound": 0, "workers": 0, "pinned": 0, "name": EXPANDER_NAME}
    for row in inventory or ():
        if not isinstance(row, dict):
            continue
        if row.get("item_class") == "cargo_item" and row.get("item_category") == "station" \
                and row.get("item_type") == EXPANDER_TYPE:
            if row.get("display_name"):
                counts["name"] = str(row["display_name"])
            counts["bound" if row.get("bound") else "expanders"] += inventory_stack_quantity(row)
        elif row.get("item_class") == "cargo" and (row.get("resource") or row.get("item_type")) == WORKER_KEY:
            counts["pinned" if row.get("pinned") else "workers"] += max(0, sgu._int(row.get("amount")))
    return counts


def station_workers(win) -> int:
    """Workers in the station's store: its ``workers`` row's ``cargo_size``, the head count the game's own WORKFORCE
    section adds up."""
    return sum(max(0, sgu._int(row.get("cargo_size", row.get("quantity", 0))))
               for row in (getattr(win, "_ps_station_cargo", None) or ())
               if isinstance(row, dict) and row.get("item_key") == WORKER_KEY)


def free_workers(win) -> int:
    return max(0, station_workers(win) - sgu._int(getattr(win, "_ps_workers_used", 0)))


def unfitted(win) -> int:
    """Expanders sitting in the station's storage that are not fitted."""
    return max(0, sgu._held(win, EXPANDER_TYPE) - sgu._fitted(win, EXPANDER_TYPE))


def build_plan(inventory, stored: int, free: int) -> dict:
    """What a press would do, given the hold, the unfitted expanders already in the station (``stored``) and the
    station's free workers (``free``): ``expanders`` and ``workers`` to move, ``fit`` expanders to fit and ``fits``, how
    many of those the workers will cover, plus the hold's numbers and the expander name."""
    hold = hold_counts(inventory)
    expanders = min(EXPANDER_COUNT, hold["expanders"])
    workers = min(WORKER_COUNT, hold["workers"])
    fit = min(EXPANDER_COUNT, stored + expanders)
    return {"expanders": expanders, "workers": workers, "fit": fit, "fits": min(fit, free + workers),
            "hold_expanders": hold["expanders"], "hold_workers": hold["workers"],
            "bound": hold["bound"], "pinned": hold["pinned"], "name": hold["name"]}


def evaluate(win):
    """``(problem, plan)``: ``problem`` is why nothing can be started (a sentence), else None."""
    problem, inventory = sgu.dock_problem(win, "A jump gate has no station modules.")
    if problem is not None:
        return problem, None
    plan = build_plan(inventory, unfitted(win), free_workers(win))
    if plan["fit"] <= 0:
        if plan["bound"]:
            return f"Your {plan['name']} is bound, so it cannot go into a station.", plan
        return f"No {plan['name']} in your hold.", plan
    return None, plan


# -- the run ----------------------------------------------------------------------------------------------------------

def start(win) -> bool:
    """The button was pressed: check, then begin. True if a run started."""
    if running(win) or sgu.running(win):
        return False
    problem, plan = evaluate(win)
    if problem is not None:
        _say(win, problem, warn=True)
        return False
    steps = [step for step in STEPS if step != "workers" or plan["workers"]]
    steps = [step for step in steps if step != "expanders" or plan["expanders"]]
    job = {"station_id": sgu._station_id(win), "plan": plan, "steps": steps, "step": "", "t": time.monotonic(),
           "base": 0, "want": 0, "got_workers": 0, "got_expanders": 0, "granted": None, "reason": "", "fit_base": 0,
           "asked": 0}
    win._sgx = job
    text = []
    if plan["expanders"]:
        text.append(f"{plan['expanders']} {plan['name']}")
    if plan["workers"]:
        text.append(f"{plan['workers']} workers")
    if text:
        _say(win, f"adding {' and '.join(text)}, then fitting {plan['fit']} expanders.")
    else:
        _say(win, f"fitting the {plan['fit']} expanders already in the station.")
    _begin(win, job, steps[0])
    return True


def _begin(win, job, step) -> None:
    """Send the command of ``step`` and start waiting for the station to show it."""
    plan = job["plan"]
    job.update(step=step, t=time.monotonic())
    if step == "workers":
        job.update(base=station_workers(win), want=plan["workers"])
        win._send_json(sgu.DEPOSIT_COMMAND, {"station_id": job["station_id"], "item_key": WORKER_KEY,
                                              "amount": plan["workers"]})
    elif step == "expanders":
        job.update(base=sgu._held(win, EXPANDER_TYPE), want=plan["expanders"])
        win._send_json(sgu.DEPOSIT_COMMAND, {"station_id": job["station_id"], "item_key": EXPANDER_KEY,
                                              "amount": plan["expanders"]})
    else:
        _send_equip(win, job)


def _send_equip(win, job) -> None:
    asked = min(EXPANDER_COUNT, unfitted(win))
    row_id = sgu._row_id(win, EXPANDER_TYPE)
    if asked <= 0 or row_id is None:
        _fail(win, job, "the station has no expanders to fit")
        return
    job.update(t=time.monotonic(), asked=asked, fit_base=sgu._fitted(win, EXPANDER_TYPE), granted=None, reason="")
    win._send_equip_json(sgu.EQUIP_COMMAND, {"station_id": job["station_id"], "row_id": row_id,
                                              "module_type": EXPANDER_TYPE, "count": asked})


def _next_step(win, job) -> None:
    """After a deposit: the next step (the fit always comes last, so there is one)."""
    steps = job["steps"]
    _begin(win, job, steps[steps.index(job["step"]) + 1])


def _finish(win, job) -> None:
    plan = job["plan"]
    fitted = sgu._fitted(win, EXPANDER_TYPE) - job["fit_base"]
    win._sgx = None
    parts = []
    if job["got_workers"]:
        parts.append(f"{job['got_workers']} workers added")
    if job["got_expanders"]:
        parts.append(f"{job['got_expanders']} {plan['name']} added")
    parts.append(f"{fitted} fitted")
    short = fitted < job["asked"] or job["got_workers"] < plan["workers"] or job["got_expanders"] < plan["expanders"]
    text = "done - " + ", ".join(parts) + "."
    if fitted < job["asked"]:
        text += f" {job['asked'] - fitted} expanders are in the station's storage, not fitted"
        text += f" ({job['reason'].rstrip('. ')})." if job["reason"] else " (not enough free workers?)."
    _say(win, text, warn=short)


def _fail(win, job, why: str) -> None:
    """Stop the run and say what was done and where everything is."""
    step = job["step"]
    moved = []
    if job["got_workers"]:
        moved.append(f"{job['got_workers']} workers")
    if job["got_expanders"]:
        moved.append(f"{job['got_expanders']} {job['plan']['name']}")
    if step == "equip":
        advice = ("The expanders and workers are in the station; fit the expanders in its Storage tab, "
                  "or press the button again.")
    elif moved:
        advice = f"{' and '.join(moved)} are in the station; press the button again to go on."
    else:
        advice = "Nothing was moved."
    win._sgx = None
    _say(win, f"stopped at the {step} step: {why.rstrip('. ')}. {advice}", warn=True)


def advance(win, now: "float | None" = None) -> None:
    """Move the run on if the station's state shows the current step happened; give up on a step that took too long."""
    job = win.__dict__.get("_sgx")
    if not job:
        return
    step = job["step"]
    now = time.monotonic() if now is None else now
    if not sgu._docked_at(win, job["station_id"]):
        _fail(win, job, "you are no longer docked at the station")
        return
    timed_out = now - job["t"] >= STEP_WAIT_S
    if step in ("workers", "expanders"):
        have = station_workers(win) if step == "workers" else sgu._held(win, EXPANDER_TYPE)
        got = have - job["base"]
        if got >= job["want"] or (timed_out and got > 0):          # a partial deposit is kept after the wait
            job["got_" + step] = min(got, job["want"])
            _next_step(win, job)
        elif timed_out:
            _fail(win, job, TIMEOUT_TEXT[step])
        return
    if step == "equip":
        fitted = sgu._fitted(win, EXPANDER_TYPE) - job["fit_base"]
        target = job["asked"] if job["granted"] is None else min(job["granted"], job["asked"])
        if fitted >= max(1, target) or (timed_out and fitted > 0):
            _finish(win, job)
        elif timed_out:
            _fail(win, job, TIMEOUT_TEXT[step])


def awaiting_equip(win) -> bool:
    job = win.__dict__.get("_sgx") if win is not None else None
    return bool(job) and job["step"] == "equip"


def on_equip_result(win, data) -> None:
    """PS_EQUIP_MODULE_RESULT while the fit is awaited: a refusal ends the run with the server's reason; an answer
    that fitted some says how many, so the run is finished when the station's cargo shows them."""
    job = win.__dict__.get("_sgx")
    if not job or job["step"] != "equip" or not isinstance(data, dict):
        return
    module = data.get("module_type")
    if module and module != EXPANDER_TYPE:
        return
    if not data.get("ok"):
        _fail(win, job, str(data.get("reason") or "the server refused to fit them"))
        return
    count = data.get("count")
    if count is not None:
        count = sgu._int(count, job["asked"])
        if count <= 0:
            _fail(win, job, str(data.get("reason") or "the server fitted none of them"))
            return
        job["granted"] = count
    job["reason"] = str(data.get("reason") or "")
    advance(win)


def stop_after_error(win) -> None:
    """The button just switched itself off: do not leave a run half-way without saying so."""
    job = win.__dict__.get("_sgx") if win is not None else None
    if job:
        win._sgx = None
        _say(win, "the run stopped after an error. Check the station's Storage tab for where the expanders and "
                  "workers are.", warn=True)


# -- the dialog -------------------------------------------------------------------------------------------------------

def panel_state(win) -> dict:
    """What the dialog column shows: ``label``, ``enabled`` and ``lines`` (kind, text); kind is row, dim or warn."""
    job = win.__dict__.get("_sgx")
    if job:
        plan = job["plan"]
        number = job["steps"].index(job["step"]) + 1 if job["step"] in job["steps"] else len(job["steps"])
        return {"label": f"ADDING...  {number} / {len(job['steps'])}", "enabled": False,
                "lines": [("row", f"{plan['fit']} {plan['name']}"), ("dim", STEP_TEXT.get(job["step"], ""))]}
    if sgu.running(win):
        return {"label": "PLEASE WAIT", "enabled": False, "lines": [("dim", "The gear upgrade is running.")]}
    problem, plan = evaluate(win)
    if problem is not None:
        return {"label": "NOTHING TO ADD", "enabled": False, "lines": [("dim", problem)]}
    rows = []
    if plan["expanders"]:
        rows.append(("row", f"{plan['expanders']} {plan['name']}"))
    if plan["workers"]:
        rows.append(("row", f"{plan['workers']} Workers"))
    if plan["fit"] > plan["expanders"]:
        rows.append(("dim", f"+ {plan['fit'] - plan['expanders']} in storage, to fit too"))
    warns = []                      # most important first: the label already shows a shortfall in the hold
    if plan["fits"] < plan["fit"]:
        warns.append(("warn", f"Workers for only {plan['fits']} of {plan['fit']}."))
    if plan["bound"]:
        warns.append(("warn", f"{plan['bound']} bound stay in your hold."))
    if plan["pinned"]:
        warns.append(("warn", f"{plan['pinned']} pinned stay in your hold."))
    if plan["expanders"] < EXPANDER_COUNT:
        warns.append(("warn", f"Hold has only {plan['hold_expanders']} expanders."
                      if plan["hold_expanders"] else "No expanders in your hold."))
    if plan["workers"] < WORKER_COUNT:
        warns.append(("warn", f"Hold has only {plan['hold_workers']} workers."
                      if plan["hold_workers"] else "No workers in your hold."))
    warns = warns[:2]
    lines = rows[:MAX_LINES - len(warns)] + warns
    return {"label": f"ADD {plan['expanders']} GAMMA + {plan['workers']} WORKERS", "enabled": True, "lines": lines}


def draw_section(win, ctx, area) -> None:
    """Draw the column into ``area`` (the right part of the gear section's band), and keep the button's rect in
    ``dialog["sxp_rect"]`` for the click."""
    dialog = win._ps_prefab_dialog
    if dialog is not None:
        dialog["sxp_rect"] = None
    if not sgu._dialog_is_plain(dialog) or area is None:
        return
    sgu.draw_panel(win, ctx, area, TITLE, "", panel_state(win), dialog, "sxp_rect")
