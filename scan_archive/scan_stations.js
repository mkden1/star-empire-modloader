// STATIONS tab of the scan archive: every station you have docked at, as of its last visit, with what the
// bots do with its production. Data: STATIONS (station_overview.station_overview()). Runs in the page after
// the rest of the script; touches only #stationsView.
(function () {
  "use strict";
  var DATA = (typeof STATIONS !== "undefined" && STATIONS) ? STATIONS : {stations: [], systems: [], resources: []};
  var root = document.getElementById("stationsView");
  if (!root) { return; }
  var NOW = DATA.generated_at || (Date.now() / 1000);
  var STORE_KEY = "se_stations_v1";
  var state = {text: "", system: "", status: "all", resource: "", gear: "", sort: "issues", collapsed: {}, open: null};

  try {
    var saved = JSON.parse(localStorage.getItem(STORE_KEY) || "null");
    if (saved && typeof saved === "object") {
      ["text", "system", "status", "resource", "gear", "sort"].forEach(function (k) { if (typeof saved[k] === "string") { state[k] = saved[k]; } });
      if (saved.collapsed && typeof saved.collapsed === "object") { state.collapsed = saved.collapsed; }
    }
  } catch (e) { /* storage can be unavailable: the tab works without it */ }
  function persist() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({text: state.text, system: state.system, status: state.status,
        resource: state.resource, gear: state.gear, sort: state.sort, collapsed: state.collapsed}));
    } catch (e) { /* ignore */ }
  }

  function esc(v) {
    return String(v === null || v === undefined ? "" : v).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"}[c];
    });
  }
  function num(v) { return (v === null || v === undefined || isNaN(v)) ? "–" : Math.round(v).toLocaleString("en-US"); }
  function compact(v) {
    if (v === null || v === undefined || isNaN(v)) { return "–"; }
    var a = Math.abs(v);
    if (a >= 1e9) { return (v / 1e9).toFixed(a >= 1e10 ? 0 : 1) + "B"; }
    if (a >= 1e6) { return (v / 1e6).toFixed(a >= 1e7 ? 0 : 1) + "M"; }
    if (a >= 1e4) { return Math.round(v / 1e3) + "k"; }
    return Math.round(v).toLocaleString("en-US");
  }
  function pretty(key) { return String(key).split(":").pop().replace(/_/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); }); }
  function policy(p) {
    if (!p) { return "–"; }
    if (p === "public") { return "Public"; }
    if (p === "owner") { return "Owner"; }
    return pretty(p) + "+";
  }
  function ageText(days) {
    if (days === null || days === undefined) { return "never"; }
    if (days < 1 / 24) { return "just now"; }
    if (days < 1) { return Math.round(days * 24) + " h ago"; }
    return (days < 10 ? days.toFixed(1) : Math.round(days)) + " d ago";
  }
  function ageClass(s) {
    if (s.source !== "profile") { return "none"; }
    if (s.age_days >= 14) { return "bad"; }
    if (s.age_days >= 3) { return "warn"; }
    return "ok";
  }
  function bar(pct, cls) {
    if (pct === null || pct === undefined) { return "<span class=\"dim\">–</span>"; }
    var w = Math.max(0, Math.min(100, pct));
    return "<span class=\"sbar " + (cls || "") + "\"><i style=\"width:" + w + "%\"></i></span><span class=\"sbar-n\">" + Math.round(pct) + "%</span>";
  }
  function duration(seconds) {
    if (seconds === null || seconds === undefined || isNaN(seconds)) { return "–"; }
    var s = Math.round(seconds);
    if (s < 60) { return s + "s"; }
    if (s < 3600) { return Math.floor(s / 60) + "m" + (s % 60 < 10 ? "0" : "") + (s % 60) + "s"; }
    return Math.floor(s / 3600) + "h" + (Math.floor(s % 3600 / 60) < 10 ? "0" : "") + Math.floor(s % 3600 / 60) + "m";
  }
  function windowText(w) {
    if (!w) { return ""; }
    var from = new Date(w.start * 1000), to = new Date(w.end * 1000);
    return Math.round(w.hours) + " h, " + from.toLocaleString([], {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"}) +
      " to " + to.toLocaleString([], {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"});
  }
  function pctClass(p) { return p === null || p === undefined ? "" : (p >= 90 ? "ok" : (p >= 50 ? "warn" : "bad")); }
  function worst(issues) {
    var bad = 0, warn = 0;
    issues.forEach(function (i) { if (i.severity === "bad") { bad++; } else if (i.severity === "warn") { warn++; } });
    return {bad: bad, warn: warn};
  }
  function attention(s) { var w = worst(s.issues); return w.bad + w.warn > 0; }
  function slotsOf(s) { return s.slots || {status: "no-snapshot"}; }
  var SLOT_NOTE = {"no-snapshot": "no modules known yet: dock once to capture them", "no-body": "which planet this is on is unknown",
    unscanned: "the planet has no scan", "no-slots": "the planet has no extractor slots", unattached: "not built on a planet"};
  function slotCell(s) {
    var k = slotsOf(s);
    if (k.status !== "ok" && k.status !== "short") { return "<span class=\"dim\" title=\"" + esc(SLOT_NOTE[k.status] || "") + "\">–</span>"; }
    var text = num(k.fitted_total) + "/" + num(k.slots_total) + (k.from_scan ? "*" : "");
    return "<span class=\"st-badge " + (k.status === "ok" ? "ok" : (k.missing_total / k.slots_total >= 0.5 && !k.from_scan ? "bad" : "warn")) +
      "\" title=\"" + esc(k.status === "ok" ? "Every extractor slot is filled" : num(k.missing_total) + " extractor slots empty") +
      (k.from_scan ? " (from an older scan: dock to confirm)" : "") + "\">" + text + "</span>";
  }

  // Marked station gear (Mk I-III shield, energy, weapons, sensor ...): what is FITTED, from the newest setup scan.
  function gearOf(s) { return s.gear || {known: false}; }
  function roman(n) {
    var out = "";
    [[10, "X"], [9, "IX"], [5, "V"], [4, "IV"], [1, "I"]].forEach(function (pair) { while (n >= pair[0]) { out += pair[1]; n -= pair[0]; } });
    return out;
  }
  function markText(n) { return "Mk " + (roman(n) || n); }
  function hasMark(g, mark) { return !!g.known && (g.marks || []).some(function (m) { return m.mark === mark; }); }
  function gearCell(s) {
    var g = gearOf(s);
    if (!g.known) { return "<span class=\"dim\" title=\"No setup scan of this station yet: dock at it once\">–</span>"; }
    if (!g.marks.length) { return "<span class=\"dim\" title=\"No Mk I-III station gear fitted\">none</span>"; }
    return g.marks.map(function (m) {
      var here = g.fitted.filter(function (f) { return f.mark === m.mark; });
      var behind = here.some(function (f) { return f.behind; });
      return "<span class=\"st-badge " + (behind ? "warn" : "ok") + "\" title=\"" + esc(here.map(function (f) { return f.label; }).join(", ")) + "\">" +
        markText(m.mark) + " ×" + num(m.count) + "</span>";
    }).join("");
  }
  function gearBlock(s) {
    var g = gearOf(s);
    if (!g.known) { return "<div class=\"dim\">No setup scan of this station yet: dock at it once.</div>"; }
    if (!g.fitted.length) { return "<div class=\"dim\">No Mk I-III station gear fitted.</div>"; }
    return g.fitted.map(function (m) {
      return "<div class=\"kv " + (m.behind ? "warn" : "") + "\"><span>" + esc(m.label) + "</span><b>" + markText(m.mark) +
        (m.count > 1 ? " ×" + num(m.count) : "") + (m.behind ? " · " + markText(m.top) + " exists" : "") + "</b></div>";
    }).join("") + "<div class=\"dim\">From the station's newest setup scan" +
      (g.age_days === null || g.age_days === undefined ? "" : " (" + esc(ageText(g.age_days)) + ")") + "; spares in storage are not counted.</div>";
  }

  var STATUS_TEXT = {ok: "OK", partial: "PARTIAL", low: "LOW", idle: "IDLE", "no-route": "NO ROUTE", unknown: "UNKNOWN",
    routed: "ROUTED", stock: "STOCK", used: "USED ON SITE"};
  var STATUS_CLASS = {ok: "ok", partial: "warn", low: "warn", idle: "warn", "no-route": "bad", unknown: "none",
    routed: "none", stock: "none", used: "none"};

  // ---- filtering and ordering ----
  function matches(s) {
    if (state.system && (s.system || "Unknown") !== state.system) { return false; }
    if (state.resource && !s.resources.some(function (r) { return r.key === state.resource && r.production_per_day > 0; })) { return false; }
    if (state.status === "attention" && !attention(s)) { return false; }
    if (state.status === "bad" && worst(s.issues).bad === 0) { return false; }
    if (state.status === "no-snapshot" && s.source === "profile") { return false; }
    if (state.status === "stale" && !(s.source === "profile" && s.age_days >= 3)) { return false; }
    if (state.status === "snapshot" && s.source !== "profile") { return false; }
    if (state.status === "short" && slotsOf(s).status !== "short") { return false; }
    if (state.gear) {
      var g = gearOf(s);
      if (state.gear === "unknown") { if (g.known) { return false; } }
      else if (!g.known) { return false; }
      else if (state.gear === "mk2" && !hasMark(g, 2)) { return false; }
      else if (state.gear === "mk1" && !hasMark(g, 1)) { return false; }
      else if (state.gear === "behind" && !g.behind) { return false; }
    }
    if (state.text) {
      var hay = [s.name, s.id, s.owner, s.system, s.motd].concat(s.modules.map(function (m) { return m.name; }))
        .concat(s.resources.map(function (r) { return r.label; })).join(" ").toLowerCase();
      var words = state.text.toLowerCase().split(/\s+/).filter(Boolean);
      for (var i = 0; i < words.length; i++) { if (hay.indexOf(words[i]) < 0) { return false; } }
    }
    return true;
  }
  var SORTS = {
    issues: function (a, b) { var x = worst(a.issues), y = worst(b.issues); return (y.bad - x.bad) || (y.warn - x.warn) || cmpName(a, b); },
    name: cmpName,
    age: function (a, b) { return (b.age_days === null ? 1e9 : b.age_days) - (a.age_days === null ? 1e9 : a.age_days) || cmpName(a, b); },
    coverage: function (a, b) { return (a.coverage_pct === null ? 101 : a.coverage_pct) - (b.coverage_pct === null ? 101 : b.coverage_pct) || cmpName(a, b); },
    hull: function (a, b) { return (b.hull_fill_pct || 0) - (a.hull_fill_pct || 0) || cmpName(a, b); }
  };
  function cmpName(a, b) { return a.name.localeCompare(b.name, undefined, {numeric: true}); }

  // ---- the page ----
  var systems = DATA.systems.map(function (s) { return s.name; });
  root.innerHTML =
    "<div class=\"st-controls\">" +
    "<input id=\"stSearch\" type=\"text\" placeholder=\"Search station, owner, module, resource...\">" +
    "<select id=\"stSystem\"><option value=\"\">All systems</option>" +
    systems.map(function (n) { return "<option value=\"" + esc(n) + "\">" + esc(n) + "</option>"; }).join("") + "</select>" +
    "<select id=\"stStatus\">" +
    "<option value=\"all\">Every station</option><option value=\"attention\">Needs attention</option>" +
    "<option value=\"bad\">Problems only</option><option value=\"stale\">Snapshot 3+ days old</option>" +
    "<option value=\"short\">Extractor slots unfilled</option>" +
    "<option value=\"no-snapshot\">No snapshot yet</option><option value=\"snapshot\">Has a snapshot</option></select>" +
    "<select id=\"stResource\"><option value=\"\">Any resource</option>" +
    DATA.resources.map(function (k) { return "<option value=\"" + esc(k) + "\">" + esc(pretty(k)) + "</option>"; }).join("") + "</select>" +
    "<select id=\"stGear\" title=\"Which Mk I-III station gear is fitted\"><option value=\"\">Any gear</option>" +
    "<option value=\"mk2\">Mk II gear fitted</option><option value=\"mk1\">Mk I gear fitted</option>" +
    "<option value=\"behind\">Gear behind the newest mark</option><option value=\"unknown\">Gear not known yet</option></select>" +
    "<select id=\"stSort\"><option value=\"issues\">Most problems first</option><option value=\"name\">Name</option>" +
    "<option value=\"age\">Oldest snapshot first</option><option value=\"coverage\">Least covered first</option>" +
    "<option value=\"hull\">Fullest hull first</option></select>" +
    "<button id=\"stToggle\" class=\"st-btn\" title=\"Collapse or expand every system\">Collapse all</button>" +
    "</div>" +
    "<div class=\"st-tiles\" id=\"stTiles\"></div>" +
    "<div class=\"st-note\">A station's setup is the snapshot taken when you last left it; production, what leaves per day and " +
    "coverage come from the station logs, scans and the bots' current orders. Stations without a snapshot show scan data only. " +
    "What leaves is shown two ways: <b>Took/day</b> is what bots really took in the newest stretch of the station's log, " +
    "<b>If non-stop</b> is their loop time and load extrapolated to 24 h - well above the first for a bot that is not logged in all day. " +
    "Coverage and status follow Took/day. <b>Gear</b> is the Mk I-III station gear a station has fitted, from its newest setup scan " +
    "(rewritten whenever the fitted modules change); spare gear in storage does not count.</div>" +
    "<div id=\"stBody\"></div>";
  var el = {search: root.querySelector("#stSearch"), system: root.querySelector("#stSystem"),
    status: root.querySelector("#stStatus"), resource: root.querySelector("#stResource"), gear: root.querySelector("#stGear"),
    sort: root.querySelector("#stSort"),
    toggle: root.querySelector("#stToggle"), tiles: root.querySelector("#stTiles"), body: root.querySelector("#stBody")};
  el.search.value = state.text; el.system.value = state.system; el.status.value = state.status;
  el.resource.value = state.resource; el.gear.value = state.gear; el.sort.value = state.sort;
  if (el.system.value !== state.system) { state.system = ""; }
  if (el.resource.value !== state.resource) { state.resource = ""; }
  if (el.gear.value !== state.gear) { state.gear = ""; }

  function tile(label, value, cls, hint) {
    return "<div class=\"st-tile " + (cls || "") + "\" title=\"" + esc(hint || "") + "\"><div class=\"v\">" + esc(value) +
      "</div><div class=\"l\">" + esc(label) + "</div></div>";
  }
  function renderTiles(list) {
    var snap = list.filter(function (s) { return s.source === "profile"; }).length;
    var needs = list.filter(attention).length;
    var stale = list.filter(function (s) { return s.source === "profile" && s.age_days >= 3; }).length;
    var prod = 0, covered = 0;
    list.forEach(function (s) {
      s.resources.forEach(function (r) {
        if (r.status === "used" || r.status === "unknown" || r.status === "routed") { return; }
        prod += r.production_per_day;
        covered += r.production_per_day * Math.min(100, r.pct_leaves || 0) / 100;
      });
    });
    var cov = prod > 0 ? (100 * covered / prod) : null;
    var short = list.filter(function (s) { return slotsOf(s).status === "short"; }).length;
    var mk2 = list.filter(function (s) { return hasMark(gearOf(s), 2); }).length;
    var noGear = list.filter(function (s) { return !gearOf(s).known; }).length;
    var mk2Hint = "Stations with Mk II station gear fitted (from each station's newest setup scan; spares in storage do not count)" +
      (noGear ? ". " + noGear + " station" + (noGear > 1 ? "s have" : " has") + " no gear data yet." : ".");
    el.tiles.innerHTML = tile("stations", list.length, "") + tile("with a snapshot", snap + " / " + list.length, snap === list.length ? "ok" : "") +
      tile("need attention", needs, needs ? "warn" : "ok") + tile("slots unfilled", short, short ? "warn" : "ok",
        "Stations with fewer extractors than the planet has slots for") + tile("on Mk II gear", mk2, mk2 ? "warn" : "ok", mk2Hint) + tile("snapshots 3+ d old", stale, stale ? "warn" : "ok") +
      tile("production covered", cov === null ? "–" : Math.round(cov) + "%", pctClass(cov),
        "Units a day that leave the station by bot or shuttle (what bots really took in the newest log window), of everything the extractors make (bulk resources dominate)");
  }

  function issueBadges(s) {
    var w = worst(s.issues), out = "";
    if (w.bad) { out += "<span class=\"st-badge bad\">" + w.bad + " problem" + (w.bad > 1 ? "s" : "") + "</span>"; }
    if (w.warn) { out += "<span class=\"st-badge warn\">" + w.warn + " warning" + (w.warn > 1 ? "s" : "") + "</span>"; }
    return out || (s.source === "profile" ? "<span class=\"st-badge ok\">OK</span>" : "<span class=\"dim\">–</span>");
  }

  function stationRow(s) {
    var open = state.open === s.id;
    var snap = s.source === "profile" ? "<span class=\"st-badge " + ageClass(s) + "\">" + esc(ageText(s.age_days)) + "</span>"
      : "<span class=\"st-badge none\">no snapshot</span>";
    return "<tr class=\"st-row" + (open ? " open" : "") + "\" data-id=\"" + esc(s.id) + "\">" +
      "<td class=\"name\">" + (open ? "▾ " : "▸ ") + esc(s.name) + "<div class=\"sub\">#" + esc(s.id) +
      (s.owner ? " · " + esc(s.owner) : "") + "</div></td>" +
      "<td>" + snap + "</td>" +
      "<td>" + esc(policy(s.docking)) + "</td><td>" + esc(policy(s.management)) + "</td>" +
      "<td class=\"num\">" + (s.source === "profile" ? num(s.extractors) : "–") + "</td>" +
      "<td class=\"num\">" + (s.source === "profile" ? num(s.workers) : "–") + "</td>" +
      "<td>" + slotCell(s) + "</td>" +
      "<td>" + gearCell(s) + "</td>" +
      "<td>" + bar(s.hull_fill_pct, s.hull_fill_pct >= 90 ? "bad" : (s.hull_fill_pct >= 70 ? "warn" : "")) + "</td>" +
      "<td>" + bar(s.coverage_pct, pctClass(s.coverage_pct)) + "</td>" +
      "<td>" + issueBadges(s) + "</td></tr>" + (open ? "<tr class=\"st-detail-row\"><td colspan=\"11\">" + detail(s) + "</td></tr>" : "");
  }

  function leavesTitle(r) {
    if (r.production_per_day <= 0) { return ""; }
    var parts = [];
    if (r.pct_observed !== null && r.pct_observed !== undefined) { parts.push(Math.round(r.pct_observed) + "% left in the newest log window"); }
    if (r.pct_satisfied !== null && r.pct_satisfied !== undefined) { parts.push(Math.round(r.pct_satisfied) + "% if the bots ran non-stop"); }
    return parts.join(" · ");
  }

  function botsTable(s) {
    if (!s.bots || !s.bots.length) {
      return "<div class=\"dim\">" + (s.log_rows ? "No bot took or left anything in this station's log." : "No station log captured yet (open its Logs tab once).") + "</div>";
    }
    var rows = "";
    s.bots.forEach(function (b) {
      // not at the station at all in the newest stretch of its log: retired, moved on, or its owner was offline
      var absent = !!s.log_window && !b.window_docks;
      var mark = b.in_fleet === true ? "<span class=\"st-badge ok\" title=\"A bot in your own fleet scan\">yours</span>" :
        (b.mine ? "<span class=\"st-badge none\" title=\"One of your accounts, but this bot is not in its current fleet scan: retired, sold or renamed\">yours · not in fleet now</span>" :
          (b.in_fleet === false ? "<span class=\"st-badge none\" title=\"Not one of your accounts (none of its bots is in your fleet scan)\">other</span>" : ""));
      b.rows.forEach(function (r, i) {
        rows += "<tr" + (i || absent ? " class=\"dim\"" : "") + "><td>" + (i ? "" : esc(b.nickname)) + "</td><td>" + (i ? "" : esc(b.account) + " " + mark) + "</td>" +
          "<td>" + esc(r.label) + "</td>" +
          "<td>" + (i ? "" : (b.last_seen ? esc(ageText((NOW - b.last_seen) / 86400)) : "–")) + "</td>" +
          "<td class=\"num\">" + (i ? "" : duration(b.loop_seconds)) + "</td>" +
          "<td class=\"num\">" + (r.per_dock === null ? "–" : compact(r.per_dock)) + "</td>" +
          "<td class=\"num\">" + (absent ? "<span title=\"Not at this station in the newest stretch of its log\">–</span>" :
            (r.observed_per_day === null || r.observed_per_day === undefined ? "–" : compact(r.observed_per_day))) + "</td>" +
          "<td class=\"num dim\">" + (r.running_per_day === null ? "–" : compact(r.running_per_day)) + "</td></tr>";
      });
    });
    return "<table class=\"st-res\"><thead><tr><th>Bot</th><th>Account</th><th>Resource</th>" +
      "<th title=\"When this bot last docked here\">Last docked</th>" +
      "<th class=\"num\" title=\"Median gap between this bot's docks here: its loop time\">Loop</th>" +
      "<th class=\"num\" title=\"Units moved per dock, on average\">Per dock</th>" +
      "<th class=\"num\" title=\"Units a day it really moved, over the newest stretch of this station's log" + (s.log_window ? " (" + esc(windowText(s.log_window)) + ")" : "") + "\">Took/day</th>" +
      "<th class=\"num\" title=\"Units a day if it ran non-stop at this loop time\">If non-stop</th></tr></thead><tbody>" + rows + "</tbody></table>" +
      "<div class=\"dim\">Worked out from the station log, so it covers any owner's bots. Took/day is a sample of " +
      (s.log_window ? esc(windowText(s.log_window)) : "the log") + ", not a long-run average.</div>";
  }

  function kv(label, value) { return "<div class=\"kv\"><span>" + esc(label) + "</span><b>" + value + "</b></div>"; }

  function detail(s) {
    var setup = "";
    setup += kv("Owner", esc(s.owner || "–")) + kv("Snapshot", s.source === "profile" ? esc(ageText(s.age_days)) +
      (s.taken_by ? " (by " + esc(s.taken_by) + ")" : "") : "none yet");
    setup += kv("Docking", esc(policy(s.docking))) + kv("Management", esc(policy(s.management)));
    setup += kv("Credits", s.credits === null || s.credits === undefined ? "–" : num(s.credits));
    setup += kv("Workers", s.source === "profile" ? num(s.workers) + (s.workers_used !== null && s.workers_used !== undefined ? " (" + num(s.workers_used) + " used)" : "") : "–");
    setup += kv("Hull", s.hull_space ? num(s.hull_used) + " / " + num(s.hull_space) + " (" + Math.round(s.hull_fill_pct || 0) + "%)" : "–");
    setup += kv("Colony", s.has_colony ? "yes" : "no");
    var flags = Object.keys(s.flags || {}).map(function (k) { return pretty(k) + ": " + (s.flags[k] ? "on" : "off"); });
    if (flags.length) { setup += kv("Switches", esc(flags.join(" · "))); }
    if (s.motd) { setup += kv("MOTD", esc(s.motd)); }
    if (s.trade_entries !== null && s.trade_entries !== undefined) { setup += kv("Trade entries", num(s.trade_entries) + (s.trade_listed !== null && s.trade_listed !== undefined ? " (" + num(s.trade_listed) + " listed)" : "")); }
    else if (s.source === "profile") { setup += kv("Trade entries", "<span class=\"dim\">not captured</span>"); }
    if (s.construction) {
      var projects = s.construction.projects || [];
      var queue = projects.length ? projects.map(function (p) {
        var done = p.manhours_total ? Math.round(100 * (p.manhours_done || 0) / p.manhours_total) + "%" : "";
        return esc((p.name || "project") + (done ? " " + done : "") + (p.build_state ? " (" + p.build_state + ")" : ""));
      }).join("<br>") : "idle";
      setup += kv("Construction", queue + " · " + num(s.construction.blueprints) + " blueprint(s)");
    }
    if (s.shuttle_covered) { setup += kv("Shuttle", "network covers this station"); }

    var mods = s.modules.length ? s.modules.map(function (m) { return "<div class=\"kv\"><span>" + esc(m.name) + "</span><b>×" + num(m.quantity) + "</b></div>"; }).join("")
      : "<div class=\"dim\">" + (s.source === "profile" ? "No modules." : "Needs a snapshot.") + "</div>";

    var k = slotsOf(s), slotBlock = "";
    if (k.status === "ok" || k.status === "short") {
      slotBlock = "<div class=\"kv\"><span>" + esc(k.body || "") + " (" + esc(k.kind || "planet") + (k.matched === "name" ? ", matched by station name" : "") + ")</span><b>" +
        num(k.fitted_total) + " / " + num(k.slots_total) + " slots" + (k.from_scan ? " *" : "") + "</b></div>" +
        k.rows.map(function (r) {
          var cls = r.missing ? "bad" : (r.extra ? "warn" : "");
          return "<div class=\"kv " + cls + "\"><span>" + esc(r.label) + "</span><b>" + num(r.fitted) + " / " + num(r.slots) +
            (r.missing ? " · " + num(r.missing) + " empty" + (r.lost_per_day ? " (~" + compact(r.lost_per_day) + "/day)" : "") : "") +
            (r.extra ? " · " + num(r.extra) + " extra" : "") + "</b></div>";
        }).join("") + (k.from_scan ? "<div class=\"dim\">* from an older scan: dock here to confirm.</div>" : "");
    } else {
      slotBlock = "<div class=\"dim\">" + esc(SLOT_NOTE[k.status] || "") + (k.body ? " (" + esc(k.body) + ")" : "") + "</div>";
    }

    var rows = s.resources.map(function (r) {
      var routes = r.routes.length ? r.routes.map(function (b) {
        return "<span class=\"st-chip\" title=\"" + esc(b.ship + " · cargo " + num(b.cargo_cap) + " · " + b.stops + " stop(s) · " + b.state) + "\">" +
          esc(b.nickname) + (b.account ? " <i>" + esc(b.account) + "</i>" : "") + "</span>";
      }).join(" ") : (r.production_per_day > 0 ? "<span class=\"dim\">none" + (r.shuttled_per_day > 0 ? " (shuttled)" : "") + "</span>" : "");
      return "<tr><td>" + esc(r.label) + "</td>" +
        "<td class=\"num\">" + compact(r.stock) + (r.cap ? " / " + compact(r.cap) : "") + "</td>" +
        "<td>" + (r.cap ? bar(r.fill_pct, r.fill_pct >= 95 ? "bad" : (r.fill_pct >= 80 ? "warn" : "")) : "") + "</td>" +
        "<td class=\"num\">" + (r.production_per_day > 0 ? compact(r.production_per_day) : "–") + "</td>" +
        "<td class=\"num\">" + (r.production_per_day > 0 ? (r.bought_observed_per_day === null || r.bought_observed_per_day === undefined ?
          "<span class=\"dim\" title=\"No stretch of this station's log spans 3 h or more\">–</span>" : compact(r.bought_observed_per_day)) : "–") + "</td>" +
        "<td class=\"num dim\">" + (r.production_per_day > 0 ? compact(r.bought_per_day) : "–") + "</td>" +
        "<td class=\"num\">" + (r.production_per_day > 0 ? compact(r.shuttled_per_day) : "–") + "</td>" +
        "<td title=\"" + esc(leavesTitle(r)) + "\">" + (r.production_per_day > 0 ? bar(r.pct_leaves, pctClass(r.pct_leaves)) +
          (r.basis === "running" ? "<span class=\"dim\">est.</span>" : "") : "") + "</td>" +
        "<td><span class=\"st-badge " + (STATUS_CLASS[r.status] || "none") + "\">" + (STATUS_TEXT[r.status] || r.status) + "</span></td>" +
        "<td>" + routes + "</td></tr>";
    }).join("");
    var table = rows ? "<table class=\"st-res\"><thead><tr><th>Resource</th><th class=\"num\">Stock / cap</th><th>Fill</th>" +
      "<th class=\"num\" title=\"Raw output per day from the extractors\">Made/day</th>" +
      "<th class=\"num\" title=\"What bots really took, per day, in the newest stretch of this station's log" + (s.log_window ? " (" + windowText(s.log_window) + ")" : "") + "\">Took/day</th>" +
      "<th class=\"num\" title=\"What bots would take per day if they ran non-stop at their loop time and load (overstates a bot that is not logged in all day)\">If non-stop</th>" +
      "<th class=\"num\" title=\"Moved out by shuttle per day\">Shuttle/day</th><th title=\"Share of production that leaves (follows Took/day; est. = no log window, so the non-stop figure)\">Leaves</th><th>Status</th><th>Bots routed to take it</th></tr></thead><tbody>" +
      rows + "</tbody></table>" : "<div class=\"dim\">No resource data yet.</div>";

    var issues = s.issues.length ? "<ul class=\"st-issues\">" + s.issues.map(function (i) {
      return "<li class=\"" + esc(i.severity) + "\">" + esc(i.text) + "</li>"; }).join("") + "</ul>" : "<div class=\"dim\">Nothing to flag.</div>";

    return "<div class=\"st-detail\"><div class=\"st-col\"><h4>SETUP</h4>" + setup + "<h4>MODULES</h4>" + mods + "<h4>GEAR FITTED</h4>" + gearBlock(s) + "<h4>EXTRACTOR SLOTS</h4>" + slotBlock + "</div>" +
      "<div class=\"st-col wide\"><h4>ISSUES</h4>" + issues + "<h4>RESOURCES &amp; ROUTES</h4>" + table +
      "<h4>BOTS SEEN AT THIS STATION</h4>" + botsTable(s) + "</div></div>";
  }

  function render() {
    var list = DATA.stations.filter(matches).sort(SORTS[state.sort] || SORTS.issues);
    renderTiles(list);
    if (!list.length) {
      el.body.innerHTML = "<div class=\"st-empty\">" + (DATA.stations.length ? "No station matches these filters." :
        "No stations yet. Dock at one of your stations (and leave it) to record its first snapshot; scan data fills the rest in.") + "</div>";
      return;
    }
    var groups = {}, order = [];
    list.forEach(function (s) {
      var g = s.system || "Unknown";
      if (!groups[g]) { groups[g] = []; order.push(g); }
      groups[g].push(s);
    });
    order.sort(function (a, b) {
      if (state.sort === "issues") { var x = worst([].concat.apply([], groups[a].map(function (s) { return s.issues; })));
        var y = worst([].concat.apply([], groups[b].map(function (s) { return s.issues; })));
        return (y.bad - x.bad) || (y.warn - x.warn) || a.localeCompare(b); }
      return a.localeCompare(b);
    });
    var html = "<table class=\"st-table\"><thead><tr><th>Station</th><th>Snapshot</th><th>Docking</th><th>Mgmt</th>" +
      "<th class=\"num\" title=\"Resource extractors fitted\">Extractors</th><th class=\"num\">Workers</th>" +
      "<th title=\"Extractors fitted / extractor slots the planet gives this station\">Slots</th><th title=\"Mk I-III station gear fitted (Mk II warns while a higher mark exists)\">Gear</th><th>Hull</th>" +
      "<th title=\"Share of production that leaves by bot or shuttle\">Covered</th><th>Issues</th></tr></thead><tbody>";
    order.forEach(function (g) {
      var members = groups[g], collapsed = !!state.collapsed[g];
      var w = worst([].concat.apply([], members.map(function (s) { return s.issues; })));
      var snaps = members.filter(function (s) { return s.source === "profile"; }).length;
      html += "<tr class=\"st-group\" data-system=\"" + esc(g) + "\"><td colspan=\"11\">" + (collapsed ? "▸ " : "▾ ") + esc(g) +
        " <span class=\"dim\">· " + members.length + " station" + (members.length > 1 ? "s" : "") + " · " + snaps + " snapshot" + (snaps === 1 ? "" : "s") + "</span>" +
        (w.bad ? " <span class=\"st-badge bad\">" + w.bad + " problem" + (w.bad > 1 ? "s" : "") + "</span>" : "") +
        (w.warn ? " <span class=\"st-badge warn\">" + w.warn + " warning" + (w.warn > 1 ? "s" : "") + "</span>" : "") + "</td></tr>";
      if (!collapsed) { members.forEach(function (s) { html += stationRow(s); }); }
    });
    el.body.innerHTML = html + "</tbody></table>";
  }

  el.search.addEventListener("input", function () { state.text = el.search.value; persist(); render(); });
  el.system.addEventListener("change", function () { state.system = el.system.value; persist(); render(); });
  el.status.addEventListener("change", function () { state.status = el.status.value; persist(); render(); });
  el.resource.addEventListener("change", function () { state.resource = el.resource.value; persist(); render(); });
  el.gear.addEventListener("change", function () { state.gear = el.gear.value; persist(); render(); });
  el.sort.addEventListener("change", function () { state.sort = el.sort.value; persist(); render(); });
  el.toggle.addEventListener("click", function () {
    var allCollapsed = systems.length && systems.every(function (n) { return state.collapsed[n]; });
    state.collapsed = {};
    if (!allCollapsed) { systems.concat(["Unknown"]).forEach(function (n) { state.collapsed[n] = true; }); }
    el.toggle.textContent = allCollapsed ? "Collapse all" : "Expand all";
    persist(); render();
  });
  el.body.addEventListener("click", function (event) {
    var group = event.target.closest(".st-group");
    if (group) { var g = group.getAttribute("data-system"); state.collapsed[g] = !state.collapsed[g]; persist(); render(); return; }
    var row = event.target.closest(".st-row");
    if (row) { var id = row.getAttribute("data-id"); state.open = state.open === id ? null : id; render(); }
  });
  render();
})();
