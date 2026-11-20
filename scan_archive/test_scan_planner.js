/* Tests for scan_planner.js (the scan archive's PLANNER tab). Run by test_scan_planner.py, or directly:
   node test_scan_planner.js

   Invented data on purpose: every expected number below can be checked by hand.
     Alpha  (planet, 3 stations): silicon 10, space_corn 5
     Alpha I(moon of Alpha, 1)  : silicon 4
     Beta   (gas giant)         : promethium 6
*/
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const core = require("./scan_planner.js");

const DATA = [
  {system: "Sys A", planet_name: "Alpha", planet_type: "Terra", parent_planet: "", stations: 3,
   resources: {silicon: 10, space_corn: 5, metal_ore: 0}},
  {system: "Sys A", planet_name: "Alpha I", planet_type: "Moon", parent_planet: "Alpha", stations: 1,
   resources: {silicon: 4}},
  {system: "Sys A", planet_name: "Beta", planet_type: "Gas Giant", parent_planet: "", stations: 3,
   resources: {promethium: 6}},
  {system: "Sys B", planet_name: "Gamma", planet_type: "Barren", parent_planet: "", stations: 3,
   resources: {silicon: 1}},
];
const GALAXY = {"Sys A": {hazard_rating: 2, total_planets: 2}, "Sys B": {hazard_rating: null, total_planets: 1}};
const CATALOG = {
  version: 1,
  kits: [
    {key: "kit_big", label: "Big Kit", kind: "planet", tech: 9, station_slots: 9, cost: {metal: 100}},
    {key: "kit_small", label: "Small Kit", kind: "planet", tech: 1, station_slots: 9, cost: null},
    {key: "kit_moon", label: "Moon Kit", kind: "moon", tech: null, station_slots: 2, cost: {metal: 40}},
    // free-standing kits: the big one has no station-slot cost entered, the small one takes 4
    {key: "ukit_big", label: "Unattached Big Kit", kind: "unattached", tech: 9, station_slots: null, cost: {credits: 50}},
    {key: "ukit_small", label: "Unattached Small Kit", kind: "unattached", tech: 3, station_slots: 4, cost: {metal: 5}},
  ],
  extractors: {
    silicon: [
      {key: "si_basic", label: "Silicon Drill", tier: "basic", cost: null, per_day: 100, per_day_inferred: true},
      {key: "si_ind", label: "Industrial Silicon Drill", tier: "industrial", tech: 9, cost: {metal: 3, microchips: 1}, per_day: 300},
    ],
    space_corn: [
      {key: "corn_basic", label: "Harvester", tier: "basic", cost: {metal: 2}, per_day: 100},
      {key: "corn_adv", label: "Advanced Harvester", tier: "advanced", cost: {metal: 3}, per_day: null},
      {key: "corn_ind", label: "Industrial Harvester", tier: "industrial", cost: {metal: 5}, per_day: 300},
    ],
    promethium: [{key: "prom_one", label: "Promethium Extractor", tier: "single", cost: {titanium: 2}, per_day: 8.64}],
  },
  gear: [
    {key: "shield1", label: "Shield I", slot: "shield", tech: 3, cost: {metal: 10}},
    {key: "weapon1", label: "Weapon I", slot: "weapon", tech: 3, cost: null},
    // an upgrade chain: gun1 is bought, gun2 and gun3 are blueprint upgrades of the one below
    {key: "gun1", label: "Gun I", slot: "weapon", tech: 3, cost: null, acquire: "bought from an AI station"},
    {key: "gun2", label: "Gun II", slot: "weapon", upgrade_from: "gun1", upgrade_qty: 1, blueprint: "Gun II Blueprint",
     cost: {manhours: 100, metal: 200}},
    {key: "gun3", label: "Gun III", slot: "weapon", upgrade_from: "gun2", upgrade_qty: 1, blueprint: "Gun III Blueprint",
     cost: {manhours: 1000, metal: 2500, gold: 100}},
    // built from scratch from its own blueprint (no chain); the blueprint is bought once
    {key: "plate3", label: "Plate III", slot: "plating", tech: 9, blueprint: "Plate III Blueprint",
     blueprint_price: {credits: 100}, cost: {metal: 10, titanium: 1}},
    {key: "rail1", label: "Rail I", slot: "weapon", cost: {credits: 7}},
    {key: "rail2", label: "Rail II", slot: "weapon", upgrade_from: "rail1", upgrade_qty: 2, cost: {metal: 10}},
  ],
};
const LABELS = {silicon: "Silicon", space_corn: "Space Corn", promethium: "Promethium"};

let passed = 0;
const failures = [];
function test(name, fn) {
  try { fn(); passed += 1; } catch (e) { failures.push(name + "\n    " + String(e && e.message || e).split("\n").join("\n    ")); }
}
const bodiesOf = systems => core.listBodies(DATA, systems);
// objects made inside the vm sandbox have that realm's prototypes: compare their plain values
const plain = x => JSON.parse(JSON.stringify(x));
const text = html => html.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/\s+/g, " ");
function planWith(builds, extra) {
  const plan = core.defaultPlan(CATALOG);
  Object.keys(builds).forEach(k => { plan.bodies["Sys A|" + k] = Object.assign({build: true, stations: 3, profile: "", skip: []}, builds[k]); });
  return Object.assign(plan, extra || {});
}

// ----------------------------------------------------------------- planning maths

test("a planet's extractors are slots x stations (10 silicon slots x 3 = 30)", () => {
  const r = core.compute(planWith({Alpha: {}}), bodiesOf(["Sys A"]), CATALOG);
  assert.strictEqual(r.totals.extractors.si_ind.count, 30);
  assert.strictEqual(r.totals.extractors.corn_ind.count, 15);
  assert.strictEqual(r.totals.stations, 3);
  assert.strictEqual(r.totals.extractorTotal, 45);
});

test("a moon holds exactly one station whatever the plan says", () => {
  const r = core.compute(planWith({"Alpha I": {stations: 3}}), bodiesOf(["Sys A"]), CATALOG);
  assert.strictEqual(r.rows[0].stations, 1);
  assert.strictEqual(r.totals.extractors.si_ind.count, 4);
});

test("fewer stations means proportionally fewer extractors", () => {
  const r = core.compute(planWith({Alpha: {stations: 2}}), bodiesOf(["Sys A"]), CATALOG);
  assert.strictEqual(r.totals.extractors.si_ind.count, 20);
  assert.strictEqual(r.totals.stations, 2);
});

test("a planet cannot be given more stations than it can hold", () => {
  const r = core.compute(planWith({Alpha: {stations: 99}}), bodiesOf(["Sys A"]), CATALOG);
  assert.strictEqual(r.rows[0].stations, 3);
});

test("bodies that are not ticked contribute nothing", () => {
  const r = core.compute(planWith({Alpha: {build: false}}), bodiesOf(["Sys A"]), CATALOG);
  assert.strictEqual(r.totals.bodies, 0);
  assert.strictEqual(r.totals.extractorTotal, 0);
});

test("bodies in systems that are not listed are ignored even if ticked", () => {
  const r = core.compute(planWith({Alpha: {}}), bodiesOf(["Sys B"]), CATALOG);
  assert.strictEqual(r.totals.bodies, 0);
});

test("skipping a resource removes just that resource", () => {
  const r = core.compute(planWith({Alpha: {skip: ["space_corn"]}}), bodiesOf(["Sys A"]), CATALOG);
  assert.strictEqual(r.totals.extractors.corn_ind, undefined);
  assert.strictEqual(r.totals.extractors.si_ind.count, 30);
  assert.deepStrictEqual(r.rows[0].skipped, ["space_corn"]);
});

test("the profile's tier picks the extractor", () => {
  const plan = planWith({Alpha: {}});
  plan.profiles.Standard.tier = "basic";
  const r = core.compute(plan, bodiesOf(["Sys A"]), CATALOG);
  assert.strictEqual(r.totals.extractors.si_basic.count, 30);
  assert.strictEqual(r.totals.extractors.corn_basic.count, 15);
});

test("a resource with one extractor uses it at any tier", () => {
  const plan = planWith({Beta: {}});
  plan.profiles.Standard.tier = "basic";
  assert.strictEqual(core.compute(plan, bodiesOf(["Sys A"]), CATALOG).totals.extractors.prom_one.count, 18);
});

test("a tier a resource lacks falls back to the nearest lower one", () => {
  assert.strictEqual(core.pickExtractor(CATALOG, "space_corn", "advanced").key, "corn_adv");
  const cat = JSON.parse(JSON.stringify(CATALOG));
  cat.extractors.space_corn = cat.extractors.space_corn.filter(e => e.tier !== "advanced");
  assert.strictEqual(core.pickExtractor(cat, "space_corn", "advanced").key, "corn_basic");
  assert.strictEqual(core.pickExtractor(cat, "nothing_here", "basic"), null);
});

test("the base kit follows planet vs moon, and station slots add up", () => {
  const r = core.compute(planWith({Alpha: {}, "Alpha I": {}}), bodiesOf(["Sys A"]), CATALOG);
  assert.strictEqual(r.totals.kits.kit_big.count, 3);
  assert.strictEqual(r.totals.kits.kit_moon.count, 1);
  assert.strictEqual(r.totals.stationSlots, 3 * 9 + 1 * 2);
});

test("gear is per station: qty 2 on 3 stations is 6", () => {
  const plan = planWith({Alpha: {}});
  plan.profiles.Standard.gear = [{item: "shield1", qty: 2}];
  const r = core.compute(plan, bodiesOf(["Sys A"]), CATALOG);
  assert.strictEqual(r.totals.gear.shield1.count, 6);
});

test("materials add up kits, extractors and gear", () => {
  const plan = planWith({Alpha: {}});
  plan.profiles.Standard.gear = [{item: "shield1", qty: 2}];
  const m = core.compute(plan, bodiesOf(["Sys A"]), CATALOG).totals.materials;
  // kit 3 x 100 + corn 15 x 5 + silicon 30 x 3 + shield 6 x 10
  assert.strictEqual(m.metal, 300 + 75 + 90 + 60);
  assert.strictEqual(m.microchips, 30);
});

test("an item with no cost entered is flagged, not treated as free", () => {
  const plan = planWith({Alpha: {}});
  plan.profiles.Standard.gear = [{item: "weapon1", qty: 1}];
  const t = core.compute(plan, bodiesOf(["Sys A"]), CATALOG).totals;
  assert.ok(t.unpriced.weapon1);
  assert.strictEqual(t.materials.weapon1, undefined);
});

test("a cost of {} is free, not 'not entered'", () => {
  const cat = JSON.parse(JSON.stringify(CATALOG));
  cat.gear[1].cost = {};
  const plan = core.defaultPlan(cat);
  plan.bodies["Sys A|Alpha"] = {build: true, stations: 3, profile: "", skip: []};
  plan.profiles.Standard.gear = [{item: "weapon1", qty: 1}];
  assert.strictEqual(core.compute(plan, bodiesOf(["Sys A"]), cat).totals.unpriced.weapon1, undefined);
});

test("two profiles on two bodies are costed separately", () => {
  const plan = planWith({Alpha: {profile: "Cheap"}, Beta: {}});
  plan.profiles.Cheap = {planetKit: "kit_small", moonKit: "kit_moon", tier: "basic", gear: []};
  const r = core.compute(plan, bodiesOf(["Sys A"]), CATALOG);
  assert.strictEqual(r.totals.kits.kit_small.count, 3);
  assert.strictEqual(r.totals.kits.kit_big.count, 3);
  assert.strictEqual(r.totals.extractors.si_basic.count, 30);
  assert.strictEqual(r.totals.extractors.prom_one.count, 18);
});

test("an unknown profile name falls back to the default loadout", () => {
  const r = core.compute(planWith({Alpha: {profile: "Deleted"}}), bodiesOf(["Sys A"]), CATALOG);
  assert.strictEqual(r.rows[0].profile, "Standard");
});

test("gear missing from the catalogue is reported, not lost or crashed on", () => {
  const plan = planWith({Alpha: {}});
  plan.profiles.Standard.gear = [{item: "retired_item", qty: 1}];
  const t = core.compute(plan, bodiesOf(["Sys A"]), CATALOG).totals;
  assert.ok(t.unknownItems.retired_item);
});

test("a resource with no extractor in the catalogue is reported", () => {
  const cat = JSON.parse(JSON.stringify(CATALOG));
  delete cat.extractors.promethium;
  const plan = core.defaultPlan(cat);
  plan.bodies["Sys A|Beta"] = {build: true, stations: 3, profile: "", skip: []};
  const t = core.compute(plan, bodiesOf(["Sys A"]), cat).totals;
  assert.ok(t.noExtractor.promethium);
  assert.strictEqual(t.extractorTotal, 0);
});

test("the default loadout is the highest-tech planet base at the highest-tier extractor", () => {
  const p = core.defaultProfile(CATALOG);
  assert.strictEqual(p.planetKit, "kit_big");
  assert.strictEqual(p.moonKit, "kit_moon");
  assert.strictEqual(p.unattachedKit, "ukit_big");
  assert.strictEqual(p.tier, "industrial");
});

// ----------------------------------------------------------------- gear upgrade chains

function withGear(items, catalog) {
  const plan = planWith({Alpha: {}});
  plan.profiles.Standard.gear = items;
  return core.compute(plan, bodiesOf(["Sys A"]), catalog || CATALOG).totals;
}

test("a Mk III costs its own step plus the Mk II step plus the bought Mk I", () => {
  const t = withGear([{item: "gun3", qty: 1}]);       // 3 stations
  assert.strictEqual(t.gear.gun3.count, 3);
  assert.strictEqual(t.gear.gun2, undefined, "only the item you asked for is gear; the Mk II is a step on the way");
  assert.strictEqual(t.upgrades.gun3.count, 3);
  assert.strictEqual(t.upgrades.gun2.count, 3);
  assert.strictEqual(t.bases.gun1.count, 3);
  // metal: 3 x (2500 + 200); manhours: 3 x (1000 + 100); gold only on the Mk III step
  assert.strictEqual(t.materials.metal - kitAndExtractorMetal(), 3 * 2700);
  assert.strictEqual(t.materials.manhours, 3 * 1100);
  assert.strictEqual(t.materials.gold, 300);
});
// metal the kits and extractors of the default plan already need (Alpha on 3 stations)
function kitAndExtractorMetal() { return 3 * 100 + 15 * 5 + 30 * 3; }

test("the item at the bottom of a chain is flagged when its price is not entered", () => {
  const t = withGear([{item: "gun3", qty: 1}]);
  assert.ok(t.unpriced.gun1);
  assert.strictEqual(t.unpriced.gun2, undefined);
  assert.strictEqual(t.unpriced.gun3, undefined);
});

test("once the base item has a price it is counted in the materials", () => {
  const cat = JSON.parse(JSON.stringify(CATALOG));
  cat.gear.filter(g => g.key === "gun1")[0].cost = {credits: 50};
  const t = withGear([{item: "gun3", qty: 1}], cat);
  assert.strictEqual(t.materials.credits, 150);
  assert.strictEqual(t.unpriced.gun1, undefined);
});

test("an upgrade that uses up two of the item below needs twice as many of those", () => {
  const t = withGear([{item: "rail2", qty: 1}]);
  assert.strictEqual(t.upgrades.rail2.count, 3);
  assert.strictEqual(t.bases.rail1.count, 6);
  assert.strictEqual(t.materials.credits, 6 * 7);
});

test("Mk II and Mk III of the same family in one loadout share the chain's totals", () => {
  const t = withGear([{item: "gun2", qty: 1}, {item: "gun3", qty: 1}]);
  assert.strictEqual(t.upgrades.gun2.count, 6);        // one as a gun2, one as the step under a gun3
  assert.strictEqual(t.upgrades.gun3.count, 3);
  assert.strictEqual(t.bases.gun1.count, 6);
  assert.strictEqual(t.gear.gun2.count, 3);
  assert.strictEqual(t.gear.gun3.count, 3);
});

test("a Mk I on its own is just something to acquire", () => {
  const t = withGear([{item: "gun1", qty: 2}]);
  assert.deepStrictEqual(Object.keys(t.upgrades), []);
  assert.strictEqual(t.bases.gun1.count, 6);
});

test("blueprints are listed with their upgrade, never as something consumed", () => {
  const t = withGear([{item: "gun3", qty: 1}]);
  assert.strictEqual(t.upgrades.gun3.blueprint, "Gun III Blueprint");
  assert.strictEqual(t.materials["Gun III Blueprint"], undefined);
});

test("a chain pointing at an item that is not in the catalogue is reported, not fatal", () => {
  const cat = JSON.parse(JSON.stringify(CATALOG));
  cat.gear.filter(g => g.key === "gun2")[0].upgrade_from = "vanished";
  const t = withGear([{item: "gun3", qty: 1}], cat);
  assert.ok(t.unknownItems.vanished);
});

test("a looping chain cannot hang the page", () => {
  const cat = JSON.parse(JSON.stringify(CATALOG));
  cat.gear.filter(g => g.key === "gun1")[0].upgrade_from = "gun3";     // gun1 -> gun3 -> gun2 -> gun1 ...
  const t = withGear([{item: "gun3", qty: 1}], cat);
  assert.ok(t.upgrades.gun3.count >= 3);
});

test("the summary text lists upgrades and the base gear to acquire", () => {
  const plan = planWith({Alpha: {}});
  plan.profiles.Standard.gear = [{item: "gun3", qty: 1}];
  const text = core.summaryText(core.compute(plan, bodiesOf(["Sys A"]), CATALOG), LABELS);
  assert.ok(/3 x Gun III \[Gun III Blueprint\]/.test(text), text);
  assert.ok(/3 x Gun I \(bought from an AI station\)/.test(text), text);
  assert.ok(/Manhours/.test(text) || /manhours/i.test(text), text);
});

// ----------------------------------------------------------------- defence (unattached) stations

function defencePlan(groups, extra) {
  const plan = core.defaultPlan(CATALOG);
  plan.systems = ["Sys A"];
  plan.defence = groups;
  return Object.assign(plan, extra || {});
}
function defend(plan, builds) {
  Object.keys(builds || {}).forEach(k => { plan.bodies["Sys A|" + k] = Object.assign({build: true, stations: 3, profile: "", skip: []}, builds[k]); });
  return core.compute(plan, bodiesOf(["Sys A"]), CATALOG);
}

test("defence stations take their loadout's unattached base, with no extractors", () => {
  const plan = defencePlan([{system: "Sys A", count: 2, profile: "Guard"}]);
  plan.profiles.Guard = {planetKit: "kit_big", moonKit: "kit_moon", unattachedKit: "ukit_small", tier: "industrial", gear: []};
  const t = defend(plan).totals;
  assert.strictEqual(t.stations, 2);
  assert.strictEqual(t.defenceStations, 2);
  assert.strictEqual(t.kits.ukit_small.count, 2);
  assert.strictEqual(t.stationSlots, 8);
  assert.strictEqual(t.materials.metal, 10);
  assert.strictEqual(t.extractorTotal, 0);
  assert.deepStrictEqual(plain(Object.keys(t.production)), []);
  assert.strictEqual(t.bodies, 0, "a defence station is not a planet or moon");
  assert.strictEqual(t.systemCount, 1);
});

test("gear is per station on defence stations too", () => {
  const plan = defencePlan([{system: "Sys A", count: 3, profile: ""}]);
  plan.profiles.Standard.gear = [{item: "shield1", qty: 2}, {item: "gun2", qty: 1}];
  const t = defend(plan).totals;
  assert.strictEqual(t.gear.shield1.count, 6);
  assert.strictEqual(t.gear.gun2.count, 3);
  assert.strictEqual(t.bases.gun1.count, 3, "the chain under a Gun II is bought once per station");
  assert.strictEqual(t.materials.manhours, 3 * 100);
});

test("a defence group in a system that is not ticked is ignored, and comes back when it is", () => {
  const plan = defencePlan([{system: "Sys A", count: 2, profile: ""}]);
  plan.systems = [];
  assert.strictEqual(defend(plan).totals.defenceStations, 0);
  plan.systems = ["Sys A"];
  assert.strictEqual(defend(plan).totals.defenceStations, 2);
});

test("two groups can use different loadouts, and bodies and defence add up together", () => {
  const plan = defencePlan([{system: "Sys A", count: 1, profile: "Guard"}, {system: "Sys A", count: 2, profile: ""}]);
  plan.profiles.Guard = {planetKit: "kit_big", moonKit: "kit_moon", unattachedKit: "ukit_small", tier: "industrial", gear: []};
  const r = defend(plan, {Alpha: {}});
  assert.strictEqual(r.totals.stations, 3 + 1 + 2);
  assert.strictEqual(r.totals.defenceStations, 3);
  assert.strictEqual(r.totals.kits.kit_big.count, 3);
  assert.strictEqual(r.totals.kits.ukit_small.count, 1);
  assert.strictEqual(r.totals.kits.ukit_big.count, 2);
  assert.strictEqual(r.defenceRows.length, 2);
});

test("an unattached base with no station-slot cost entered is reported, not counted as 0 slots", () => {
  const t = defend(defencePlan([{system: "Sys A", count: 2, profile: ""}])).totals;     // ukit_big: station_slots null
  assert.ok(t.unknownSlots.ukit_big);
  assert.strictEqual(t.stationSlots, 0);
  assert.strictEqual(t.materials.credits, 100);
});

test("an unknown loadout name falls back to the default one", () => {
  const r = defend(defencePlan([{system: "Sys A", count: 1, profile: "Gone"}]));
  assert.strictEqual(r.defenceRows[0].profile, "Standard");
});

test("saved defence groups are cleaned up: junk dropped, counts clamped", () => {
  const p = core.normalisePlan({defence: [{system: "Sys A", count: 500, profile: "x"}, {system: "Sys A", count: "abc"},
                                          {count: 2}, null, 7, {system: "", count: 1}]}, CATALOG);
  assert.deepStrictEqual(plain(p.defence), [{system: "Sys A", count: 99, profile: "x"}, {system: "Sys A", count: 1, profile: ""}]);
  assert.deepStrictEqual(plain(core.normalisePlan({defence: "nope"}, CATALOG).defence), []);
  assert.deepStrictEqual(plain(core.normalisePlan(null, CATALOG).defence), []);
});

test("a plan saved before defence stations existed still loads", () => {
  const old = {profiles: {Standard: {planetKit: "kit_big", moonKit: "kit_moon", tier: "industrial", gear: []}},
               defaultProfile: "Standard", systems: ["Sys A"], bodies: {}};
  const p = core.normalisePlan(old, CATALOG);
  assert.strictEqual(p.profiles.Standard.unattachedKit, "ukit_big");
  assert.deepStrictEqual(plain(p.defence), []);
});

test("the summary text lists defence stations", () => {
  const text = core.summaryText(defend(defencePlan([{system: "Sys A", count: 2, profile: ""}])), LABELS);
  assert.ok(/2 stations \(2 defence\)/.test(text), text);
  assert.ok(/2 x Unattached Big Kit in Sys A \[Standard\]/.test(text), text);
});

test("defence stations can be added, counted, given a loadout and removed in the UI", () => {
  const {P, byId} = makeEnv();
  P.act("sys-toggle", {system: "Sys A", on: true});
  assert.ok(/None planned/.test(byId.plBodies.innerHTML));
  P.act("def-add", {system: "Sys A"});
  P.act("def-count", {idx: 0, value: "4"});
  assert.strictEqual(P.compute().totals.defenceStations, 4);
  assert.ok(/Defence stations/.test(byId.plBodies.innerHTML) && /def-remove/.test(byId.plBodies.innerHTML));
  P.act("prof-new", {name: "Guard"});
  P.act("prof-freekit", {value: "ukit_small"});
  P.act("def-profile", {idx: 0, value: "Guard"});
  assert.strictEqual(P.compute().defenceRows[0].kit, "Unattached Small Kit");
  assert.strictEqual(P.act("def-profile", {idx: 0, value: "NoSuch"}), true);          // ignored, not a crash
  assert.strictEqual(P.compute().defenceRows[0].profile, "Guard");
  P.act("def-remove", {idx: 0});
  assert.strictEqual(P.compute().totals.defenceStations, 0);
});

test("a defence group only shows under its own system", () => {
  const {P, byId} = makeEnv();
  P.act("sys-toggle", {system: "Sys A", on: true});
  P.act("sys-toggle", {system: "Sys B", on: true});
  P.act("def-add", {system: "Sys B"});
  const html = byId.plBodies.innerHTML;
  const a = html.slice(html.indexOf("Sys A"), html.indexOf("Sys B"));
  assert.ok(/None planned/.test(a), "Sys A should have no defence group");
  assert.ok(/def-count/.test(html.slice(html.indexOf("Sys B"))));
});

test("renaming or deleting a loadout carries the defence groups that use it", () => {
  const {P} = makeEnv();
  P.act("sys-toggle", {system: "Sys A", on: true});
  P.act("prof-new", {name: "Guard"});
  P.act("def-add", {system: "Sys A"});                      // takes the loadout being edited
  P.act("prof-rename", {name: "Watch"});
  assert.strictEqual(P.compute().defenceRows[0].profile, "Watch");
  P.act("prof-delete");
  assert.strictEqual(P.compute().defenceRows[0].profile, "Standard");
});

test("'use this loadout for every planned body' leaves defence groups alone", () => {
  const {P} = makeEnv();
  P.act("sys-toggle", {system: "Sys A", on: true});
  P.act("body-build", {body: "Sys A|Alpha", on: true});
  P.act("def-add", {system: "Sys A"});
  P.act("prof-new", {name: "Other"});
  P.act("prof-apply");
  assert.strictEqual(P.compute().rows[0].profile, "Other");
  assert.strictEqual(P.compute().defenceRows[0].profile, "Standard");
});

test("a plan of only defence stations still shows results, with a Defence tile and the slot warning", () => {
  const {P, byId} = makeEnv();
  P.act("sys-toggle", {system: "Sys A", on: true});
  P.act("def-add", {system: "Sys A"});
  const shown = text(byId.plResults.innerHTML);
  assert.ok(!/Nothing planned yet/.test(shown), shown);
  assert.ok(/1 Defence/.test(shown) && /Unattached Big Kit/.test(shown), shown);
  assert.ok(/Defence stations \(unattached\)/.test(shown) && /no extractors/.test(shown), shown);
  assert.ok(/Station-slot cost not entered for: Unattached Big Kit/.test(shown), shown);
});

test("defence stations survive a save and reload", () => {
  const first = makeEnv();
  first.P.act("sys-toggle", {system: "Sys A", on: true});
  first.P.act("def-add", {system: "Sys A"});
  first.P.act("def-count", {idx: 0, value: "3"});
  const saved = first.store["se_scan_planner_v1"];
  const again = makeEnv({localStorage: {getItem: () => saved, setItem() {}}});
  assert.strictEqual(again.P.compute().totals.defenceStations, 3);
});

// ----------------------------------------------------------------- production per day

test("daily output is extractors x the per-extractor rate (30 x 300 silicon, 15 x 300 corn)", () => {
  const p = core.compute(planWith({Alpha: {}}), bodiesOf(["Sys A"]), CATALOG).totals.production;
  assert.strictEqual(p.silicon.perDay, 9000);
  assert.strictEqual(p.silicon.extractors, 30);
  assert.strictEqual(p.space_corn.perDay, 4500);
});

test("a body's own row carries its output per resource", () => {
  const row = core.compute(planWith({Alpha: {}}), bodiesOf(["Sys A"]), CATALOG).rows[0];
  assert.deepStrictEqual(plain(row.extractors.map(x => [x.resource, x.count, x.perDay])),
                         [["silicon", 30, 9000], ["space_corn", 15, 4500]]);
});

test("output follows the extractor tier chosen", () => {
  const plan = planWith({Alpha: {}});
  plan.profiles.Standard.tier = "basic";
  const p = core.compute(plan, bodiesOf(["Sys A"]), CATALOG).totals.production;
  assert.strictEqual(p.silicon.perDay, 3000);
});

test("output adds up across bodies and a moon counts one station", () => {
  const t = core.compute(planWith({Alpha: {}, "Alpha I": {}}), bodiesOf(["Sys A"]), CATALOG).totals;
  assert.strictEqual(t.production.silicon.perDay, (10 * 3 + 4 * 1) * 300);
});

test("a skipped resource produces nothing", () => {
  const t = core.compute(planWith({Alpha: {skip: ["space_corn"]}}), bodiesOf(["Sys A"]), CATALOG).totals;
  assert.strictEqual(t.production.space_corn, undefined);
});

test("fractional rates keep their decimals (18 promethium extractors x 8.64)", () => {
  const t = core.compute(planWith({Beta: {}}), bodiesOf(["Sys A"]), CATALOG).totals;
  assert.ok(Math.abs(t.production.promethium.perDay - 155.52) < 1e-9);
  assert.strictEqual(core.fmtRate(t.production.promethium.perDay), "155.52");
  assert.strictEqual(core.fmtRate(25920), "25,920");
});

test("an extractor with no rate entered is counted as unrated, never as zero output", () => {
  const plan = planWith({Alpha: {}});
  plan.profiles.Standard.tier = "advanced";            // silicon has no advanced entry -> basic; corn advanced has no rate
  const p = core.compute(plan, bodiesOf(["Sys A"]), CATALOG).totals.production;
  assert.strictEqual(p.space_corn.unrated, 15);
  assert.strictEqual(p.space_corn.perDay, 0);
  assert.strictEqual(p.silicon.unrated, 0);
});

test("an extractor entry with no per_day field at all is treated as unrated", () => {
  const cat = JSON.parse(JSON.stringify(CATALOG));
  delete cat.extractors.silicon[1].per_day;
  const t = core.compute(planWith({Alpha: {}}), bodiesOf(["Sys A"]), cat).totals;
  assert.strictEqual(t.production.silicon.unrated, 30);
});

test("an assumed rate is reported so the page can say so", () => {
  const plan = planWith({Alpha: {}});
  plan.profiles.Standard.tier = "basic";
  const t = core.compute(plan, bodiesOf(["Sys A"]), CATALOG).totals;
  assert.deepStrictEqual(plain(Object.keys(t.assumedRates)), ["si_basic"]);
  assert.deepStrictEqual(plain(Object.keys(core.compute(planWith({Alpha: {}}), bodiesOf(["Sys A"]), CATALOG).totals.assumedRates)), []);
});

test("the summary text lists production per day", () => {
  const text = core.summaryText(core.compute(planWith({Alpha: {}, Beta: {}}), bodiesOf(["Sys A"]), CATALOG), LABELS);
  assert.ok(/Production per day/.test(text), text);
  assert.ok(/Silicon: 9,000/.test(text), text);
  assert.ok(/Promethium: 155.52/.test(text), text);
});

test("the results panel shows production per day", () => {
  const {P, byId} = makeEnv();
  P.act("sys-toggle", {system: "Sys A", on: true});
  P.act("body-build", {body: "Sys A|Alpha", on: true});
  const shown = text(byId.plResults.innerHTML);
  assert.ok(/Production per day/.test(shown) && /Silicon 30 9,000/.test(shown), shown);
  assert.ok(/Space Corn 15 4,500/.test(shown), shown);
  assert.ok(/30 → 9,000\/day/.test(shown) || /= 30 → 9,000\/day/.test(shown), shown);
});

test("an unrated tier is shown as 'rate not entered', not as 0 a day", () => {
  const {P, byId} = makeEnv();
  P.act("sys-toggle", {system: "Sys A", on: true});
  P.act("body-build", {body: "Sys A|Alpha", on: true});
  P.act("prof-tier", {value: "advanced"});
  assert.ok(/Space Corn 15 rate not entered/.test(text(byId.plResults.innerHTML)), text(byId.plResults.innerHTML));
});

test("assumed rates are called out on the page", () => {
  const {P, byId} = makeEnv();
  P.act("sys-toggle", {system: "Sys A", on: true});
  P.act("body-build", {body: "Sys A|Alpha", on: true});
  P.act("prof-tier", {value: "basic"});
  assert.ok(/Rate assumed, not yet seen on a real item: Silicon Drill/.test(text(byId.plResults.innerHTML)));
});

// ----------------------------------------------------------------- items built from scratch, and blueprints

test("an item built from its own blueprint costs everything it takes and needs nothing underneath", () => {
  const t = withGear([{item: "plate3", qty: 1}]);       // 3 stations
  assert.strictEqual(t.built.plate3.count, 3);
  assert.strictEqual(t.gear.plate3.count, 3);
  assert.deepStrictEqual(plain(Object.keys(t.upgrades)), []);
  assert.strictEqual(t.bases.plate3, undefined, "it is built, not bought");
  assert.strictEqual(t.materials.titanium, 3);
  assert.strictEqual(t.materials.metal - kitAndExtractorMetal(), 30);
});

test("every blueprint used is listed once, with how often it is used", () => {
  const t = withGear([{item: "gun3", qty: 1}, {item: "plate3", qty: 2}]);
  assert.deepStrictEqual(plain(Object.keys(t.blueprints).sort()), ["Gun II Blueprint", "Gun III Blueprint", "Plate III Blueprint"]);
  assert.strictEqual(t.blueprints["Plate III Blueprint"].uses, 6);
  assert.strictEqual(t.blueprints["Gun III Blueprint"].uses, 3);
  assert.deepStrictEqual(plain(t.blueprints["Plate III Blueprint"].price), {credits: 100});
  assert.strictEqual(t.blueprints["Gun III Blueprint"].price, null);
});

function withBlueprintOption(on, items) {
  const plan = planWith({Alpha: {}});
  plan.profiles.Standard.gear = items;
  plan.countBlueprints = on;
  return core.compute(plan, bodiesOf(["Sys A"]), CATALOG).totals;
}

test("blueprint purchases are left out of the totals unless asked for", () => {
  const t = withBlueprintOption(false, [{item: "plate3", qty: 1}]);
  assert.strictEqual(t.materials.credits, undefined);
  assert.strictEqual(t.unpriced["blueprint:Plate III Blueprint"], undefined);
});

test("when asked for, a blueprint is bought ONCE however many times it is used", () => {
  const t = withBlueprintOption(true, [{item: "plate3", qty: 5}]);      // 15 plates
  assert.strictEqual(t.materials.credits, 100);
});

test("when asked for, a blueprint with no price entered is flagged, not counted as free", () => {
  const t = withBlueprintOption(true, [{item: "gun2", qty: 1}]);
  assert.ok(t.unpriced["blueprint:Gun II Blueprint"]);
});

test("the blueprint option is part of a saved plan", () => {
  const plan = core.defaultPlan(CATALOG);
  assert.strictEqual(plan.countBlueprints, false);
  plan.countBlueprints = true;
  assert.strictEqual(core.normalisePlan(JSON.parse(JSON.stringify(plan)), CATALOG).countBlueprints, true);
  assert.strictEqual(core.normalisePlan({countBlueprints: "yes"}, CATALOG).countBlueprints, false);
});

test("the summary text lists built items and the blueprints with their prices", () => {
  const plan = planWith({Alpha: {}});
  plan.profiles.Standard.gear = [{item: "plate3", qty: 1}, {item: "gun2", qty: 1}];
  const text = core.summaryText(core.compute(plan, bodiesOf(["Sys A"]), CATALOG), LABELS);
  assert.ok(/3 x Plate III \[Plate III Blueprint\]/.test(text), text);
  assert.ok(/Plate III Blueprint - 100 Credits/.test(text), text);
  assert.ok(/Gun II Blueprint - price not entered/.test(text), text);
});

test("the results show crafted items with how they are made, and the blueprints to own", () => {
  const {P, byId} = makeEnv();
  P.act("sys-toggle", {system: "Sys A", on: true});
  P.act("body-build", {body: "Sys A|Alpha", on: true});
  P.act("gear-add", {value: "gun2"});
  P.act("gear-add", {value: "plate3"});
  const shown = text(byId.plResults.innerHTML);
  assert.ok(/Items to craft/.test(shown) && /upgrade of Gun I/.test(shown) && /from scratch/.test(shown), shown);
  assert.ok(/Blueprints to own/.test(shown) && /Plate III Blueprint/.test(shown) && /100 Credits/.test(shown), shown);
  assert.ok(/Base gear to acquire/.test(shown) && /Gun I/.test(shown), shown);
});

test("ticking 'include blueprint purchases' changes the totals", () => {
  const {P} = makeEnv();
  P.act("sys-toggle", {system: "Sys A", on: true});
  P.act("body-build", {body: "Sys A|Alpha", on: true});
  P.act("gear-add", {value: "plate3"});
  assert.strictEqual(P.compute().totals.materials.credits, undefined);
  P.act("count-blueprints", {on: true});
  assert.strictEqual(P.compute().totals.materials.credits, 100);
  P.act("count-blueprints", {on: false});
  assert.strictEqual(P.compute().totals.materials.credits, undefined);
});

// ----------------------------------------------------------------- bodies and saved plans

test("planets come in name order, each followed by its moons", () => {
  assert.deepStrictEqual(bodiesOf(["Sys A"]).map(b => b.name), ["Alpha", "Alpha I", "Beta"]);
});

test("zero-slot resources are not offered", () => {
  assert.deepStrictEqual(Object.keys(bodiesOf(["Sys A"])[0].perStation).sort(), ["silicon", "space_corn"]);
});

test("a moon's maximum is one station, a planet's three", () => {
  const b = bodiesOf(["Sys A"]);
  assert.strictEqual(b[0].maxStations, 3);
  assert.strictEqual(b[1].maxStations, 1);
  assert.strictEqual(b[1].isMoon, true);
});

test("junk saved state becomes a usable default plan", () => {
  [null, undefined, 5, "text", [], {profiles: 3, bodies: "x", systems: {}}].forEach(junk => {
    const p = core.normalisePlan(junk, CATALOG);
    assert.ok(p.profiles[p.defaultProfile]);
    assert.deepStrictEqual(p.systems, []);
  });
});

test("a saved plan survives normalising unchanged, and bad numbers are clamped", () => {
  const plan = planWith({Alpha: {stations: 2, skip: ["space_corn"]}}, {systems: ["Sys A"]});
  plan.profiles.Standard.gear = [{item: "shield1", qty: 4}];
  assert.deepStrictEqual(core.normalisePlan(JSON.parse(JSON.stringify(plan)), CATALOG), plan);
  const messy = core.normalisePlan({bodies: {k: {build: 1, stations: 99, skip: ["a", 7]}},
                                    profiles: {P: {gear: [{item: "shield1", qty: -5}, {nope: 1}]}}}, CATALOG);
  assert.strictEqual(messy.bodies.k.stations, 3);
  assert.deepStrictEqual(messy.bodies.k.skip, ["a"]);
  assert.strictEqual(messy.profiles.P.gear[0].qty, 1);
  assert.strictEqual(messy.profiles.P.gear.length, 1);
});

test("the summary text lists stations, extractors, gear and what is unpriced", () => {
  const plan = planWith({Alpha: {}});
  plan.profiles.Standard.gear = [{item: "weapon1", qty: 1}];
  const text = core.summaryText(core.compute(plan, bodiesOf(["Sys A"]), CATALOG), LABELS);
  assert.ok(/3 stations/.test(text));
  assert.ok(/30 x Industrial Silicon Drill/.test(text));
  assert.ok(/3 x Weapon I/.test(text));
  assert.ok(/Cost not entered yet for: .*Weapon I/.test(text));
});

// ----------------------------------------------------------------- the UI, through its actions

function makeEnv(extra) {
  const byId = {};
  const store = {};
  const mk = tag => ({tag, children: [], innerHTML: "", textContent: "", value: "", checked: false, style: {}, dataset: {},
    classList: {add() {}, remove() {}, toggle() {}, contains() { return false; }}, addEventListener() {},
    appendChild(c) { return c; }, remove() {}, click() {}});
  const document = {getElementById: id => byId[id] || (byId[id] = mk(id)), createElement: mk, body: mk("body")};
  const ctx = Object.assign({document, console, JSON, Object, Array, Set, Math, String, Number, parseInt, isNaN,
    setTimeout() {}, DATA, GALAXY, PLANNER_CATALOG: CATALOG,
    RESOURCE_COLS: Object.keys(LABELS).map(k => ({key: k, label: LABELS[k]})),
    localStorage: {getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }},
    prompt: () => null, confirm: () => true, alert() {}, navigator: {}, Blob: function () {}, URL: {}, FileReader: function () {}},
    extra || {});
  ctx.window = ctx;
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "scan_planner.js"), "utf8"), ctx, {filename: "scan_planner.js"});
  return {ctx, byId, store, P: ctx.PLANNER};
}

test("ticking a system lists its bodies; ticking a planet fills in the totals", () => {
  const {P, byId} = makeEnv();
  P.act("sys-toggle", {system: "Sys A", on: true});
  assert.ok(/Alpha/.test(byId.plBodies.innerHTML) && /Beta/.test(byId.plBodies.innerHTML));
  assert.ok(!/Gamma/.test(byId.plBodies.innerHTML), "an unticked system's bodies must not be listed");
  P.act("body-build", {body: "Sys A|Alpha", on: true});
  assert.ok(/\b30\b/.test(text(byId.plResults.innerHTML)), text(byId.plResults.innerHTML));
  assert.strictEqual(P.compute().totals.stations, 3);
});

test("'All planets' ticks planets but not moons", () => {
  const {P} = makeEnv();
  P.act("sys-toggle", {system: "Sys A", on: true});
  P.act("sys-all-planets", {system: "Sys A"});
  const r = P.compute();
  assert.deepStrictEqual(plain(r.rows.map(x => x.name)), ["Alpha", "Beta"]);
  P.act("sys-none", {system: "Sys A"});
  assert.strictEqual(P.compute().totals.bodies, 0);
});

test("clicking a resource chip skips it, and clicking again restores it", () => {
  const {P} = makeEnv();
  P.act("sys-toggle", {system: "Sys A", on: true});
  P.act("body-build", {body: "Sys A|Alpha", on: true});
  P.act("chip", {body: "Sys A|Alpha", res: "space_corn"});
  assert.strictEqual(P.compute().totals.extractors.corn_ind, undefined);
  P.act("chip", {body: "Sys A|Alpha", res: "space_corn"});
  assert.strictEqual(P.compute().totals.extractors.corn_ind.count, 15);
});

test("changing a planet's station count changes the totals", () => {
  const {P} = makeEnv();
  P.act("sys-toggle", {system: "Sys A", on: true});
  P.act("body-build", {body: "Sys A|Alpha", on: true});
  P.act("body-stations", {body: "Sys A|Alpha", value: "1"});
  assert.strictEqual(P.compute().totals.extractors.si_ind.count, 10);
});

test("editing a loadout: add gear, change quantity, remove it", () => {
  const {P} = makeEnv();
  P.act("sys-toggle", {system: "Sys A", on: true});
  P.act("body-build", {body: "Sys A|Alpha", on: true});
  P.act("gear-add", {value: "shield1"});
  P.act("gear-qty", {idx: 0, value: "3"});
  assert.strictEqual(P.compute().totals.gear.shield1.count, 9);
  P.act("gear-item", {idx: 0, value: "weapon1"});
  assert.strictEqual(P.compute().totals.gear.weapon1.count, 9);
  P.act("gear-remove", {idx: 0});
  assert.deepStrictEqual(plain(P.compute().totals.gear), {});
});

test("a new loadout can be made, used by a body, renamed and deleted", () => {
  const {P} = makeEnv();
  P.act("sys-toggle", {system: "Sys A", on: true});
  assert.strictEqual(P.act("prof-new", {name: ""}), false);
  assert.strictEqual(P.act("prof-new", {name: "Standard"}), false, "an existing name is refused");
  P.act("prof-new", {name: "T9 full"});
  assert.strictEqual(P.ui.profile, "T9 full");
  P.act("body-build", {body: "Sys A|Alpha", on: true});          // takes the loadout being edited
  assert.strictEqual(P.compute().rows[0].profile, "T9 full");
  P.act("prof-rename", {name: "T9 kit"});
  assert.strictEqual(P.compute().rows[0].profile, "T9 kit");
  P.act("prof-delete");
  assert.deepStrictEqual(plain(Object.keys(P.state().profiles)), ["Standard"]);
  assert.strictEqual(P.compute().rows[0].profile, "Standard");
  assert.strictEqual(P.act("prof-delete"), false, "the last loadout cannot be deleted");
});

test("'use this loadout for every planned body' re-points the planned bodies only", () => {
  const {P} = makeEnv();
  P.act("sys-toggle", {system: "Sys A", on: true});
  P.act("body-build", {body: "Sys A|Alpha", on: true});
  P.act("prof-new", {name: "Other"});
  P.act("prof-apply");
  assert.strictEqual(P.compute().rows[0].profile, "Other");
});

test("the plan is saved to localStorage and restored by a fresh page", () => {
  const first = makeEnv();
  first.P.act("sys-toggle", {system: "Sys A", on: true});
  first.P.act("body-build", {body: "Sys A|Alpha", on: true});
  const saved = first.store["se_scan_planner_v1"];
  assert.ok(saved, "nothing was saved");
  const second = makeEnv();
  second.ctx.localStorage.setItem("se_scan_planner_v1", saved);
  const again = makeEnv({localStorage: second.ctx.localStorage});
  assert.strictEqual(again.P.compute().totals.stations, 3);
  assert.deepStrictEqual(plain(again.P.state().systems), ["Sys A"]);
});

test("import replaces the plan, reset clears it", () => {
  const {P} = makeEnv();
  const plan = planWith({Alpha: {stations: 2}}, {systems: ["Sys A"]});
  P.act("import", {plan: JSON.parse(JSON.stringify(plan))});
  assert.strictEqual(P.compute().totals.stations, 2);
  P.act("reset");
  assert.strictEqual(P.compute().totals.bodies, 0);
});

test("a name that looks like markup is shown as text, not run", () => {
  const evil = [{system: "<img src=x onerror=boom>", planet_name: "<b>P</b>", planet_type: "Terra", parent_planet: "",
                 stations: 3, resources: {silicon: 1}}];
  const {P, byId} = makeEnv({DATA: evil, GALAXY: {}});
  P.act("sys-toggle", {system: "<img src=x onerror=boom>", on: true});
  const all = byId.plSystemList.innerHTML + byId.plBodies.innerHTML;
  assert.ok(!/<img/.test(all) && !/<b>P/.test(all), "unescaped markup reached the page");
  assert.ok(/&lt;img/.test(all));
});

test("a broken catalogue shows its problems instead of failing silently", () => {
  const {byId} = makeEnv({PLANNER_PROBLEMS: ["gear[0]: missing key"]});
  assert.ok(/gear\[0\]: missing key/.test(byId.plNotice.innerHTML));
});

if (failures.length) {
  console.log(failures.length + " FAILED, " + passed + " passed");
  failures.forEach(f => console.log("FAIL " + f));
  process.exit(1);
}
console.log(passed + " passed");
