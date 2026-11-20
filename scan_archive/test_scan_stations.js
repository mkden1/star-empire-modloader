/* Tests for scan_stations.js (the scan archive's STATIONS tab), run in node against a tiny stand-in for the page.
   Run by test_scan_stations.py, or directly: node test_scan_stations.js */
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const CODE = fs.readFileSync(path.join(__dirname, "scan_stations.js"), "utf8");

function res(key, extra) {
  return Object.assign({key: key, label: key.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase()), stock: 100, cap: 1000,
    reserve: 0, fill_pct: 10, production_per_day: 5000, bought_per_day: 0, bought_observed_per_day: 0, shuttled_per_day: 0,
    shuttled_to: [], pct_satisfied: 100, pct_observed: 100, pct_leaves: 100, basis: "observed", routes: [], status: "ok"}, extra || {});
}
function station(id, extra) {
  return Object.assign({id: id, name: "Station " + id, system: "Alpha", source: "profile", seen_at: 1, age_days: 0.5, owner: "ExamplePlayer",
    taken_by: "ExamplePlayer", docking: "public", management: "owner", motd: "", has_colony: false, flags: {}, credits: 10, workers_used: 1,
    workers: 5, hull_used: 10, hull_space: 100, hull_fill_pct: 10, modules: [{name: "Drill", quantity: 3}], extractors: 3,
    construction: null, trade_entries: 2, trade_listed: 3, resources: [res("metal_ore")], bot_docks: 5, unique_bots: 1,
    shuttle_covered: false, coverage_pct: 100, slots: {status: "no-snapshot"}, issues: [], log_window: null, log_rows: 40,
    bots: []}, extra || {});
}
const SAMPLE = {
  generated_at: 1000000,
  systems: [{name: "Alpha"}, {name: "Beta"}],
  resources: ["metal_ore", "space_corn", "wood"],
  stations: [
    station("1", {name: "alpha-one", resources: [res("metal_ore")],
      slots: {status: "ok", body: "Alpha <b>One</b>", kind: "planet", matched: "snapshot", slots_total: 12, fitted_total: 12,
        missing_total: 0, from_scan: false, rows: [{key: "metal_ore", label: "Metal Ore", slots: 12, fitted: 12, missing: 0, extra: 0, lost_per_day: null}]}}),
    station("2", {name: "alpha-two", age_days: 5, resources: [res("space_corn", {status: "no-route", pct_satisfied: 0, pct_observed: 0, pct_leaves: 0, routes: []})],
      slots: {status: "short", body: "Alpha", kind: "planet", matched: "name", slots_total: 10, fitted_total: 7, missing_total: 3,
        from_scan: true, rows: [{key: "metal_ore", label: "Metal Ore", slots: 10, fitted: 7, missing: 3, extra: 0, lost_per_day: 77760},
          {key: "copper", label: "Copper", slots: 1, fitted: 2, missing: 0, extra: 1, lost_per_day: null}]},
      issues: [{code: "no-route", severity: "bad", text: "Space Corn: nothing takes it", resource: "space_corn"},
               {code: "stale", severity: "warn", text: "Snapshot is 5 days old."}]}),
    station("3", {name: "beta-one", system: "Beta", source: "scan", seen_at: null, age_days: null, owner: "", docking: null,
      management: null, modules: [], extractors: 0, hull_fill_pct: 95, coverage_pct: 40, resources: [res("wood", {pct_satisfied: 40, pct_observed: 40, pct_leaves: 40, status: "low"})],
      issues: [{code: "no-snapshot", severity: "info", text: "No docked snapshot yet."}]}),
    station("4", {name: "<img src=x onerror=alert(1)>", system: "Beta", owner: "<b>evil</b>", motd: "</script><i>hi</i>",
      resources: [res("metal_ore", {routes: [{nickname: "<s>bot</s>", account: "a&b", ship: "Caravan", cargo_cap: 1000, stops: 2, state: "hauling"}]})],
      issues: [{code: "x", severity: "warn", text: "<u>warn</u>"}]}),
  ],
};

function makeEnv(data, stored) {
  const elements = {};
  function el(id) {
    if (!elements[id]) {
      let value = "";
      elements[id] = {id: id, innerHTML: "", textContent: "", _on: {},
        addEventListener(type, fn) { this._on[type] = fn; },
        querySelector(selector) { return el(selector.replace(/^#/, "")); },
        // a <select> keeps its old value when set to something that is not one of its options (a browser sets "")
        get value() { return value; },
        set value(next) {
          const select = new RegExp("<select id=\"" + id + "\"[^>]*>(.*?)</select>").exec(elements.stationsView.innerHTML);
          if (!select) { value = next; return; }
          const options = (select[1].match(/value="([^"]*)"/g) || []).map(o => o.slice(7, -1));
          value = options.indexOf(next) >= 0 ? next : "";
        }};
    }
    return elements[id];
  }
  const root = el("stationsView");
  const saved = {value: null};
  const context = {
    document: {getElementById: id => id === "stationsView" ? root : null},
    localStorage: {getItem: () => stored ? JSON.stringify(stored) : null, setItem: (k, v) => { saved.value = JSON.parse(v); }},
    STATIONS: data,
  };
  vm.runInNewContext(CODE, context);
  return {
    el: el, saved: saved,
    body: () => el("stBody").innerHTML,
    tiles: () => el("stTiles").innerHTML,
    set(id, value, type) { el(id).value = value; el(id)._on[type || "change"](); },
    clickRow(id) { el("stBody")._on.click({target: {closest: s => s === ".st-row" ? {getAttribute: () => id} : null}}); },
    clickGroup(name) { el("stBody")._on.click({target: {closest: s => s === ".st-group" ? {getAttribute: () => name} : null}}); },
  };
}
const names = html => (html.match(/class="name">[^<]*<\/td>|class="name">(?:▾|▸) [^<]*</g) || []);
const rowIds = html => (html.match(/class="st-row[^"]*" data-id="([^"]*)"/g) || []).map(s => s.match(/data-id="([^"]*)"/)[1]);

// ---- rendering ----
let env = makeEnv(SAMPLE);
assert.ok(env.body().includes("Alpha"), "system groups are shown");
assert.deepStrictEqual(rowIds(env.body()).sort(), ["1", "2", "3", "4"]);
assert.ok(env.body().includes("2 snapshots") || env.body().includes("snapshot"), "group header counts snapshots");
assert.ok(env.tiles().includes(">4<"), "the stations tile counts them");
assert.ok(env.tiles().includes("3 / 4"), "snapshots tile: 3 of 4 have one");

// ---- everything from the game is escaped ----
assert.ok(!env.body().includes("<img"), "a station name cannot inject markup");
assert.ok(env.body().includes("&lt;img src=x onerror=alert(1)&gt;"));
env.clickRow("4");
assert.ok(!env.body().includes("<b>evil</b>") && env.body().includes("&lt;b&gt;evil&lt;/b&gt;"), "owner is escaped");
assert.ok(!env.body().includes("<i>hi</i>") && env.body().includes("&lt;/script&gt;"), "a MOTD cannot close the script or inject");
assert.ok(!env.body().includes("<s>bot</s>") && env.body().includes("&lt;s&gt;bot&lt;/s&gt;"), "bot nicknames are escaped");
assert.ok(!env.body().includes("<u>warn</u>") && env.body().includes("&lt;u&gt;warn&lt;/u&gt;"), "issue text is escaped");
assert.ok(env.body().includes("a&amp;b"), "an ampersand in an account name is escaped");

// ---- filters ----
env = makeEnv(SAMPLE);
env.set("stSearch", "alpha", "input");
assert.deepStrictEqual(rowIds(env.body()).sort(), ["1", "2"]);
env.set("stSearch", "drill alpha", "input");                    // every word must match, in any field
assert.deepStrictEqual(rowIds(env.body()).sort(), ["1", "2"]);
env.set("stSearch", "wood", "input");                           // a resource label
assert.deepStrictEqual(rowIds(env.body()), ["3"]);
env.set("stSearch", "", "input");
env.set("stSystem", "Beta");
assert.deepStrictEqual(rowIds(env.body()).sort(), ["3", "4"]);
env.set("stSystem", "");
env.set("stStatus", "attention");
assert.deepStrictEqual(rowIds(env.body()).sort(), ["2", "4"], "warnings and problems count, info does not");
env.set("stStatus", "bad");
assert.deepStrictEqual(rowIds(env.body()), ["2"]);
env.set("stStatus", "no-snapshot");
assert.deepStrictEqual(rowIds(env.body()), ["3"]);
env.set("stStatus", "snapshot");
assert.deepStrictEqual(rowIds(env.body()).sort(), ["1", "2", "4"]);
env.set("stStatus", "stale");
assert.deepStrictEqual(rowIds(env.body()), ["2"], "3+ days old, from a snapshot only");
env.set("stStatus", "all");
env.set("stResource", "wood");
assert.deepStrictEqual(rowIds(env.body()), ["3"], "stations that produce the resource");
env.set("stResource", "");
env.set("stSearch", "zzz-nothing", "input");
assert.ok(env.body().includes("No station matches"), "an empty result says so");

// ---- sorting ----
env = makeEnv(SAMPLE);
assert.strictEqual(rowIds(env.body())[0], "2", "most problems first: the station with a problem leads");
env.set("stSort", "name");
assert.deepStrictEqual(rowIds(env.body()), ["1", "2", "4", "3"], "by name inside each system (Alpha then Beta; <img... sorts before beta)");
env.set("stSort", "age");
assert.strictEqual(rowIds(env.body())[0], "2", "oldest snapshot first inside a system");
env.set("stSort", "coverage");
assert.ok(rowIds(env.body()).indexOf("3") >= 0, "coverage sort keeps every station");
env.set("stSort", "hull");
assert.strictEqual(rowIds(env.body()).indexOf("3") < rowIds(env.body()).indexOf("4"), true, "fullest hull first inside Beta");

// ---- extractor slots ----
env = makeEnv(SAMPLE);
assert.ok(env.body().includes(">12/12<") || env.body().includes(">12/12"), "a filled station shows fitted/slots");
assert.ok(env.body().includes("7/10*"), "a figure from an older scan is starred");
assert.ok(env.tiles().includes("slots unfilled"), "the tile is there");
env.set("stStatus", "short");
assert.deepStrictEqual(rowIds(env.body()), ["2"], "the filter keeps only stations with unfilled slots");
env.set("stStatus", "all");
env.clickRow("2");
assert.ok(env.body().includes("EXTRACTOR SLOTS"), "the detail has its slots section");
assert.ok(env.body().includes("3 empty") && env.body().includes("(~78k/day)"), "empty slots and the output they cost");
assert.ok(env.body().includes("1 extra"), "extractors beyond the slots are shown too");
assert.ok(env.body().includes("matched by station name") && env.body().includes("from an older scan"), "how sure the match is");
env.clickRow("2");
env.clickRow("1");
assert.ok(!env.body().includes("Alpha <b>One</b>") && env.body().includes("Alpha &lt;b&gt;One&lt;/b&gt;"), "the body name is escaped");
env.clickRow("1");
env.clickRow("3");
assert.ok(env.body().includes("no modules known yet") || env.body().includes("Needs a snapshot"), "an unknown station says why");
env = makeEnv({generated_at: 1, systems: [{name: "A"}], resources: [], stations: [station("9", {slots: {status: "unattached"}})]});
env.clickRow("9");
assert.ok(env.body().includes("not built on a planet"));
env = makeEnv({generated_at: 1, systems: [{name: "A"}], resources: [], stations: [station("9", {slots: undefined})]});
assert.ok(rowIds(env.body()).length === 1, "a station with no slots data still renders");

// ---- opening and closing a station ----
env = makeEnv(SAMPLE);
assert.ok(!env.body().includes("st-detail"));
env.clickRow("2");
assert.ok(env.body().includes("st-detail") && env.body().includes("Space Corn: nothing takes it"));
assert.ok(env.body().includes("NO ROUTE"), "the resource status badge");
env.clickRow("2");
assert.ok(!env.body().includes("st-detail"), "a second click closes it");
env.clickRow("1");
env.clickRow("2");
assert.strictEqual((env.body().match(/class="st-detail"/g) || []).length, 1, "one station open at a time");

// ---- collapsing a system, and remembering it ----
env = makeEnv(SAMPLE);
env.clickGroup("Alpha");
assert.deepStrictEqual(rowIds(env.body()).sort(), ["3", "4"], "a collapsed system hides its stations");
assert.deepStrictEqual(env.saved.value.collapsed, {Alpha: true});
env = makeEnv(SAMPLE, {collapsed: {Beta: true}, text: "", system: "", status: "all", resource: "", sort: "name"});
assert.deepStrictEqual(rowIds(env.body()).sort(), ["1", "2"], "saved collapsed systems come back");
assert.strictEqual(env.el("stSort").value, "name", "and so does the saved sort");

// ---- saved filters that no longer fit are dropped, not fatal ----
env = makeEnv(SAMPLE, {system: "Gone Sound", resource: "unobtainium", status: "attention", text: "x", sort: "issues"});
assert.strictEqual(env.el("stSystem").value, "");
assert.strictEqual(env.el("stResource").value, "", "an unknown saved resource is dropped");
assert.strictEqual(env.el("stSearch").value, "x", "the rest of the saved filters stay");
env = makeEnv(SAMPLE, "not an object");
assert.strictEqual(rowIds(env.body()).length, 4);

// ---- empty data ----
env = makeEnv({generated_at: 1, stations: [], systems: [], resources: []});
assert.ok(env.body().includes("No stations yet"), "tells you how to get the first snapshot");
env = makeEnv(undefined);
assert.ok(env.body().includes("No stations yet"), "a page without data does not crash");

// ---- a snapshot-less station shows no made-up figures ----
env = makeEnv(SAMPLE);
env.clickRow("3");
assert.ok(env.body().includes("none yet") && env.body().includes("Needs a snapshot."));

// ---- "production covered" only counts what is a question of hauling ----
const tileValue = html => (html.match(/<div class="v">([^<]*)<\/div><div class="l">production covered/) || [])[1];
const hauling = {generated_at: 1, systems: [{name: "A"}], resources: ["metal_ore", "space_corn", "wood"], stations: [
  station("1", {system: "A", resources: [res("metal_ore", {production_per_day: 1000, pct_leaves: 100, status: "ok"}),
    res("space_corn", {production_per_day: 9000, pct_leaves: 0, status: "used"})]}),
  station("2", {system: "A", resources: [res("wood", {production_per_day: 9000, pct_leaves: null, status: "unknown"}),
    res("metal_ore", {production_per_day: 9000, pct_leaves: null, status: "routed"})]})]};
env = makeEnv(hauling);
assert.strictEqual(tileValue(env.tiles()), "100%", "output used on site and output with no log figure are left out");
hauling.stations[1].resources[0].status = "no-route";
hauling.stations[1].resources[0].pct_leaves = 0;
env = makeEnv(hauling);
assert.strictEqual(tileValue(env.tiles()), "10%", "but an unserved resource counts: 1,000 of 10,000 leaves");
env = makeEnv(hauling);
env.clickRow("1");
assert.ok(env.body().includes("USED ON SITE"), "the used-on-site badge is spelled out in the detail");
hauling.stations = [station("1", {system: "A", resources: [res("space_corn", {status: "used", pct_leaves: 0})]})];
env = makeEnv(hauling);
assert.strictEqual(tileValue(env.tiles()), "–", "nothing to haul: no percentage is invented");

// ---- both rates: what bots really took, and what they would take non-stop ----
const WINDOW = {start: 1000000 - 80000, end: 1000000, hours: 22.2};
const twoRates = {generated_at: 1000000, systems: [{name: "A"}], resources: ["metal_ore"], stations: [
  station("1", {system: "A", log_window: WINDOW, resources: [res("metal_ore", {production_per_day: 1000000, bought_per_day: 1500000,
    bought_observed_per_day: 300000, pct_satisfied: 150, pct_observed: 30, pct_leaves: 30, basis: "observed", status: "low"})]})]};
env = makeEnv(twoRates);
assert.strictEqual(tileValue(env.tiles()), "30%", "the tile follows what really left, not the non-stop figure");
env.clickRow("1");
let body = env.body();
assert.ok(body.includes(">Took/day<") && body.includes(">If non-stop<"), "both columns are in the resource table");
assert.ok(body.includes("<td class=\"num\">300k</td>") && body.includes("<td class=\"num dim\">1.5M</td>"), "with both figures");
assert.ok(body.includes("30% left in the newest log window · 150% if the bots ran non-stop"), "the Leaves bar spells out both");
assert.ok(!body.includes(">est.<"), "a measured figure is not marked as an estimate");
// no log window: the observed column is empty and the bar is the non-stop figure, marked
twoRates.stations[0].log_window = null;
twoRates.stations[0].resources[0].bought_observed_per_day = null;
twoRates.stations[0].resources[0].pct_observed = null;
twoRates.stations[0].resources[0].pct_leaves = 150;
twoRates.stations[0].resources[0].basis = "running";
env = makeEnv(twoRates);
env.clickRow("1");
body = env.body();
assert.ok(body.includes("No stretch of this station's log spans 3 h or more"), "the missing observed figure says why");
assert.ok(body.includes(">est.<"), "a non-stop figure used for coverage is marked est.");

// ---- the bots seen at a station, from its log ----
const botStation = station("1", {system: "A", log_window: WINDOW, bots: [
  {name: "Metal Ore (TB-No U)", nickname: "Metal Ore", account: "TB-No U", docks: 30, window_docks: 14, loop_seconds: 262, in_fleet: false,
   rows: [{key: "metal_ore", label: "Metal Ore", per_dock: 133136, running_per_day: 21460743, observed_per_day: 7264494},
          {key: "silicon", label: "Silicon", per_dock: 500, running_per_day: 4000, observed_per_day: null}]},
  {name: "<i>x</i>", nickname: "<i>x</i>", account: "TB-111", docks: 3, window_docks: 0, loop_seconds: null, in_fleet: true,
   rows: [{key: "wood", label: "Wood", per_dock: null, running_per_day: null, observed_per_day: 0}]}]});
env = makeEnv({generated_at: 1000000, systems: [{name: "A"}], resources: [], stations: [botStation]});
env.clickRow("1");
body = env.body();
assert.ok(body.includes("BOTS SEEN AT THIS STATION"), "the detail has the bots section");
assert.ok(body.includes("Metal Ore") && body.includes("TB-No U") && body.includes("4m22s"), "name, account and loop time (262 s)");
assert.ok(body.includes("133k") && body.includes("7.3M") && body.includes("21M"), "load per dock, really moved, and non-stop");
assert.ok(body.includes(">other<") && body.includes(">yours<"), "a bot outside the user's fleet scan is told from their own");
assert.ok(!body.includes("<i>x</i>") && body.includes("&lt;i&gt;x&lt;/i&gt;"), "a bot's name is escaped");
// a bot of one of the user's accounts that is no longer in its fleet scan is not "other", and not at the station lately
const retired = station("1", {system: "A", log_window: WINDOW, bots: [
  {name: "1 (TB-111)", nickname: "1", account: "TB-111", docks: 500, window_docks: 0, last_seen: 1000000 - 3 * 86400, loop_seconds: 315,
   in_fleet: false, mine: true, rows: [{key: "metal_ore", label: "Metal Ore", per_dock: 13000, running_per_day: 3600000, observed_per_day: 0}]},
  {name: "1 (TB-444)", nickname: "1", account: "TB-444", docks: 80, window_docks: 9, last_seen: 1000000 - 3600, loop_seconds: 314,
   in_fleet: true, mine: true, rows: [{key: "metal_ore", label: "Metal Ore", per_dock: 34000, running_per_day: 9400000, observed_per_day: 4100000}]},
  {name: "Metal Ore (TB-No U)", nickname: "Metal Ore", account: "TB-No U", docks: 5, window_docks: 3, last_seen: 1000000 - 60, loop_seconds: 60,
   in_fleet: false, mine: false, rows: [{key: "metal_ore", label: "Metal Ore", per_dock: 7, running_per_day: 70, observed_per_day: 7}]}]});
env = makeEnv({generated_at: 1000000, systems: [{name: "A"}], resources: [], stations: [retired]});
env.clickRow("1");
body = env.body();
assert.strictEqual((body.match(/>yours</g) || []).length, 1, "only the bot that is in the fleet scan is plain yours");
assert.ok(body.includes("yours · not in fleet now"), "a retired bot of the user's own account says so");
assert.strictEqual((body.match(/>other</g) || []).length, 1, "and only a bot of another account is other");
assert.ok(body.includes(">Last docked<") && body.includes("3.0 d ago") && body.includes("1 h ago"), "when each bot last docked here");
const retiredRow = body.split("<tr").find(r => r.includes("TB-111"));
assert.ok(retiredRow.includes("class=\"dim\"") && retiredRow.includes("Not at this station in the newest stretch of its log"),
  "a bot absent from the newest log window is dimmed and shows no Took/day figure");
assert.ok(!body.split("<tr").find(r => r.includes("TB-444")).includes("Not at this station"), "a bot that was there shows its figure");
assert.ok(body.split("<tr").find(r => r.includes("TB-444")).includes("4.1M"));
// without a log window there is nothing to be absent from
retired.log_window = null;
env = makeEnv({generated_at: 1000000, systems: [{name: "A"}], resources: [], stations: [retired]});
env.clickRow("1");
assert.ok(!env.body().includes("Not at this station"), "no window: no bot is called absent");
env = makeEnv({generated_at: 1, systems: [{name: "A"}], resources: [], stations: [station("1", {log_rows: 0, bots: []})]});
env.clickRow("1");
assert.ok(env.body().includes("No station log captured yet"), "no log at all is told from a log with no bots");
env = makeEnv({generated_at: 1, systems: [{name: "A"}], resources: [], stations: [station("1", {log_rows: 40, bots: []})]});
env.clickRow("1");
assert.ok(env.body().includes("No bot took or left anything"), "a log with no bot txns says so");
env = makeEnv({generated_at: 1, systems: [{name: "A"}], resources: [], stations: [station("1", {bots: undefined})]});
env.clickRow("1");
assert.ok(env.body().includes("st-detail"), "a station from an older build, with no bots list, still opens");

// ---- gear: which Mk I-III station modules are fitted ----
function mod(type, label, mark, top, count) {
  return {type: type, family: type.replace(/_mk\d+$/, ""), label: label, mark: mark, count: count || 1, top: top, behind: mark < top};
}
function gearOfMods(mods, extra) {
  const per = {};
  mods.forEach(m => { per[m.mark] = (per[m.mark] || 0) + m.count; });
  return Object.assign({known: true, seen_at: 1, age_days: 2, fitted: mods,
    marks: Object.keys(per).map(Number).sort((a, b) => a - b).map(k => ({mark: k, count: per[k]})),
    behind: mods.filter(m => m.behind).reduce((n, m) => n + m.count, 0)}, extra || {});
}
const GEAR = {generated_at: 1000000, systems: [{name: "A"}, {name: "B"}], resources: [], stations: [
  station("1", {name: "mixed", system: "A", gear: gearOfMods([mod("station_shield_mk2", "Station Shield", 2, 3),
    mod("station_energy_mk2", "Station Energy", 2, 3), mod("station_sensor_mk3", "Station Sensor", 3, 3)])}),
  station("2", {name: "all-three", system: "A", gear: gearOfMods([mod("station_shield_mk3", "Station Shield", 3, 3, 2),
    mod("station_energy_mk3", "Station Energy", 3, 3)])}),
  station("3", {name: "old-one", system: "B", gear: gearOfMods([mod("station_shield_mk1", "Station Shield", 1, 3),
    mod("station_point_defense_laser_mk1", "Station Point Defense Laser", 1, 1)])}),
  station("4", {name: "unknown-gear", system: "B", gear: {known: false}}),
  station("5", {name: "bare", system: "B", gear: gearOfMods([])}),
  station("6", {name: "evil", system: "B", gear: gearOfMods([mod("station_shield_mk2", "<b>Shield</b>", 2, 3)])}),
  station("7", {name: "no-gear-field", system: "B"}),
]};
const gearIds = e => rowIds(e.body()).sort();
const rowHtml = (e, id) => e.body().split("<tr class=\"st-row").find(r => r.includes("data-id=\"" + id + "\""));
const mk2Tile = html => (html.match(/<div class="v">(\d+)<\/div><div class="l">on Mk II gear/) || [])[1];

env = makeEnv(GEAR);
assert.deepStrictEqual(gearIds(env), ["1", "2", "3", "4", "5", "6", "7"], "no gear filter: every station");
assert.ok(env.el("stationsView").innerHTML.includes("<select id=\"stGear\""), "the gear filter is on the page");
["Any gear", "Mk II gear fitted", "Mk I gear fitted", "Gear behind the newest mark", "Gear not known yet"].forEach(label =>
  assert.ok(env.el("stationsView").innerHTML.includes(">" + label + "<"), label));

env.set("stGear", "mk2");
assert.deepStrictEqual(gearIds(env), ["1", "6"], "stations still using Mk II gear");
assert.strictEqual(mk2Tile(env.tiles()), "2");
env.set("stGear", "mk1");
assert.deepStrictEqual(gearIds(env), ["3"], "stations with Mk I gear fitted");
assert.strictEqual(mk2Tile(env.tiles()), "0");
env.set("stGear", "behind");
assert.deepStrictEqual(gearIds(env), ["1", "3", "6"], "a module below its family's newest mark; Mk III-only and bare stations are not");
env.set("stGear", "unknown");
assert.deepStrictEqual(gearIds(env), ["4", "7"], "no gear data yet (a station from an older build has no gear field at all)");
env.set("stGear", "");
assert.deepStrictEqual(gearIds(env), ["1", "2", "3", "4", "5", "6", "7"]);

// it combines with the other filters
env.set("stGear", "mk2");
env.set("stSystem", "B");
assert.deepStrictEqual(gearIds(env), ["6"]);
env.set("stSearch", "nothing-like-it", "input");
assert.ok(env.body().includes("No station matches these filters."), "an empty result says so");
env.set("stSearch", "", "input");
env.set("stSystem", "");
env.set("stGear", "");

// the Gear column: a badge per mark, warn while a higher mark exists
let row = rowHtml(env, "1");
assert.ok(row.includes("Mk II ×2") && row.includes("Mk III ×1"), "marks and counts");
assert.ok(/st-badge warn[^>]*>[^<]*Mk II ×2/.test(row), "Mk II is a warning while Mk III exists");
assert.ok(/st-badge ok[^>]*>[^<]*Mk III ×1/.test(row), "a module at the top mark is fine");
assert.ok(row.indexOf("Mk II ×2") < row.indexOf("Mk III ×1"), "lowest mark first");
assert.ok(row.includes("Station Shield, Station Energy"), "the badge title names the gear");
assert.ok(/st-badge ok[^>]*>[^<]*Mk III ×3/.test(rowHtml(env, "2")), "counts add up per mark");
assert.ok(/st-badge warn[^>]*>[^<]*Mk I ×2/.test(rowHtml(env, "3")), "Mk I is behind too");
assert.ok(rowHtml(env, "4").includes("No setup scan of this station yet") && !rowHtml(env, "4").includes("Mk I"), "unknown is a dash with a reason");
assert.ok(rowHtml(env, "5").includes(">none<"), "a station with no marked gear fitted says none");
assert.ok(rowHtml(env, "7") && rowHtml(env, "7").includes("no-gear-field"), "a station with no gear field still renders");
assert.ok(env.body().includes("<th title=\"Mk I-III station gear fitted") && env.body().includes(">Gear</th>"), "the column header");
assert.ok(!env.body().includes("colspan=\"10\"") && env.body().includes("colspan=\"11\""), "group rows span the new column");

// the tile counts the stations in view and says how many have no data
env = makeEnv(GEAR);
assert.strictEqual(mk2Tile(env.tiles()), "2");
assert.ok(env.tiles().includes("2 stations have no gear data yet"), "the tile says what it cannot see");
assert.ok(/st-tile warn"[^>]*><div class="v">2<\/div><div class="l">on Mk II gear/.test(env.tiles()), "Mk II stations in view: the tile warns");
env.set("stSystem", "A");
assert.strictEqual(mk2Tile(env.tiles()), "1");
assert.ok(!env.tiles().includes("no gear data yet"), "no hint when every station in view has data");
assert.ok(/st-tile ok"[^>]*><div class="v">0<\/div><div class="l">on Mk II gear/.test(makeEnv(Object.assign({}, GEAR,
  {stations: [GEAR.stations[1]]})).tiles()), "no Mk II station: the tile is calm");

// the detail
env = makeEnv(GEAR);
env.clickRow("1");
body = env.body();
assert.ok(body.includes("GEAR FITTED"), "the detail has a gear section");
assert.ok(body.includes("<tr class=\"st-detail-row\"><td colspan=\"11\">") && !body.includes("colspan=\"10\""), "an open detail spans every column");
assert.ok(/<div class="kv warn"><span>Station Shield<\/span><b>Mk II · Mk III exists<\/b>/.test(body), "a module behind the top mark says what exists");
assert.ok(/<div class="kv "><span>Station Sensor<\/span><b>Mk III<\/b>/.test(body), "one at the top mark does not");
assert.ok(body.includes("spares in storage are not counted") && body.includes("2.0 d ago"), "where the figures come from");
env.clickRow("1");
env.clickRow("2");
assert.ok(env.body().includes("Mk III ×2"), "a count above one is shown");
env.clickRow("2");
env.clickRow("4");
assert.ok(env.body().includes("GEAR FITTED") && env.body().includes("dock at it once"), "unknown gear tells you how to get it");
env.clickRow("4");
env.clickRow("5");
assert.ok(env.body().includes("No Mk I-III station gear fitted."), "nothing fitted says so");
env.clickRow("5");
env.clickRow("7");
assert.ok(env.body().includes("GEAR FITTED") && env.body().includes("st-detail"), "a station with no gear field still opens");

// text from the game cannot inject markup through the gear
env = makeEnv(GEAR);
env.set("stGear", "mk2");
assert.ok(!env.body().includes("<b>Shield</b>") && env.body().includes("&lt;b&gt;Shield&lt;/b&gt;"), "a gear label is escaped in the badge title");
env.clickRow("6");
assert.ok(!env.body().includes("<b>Shield</b>") && env.body().includes("&lt;b&gt;Shield&lt;/b&gt;"), "and in the detail");

// roman numerals
const romans = {generated_at: 1, systems: [{name: "A"}], resources: [], stations: [station("1", {system: "A",
  gear: gearOfMods([mod("a_mk4", "A", 4, 9), mod("b_mk9", "B", 9, 9), mod("c_mk14", "C", 14, 14), mod("d_mk10", "D", 10, 14)])})]};
env = makeEnv(romans);
row = rowHtml(env, "1");
["Mk IV ×1", "Mk IX ×1", "Mk X ×1", "Mk XIV ×1"].forEach(text => assert.ok(row.includes(text), text));

// the choice is remembered, and a stale or unknown value falls back to every station
env = makeEnv(GEAR);
env.set("stGear", "mk2");
assert.strictEqual(env.saved.value.gear, "mk2");
env = makeEnv(GEAR, {gear: "mk2"});
assert.deepStrictEqual(gearIds(env), ["1", "6"], "restored on load");
env = makeEnv(GEAR, {gear: "mk9000"});
assert.deepStrictEqual(gearIds(env), ["1", "2", "3", "4", "5", "6", "7"], "an option that no longer exists is dropped");
env = makeEnv(GEAR, {gear: 5});
assert.strictEqual(gearIds(env).length, 7, "a stored value of the wrong type is ignored");

console.log("scan_stations.js: all tests passed");
