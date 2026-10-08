"use strict";

const $ = (s) => document.querySelector(s);
const state = {
  index: null,           // data/index.json
  cache: {},             // file -> orario
  data: null,            // orario per la data scelta
  from: null, to: null,  // chiavi fermata (k)
  date: todayISO(),
  dateAuto: true,        // true finche' l'utente non sceglie un altro giorno
  time: null,            // null = "adesso" (ora della ricerca); numero = scelta dall'utente
  showAll: false,
};

/* ------------------------------------------------------------ date utils */
function todayISO(offset = 0) {
  const d = new Date(); d.setDate(d.getDate() + offset);
  return d.toLocaleDateString("sv-SE");           // AAAA-MM-GG in ora locale
}
function hm(min) { return `${String(Math.floor(min / 60) % 24).padStart(2, "0")}:${String(min % 60).padStart(2, "0")}`; }
function nowMinutes() { const d = new Date(); return d.getHours() * 60 + d.getMinutes(); }
function effectiveTime() { return state.time !== null ? state.time : nowMinutes(); }
function syncTimeInput() {
  const t = $("#time");
  if (document.activeElement !== t) t.value = hm(effectiveTime());
  $("#now").hidden = state.time === null && state.date === todayISO();
}
function dateObj(iso) { const [y, m, d] = iso.split("-").map(Number); return new Date(y, m - 1, d); }
function longDate(iso) {
  return dateObj(iso).toLocaleDateString(LOCALE, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
}
function shortDate(iso) { return dateObj(iso).toLocaleDateString(LOCALE, { day: "numeric", month: "long", year: "numeric" }); }
function duration(m) { const h = Math.floor(m / 60); return h ? `${h} h ${String(m % 60).padStart(2, "0")}` : `${m} min`; }

/* ------------------------------------------------------------ calendario */
function dayInfo(iso, cal) {
  const d = dateObj(iso);
  const sunday = d.getDay() === 0, saturday = d.getDay() === 6;
  const holiday = sunday || cal.festivi.includes(iso);
  let school = false;
  if (!holiday) {
    for (const y of cal.scuola) {
      if (iso >= y.inizio && iso <= y.fine && !(y.sospensioni || []).some(([a, b]) => iso >= a && iso <= b)) school = true;
    }
  }
  const warn = (cal.avvisi || []).find((a) => a.data === iso);
  return { sunday, saturday, holiday, school, warn };
}
function runsOn(trip, info) {
  if (info.holiday) return [false, info.sunday ? t("whySunday") : t("whyHoliday")];
  if (trip.f.includes("nosat") && info.saturday) return [false, t("whyNoSat")];
  if (trip.f.includes("school") && !info.school) return [false, t("whySchool")];
  return [true, ""];
}

/* ------------------------------------------------------------ dati */
async function getJSON(url) {
  const r = await fetch(url, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}
function versionFor(iso) {
  const vs = [...state.index.versions].sort((a, b) => a.valid_from.localeCompare(b.valid_from));
  let pick = vs[0];
  for (const v of vs) if (v.valid_from <= iso) pick = v;
  return pick;
}
async function loadDataFor(iso) {
  const v = versionFor(iso);
  if (!v) return null;
  if (!state.cache[v.file]) state.cache[v.file] = await getJSON(`data/${v.file}`);
  const d = state.cache[v.file];
  d._version = v;
  d._stopByKey = Object.fromEntries(d.stops.map((s, i) => [s.k, i]));
  // paesi con piu' fermate -> voce "<paese> – tutte le fermate" (chiave "@paese")
  if (!d._groups) {
    const byTown = {};
    d.stops.forEach((s, i) => { const tw = s.t || s.name; (byTown[tw] ||= []).push(i); });
    d._groups = {};
    for (const [town, ids] of Object.entries(byTown)) {
      if (ids.length > 1) d._groups["@" + town] = { k: "@" + town, town, ids: new Set(ids), group: true };
    }
  }
  return d;
}

/* ------------------------------------------------------------ luoghi: fermata singola o paese intero */
function isGroup(k) { return typeof k === "string" && k.startsWith("@"); }
function placeIds(d, k) {
  if (isGroup(k)) return d._groups[k]?.ids;
  const i = d._stopByKey[k];
  return i === undefined ? undefined : new Set([i]);
}
function placeName(d, k) {
  if (!k) return "";
  if (isGroup(k)) return d._groups[k] ? t("allStops", { town: d._groups[k].town }) : "";
  return d.stops[d._stopByKey[k]]?.name || "";
}
function placeList(d) {
  const groups = Object.values(d._groups).map((g) => ({ k: g.k, name: t("allStops", { town: g.town }), town: g.town, group: true }));
  const stops = d.stops.map((s) => ({ k: s.k, name: s.name, town: s.t || s.name, group: false }));
  return [...groups, ...stops].sort((a, b) => a.town.localeCompare(b.town, LOCALE) || (b.group - a.group) || a.name.localeCompare(b.name, LOCALE));
}

/* ------------------------------------------------------------ ricerca */
const MIN_CHANGE = 3;        // minuti minimi per cambiare bus alla stessa fermata
const MAX_WAIT = 90;         // attesa massima accettata al cambio
const MAX_TOTAL = 180;       // durata massima di un viaggio con cambio (evita giri assurdi)

function indexTrips(d) {
  if (d._byStop) return;
  d._byStop = {};
  d.trips.forEach((t, ti) => t.s.forEach(([sid], pos) => { (d._byStop[sid] ||= []).push([ti, pos]); }));
}
function blocked(route, a, b) { return route.noLocal.some((g) => g.includes(a) && g.includes(b)); }
function leg(d, t, i, j) { return { t, route: d.routes[t.r], i, j, dep: t.s[i][2], arr: t.s[j][1] }; }

function search(d, fromK, toK, iso) {
  const info = dayInfo(iso, d.calendar);
  const res = [];
  let blockedLocal = 0;
  let SA = placeIds(d, fromK), SB = placeIds(d, toK);
  if (!SA || !SB) return { res, info, blockedLocal, missing: true };
  SA = new Set([...SA].filter((x) => !SB.has(x)));          // es. "Catania (tutte)" -> "Catania Borgo"
  if (!SA.size) return { res, info, blockedLocal, same: true };
  indexTrips(d);
  const runs = (t) => runsOn(t, info);

  // 1) corse dirette: si sale alla prima fermata di partenza, si scende alla prima di arrivo
  d.trips.forEach((t) => {
    const i = t.s.findIndex(([sid]) => SA.has(sid));
    if (i < 0) return;
    const j = t.s.findIndex(([sid], k) => k > i && SB.has(sid));
    if (j < 0) return;
    if (blocked(d.routes[t.r], t.s[i][0], t.s[j][0])) { blockedLocal++; return; }
    const [ok, why] = runs(t);
    const l = leg(d, t, i, j);
    res.push({ legs: [l], t, route: l.route, dep: l.dep, arr: l.arr, runs: ok, why });
  });

  // 2) un cambio alla stessa fermata (solo corse che circolano quel giorno)
  const transfers = [];
  d.trips.forEach((t1, t1i) => {
    if (!runs(t1)[0]) return;
    const i = t1.s.findIndex(([sid]) => SA.has(sid));
    if (i < 0) return;
    const A = t1.s[i][0];
    let best = null;
    for (let k = i + 1; k < t1.s.length; k++) {
      const X = t1.s[k][0];
      if (SA.has(X) || SB.has(X) || blocked(d.routes[t1.r], A, X)) continue;
      const a1 = t1.s[k][1];
      for (const [t2i, m] of d._byStop[X] || []) {
        if (t2i === t1i) continue;
        const t2 = d.trips[t2i];
        if (t2.r === t1.r) continue;          // stessa linea: il cambio servirebbe solo a tornare indietro
        const dep2 = t2.s[m][2], wait = dep2 - a1;
        // le navette di collegamento sono sincronizzate con i bus: cambio anche immediato
        const minChange = t1.f.includes("est") || t2.f.includes("est") ? 0 : MIN_CHANGE;
        if (wait < minChange || wait > MAX_WAIT) continue;
        const n = t2.s.findIndex(([sid], q) => q > m && SB.has(sid));
        if (n < 0 || blocked(d.routes[t2.r], X, t2.s[n][0]) || !runs(t2)[0]) continue;
        // niente percorsi "avanti e indietro": il secondo bus non deve ripassare da fermate gia' percorse
        const seen = new Set(t1.s.slice(i, k).map(([sid]) => sid));
        if (t2.s.slice(m + 1, n + 1).some(([sid]) => seen.has(sid))) continue;
        const l1 = leg(d, t1, i, k), l2 = leg(d, t2, m, n);
        if (l2.arr - l1.dep > MAX_TOTAL) continue;
        if (!best || l2.arr < best.arr || (l2.arr === best.arr && l2.dep > best.legs[1].dep)) {
          best = { legs: [l1, l2], t: t1, route: l1.route, dep: l1.dep, arr: l2.arr, runs: true, why: "", change: X, wait };
        }
      }
    }
    if (best) transfers.push(best);
  });

  // tieni solo i viaggi con cambio che convengono: nessun altro viaggio parte piu' tardi
  // (o insieme) e arriva prima (o insieme)
  const running = res.filter((r) => r.runs);
  for (const tr of transfers) {
    const dominated = [...running, ...transfers].some((o) => o !== tr &&
      o.dep >= tr.dep && o.arr <= tr.arr && (o.legs.length < tr.legs.length || o.dep > tr.dep || o.arr < tr.arr));
    const dup = res.some((o) => o.legs.length === 2 && o.dep === tr.dep && o.arr === tr.arr);
    if (!dominated && !dup) res.push(tr);
  }
  res.sort((a, b) => a.dep - b.dep || a.arr - b.arr || a.legs.length - b.legs.length);
  return { res, info, blockedLocal };
}

function reachableFrom(d, fromK) {
  const SA = placeIds(d, fromK);
  const direct = new Set(), oneChange = new Set();
  if (!SA) return { direct, oneChange };
  indexTrips(d);
  const hop = (S, set) => {
    for (const [ti, i] of d._byStop[S] || []) {
      const t = d.trips[ti], route = d.routes[t.r];
      t.s.slice(i + 1).forEach(([b]) => { if (!blocked(route, S, b)) set.add(b); });
    }
  };
  SA.forEach((a) => hop(a, direct));
  for (const X of direct) hop(X, oneChange);
  direct.forEach((x) => oneChange.delete(x));
  SA.forEach((x) => { direct.delete(x); oneChange.delete(x); });
  // un paese e' raggiungibile se lo e' almeno una sua fermata
  const keys = (set) => {
    const out = new Set([...set].map((i) => d.stops[i].k));
    for (const g of Object.values(d._groups)) if ([...g.ids].some((i) => set.has(i))) out.add(g.k);
    return out;
  };
  const dk = keys(direct), ok = keys(oneChange);
  dk.forEach((x) => ok.delete(x));
  return { direct: dk, oneChange: ok };
}

/* ------------------------------------------------------------ fermate: combobox */
function setupCombo(inputId, listId, which) {
  const input = $(inputId), list = $(listId);
  let items = [], active = -1, itemByKey = {};

  function render() {
    const d = state.data; if (!d) return;
    const q = input.value.trim().toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    const reach = which === "to" && state.from ? reachableFrom(d, state.from) : null;
    const norm = (x) => x.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    const all = placeList(d).filter((s) => !q || norm(s.name).includes(q) || norm(s.town).includes(q) || s.k.toLowerCase().includes(q));
    itemByKey = Object.fromEntries(all.map((s) => [s.k, s]));
    list.innerHTML = "";
    items = [];
    const groups = reach ? [[t("reachGroup"), all.filter((s) => reach.direct.has(s.k))],
                           [t("changeGroup"), all.filter((s) => reach.oneChange.has(s.k))],
                           [t("otherGroup"), all.filter((s) => !reach.direct.has(s.k) && !reach.oneChange.has(s.k) && s.k !== state.from)]]
                         : [[null, all]];
    for (const [label, arr] of groups) {
      if (!arr.length) continue;
      if (label) { const g = document.createElement("li"); g.className = "group"; g.textContent = label; list.append(g); }
      for (const s of arr) {
        const li = document.createElement("li");
        li.role = "option"; li.textContent = s.name; li.dataset.k = s.k;
        if (s.group) li.classList.add("grp");
        if (reach && !reach.direct.has(s.k) && !reach.oneChange.has(s.k)) li.classList.add("dim");
        li.addEventListener("mousedown", (e) => { e.preventDefault(); choose(s); });
        list.append(li); items.push(li);
      }
    }
    active = -1;
    list.hidden = items.length === 0;
    input.setAttribute("aria-expanded", String(!list.hidden));
  }
  function choose(s) {
    state[which] = s.k; input.value = s.name; close(); saveLast(); refresh();
    if (which === "from" && !state.to) $("#to").focus();
  }
  function close() { list.hidden = true; input.setAttribute("aria-expanded", "false"); }
  input.addEventListener("focus", () => { input.select(); render(); });
  input.addEventListener("input", render);
  input.addEventListener("blur", () => setTimeout(() => {
    close();
    const d = state.data; const k = state[which];
    if (d && k !== null) input.value = placeName(d, k) || input.value;
  }, 120));
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      active = Math.max(0, Math.min(items.length - 1, active + (e.key === "ArrowDown" ? 1 : -1)));
      items.forEach((li, i) => li.setAttribute("aria-selected", String(i === active)));
      items[active]?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      const li = items[active] || items[0];
      if (li) choose(itemByKey[li.dataset.k]);
    } else if (e.key === "Escape") close();
  });
}

/* ------------------------------------------------------------ preferiti / memoria */
const LS = { get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
             set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} } };
function saveLast() { LS.set("fce-last", { from: state.from, to: state.to }); }
function renderFavs() {
  const box = $("#favs"); box.innerHTML = "";
  const d = state.data; if (!d) return;
  for (const f of LS.get("fce-favs", [])) {
    const a = { name: placeName(d, f.from) }, b = { name: placeName(d, f.to) };
    if (!a.name || !b.name) continue;
    const btn = document.createElement("button");
    btn.className = "fav"; btn.type = "button";
    btn.innerHTML = `${short(a.name)} → ${short(b.name)}<span class="x" aria-label="${t("remove")}">×</span>`;
    btn.addEventListener("click", (e) => {
      if (e.target.classList.contains("x")) {
        LS.set("fce-favs", LS.get("fce-favs", []).filter((x) => !(x.from === f.from && x.to === f.to)));
        renderFavs(); return;
      }
      state.from = f.from; state.to = f.to; syncInputs(); saveLast(); refresh();
    });
    box.append(btn);
  }
}
function short(n) { return n.replace(/^Catania – /, "").replace(/ \(.*\)$/, ""); }

/* ------------------------------------------------------------ rendering */
function syncInputs() {
  const d = state.data;
  $("#from").value = state.from && d ? placeName(d, state.from) : "";
  $("#to").value = state.to && d ? placeName(d, state.to) : "";
}
function notice(title, text) {
  const n = document.createElement("div"); n.className = "notice";
  n.innerHTML = `<strong>${title}</strong>${text || ""}`;
  return n;
}
function tripItem(r, d, isNext, gone) {
  const li = $("#trip-tpl").content.firstElementChild.cloneNode(true);
  if (isNext) li.classList.add("next");
  if (gone) li.classList.add("gone");
  if (!r.runs) li.classList.add("off");
  const last = r.legs[r.legs.length - 1];
  const estArr = last.t.f.includes("est");
  li.querySelector(".dep").textContent = hm(r.dep);
  li.querySelector(".arr").textContent = (estArr ? "~" : "") + hm(r.arr);
  const dur = document.createElement("span"); dur.className = "dur"; dur.textContent = duration(r.arr - r.dep);
  li.querySelector(".track").append(dur);
  if (r.legs.length > 1) li.querySelector(".track").classList.add("with-change");

  const meta = li.querySelector(".meta");
  if (isGroup(state.from) || isGroup(state.to)) {
    const where = document.createElement("span"); where.className = "where";
    const first = r.legs[0], lastL = r.legs[r.legs.length - 1];
    where.textContent = `${d.stops[first.t.s[first.i][0]].name} → ${d.stops[lastL.t.s[lastL.j][0]].name}`;
    meta.dataset.where = where.textContent;
  }
  if (r.legs.length === 1) {
    const nStops = r.legs[0].j - r.legs[0].i - 1;
    meta.textContent = `${r.route.name} · ${t("trip", { c: r.t.c })} · ${nStops === 0 ? t("direct") : nStops === 1 ? t("oneStop") : t("nStops", { n: nStops })}`;
  } else {
    meta.textContent = `${t("changeAt", { stop: d.stops[r.change].name, m: r.wait })} · ${r.legs.map((l) => l.route.name).join(" → ")}`;
  }

  if (meta.dataset.where) {
    const w = document.createElement("span"); w.className = "where"; w.textContent = meta.dataset.where;
    meta.prepend(w);
  }
  const tags = li.querySelector(".tags");
  const tag = (cls, txt) => { const s = document.createElement("span"); s.className = `tag ${cls}`; s.textContent = txt; tags.append(s); };
  if (isNext) tag("next", t("next"));
  if (r.legs.length > 1) tag("change", t("oneChange"));
  if (!r.runs) tag("off", t("notRuns", { why: r.why }));
  const allFlags = new Set(r.legs.flatMap((l) => l.t.f));
  if (allFlags.has("school")) tag("school", t("school"));
  if (allFlags.has("nosat")) tag("off", t("noSat"));
  if (estArr) tag("off", t("estimated"));
  else if (allFlags.has("est")) tag("off", t("estimatedChange"));
  const notes = new Set(r.legs.flatMap((l) => l.t.n));
  for (const n of notes) {
    if (n.startsWith("Bus sostitutivo")) tag("sost", t("replacement"));
    else if (!n.startsWith("Navetta di collegamento")) tag("off", n);
  }

  const btn = li.querySelector(".trip-main"), ol = li.querySelector(".stops");
  btn.addEventListener("click", () => {
    const open = ol.hidden;
    if (open && !ol.childElementCount) {
      const single = r.legs.length === 1;
      r.legs.forEach((l, li2) => {
        if (li2 > 0) {
          const c = document.createElement("li"); c.className = "change";
          c.textContent = t("changeAt", { stop: d.stops[r.change].name, m: r.wait });
          ol.append(c);
        }
        const est = l.t.f.includes("est");
        l.t.s.forEach(([sid, a, dp], idx) => {
          if (!single && (idx < l.i || idx > l.j)) return;    // con il cambio mostro solo i tratti percorsi
          const s = document.createElement("li");
          if (idx === l.i || idx === l.j) s.classList.add("here");
          if (idx < l.i || idx > l.j) s.classList.add("outside");
          const time = hm(idx === l.j ? a : dp);
          s.innerHTML = `<time>${est && idx > 0 ? "~" + time : time}</time><span>${d.stops[sid].name}</span>`;
          ol.append(s);
        });
      });
    }
    ol.hidden = !open; btn.setAttribute("aria-expanded", String(open));
  });
  return li;
}

function from0() { return effectiveTime(); }

async function refresh() {
  const out = $("#results");
  out.innerHTML = "";
  state.data = await loadDataFor(state.date);
  const d = state.data;
  if (!d) { out.append(notice(t("noData"), t("noDataText"))); return; }

  $("#validity").textContent = t("validity", { d: shortDate(d.valid_from) });
  const src = d.source.url ? `<a href="${d.source.url}" rel="noopener">${t("officialPdf")}</a>` : `PDF ${d.source.file}`;
  $("#source").innerHTML = t("source", { src, d: new Date(d.generated).toLocaleDateString(LOCALE) });
  $("#date").value = state.date;
  syncTimeInput();
  renderFavs();

  if (!state.from || !state.to) {
    out.append(notice(t("choose"), t("chooseText")));
    return;
  }
  if (state.from === state.to) { out.append(notice(t("same"), "")); return; }

  const { res, info, blockedLocal, missing, same } = search(d, state.from, state.to, state.date);
  if (same) { out.append(notice(t("same"), "")); return; }
  const h = document.createElement("h2");
  h.textContent = t("heading", { date: longDate(state.date).replace(/^./, (c) => c.toUpperCase()), t: hm(from0()) }) +
    (state.time === null && state.date === todayISO() ? t("nowSuffix") : "");
  out.append(h);

  if (info.warn) out.append(notice(t("feastTitle", { name: info.warn.nome }), t("feastText")));
  if (missing) { out.append(notice(t("missing"), t("missingText"))); return; }
  if (info.holiday) {
    out.append(notice(t("holiday"), t("holidayText")));
    return;
  }
  if (!res.length) {
    out.append(blockedLocal
      ? notice(t("noLocal"), t("noLocalText"))
      : notice(t("noDirect"), t("noDirectText")));
    return;
  }

  const running = res.filter((r) => r.runs);
  const notRunning = res.filter((r) => !r.runs);
  const isToday = state.date === todayISO();
  const from = effectiveTime();
  const ul = document.createElement("ul"); ul.className = "trips";
  const past = running.filter((r) => r.dep < from);
  const upcoming = running.filter((r) => r.dep >= from);

  if (!running.length) out.append(notice(t("noneDay"), t("noneDayText")));
  if (past.length) {
    const more = document.createElement("button");
    more.className = "more"; more.type = "button";
    more.textContent = past.length === 1 ? t("showPast1") : t("showPastN", { n: past.length });
    more.addEventListener("click", () => {
      // le corse passate entrano nella stessa lista, cosi' la spaziatura e' identica
      [...past].reverse().forEach((r) => ul.prepend(tripItem(r, d, false, isToday)));
      more.remove();
    });
    out.append(more);
  }
  upcoming.forEach((r, k) => ul.append(tripItem(r, d, k === 0, false)));
  if (running.length && !upcoming.length) out.append(isToday && state.time === null
    ? notice(t("endToday"), t("endTodayText"))
    : notice(t("noneAfter", { t: hm(from) }), t("noneAfterText")));
  out.append(ul);

  if (notRunning.length) {
    const tg = document.createElement("button");
    tg.className = "more"; tg.type = "button";
    tg.textContent = state.showAll ? t("hideOff") : t("showOff", { n: notRunning.length });
    tg.addEventListener("click", () => { state.showAll = !state.showAll; refresh(); });
    out.append(tg);
    if (state.showAll) {
      const ul2 = document.createElement("ul"); ul2.className = "trips";
      notRunning.forEach((r) => ul2.append(tripItem(r, d, false, false)));
      out.append(ul2);
    }
  }

  const favs = LS.get("fce-favs", []);
  if (!favs.some((f) => f.from === state.from && f.to === state.to)) {
    const s = document.createElement("button"); s.className = "save-fav"; s.type = "button";
    s.textContent = t("save");
    s.addEventListener("click", () => { favs.push({ from: state.from, to: state.to }); LS.set("fce-favs", favs); refresh(); });
    out.append(s);
  }
}

function showStatus() {
  const st = state.index.status || {};
  const alert = $("#alert"), b = $("#banner");
  // ALLARME ROSSO: FCE ha pubblicato un orario nuovo che non ha superato i controlli
  const pend = (st.pending || []).map((p) => (typeof p === "string" ? { title: p } : p));
  if (pend.length) {
    const p = pend[0];
    const when = p.valid_from ? t("alertWhen", { d: shortDate(p.valid_from) }) : "";
    const link = p.url || "https://www.circumetnea.it/le-nostre-linee/";
    alert.innerHTML = `
      <strong>${t("alertTitle")}</strong>
      <p>${t("alertText", { when })}</p>
      <p><a href="${link}" rel="noopener">${t("alertLink")}</a></p>
      ${p.reason ? `<p class="reason">${t("alertReason")}: ${p.reason.replace(/</g, "&lt;")}</p>` : ""}`;
    alert.hidden = false;
  } else {
    alert.hidden = true;
  }
  const msgs = [];
  if (st.last_check) {
    const days = Math.round((dateObj(todayISO()) - dateObj(st.last_check)) / 864e5);
    if (days >= 2) msgs.push(t("lastCheck", { n: days }));
  }
  if (msgs.length) { b.innerHTML = msgs.join("<br>"); b.hidden = false; } else b.hidden = true;
}

/* ------------------------------------------------------------ avvio */
async function init() {
  applyStaticI18n();
  try {
    state.index = await getJSON("data/index.json");
  } catch (e) {
    $("#validity").textContent = t("offline");
    return;
  }
  showStatus();
  const last = LS.get("fce-last", {});
  state.from = last.from || null; state.to = last.to || null;
  state.data = await loadDataFor(state.date);
  syncInputs();

  setupCombo("#from", "#from-list", "from");
  setupCombo("#to", "#to-list", "to");
  $("#swap").addEventListener("click", () => { [state.from, state.to] = [state.to, state.from]; syncInputs(); saveLast(); refresh(); });
  $("#date").addEventListener("change", (e) => {
    if (e.target.value) { state.date = e.target.value; state.dateAuto = e.target.value === todayISO(); }
    else { state.date = todayISO(); state.dateAuto = true; }      // data cancellata: torna a oggi
    refresh();
  });
  $("#time").addEventListener("change", (e) => {
    const v = e.target.value; state.time = v ? (+v.slice(0, 2)) * 60 + (+v.slice(3, 5)) : null; refresh();
  });
  $("#now").addEventListener("click", () => { state.time = null; state.date = todayISO(); state.dateAuto = true; refresh(); });
  await refresh();

  // L'ora predefinita e' sempre quella attuale: ogni minuto la lista si riallinea,
  // e quando riapri l'app (anche il giorno dopo) riparte da oggi e da adesso.
  setInterval(() => { if (state.time === null && !document.hidden) refresh(); }, 60_000);
  let lastIndexCheck = Date.now();
  document.addEventListener("visibilitychange", async () => {
    if (document.hidden) return;
    if (state.dateAuto) state.date = todayISO();
    if (Date.now() - lastIndexCheck > 10 * 60_000) {           // nuovi orari pubblicati?
      try { state.index = await getJSON("data/index.json"); state.cache = {}; showStatus(); } catch {}
      lastIndexCheck = Date.now();
    }
    refresh();
  });

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
}
init();
