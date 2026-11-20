/* scan_planner.js -- the PLANNER tab of the scan archive (scan_dashboard.py embeds this file).

   Pick systems, pick the planets and moons to build on, give each a loadout profile (which base kit,
   which extractor tier, which gear), and read off how many stations, extractors and gear pieces that
   takes and what it all costs to make.

   Two halves. The first is pure planning maths with no DOM, exported for node (test_scan_planner.py
   drives it directly). The second is the tab's UI; it only starts when the page provides DATA (the
   scanned bodies), GALAXY and PLANNER_CATALOG (planner_catalog.json), and keeps all its state in one
   plan object that it saves to localStorage.

   Slot arithmetic: a scan reports extractor slots PER STATION (see scan_logger.stations_for_body), so
   the extractors one body needs for a resource = slots x the stations built on it (a planet holds up
   to 3, a moon 1). */
(function (root) {
  "use strict";

  var TIER_ORDER = ["basic", "advanced", "industrial"];
  var STORAGE_KEY = "se_scan_planner_v1";

  // ---------------------------------------------------------------- pure planning maths

  function isObj(v) { return v !== null && typeof v === "object" && !Array.isArray(v); }
  function clampInt(v, lo, hi, dflt) {
    var n = parseInt(v, 10);
    if (isNaN(n)) n = dflt;
    return Math.max(lo, Math.min(hi, n));
  }
  function fmt(n) { return Number(n).toLocaleString("en-US"); }
  function fmtRate(n) { return Number(n).toLocaleString("en-US", {maximumFractionDigits: 2}); }
  function bodyKey(system, name) { return system + "|" + name; }
  function titleCase(key) {
    return String(key).replace(/_/g, " ").replace(/\b[a-z]/g, function (c) { return c.toUpperCase(); });
  }

  function tiersOf(catalog) {
    var seen = {};
    Object.keys(catalog.extractors || {}).forEach(function (res) {
      (catalog.extractors[res] || []).forEach(function (e) {
        if (e.tier && e.tier !== "single") seen[e.tier] = true;
      });
    });
    var known = TIER_ORDER.filter(function (t) { return seen[t]; });
    Object.keys(seen).forEach(function (t) { if (known.indexOf(t) < 0) known.push(t); });
    return known;
  }

  // key -> {item, group: "kit" | "extractor" | "gear", resource?}
  function itemIndex(catalog) {
    var idx = {};
    (catalog.kits || []).forEach(function (k) { idx[k.key] = {item: k, group: "kit"}; });
    Object.keys(catalog.extractors || {}).forEach(function (res) {
      (catalog.extractors[res] || []).forEach(function (e) {
        idx[e.key] = {item: e, group: "extractor", resource: res};
      });
    });
    (catalog.gear || []).forEach(function (g) { idx[g.key] = {item: g, group: "gear"}; });
    return idx;
  }

  // The extractor to use for ``resource`` at ``tier``: the exact tier if the catalogue has it, the only
  // one when a resource has a single extractor, else the nearest lower tier (or the lowest there is).
  function pickExtractor(catalog, resource, tier) {
    var list = (catalog.extractors || {})[resource];
    if (!list || !list.length) return null;
    var exact = list.filter(function (e) { return e.tier === tier; })[0];
    if (exact) return exact;
    if (list.length === 1) return list[0];
    var rank = function (e) { var i = TIER_ORDER.indexOf(e.tier); return i < 0 ? 99 : i; };
    var sorted = list.slice().sort(function (a, b) { return rank(a) - rank(b); });
    var want = TIER_ORDER.indexOf(tier);
    var best = sorted[0];
    sorted.forEach(function (e) { if (rank(e) <= want) best = e; });
    return best;
  }

  function defaultProfile(catalog) {
    var kits = catalog.kits || [];
    var planetKits = kits.filter(function (k) { return k.kind === "planet"; })
      .sort(function (a, b) { return (b.tech || 0) - (a.tech || 0); });
    var moonKits = kits.filter(function (k) { return k.kind === "moon"; });
    var freeKits = kits.filter(function (k) { return k.kind === "unattached"; })
      .sort(function (a, b) { return (b.tech || 0) - (a.tech || 0); });
    var tiers = tiersOf(catalog);
    return {
      planetKit: planetKits.length ? planetKits[0].key : "",
      moonKit: moonKits.length ? moonKits[0].key : "",
      unattachedKit: freeKits.length ? freeKits[0].key : "",
      tier: tiers.indexOf("industrial") >= 0 ? "industrial" : (tiers[tiers.length - 1] || ""),
      gear: []
    };
  }

  function defaultPlan(catalog) {
    var profiles = {};
    profiles["Standard"] = defaultProfile(catalog);
    return {version: 1, profiles: profiles, defaultProfile: "Standard", countBlueprints: false, systems: [], bodies: {}, defence: []};
  }

  // Make any saved / imported object safe to use. Unknown items are KEPT (compute() reports them),
  // so a plan is never silently emptied by a catalogue that changed under it.
  function normalisePlan(raw, catalog) {
    var plan = defaultPlan(catalog);
    if (!isObj(raw)) return plan;
    var profiles = {};
    if (isObj(raw.profiles)) {
      Object.keys(raw.profiles).forEach(function (name) {
        var p = raw.profiles[name];
        if (!isObj(p)) return;
        var base = defaultProfile(catalog);
        var gear = [];
        (Array.isArray(p.gear) ? p.gear : []).forEach(function (g) {
          if (isObj(g) && typeof g.item === "string") {
            gear.push({item: g.item, qty: clampInt(g.qty, 1, 9999, 1)});
          }
        });
        profiles[name] = {
          planetKit: typeof p.planetKit === "string" ? p.planetKit : base.planetKit,
          moonKit: typeof p.moonKit === "string" ? p.moonKit : base.moonKit,
          unattachedKit: typeof p.unattachedKit === "string" ? p.unattachedKit : base.unattachedKit,
          tier: typeof p.tier === "string" ? p.tier : base.tier,
          gear: gear
        };
      });
    }
    var names = Object.keys(profiles);
    if (names.length) plan.profiles = profiles;
    names = Object.keys(plan.profiles);
    plan.defaultProfile = (typeof raw.defaultProfile === "string" && plan.profiles[raw.defaultProfile])
      ? raw.defaultProfile : names[0];
    plan.countBlueprints = raw.countBlueprints === true;
    if (Array.isArray(raw.systems)) {
      raw.systems.forEach(function (s) {
        if (typeof s === "string" && plan.systems.indexOf(s) < 0) plan.systems.push(s);
      });
    }
    if (isObj(raw.bodies)) {
      Object.keys(raw.bodies).forEach(function (key) {
        var e = raw.bodies[key];
        if (!isObj(e)) return;
        plan.bodies[key] = {
          build: !!e.build,
          stations: clampInt(e.stations, 1, 3, 3),
          profile: typeof e.profile === "string" ? e.profile : "",
          skip: (Array.isArray(e.skip) ? e.skip : []).filter(function (s) { return typeof s === "string"; })
        };
      });
    }
    // Defence: groups of unattached stations in a system (no extractors; system protection).
    (Array.isArray(raw.defence) ? raw.defence : []).forEach(function (g) {
      if (!isObj(g) || typeof g.system !== "string" || !g.system) return;
      plan.defence.push({
        system: g.system,
        count: clampInt(g.count, 1, 99, 1),
        profile: typeof g.profile === "string" ? g.profile : ""
      });
    });
    return plan;
  }

  // Scanned bodies of ``systems`` as planner rows: planets by name, each followed by its moons.
  function listBodies(data, systems) {
    var wanted = {};
    systems.forEach(function (s) { wanted[s] = true; });
    var bySystem = {};
    data.forEach(function (r) {
      if (!wanted[r.system]) return;
      var per = {};
      Object.keys(r.resources || {}).forEach(function (res) {
        if (r.resources[res] > 0) per[res] = r.resources[res];
      });
      (bySystem[r.system] = bySystem[r.system] || []).push({
        key: bodyKey(r.system, r.planet_name), system: r.system, name: r.planet_name,
        type: r.planet_type || "", parent: r.parent_planet || "", isMoon: !!r.parent_planet,
        maxStations: r.stations || (r.parent_planet ? 1 : 3), perStation: per
      });
    });
    var out = [];
    Object.keys(bySystem).sort().forEach(function (sys) {
      var all = bySystem[sys];
      var byName = function (a, b) { return a.name.localeCompare(b.name); };
      var planets = all.filter(function (b) { return !b.isMoon; }).sort(byName);
      var moons = all.filter(function (b) { return b.isMoon; }).sort(byName);
      var placed = {};
      planets.forEach(function (p) {
        out.push(p);
        moons.forEach(function (m) {
          if (m.parent === p.name) { out.push(m); placed[m.key] = true; }
        });
      });
      moons.forEach(function (m) { if (!placed[m.key]) out.push(m); });
    });
    return out;
  }

  // The bill for ``plan``: ``rows`` per planned body, ``totals`` for the whole plan.
  function compute(plan, bodies, catalog) {
    var idx = itemIndex(catalog);
    var rows = [];
    var totals = {
      bodies: 0, systems: {}, stations: 0, stationSlots: 0, kits: {}, extractors: {},
      extractorTotal: 0, production: {}, assumedRates: {}, gear: {}, upgrades: {}, built: {}, bases: {}, blueprints: {}, materials: {}, unpriced: {},
      unknownItems: {},
      noExtractor: {}, defenceStations: 0, unknownSlots: {}
    };

    function cost(item, n) {
      if (item.cost === null || item.cost === undefined) {
        totals.unpriced[item.key] = item.label || item.key;
        return;
      }
      Object.keys(item.cost).forEach(function (res) {
        totals.materials[res] = (totals.materials[res] || 0) + item.cost[res] * n;
      });
    }

    // Count ``n`` stations built from base kit ``kitKey``: the kit, its cost, and the station slots it
    // takes. Returns the kit's label (null if the plan names a kit the catalogue no longer has).
    function addKit(kitKey, n) {
      var kit = idx[kitKey];
      if (!kit) { if (kitKey) totals.unknownItems[kitKey] = true; return null; }
      var label = kit.item.label || kitKey;
      var t = totals.kits[kitKey] || (totals.kits[kitKey] = {key: kitKey, label: label, count: 0});
      t.count += n;
      if (typeof kit.item.station_slots === "number") totals.stationSlots += n * kit.item.station_slots;
      else totals.unknownSlots[kitKey] = label;             // slot cost not entered: say so, do not count it as 0
      cost(kit.item, n);
      return label;
    }

    // Put loadout ``prof``'s gear on each of ``stations`` stations.
    function equip(prof, stations) {
      (prof.gear || []).forEach(function (g) {
        var hit = idx[g.item];
        if (!hit) { totals.unknownItems[g.item] = true; return; }
        var n = g.qty * stations;
        var t = totals.gear[g.item] || (totals.gear[g.item] =
          {key: g.item, label: hit.item.label || g.item, slot: hit.item.slot || "", tech: hit.item.tech, count: 0});
        t.count += n;
        makeGear(g.item, n, 0);
      });
    }

    // Make ``n`` of a gear item, three ways:
    //  * ``upgrade_from``: its blueprint is run on ``upgrade_qty`` of the item it names (which is used
    //    up), so it costs its own step plus everything underneath it;
    //  * a ``blueprint`` but no ``upgrade_from``: built from scratch, ``cost`` is everything it takes;
    //  * neither: bought, ``cost`` is its price.
    // Blueprints are reusable, so each is listed once with how many times it is used.
    function makeGear(key, n, depth) {
      var hit = idx[key];
      if (!hit) { totals.unknownItems[key] = true; return; }
      if (depth > 12) return;                         // a looping chain (the catalogue check rejects those)
      var item = hit.item;
      cost(item, n);
      if (item.blueprint) {
        var bp = totals.blueprints[item.blueprint] || (totals.blueprints[item.blueprint] =
          {name: item.blueprint, price: item.blueprint_price || null, uses: 0});
        bp.uses += n;
      }
      if (item.upgrade_from) {
        var src = idx[item.upgrade_from];
        var up = totals.upgrades[key] || (totals.upgrades[key] =
          {key: key, label: item.label || key, blueprint: item.blueprint || "", from: src ? (src.item.label || item.upgrade_from) : item.upgrade_from, count: 0});
        up.count += n;
        makeGear(item.upgrade_from, n * (item.upgrade_qty || 1), depth + 1);
      } else if (item.blueprint) {
        var made = totals.built[key] || (totals.built[key] =
          {key: key, label: item.label || key, blueprint: item.blueprint, count: 0});
        made.count += n;
      } else {
        var base = totals.bases[key] || (totals.bases[key] =
          {key: key, label: item.label || key, acquire: item.acquire || "", count: 0});
        base.count += n;
      }
    }

    bodies.forEach(function (b) {
      var entry = plan.bodies[b.key];
      if (!entry || !entry.build) return;
      var stations = Math.min(b.maxStations || 1, Math.max(1, entry.stations || 1));
      if (b.isMoon) stations = 1;
      var pname = (entry.profile && plan.profiles[entry.profile]) ? entry.profile : plan.defaultProfile;
      var prof = plan.profiles[pname];
      var row = {
        key: b.key, system: b.system, name: b.name, type: b.type, isMoon: b.isMoon, stations: stations,
        profile: pname, kit: null, extractors: [], extractorTotal: 0, skipped: []
      };

      row.kit = addKit(b.isMoon ? prof.moonKit : prof.planetKit, stations);

      Object.keys(b.perStation).sort().forEach(function (res) {
        if ((entry.skip || []).indexOf(res) >= 0) { row.skipped.push(res); return; }
        var ex = pickExtractor(catalog, res, prof.tier);
        if (!ex) { totals.noExtractor[res] = true; return; }
        var count = b.perStation[res] * stations;
        var rate = (typeof ex.per_day === "number") ? ex.per_day : null;
        var prod = totals.production[res] || (totals.production[res] = {resource: res, perDay: 0, extractors: 0, unrated: 0});
        prod.extractors += count;
        if (rate === null) prod.unrated += count; else prod.perDay += count * rate;
        if (ex.per_day_inferred && rate !== null) totals.assumedRates[ex.key] = ex.label || ex.key;
        row.extractors.push({resource: res, item: ex.key, label: ex.label || ex.key,
                             perStation: b.perStation[res], count: count,
                             perDay: rate === null ? null : count * rate});
        row.extractorTotal += count;
        var t = totals.extractors[ex.key] || (totals.extractors[ex.key] =
          {key: ex.key, label: ex.label || ex.key, resource: res, count: 0});
        t.count += count;
        totals.extractorTotal += count;
        cost(ex, count);
      });

      equip(prof, stations);

      totals.bodies += 1;
      totals.systems[b.system] = true;
      totals.stations += stations;
      rows.push(row);
    });
    // Defence stations: free-standing (unattached) stations in the selected systems. No extractors, so
    // nothing is produced; each takes its loadout's unattached base and gear.
    var defenceRows = [];
    (plan.defence || []).forEach(function (g) {
      if ((plan.systems || []).indexOf(g.system) < 0) return;      // only systems currently ticked
      var pname = (g.profile && plan.profiles[g.profile]) ? g.profile : plan.defaultProfile;
      var prof = plan.profiles[pname];
      var n = Math.max(1, g.count || 1);
      defenceRows.push({system: g.system, stations: n, profile: pname, kit: addKit(prof.unattachedKit, n)});
      equip(prof, n);
      totals.stations += n;
      totals.defenceStations += n;
      totals.systems[g.system] = true;
    });
    totals.systemCount = Object.keys(totals.systems).length;
    if (plan.countBlueprints) {
      // each blueprint is bought once however many times it is used
      Object.keys(totals.blueprints).forEach(function (name) {
        var bp = totals.blueprints[name];
        if (bp.price) {
          Object.keys(bp.price).forEach(function (res) { totals.materials[res] = (totals.materials[res] || 0) + bp.price[res]; });
        } else {
          totals.unpriced["blueprint:" + name] = name + " (blueprint)";
        }
      });
    }
    return {rows: rows, defenceRows: defenceRows, totals: totals};
  }

  function resourceLabel(key, labels) { return (labels && labels[key]) || titleCase(key); }

  // The plan as plain text, for pasting somewhere.
  function summaryText(result, labels) {
    var t = result.totals;
    var out = [];
    out.push("PLAN: " + t.bodies + " bodies in " + t.systemCount + " system" + (t.systemCount === 1 ? "" : "s")
      + ", " + t.stations + " station" + (t.stations === 1 ? "" : "s")
      + (t.defenceStations ? " (" + t.defenceStations + " defence)" : "")
      + ", " + fmt(t.stationSlots) + " station slots");
    var section = function (title, lines) { if (lines.length) { out.push(""); out.push(title); lines.forEach(function (l) { out.push("  " + l); }); } };
    section("Defence stations (unattached, no extractors)", (result.defenceRows || []).map(function (d) {
      return d.stations + " x " + (d.kit || "?") + " in " + d.system + " [" + d.profile + "]"; }));
    section("Station kits", Object.keys(t.kits).map(function (k) { return t.kits[k].count + " x " + t.kits[k].label; }));
    section("Extractors (" + fmt(t.extractorTotal) + ")", Object.keys(t.extractors).sort().map(function (k) {
      return fmt(t.extractors[k].count) + " x " + t.extractors[k].label; }));
    section("Production per day", Object.keys(t.production).sort().map(function (k) {
      var p = t.production[k];
      return resourceLabel(k, labels) + ": " + (p.unrated === p.extractors ? "rate not entered" : fmtRate(p.perDay)
        + (p.unrated ? " + " + fmt(p.unrated) + " extractors with no rate entered" : "")); }));
    section("Gear", Object.keys(t.gear).sort().map(function (k) { return fmt(t.gear[k].count) + " x " + t.gear[k].label; }));
    section("Upgrades to craft (blueprints are reusable)", Object.keys(t.upgrades).sort().map(function (k) {
      var u = t.upgrades[k];
      return fmt(u.count) + " x " + u.label + (u.blueprint ? " [" + u.blueprint + "]" : ""); }));
    section("Built from scratch", Object.keys(t.built).sort().map(function (k) {
      var m = t.built[k];
      return fmt(m.count) + " x " + m.label + " [" + m.blueprint + "]"; }));
    section("Blueprints needed (reusable, own each once)", Object.keys(t.blueprints).sort().map(function (k) {
      var bp = t.blueprints[k];
      return bp.name + " - " + (bp.price ? Object.keys(bp.price).map(function (r) { return fmt(bp.price[r]) + " " + resourceLabel(r, labels); }).join(", ") : "price not entered"); }));
    section("Base gear to acquire", Object.keys(t.bases).sort().map(function (k) {
      var b = t.bases[k];
      return fmt(b.count) + " x " + b.label + (b.acquire ? " (" + b.acquire + ")" : ""); }));
    section("Materials", Object.keys(t.materials).sort().map(function (k) {
      return fmt(t.materials[k]) + " " + resourceLabel(k, labels); }));
    var unpriced = Object.keys(t.unpriced).map(function (k) { return t.unpriced[k]; });
    if (unpriced.length) { out.push(""); out.push("Cost not entered yet for: " + unpriced.join(", ")); }
    return out.join("\n");
  }

  var core = {
    TIER_ORDER: TIER_ORDER, STORAGE_KEY: STORAGE_KEY, bodyKey: bodyKey, tiersOf: tiersOf,
    itemIndex: itemIndex, pickExtractor: pickExtractor, defaultProfile: defaultProfile,
    defaultPlan: defaultPlan, normalisePlan: normalisePlan, listBodies: listBodies,
    compute: compute, summaryText: summaryText, fmt: fmt, fmtRate: fmtRate, titleCase: titleCase
  };
  if (typeof module !== "undefined" && module.exports) module.exports = core;
  root.PlannerCore = core;

  // ---------------------------------------------------------------- the tab

  if (typeof document === "undefined" || typeof DATA === "undefined" ||
      typeof GALAXY === "undefined" || typeof PLANNER_CATALOG === "undefined") return;

  var catalog = PLANNER_CATALOG;
  var labels = {};
  (typeof RESOURCE_COLS !== "undefined" ? RESOURCE_COLS : []).forEach(function (c) { labels[c.key] = c.label; });
  var idx = itemIndex(catalog);
  var tiers = tiersOf(catalog);

  var systemInfo = (function () {
    var by = {};
    DATA.forEach(function (r) {
      var s = by[r.system] || (by[r.system] = {name: r.system, planets: 0, moons: 0});
      if (r.parent_planet) s.moons += 1; else s.planets += 1;
    });
    return Object.keys(by).sort(function (a, b) { return a.localeCompare(b); }).map(function (k) {
      var g = GALAXY[k] || {};
      by[k].hazard = (g.hazard_rating === undefined) ? null : g.hazard_rating;
      by[k].totalPlanets = g.total_planets || 0;
      return by[k];
    });
  })();

  function load() {
    try { return normalisePlan(JSON.parse(localStorage.getItem(STORAGE_KEY) || "null"), catalog); }
    catch (e) { return defaultPlan(catalog); }
  }
  var state = load();
  var ui = {profile: state.defaultProfile};
  if (!state.profiles[ui.profile]) ui.profile = Object.keys(state.profiles)[0];

  function save() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch (e) { /* private mode etc. */ }
  }
  function el(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function lbl(key) { return resourceLabel(key, labels); }
  if (typeof PLANNER_PROBLEMS !== "undefined" && PLANNER_PROBLEMS.length) {
    el("plNotice").innerHTML = '<div class="pl-error">planner_catalog.json has problems, so the planner has no items to offer: '
      + esc(PLANNER_PROBLEMS.join("; ")) + "</div>";
  }
  function itemTag(item) {
    var bits = [];
    if (item.tech !== null && item.tech !== undefined) bits.push("tech " + item.tech);
    if (item.cost === null || item.cost === undefined) bits.push("cost not entered");
    return bits.join(" · ");
  }
  function options(list, selected, blank) {
    return (blank ? '<option value="">' + esc(blank) + "</option>" : "") + list.map(function (o) {
      return '<option value="' + esc(o.value) + '"' + (o.value === selected ? " selected" : "") + ">" + esc(o.label) + "</option>";
    }).join("");
  }
  function gearOptions(selected, blank) {
    var groups = {};
    (catalog.gear || []).forEach(function (g) { (groups[g.slot || "other"] = groups[g.slot || "other"] || []).push(g); });
    var html = blank ? '<option value="">' + esc(blank) + "</option>" : "";
    Object.keys(groups).forEach(function (slot) {
      html += '<optgroup label="' + esc(titleCase(slot)) + '">' + groups[slot].map(function (g) {
        return '<option value="' + esc(g.key) + '"' + (g.key === selected ? " selected" : "") + ">" + esc(g.label || g.key) + "</option>";
      }).join("") + "</optgroup>";
    });
    if (selected && !idx[selected]) html += '<option value="' + esc(selected) + '" selected>' + esc(selected) + " (not in catalogue)</option>";
    return html;
  }

  // ---- rendering

  function renderSystems() {
    var q = (el("plSystemSearch").value || "").trim().toLowerCase();
    var sel = {};
    state.systems.forEach(function (s) { sel[s] = true; });
    var shown = systemInfo.filter(function (s) { return sel[s.name] || !q || s.name.toLowerCase().indexOf(q) >= 0; });
    el("plSystemList").innerHTML = shown.map(function (s) {
      var haz = s.hazard === null ? "" : ' <span class="pl-dim">hazard ' + s.hazard + "</span>";
      return '<label class="pl-sys' + (sel[s.name] ? " on" : "") + '"><input type="checkbox" data-act="sys-toggle" data-system="'
        + esc(s.name) + '"' + (sel[s.name] ? " checked" : "") + "><span class=\"pl-sys-name\">" + esc(s.name) + "</span>"
        + '<span class="pl-dim">' + s.planets + " planet" + (s.planets === 1 ? "" : "s") + (s.moons ? " + " + s.moons + " moon" + (s.moons === 1 ? "" : "s") : "")
        + " scanned</span>" + haz + "</label>";
    }).join("") || '<div class="pl-empty">No systems match.</div>';
    el("plSystemCount").textContent = state.systems.length + " selected";
  }

  function bodyRowHtml(b) {
    var e = state.bodies[b.key] || {build: false, stations: b.maxStations, profile: "", skip: []};
    var chips = Object.keys(b.perStation).sort().map(function (res) {
      var off = (e.skip || []).indexOf(res) >= 0;
      return '<button class="pl-chip' + (off ? " off" : "") + '" data-act="chip" data-body="' + esc(b.key) + '" data-res="' + esc(res)
        + '" title="Click to ' + (off ? "extract" : "skip") + ' this resource">' + esc(lbl(res)) + " " + b.perStation[res] + "</button>";
    }).join("") || '<span class="pl-dim">no resources scanned</span>';
    var stationsCell = b.isMoon || b.maxStations <= 1 ? '<span class="pl-dim">1</span>'
      : '<select data-act="body-stations" data-body="' + esc(b.key) + '"' + (e.build ? "" : " disabled") + ">"
        + [1, 2, 3].filter(function (n) { return n <= b.maxStations; }).map(function (n) {
          return '<option value="' + n + '"' + (n === (e.stations || b.maxStations) ? " selected" : "") + ">" + n + "</option>";
        }).join("") + "</select>";
    var profileCell = '<select data-act="body-profile" data-body="' + esc(b.key) + '"' + (e.build ? "" : " disabled") + ">"
      + options(Object.keys(state.profiles).map(function (n) { return {value: n, label: n}; }),
                (e.profile && state.profiles[e.profile]) ? e.profile : state.defaultProfile) + "</select>";
    return '<tr class="pl-body' + (e.build ? " on" : "") + (b.isMoon ? " moon" : "") + '">'
      + '<td><input type="checkbox" data-act="body-build" data-body="' + esc(b.key) + '"' + (e.build ? " checked" : "") + "></td>"
      + '<td class="pl-bname">' + (b.isMoon ? "↳ " : "") + esc(b.name) + ' <span class="pl-dim">' + (b.isMoon ? "moon of " + esc(b.parent) : esc(b.type)) + "</span></td>"
      + "<td>" + stationsCell + "</td><td>" + profileCell + '</td><td class="pl-chips">' + chips + "</td></tr>";
  }

  // The unattached ("defence") stations planned for one system: a count and a loadout per group.
  function defenceBlockHtml(sys) {
    var profileNames = Object.keys(state.profiles).map(function (n) { return {value: n, label: n}; });
    var rows = state.defence.map(function (g, i) {
      if (g.system !== sys) return "";
      return '<div class="pl-defrow"><input type="number" min="1" max="99" value="' + g.count + '" data-act="def-count" data-idx="' + i + '">'
        + "<span>free-standing station" + (g.count === 1 ? "" : "s") + " with loadout</span>"
        + '<select data-act="def-profile" data-idx="' + i + '">'
        + options(profileNames, (g.profile && state.profiles[g.profile]) ? g.profile : state.defaultProfile) + "</select>"
        + '<button class="btn" data-act="def-remove" data-idx="' + i + '" title="Remove">\u2715</button></div>';
    }).join("");
    return '<div class="pl-defence"><div class="pl-defhead"><strong>Defence stations</strong>'
      + ' <span class="pl-dim">unattached, no extractors, to protect the system</span>'
      + '<button class="btn" data-act="def-add" data-system="' + esc(sys) + '">+ Add</button></div>'
      + (rows || '<div class="pl-dim pl-defnone">None planned.</div>') + "</div>";
  }

  function renderBodies() {
    var bodies = listBodies(DATA, state.systems);
    if (!state.systems.length) { el("plBodies").innerHTML = '<div class="pl-empty">Tick one or more systems to list their scanned planets and moons.</div>'; return; }
    var html = "";
    state.systems.slice().sort().forEach(function (sys) {
      var inSys = bodies.filter(function (b) { return b.system === sys; });
      var planned = inSys.filter(function (b) { return (state.bodies[b.key] || {}).build; }).length;
      html += '<div class="pl-sysblock"><div class="pl-syshead"><strong>' + esc(sys) + '</strong> <span class="pl-dim">' + planned + " of " + inSys.length + " planned</span>"
        + '<span class="pl-sysbtns"><button class="btn" data-act="sys-all-planets" data-system="' + esc(sys) + '">All planets</button>'
        + '<button class="btn" data-act="sys-none" data-system="' + esc(sys) + '">None</button></span></div>'
        + '<table class="pl-table"><thead><tr><th></th><th>Body</th><th>Stations</th><th>Loadout</th><th>Extractor slots per station (click to skip)</th></tr></thead><tbody>'
        + inSys.map(bodyRowHtml).join("") + "</tbody></table>" + defenceBlockHtml(sys) + "</div>";
    });
    el("plBodies").innerHTML = html;
  }

  function renderProfiles() {
    var names = Object.keys(state.profiles);
    if (!state.profiles[ui.profile]) ui.profile = names[0];
    var p = state.profiles[ui.profile];
    var kitOpts = function (kind) {
      return (catalog.kits || []).filter(function (k) { return k.kind === kind; }).map(function (k) {
        return {value: k.key, label: (k.label || k.key) + (k.tech !== null && k.tech !== undefined ? " (tech " + k.tech + ")" : "")};
      });
    };
    var withUnknown = function (opts, sel) {
      if (sel && !opts.some(function (o) { return o.value === sel; })) opts = opts.concat([{value: sel, label: sel + " (not in catalogue)"}]);
      return opts;
    };
    var tierOpts = tiers.map(function (t) { return {value: t, label: titleCase(t)}; });
    var gearRows = p.gear.map(function (g, i) {
      var hit = idx[g.item];
      return '<tr><td><select data-act="gear-item" data-idx="' + i + '">' + gearOptions(g.item) + "</select></td>"
        + '<td><input type="number" min="1" max="9999" value="' + g.qty + '" data-act="gear-qty" data-idx="' + i + '"></td>'
        + '<td class="pl-dim">' + (hit ? esc(itemTag(hit.item)) : "not in catalogue") + "</td>"
        + '<td><button class="btn" data-act="gear-remove" data-idx="' + i + '" title="Remove">✕</button></td></tr>';
    }).join("");
    el("plProfiles").innerHTML =
      '<div class="pl-profhead"><select data-act="prof-select">' + options(names.map(function (n) { return {value: n, label: n}; }), ui.profile) + "</select>"
      + '<button class="btn" data-act="prof-new">New</button><button class="btn" data-act="prof-dup">Duplicate</button>'
      + '<button class="btn" data-act="prof-rename">Rename</button><button class="btn" data-act="prof-delete"' + (names.length < 2 ? " disabled" : "") + ">Delete</button></div>"
      + '<div class="pl-field"><label>Planet base</label><select data-act="prof-planetkit">' + options(withUnknown(kitOpts("planet"), p.planetKit), p.planetKit) + "</select></div>"
      + '<div class="pl-field"><label>Moon base</label><select data-act="prof-moonkit">' + options(withUnknown(kitOpts("moon"), p.moonKit), p.moonKit) + "</select></div>"
      + '<div class="pl-field"><label>Unattached base</label><select data-act="prof-freekit">' + options(withUnknown(kitOpts("unattached"), p.unattachedKit), p.unattachedKit) + "</select></div>"
      + '<div class="pl-field"><label>Extractors</label><select data-act="prof-tier">' + options(tierOpts, p.tier) + "</select></div>"
      + '<div class="pl-subhead">Gear on every station using this loadout</div>'
      + (p.gear.length ? '<table class="pl-table pl-gear"><thead><tr><th>Item</th><th>Per station</th><th></th><th></th></tr></thead><tbody>' + gearRows + "</tbody></table>"
                       : '<div class="pl-empty">No gear yet.</div>')
      + '<select data-act="gear-add">' + gearOptions("", "+ Add gear…") + "</select>"
      + '<div class="pl-applyrow"><button class="btn" data-act="prof-apply">Use this loadout for every planned body</button></div>';
  }

  function tableHtml(head, rows, cls) {
    if (!rows.length) return "";
    return '<table class="pl-table ' + (cls || "") + '"><thead><tr>' + head.map(function (h) { return "<th" + (h.num ? ' class="num"' : "") + ">" + esc(h.t) + "</th>"; }).join("")
      + "</tr></thead><tbody>" + rows.map(function (r) {
        return "<tr>" + r.map(function (c, i) { return "<td" + (head[i].num ? ' class="num"' : "") + ">" + c + "</td>"; }).join("") + "</tr>";
      }).join("") + "</tbody></table>";
  }

  function renderResults() {
    var bodies = listBodies(DATA, state.systems);
    var result = compute(state, bodies, catalog);
    var t = result.totals;
    el("plCountBp").checked = !!state.countBlueprints;
    if (!t.bodies && !t.defenceStations) {
      el("plResults").innerHTML = '<div class="pl-empty">Nothing planned yet. Tick the planets and moons you want to build on, or add defence stations.</div>';
      el("plSummaryLine").textContent = "";
      return result;
    }
    el("plSummaryLine").textContent = t.bodies + " bodies \u00b7 " + t.stations + " stations"
      + (t.defenceStations ? " (" + t.defenceStations + " defence)" : "") + " \u00b7 " + fmt(t.extractorTotal) + " extractors";
    var tiles = [["Systems", t.systemCount], ["Bodies", t.bodies], ["Stations", t.stations]].concat(t.defenceStations ? [["Defence", t.defenceStations]] : [])
      .concat([["Station slots", t.stationSlots], ["Extractors", t.extractorTotal]])
      .map(function (x) { return '<div class="pl-tile"><div class="pl-tile-n">' + fmt(x[1]) + '</div><div class="pl-tile-l">' + x[0] + "</div></div>"; }).join("");
    var warn = [];
    var unpriced = Object.keys(t.unpriced).map(function (k) { return t.unpriced[k]; });
    if (unpriced.length) warn.push("Cost not entered yet for: " + unpriced.join(", ") + ". The materials total leaves these out.");
    var noSlots = Object.keys(t.unknownSlots).map(function (k) { return t.unknownSlots[k]; });
    if (noSlots.length) warn.push("Station-slot cost not entered for: " + noSlots.join(", ") + ". The station slots total leaves these out.");
    var unknown = Object.keys(t.unknownItems);
    if (unknown.length) warn.push("Not in the catalogue any more: " + unknown.join(", ") + ".");
    var noex = Object.keys(t.noExtractor).map(lbl);
    if (noex.length) warn.push("No extractor in the catalogue for: " + noex.join(", ") + ".");

    var kitRows = Object.keys(t.kits).map(function (k) { return [esc(t.kits[k].label), fmt(t.kits[k].count)]; });
    var exRows = Object.keys(t.extractors).sort(function (a, b) {
      return lbl(t.extractors[a].resource).localeCompare(lbl(t.extractors[b].resource)); }).map(function (k) {
        var x = t.extractors[k]; return [esc(x.label), esc(lbl(x.resource)), fmt(x.count)]; });
    var gearRows = Object.keys(t.gear).sort().map(function (k) {
      var g = t.gear[k]; return [esc(g.label), esc(titleCase(g.slot || "")), g.tech === null || g.tech === undefined ? "—" : g.tech, fmt(g.count)]; });
    var upRows = Object.keys(t.upgrades).sort().map(function (k) {
      var u = t.upgrades[k];
      return [esc(u.label), "upgrade of " + esc(u.from), u.blueprint ? esc(u.blueprint) : "—", fmt(u.count)]; })
      .concat(Object.keys(t.built).sort().map(function (k) {
        var m = t.built[k]; return [esc(m.label), "from scratch", esc(m.blueprint), fmt(m.count)]; }));
    var priceText = function (price) {
      return price ? Object.keys(price).map(function (r) { return fmt(price[r]) + " " + esc(lbl(r)); }).join(", ") : '<span class="pl-dim">not entered</span>';
    };
    var prodRows = Object.keys(t.production).sort(function (a, b) { return lbl(a).localeCompare(lbl(b)); }).map(function (k) {
      var p = t.production[k];
      var amount = p.unrated === p.extractors ? '<span class="pl-dim">rate not entered</span>'
        : fmtRate(p.perDay) + (p.unrated ? ' <span class="pl-dim">+ ' + fmt(p.unrated) + " extractors with no rate entered</span>" : "");
      return [esc(lbl(k)), fmt(p.extractors), amount];
    });
    var assumed = Object.keys(t.assumedRates).map(function (k) { return t.assumedRates[k]; });
    var bpRows = Object.keys(t.blueprints).sort().map(function (k) {
      var bp = t.blueprints[k]; return [esc(bp.name), priceText(bp.price), fmt(bp.uses)]; });
    var baseRows = Object.keys(t.bases).sort().map(function (k) {
      var b = t.bases[k]; return [esc(b.label), esc(b.acquire || "—"), fmt(b.count)]; });
    var matRows = Object.keys(t.materials).sort().map(function (k) { return [esc(lbl(k)), fmt(t.materials[k])]; });
    var bodyRows = result.rows.map(function (r) {
      var ex = r.extractors.map(function (x) {
        return esc(lbl(x.resource)) + " " + x.perStation + " × " + r.stations + " = " + fmt(x.count)
          + (x.perDay === null ? "" : ' <span class="pl-dim">→ ' + fmtRate(x.perDay) + "/day</span>"); }).join("<br>");
      return [esc(r.system), esc(r.name) + (r.isMoon ? ' <span class="pl-dim">moon</span>' : ""), r.stations, esc(r.profile), esc(r.kit || "—"), ex || '<span class="pl-dim">none</span>', fmt(r.extractorTotal)];
    });

    var defRows = (result.defenceRows || []).map(function (d) {
      return [esc(d.system), "Defence stations <span class=\"pl-dim\">(unattached)</span>", d.stations, esc(d.profile), esc(d.kit || "\u2014"),
              '<span class="pl-dim">no extractors</span>', "\u2014"]; });
    bodyRows = bodyRows.concat(defRows);

    el("plResults").innerHTML =
      '<div class="pl-tiles">' + tiles + "</div>"
      + warn.map(function (w) { return '<div class="pl-warn">' + esc(w) + "</div>"; }).join("")
      + '<div class="pl-resgrid"><div><div class="pl-subhead">Station bases</div>' + tableHtml([{t: "Base"}, {t: "Count", num: 1}], kitRows)
      + '<div class="pl-subhead">Extractors needed</div>' + tableHtml([{t: "Extractor"}, {t: "Resource"}, {t: "Count", num: 1}], exRows)
      + '<div class="pl-subhead">Gear</div>' + (gearRows.length ? tableHtml([{t: "Item"}, {t: "Slot"}, {t: "Tech", num: 1}, {t: "Count", num: 1}], gearRows) : '<div class="pl-empty">No gear in the loadouts used.</div>')
      + (upRows.length ? '<div class="pl-subhead">Items to craft</div>' + tableHtml([{t: "Makes"}, {t: "How"}, {t: "Blueprint (reusable)"}, {t: "Times", num: 1}], upRows) : "")
      + (bpRows.length ? '<div class="pl-subhead">Blueprints to own (buy once)</div>' + tableHtml([{t: "Blueprint"}, {t: "Price"}, {t: "Uses", num: 1}], bpRows) : "")
      + (baseRows.length ? '<div class="pl-subhead">Base gear to acquire</div>' + tableHtml([{t: "Item"}, {t: "From"}, {t: "Count", num: 1}], baseRows) : "")
      + '</div><div><div class="pl-subhead">Production per day</div>'
      + (prodRows.length ? tableHtml([{t: "Resource"}, {t: "Extractors", num: 1}, {t: "Per day", num: 1}], prodRows)
          + (assumed.length ? '<div class="pl-dim pl-assumed">Rate assumed, not yet seen on a real item: ' + esc(assumed.join(", ")) + ".</div>" : "")
        : '<div class="pl-empty">No extractors in the plan.</div>')
      + '<div class="pl-subhead">Materials to make it all</div>'
      + (matRows.length ? tableHtml([{t: "Resource"}, {t: "Amount", num: 1}], matRows) : '<div class="pl-empty">No costs entered yet.</div>') + "</div></div>"
      + '<div class="pl-subhead">By body</div>' + tableHtml([{t: "System"}, {t: "Body"}, {t: "Stations", num: 1}, {t: "Loadout"}, {t: "Base"}, {t: "Extractors (slots × stations → output)"}, {t: "Total", num: 1}], bodyRows, "pl-bybody");
    return result;
  }

  function renderAll() { renderSystems(); renderBodies(); renderProfiles(); renderResults(); }

  // ---- actions (every UI event ends up here, which is also how the tests drive it)

  function ensureBody(key) {
    var maxSt = 3;
    listBodies(DATA, state.systems).forEach(function (b) { if (b.key === key) maxSt = b.maxStations; });
    return state.bodies[key] || (state.bodies[key] = {build: false, stations: maxSt, profile: "", skip: []});
  }
  function bodiesOf(system) { return listBodies(DATA, [system]); }
  function editing() { return state.profiles[ui.profile]; }

  function act(name, p) {
    p = p || {};
    var e;
    switch (name) {
      case "sys-toggle":
        var at = state.systems.indexOf(p.system);
        if (p.on && at < 0) state.systems.push(p.system);
        if (!p.on && at >= 0) state.systems.splice(at, 1);
        break;
      case "sys-clear": state.systems = []; break;
      case "sys-all-planets":
        bodiesOf(p.system).forEach(function (b) {
          if (b.isMoon) return;
          e = ensureBody(b.key); e.build = true; e.stations = b.maxStations; if (!e.profile) e.profile = ui.profile;
        });
        break;
      case "sys-none":
        bodiesOf(p.system).forEach(function (b) { if (state.bodies[b.key]) state.bodies[b.key].build = false; });
        break;
      case "body-build":
        e = ensureBody(p.body); e.build = !!p.on; if (e.build && !e.profile) e.profile = ui.profile;
        break;
      case "body-stations": ensureBody(p.body).stations = clampInt(p.value, 1, 3, 3); break;
      case "body-profile": ensureBody(p.body).profile = p.value; break;
      case "chip":
        e = ensureBody(p.body);
        var at2 = e.skip.indexOf(p.res);
        if (at2 >= 0) e.skip.splice(at2, 1); else e.skip.push(p.res);
        break;
      case "prof-select": if (state.profiles[p.value]) ui.profile = p.value; break;
      case "prof-new":
      case "prof-dup":
        var nm = (p.name || "").trim();
        if (!nm || state.profiles[nm]) return false;
        state.profiles[nm] = name === "prof-dup" ? JSON.parse(JSON.stringify(editing())) : defaultProfile(catalog);
        ui.profile = nm;
        break;
      case "prof-rename":
        var to = (p.name || "").trim();
        if (!to || state.profiles[to] || to === ui.profile) return false;
        state.profiles[to] = state.profiles[ui.profile]; delete state.profiles[ui.profile];
        Object.keys(state.bodies).forEach(function (k) { if (state.bodies[k].profile === ui.profile) state.bodies[k].profile = to; });
        state.defence.forEach(function (g) { if (g.profile === ui.profile) g.profile = to; });
        if (state.defaultProfile === ui.profile) state.defaultProfile = to;
        ui.profile = to;
        break;
      case "prof-delete":
        if (Object.keys(state.profiles).length < 2) return false;
        var gone = ui.profile;
        delete state.profiles[gone];
        ui.profile = Object.keys(state.profiles)[0];
        if (state.defaultProfile === gone) state.defaultProfile = ui.profile;
        Object.keys(state.bodies).forEach(function (k) { if (state.bodies[k].profile === gone) state.bodies[k].profile = ui.profile; });
        state.defence.forEach(function (g) { if (g.profile === gone) g.profile = ui.profile; });
        break;
      case "prof-planetkit": editing().planetKit = p.value; break;
      case "prof-moonkit": editing().moonKit = p.value; break;
      case "prof-tier": editing().tier = p.value; break;
      case "gear-add": if (p.value) editing().gear.push({item: p.value, qty: 1}); break;
      case "gear-item": if (editing().gear[p.idx]) editing().gear[p.idx].item = p.value; break;
      case "gear-qty": if (editing().gear[p.idx]) editing().gear[p.idx].qty = clampInt(p.value, 1, 9999, 1); break;
      case "gear-remove": editing().gear.splice(p.idx, 1); break;
      case "prof-apply":
        Object.keys(state.bodies).forEach(function (k) { if (state.bodies[k].build) state.bodies[k].profile = ui.profile; });
        break;
      case "def-add": state.defence.push({system: p.system, count: 1, profile: ui.profile}); break;
      case "def-count": if (state.defence[p.idx]) state.defence[p.idx].count = clampInt(p.value, 1, 99, 1); break;
      case "def-profile": if (state.defence[p.idx] && state.profiles[p.value]) state.defence[p.idx].profile = p.value; break;
      case "def-remove": state.defence.splice(p.idx, 1); break;
      case "prof-freekit": editing().unattachedKit = p.value; break;
      case "count-blueprints": state.countBlueprints = !!p.on; break;
      case "reset": state = defaultPlan(catalog); ui.profile = state.defaultProfile; break;
      case "import": state = normalisePlan(p.plan, catalog); ui.profile = state.defaultProfile; break;
      default: return false;
    }
    save();
    renderAll();
    return true;
  }

  // ---- DOM events -> actions

  function dataOf(target) {
    var d = target.dataset || {};
    return {system: d.system, body: d.body, res: d.res, idx: d.idx === undefined ? undefined : parseInt(d.idx, 10)};
  }
  var viewEl = el("plannerView");
  viewEl.addEventListener("click", function (ev) {
    var t = ev.target.closest ? ev.target.closest("[data-act]") : null;
    if (!t) return;
    var name = t.dataset.act, p = dataOf(t);
    if (t.tagName === "SELECT" || t.tagName === "INPUT") return;     // these act on "change"
    if (name === "prof-new" || name === "prof-dup") p.name = window.prompt(name === "prof-new" ? "Name for the new loadout:" : "Name for the copy:", "");
    if (name === "prof-rename") p.name = window.prompt("New name:", ui.profile);
    if (name === "prof-delete" && !window.confirm('Delete the loadout "' + ui.profile + '"? Bodies using it switch to another loadout.')) return;
    if (name === "sys-clear" && !window.confirm("Clear the selected systems? Your planned bodies are kept.")) return;
    act(name, p);
  });
  viewEl.addEventListener("change", function (ev) {
    var t = ev.target.closest ? ev.target.closest("[data-act]") : null;
    if (!t) return;
    var name = t.dataset.act, p = dataOf(t);
    p.value = t.value; p.on = t.checked;
    if (name === "sys-toggle" || name === "body-build" || name.indexOf("prof-") === 0 || name.indexOf("gear-") === 0 || name.indexOf("body-") === 0 || name.indexOf("def-") === 0 || name === "count-blueprints") act(name, p);
  });
  el("plSystemSearch").addEventListener("input", renderSystems);
  el("plReset").addEventListener("click", function () { if (window.confirm("Reset the whole plan?")) act("reset"); });
  el("plClearSystems").addEventListener("click", function () { act("sys-clear"); });
  el("plExport").addEventListener("click", function () {
    var blob = new Blob([JSON.stringify(state, null, 2)], {type: "application/json"});
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "scan-planner-plan.json";
    document.body.appendChild(a); a.click(); a.remove();
  });
  el("plImport").addEventListener("change", function (ev) {
    var f = ev.target.files && ev.target.files[0];
    if (!f) return;
    var reader = new FileReader();
    reader.onload = function () {
      try { act("import", {plan: JSON.parse(String(reader.result))}); }
      catch (e) { window.alert("That file isn't a planner plan."); }
    };
    reader.readAsText(f);
    ev.target.value = "";
  });
  el("plCopy").addEventListener("click", function () {
    var text = summaryText(compute(state, listBodies(DATA, state.systems), catalog), labels);
    try { navigator.clipboard.writeText(text); el("plCopy").textContent = "Copied"; setTimeout(function () { el("plCopy").textContent = "Copy summary"; }, 1500); }
    catch (e) { window.prompt("Copy this summary:", text); }
  });

  root.PLANNER = {state: function () { return state; }, act: act, render: renderAll, compute: function () {
    return compute(state, listBodies(DATA, state.systems), catalog); }, editing: editing, ui: ui};
  renderAll();
})(typeof window !== "undefined" ? window : this);
