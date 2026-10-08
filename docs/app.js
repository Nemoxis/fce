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
  return dateObj(iso).toLocaleDateString("it-IT", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
}
function shortDate(iso) { return dateObj(iso).toLocaleDateString("it-IT", { day: "numeric", month: "long", year: "numeric" }); }
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
  if (info.holiday) return [false, info.sunday ? "domenica" : "festivo"];
  if (trip.f.includes("nosat") && info.saturday) return [false, "non circola il sabato"];
  if (trip.f.includes("school") && !info.school) return [false, "solo nei giorni di scuola"];
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
  return d;
}

/* ------------------------------------------------------------ ricerca */
function search(d, fromK, toK, iso) {
  const A = d._stopByKey[fromK], B = d._stopByKey[toK];
  const info = dayInfo(iso, d.calendar);
  const res = [];
  let blockedLocal = 0;
  if (A === undefined || B === undefined) return { res, info, blockedLocal, missing: true };
  d.trips.forEach((t) => {
    const ids = t.s.map((x) => x[0]);
    for (let j = 0; j < ids.length; j++) {
      if (ids[j] !== B) continue;
      const i = ids.lastIndexOf(A, j - 1);
      if (i < 0 || j === 0) continue;
      const route = d.routes[t.r];
      if (route.noLocal.some((g) => g.includes(A) && g.includes(B))) { blockedLocal++; return; }
      const [runs, why] = runsOn(t, info);
      res.push({ t, route, i, j, dep: t.s[i][2], arr: t.s[j][1], runs, why });
      return;
    }
  });
  res.sort((a, b) => a.dep - b.dep || a.arr - b.arr);
  return { res, info, blockedLocal };
}
function reachableFrom(d, fromK) {
  const A = d._stopByKey[fromK];
  const out = new Set();
  if (A === undefined) return out;
  d.trips.forEach((t) => {
    const ids = t.s.map((x) => x[0]);
    const i = ids.indexOf(A);
    if (i < 0) return;
    const route = d.routes[t.r];
    ids.slice(i + 1).forEach((b) => {
      if (!route.noLocal.some((g) => g.includes(A) && g.includes(b))) out.add(d.stops[b].k);
    });
  });
  return out;
}

/* ------------------------------------------------------------ fermate: combobox */
function setupCombo(inputId, listId, which) {
  const input = $(inputId), list = $(listId);
  let items = [], active = -1;

  function render() {
    const d = state.data; if (!d) return;
    const q = input.value.trim().toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    const reach = which === "to" && state.from ? reachableFrom(d, state.from) : null;
    const all = d.stops.map((s) => s)
      .filter((s) => !q || s.name.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").includes(q) || s.k.toLowerCase().includes(q))
      .sort((a, b) => a.name.localeCompare(b.name, "it"));
    list.innerHTML = "";
    items = [];
    const groups = reach ? [["Raggiungibili con un bus diretto", all.filter((s) => reach.has(s.k))],
                           ["Altre fermate (nessun bus diretto)", all.filter((s) => !reach.has(s.k) && s.k !== state.from)]]
                         : [[null, all]];
    for (const [label, arr] of groups) {
      if (!arr.length) continue;
      if (label) { const g = document.createElement("li"); g.className = "group"; g.textContent = label; list.append(g); }
      for (const s of arr) {
        const li = document.createElement("li");
        li.role = "option"; li.textContent = s.name; li.dataset.k = s.k;
        if (reach && !reach.has(s.k)) li.classList.add("dim");
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
    if (d && k !== null) input.value = d.stops[d._stopByKey[k]]?.name || input.value;
  }, 120));
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      active = Math.max(0, Math.min(items.length - 1, active + (e.key === "ArrowDown" ? 1 : -1)));
      items.forEach((li, i) => li.setAttribute("aria-selected", String(i === active)));
      items[active]?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      const li = items[active] || items[0];
      if (li) { const d = state.data; choose(d.stops[d._stopByKey[li.dataset.k]]); }
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
    const a = d.stops[d._stopByKey[f.from]], b = d.stops[d._stopByKey[f.to]];
    if (!a || !b) continue;
    const btn = document.createElement("button");
    btn.className = "fav"; btn.type = "button";
    btn.innerHTML = `${short(a.name)} → ${short(b.name)}<span class="x" aria-label="Rimuovi">×</span>`;
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
  $("#from").value = state.from && d ? (d.stops[d._stopByKey[state.from]]?.name || "") : "";
  $("#to").value = state.to && d ? (d.stops[d._stopByKey[state.to]]?.name || "") : "";
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
  li.querySelector(".dep").textContent = hm(r.dep);
  li.querySelector(".arr").textContent = hm(r.arr);
  const dur = document.createElement("span"); dur.className = "dur"; dur.textContent = duration(r.arr - r.dep);
  li.querySelector(".track").append(dur);
  const nStops = r.j - r.i - 1;
  li.querySelector(".meta").textContent =
    `${r.route.name} · Corsa ${r.t.c} · ${nStops === 0 ? "diretta" : nStops === 1 ? "1 fermata intermedia" : `${nStops} fermate intermedie`}`;
  const tags = li.querySelector(".tags");
  const tag = (cls, txt) => { const s = document.createElement("span"); s.className = `tag ${cls}`; s.textContent = txt; tags.append(s); };
  if (isNext) tag("next", "Prossima");
  if (!r.runs) tag("off", `Non circola: ${r.why}`);
  if (r.t.f.includes("school")) tag("school", "Scolastica");
  if (r.t.f.includes("nosat")) tag("off", "No sabato");
  for (const n of r.t.n) tag(n.startsWith("Bus sostitutivo") ? "sost" : "off", n);

  const btn = li.querySelector(".trip-main"), ol = li.querySelector(".stops");
  btn.addEventListener("click", () => {
    const open = ol.hidden;
    if (open && !ol.childElementCount) {
      r.t.s.forEach(([sid, a, dp], idx) => {
        const s = document.createElement("li");
        if (idx === r.i || idx === r.j) s.classList.add("here");
        if (idx < r.i || idx > r.j) s.classList.add("outside");
        s.innerHTML = `<time>${hm(idx === r.j ? a : dp)}</time><span>${d.stops[sid].name}</span>`;
        ol.append(s);
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
  if (!d) { out.append(notice("Nessun orario disponibile", "La pipeline non ha ancora pubblicato dati.")); return; }

  $("#validity").textContent = `Orario in vigore dal ${shortDate(d.valid_from)}`;
  const src = d.source.url ? `<a href="${d.source.url}" rel="noopener">PDF ufficiale</a>` : `PDF ${d.source.file}`;
  $("#source").innerHTML = `Fonte: ${src}, elaborato il ${new Date(d.generated).toLocaleDateString("it-IT")}.`;
  document.querySelectorAll(".chip").forEach((c) =>
    c.setAttribute("aria-pressed", String(todayISO(+c.dataset.day) === state.date)));
  $("#date").value = state.date;
  syncTimeInput();
  renderFavs();

  if (!state.from || !state.to) {
    out.append(notice("Scegli partenza e arrivo", "Scrivi il nome del paese o della fermata. Puoi salvare i viaggi che fai spesso."));
    return;
  }
  if (state.from === state.to) { out.append(notice("Partenza e arrivo coincidono", "")); return; }

  const { res, info, blockedLocal, missing } = search(d, state.from, state.to, state.date);
  const h = document.createElement("h2");
  h.textContent = longDate(state.date).replace(/^./, (c) => c.toUpperCase()) +
    (state.time === null ? `, partenze dalle ${hm(from0())} (adesso)` : `, partenze dalle ${hm(state.time)}`);
  out.append(h);

  if (info.warn) out.append(notice(`Attenzione: ${info.warn.nome}`, "Nei giorni di festa patronale il servizio potrebbe cambiare. Controlla gli avvisi FCE."));
  if (missing) { out.append(notice("Fermata non presente in questo orario", "Prova a sceglierla di nuovo dall'elenco.")); return; }
  if (info.holiday) {
    out.append(notice("Le autolinee FCE non circolano", "Il servizio è sospeso la domenica e nei giorni festivi."));
    return;
  }
  if (!res.length) {
    out.append(blockedLocal
      ? notice("Tratta non servita per i passeggeri locali", "Su questa linea FCE non effettua servizio tra queste due fermate (nota del PDF: «non si effettua servizio per salita passeggeri»).")
      : notice("Nessun bus diretto tra queste fermate", "Potrebbe servire un cambio: prova con una fermata intermedia, ad esempio Paternò, Adrano o Catania – Metro Nesima."));
    return;
  }

  const running = res.filter((r) => r.runs);
  const notRunning = res.filter((r) => !r.runs);
  const isToday = state.date === todayISO();
  const from = effectiveTime();
  const ul = document.createElement("ul"); ul.className = "trips";
  const past = running.filter((r) => r.dep < from);
  const upcoming = running.filter((r) => r.dep >= from);

  if (!running.length) out.append(notice("Nessuna corsa in questo giorno", "Ci sono corse su questa tratta, ma non circolano nel giorno scelto."));
  if (past.length) {
    const more = document.createElement("button");
    more.className = "more"; more.type = "button";
    more.textContent = `Mostra ${past.length === 1 ? "la corsa precedente" : `le ${past.length} corse precedenti`}`;
    more.addEventListener("click", () => {
      // le corse passate entrano nella stessa lista, cosi' la spaziatura e' identica
      [...past].reverse().forEach((r) => ul.prepend(tripItem(r, d, false, isToday)));
      more.remove();
    });
    out.append(more);
  }
  upcoming.forEach((r, k) => ul.append(tripItem(r, d, k === 0, false)));
  if (running.length && !upcoming.length) out.append(isToday && state.time === null
    ? notice("Per oggi le corse sono finite", "Guarda gli orari di domani.")
    : notice(`Nessuna corsa dopo le ${hm(from)}`, "Tocca «mostra le corse precedenti» o cambia orario."));
  out.append(ul);

  if (notRunning.length) {
    const t = document.createElement("button");
    t.className = "more"; t.type = "button";
    t.textContent = state.showAll ? "Nascondi le corse che non circolano" : `Mostra ${notRunning.length} corse che in questo giorno non circolano`;
    t.addEventListener("click", () => { state.showAll = !state.showAll; refresh(); });
    out.append(t);
    if (state.showAll) {
      const ul2 = document.createElement("ul"); ul2.className = "trips";
      notRunning.forEach((r) => ul2.append(tripItem(r, d, false, false)));
      out.append(ul2);
    }
  }

  const favs = LS.get("fce-favs", []);
  if (!favs.some((f) => f.from === state.from && f.to === state.to)) {
    const s = document.createElement("button"); s.className = "save-fav"; s.type = "button";
    s.textContent = "Salva questo viaggio";
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
    const when = p.valid_from ? ` (in vigore dal ${shortDate(p.valid_from)})` : "";
    const link = p.url || "https://www.circumetnea.it/le-nostre-linee/";
    alert.innerHTML = `
      <strong>Attenzione: orari forse non aggiornati</strong>
      <p>FCE ha pubblicato un nuovo orario${when}, ma il controllo automatico non è riuscito a verificarlo.
      Gli orari mostrati qui sotto sono quelli <em>precedenti</em> e potrebbero essere sbagliati.</p>
      <p><a href="${link}" rel="noopener">Apri il PDF ufficiale FCE</a></p>
      ${p.reason ? `<p class="reason">Motivo: ${p.reason.replace(/</g, "&lt;")}</p>` : ""}`;
    alert.hidden = false;
  } else {
    alert.hidden = true;
  }
  const msgs = [];
  if (st.last_check) {
    const days = Math.round((dateObj(todayISO()) - dateObj(st.last_check)) / 864e5);
    if (days >= 2) msgs.push(`L'ultimo controllo del sito FCE risale a ${days} giorni fa.`);
  }
  if (msgs.length) { b.innerHTML = msgs.join("<br>"); b.hidden = false; } else b.hidden = true;
}

/* ------------------------------------------------------------ avvio */
async function init() {
  try {
    state.index = await getJSON("data/index.json");
  } catch (e) {
    $("#validity").textContent = "Orari non disponibili offline: apri l'app una volta con la connessione attiva.";
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
  document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => {
    state.date = todayISO(+c.dataset.day); state.dateAuto = +c.dataset.day === 0; refresh();
  }));
  $("#date").addEventListener("change", (e) => {
    if (e.target.value) { state.date = e.target.value; state.dateAuto = e.target.value === todayISO(); refresh(); }
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
