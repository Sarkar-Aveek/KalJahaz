// KalJahaz dashboard: fetches /api/meta once, /api/forecast on every voyage change.
const $ = (id) => document.getElementById(id);
const API = window.KALJAHAZ_API || ""; // set in config.js when the backend is elsewhere
const CLASSES = ["handysize", "supramax", "panamax", "capesize"];
const HZ_LABEL = { now: "Today", d7: "+7 days", d30: "+30 days", d60: "+60 days" };
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const C = {};
["--ink", "--ink-2", "--rule", "--rule-soft", "--hull", "--antifoul", "--green", "--brass", "--hull-ink-2"].forEach((k) => (C[k.slice(2)] = css(k)));
const COST_PARTS = [
  ["hire_sea", "Charter hire at sea", "#0B2E6B"],
  ["hire_port", "Hire in port", "#3D6BB3"],
  ["hire_wait", "Waiting: congestion, tides", "#9DB4D9"],
  ["bunkers", "Bunkers", "#2E7C8C"],
  ["port_dues", "Port dues", "#5C8A3A"],
  ["commission", "Commissions", "#B8C0CC"],
];

// Display currency: USD from the model, or INR at the day's reference rate (set in initCurrency).
const CUR = { code: "USD", rate: 1 };
const money = (n, step = 100) => CUR.code === "INR"
  ? "₹" + (Math.round((n * CUR.rate) / step) * step).toLocaleString("en-IN")
  : "$" + (Math.round(n / step) * step).toLocaleString("en-US");
const per = (n) => CUR.code === "INR"
  ? "₹" + (n * CUR.rate).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  : "$" + n.toFixed(2);
const int = (n) => Math.round(n).toLocaleString("en-US");
const pct = (n, d = 0) => (n * 100).toFixed(d) + "%";
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const icon = (id, cls = "") => `<svg class="${cls}" aria-hidden="true"><use href="#${id}"/></svg>`;
const fmtDate = (iso) => new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short" });

let META, LAST, BASE, LIMITS = {}, selectedDay = 0, charts = {}, map, layers = {};
let FX_LIVE = null, setCurrency = () => {};

// ---------- DWT slider: each class owns a quarter of the track
function dwtFromSlider(v) {
  const seg = Math.min(3, Math.floor(v / 250)), [lo, hi] = META.vessels[CLASSES[seg]].dwt;
  return { cls: CLASSES[seg], dwt: Math.round((lo + ((v - seg * 250) / 250) * (hi - lo)) / 500) * 500 };
}
function sliderFromDwt(cls, dwt) {
  const seg = CLASSES.indexOf(cls), [lo, hi] = META.vessels[cls].dwt;
  return Math.min(seg * 250 + 249, seg * 250 + ((dwt - lo) / (hi - lo)) * 250);
}
function paintSlider() {
  const s = $("dwt");
  s.style.setProperty("--fill", (s.value / 10) + "%");
}
function renderTicks() {
  const parts = CLASSES.map((c, i) => {
    const v = META.vessels[c];
    let h = `<span class="tick tick--class" style="left:${i * 25}%"></span><span class="tick__label" style="left:${i * 25 + 12.5}%">${v.name}</span>`;
    const lim = LIMITS[c];
    if (lim && lim < v.dwt[1]) h += `<span class="tick tick--limit" style="left:${sliderFromDwt(c, lim) / 10}%" title="Full cargo limit ${int(lim)} DWT"></span>`;
    return h;
  });
  $("ticks").innerHTML = parts.join("");
}
function dwtNote() {
  const cls = $("cls").value, lim = LIMITS[cls], dwt = +$("dwtOut").dataset.dwt;
  const v = META.vessels[cls];
  if (lim === null) return ($("dwtNote").innerHTML = `<b>No ${v.name} loads full on this route.</b>`);
  if (lim < v.dwt[1]) {
    $("dwtNote").innerHTML = dwt > lim
      ? `<b>Above ${int(lim)} DWT the ship sails part-loaded</b> (red mark).`
      : `Full cargo up to ${int(lim)} DWT on this route (red mark).`;
  } else $("dwtNote").textContent = `${v.name}, ${int(v.dwt[0])} to ${int(v.dwt[1])} DWT. Priced on the ${v.index}.`;
}
function setDwt(cls, dwt) {
  $("cls").value = cls;
  $("dwt").value = sliderFromDwt(cls, dwt);
  $("dwtOut").textContent = int(dwt);
  $("dwtOut").dataset.dwt = dwt;
  paintSlider();
  dwtNote();
}

// ---------- setup
async function init() {
  initCharts();
  try {
    META = await (await fetch(API + "/api/meta")).json();
  } catch (e) {
    return showError("The forecast server isn't reachable", "Start it with py server.py and reload this page.");
  }
  const asOf = `${fmtDate(META.data.last_date)} ${META.data.last_date.slice(0, 4)}`;
  $("dataLabel").textContent = `${META.data.label} to ${asOf}`;
  const src = META.data.sources || {};
  $("updated").textContent = src.refreshed
    ? `Market data ${src.live ? "refreshed" : "last refreshed (live feed unreachable now)"} ${new Date(src.refreshed).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })}`
    : `Market data to ${asOf}`;
  $("srcTable").innerHTML = `<thead><tr><th>Series</th><th>Source</th><th>Status</th><th>Latest value from</th><th>Note</th></tr></thead><tbody>` +
    Object.values(src.series || {}).map((s) => `<tr><td>${esc(s.label)}</td><td class="wrap">${esc(s.source)}</td>
      <td><span class="${s.live ? "live" : "saved"}">${s.live ? "Live" : "Saved"}</span></td><td>${esc(s.latest || "–")}</td><td class="wrap">${esc(s.note || "")}</td></tr>`).join("") + "</tbody>";
  const byCountry = {};
  Object.entries(META.origins).forEach(([k, p]) => (byCountry[p.country] ??= []).push([k, p]));
  $("origin").innerHTML = Object.entries(byCountry).map(([c, ps]) =>
    `<optgroup label="${c}">${ps.map(([k, p]) => `<option value="${k}">${esc(p.name)}</option>`).join("")}</optgroup>`).join("");
  $("dest").innerHTML = Object.entries(META.destinations).map(([k, p]) => `<option value="${k}">${esc(p.name)}</option>`).join("");
  $("cls").innerHTML = CLASSES.map((c) => `<option value="${c}">${META.vessels[c].name}</option>`).join("");
  const u = new URLSearchParams(location.search), cls0 = CLASSES.includes(u.get("cls")) ? u.get("cls") : "panamax";
  $("origin").value = META.origins[u.get("origin")] ? u.get("origin") : "NEW";
  $("dest").value = META.destinations[u.get("dest")] ? u.get("dest") : "PRT";
  const [lo0, hi0] = META.vessels[cls0].dwt;
  setDwt(cls0, Math.min(hi0, Math.max(lo0, +u.get("dwt") || META.vessels[cls0].typical)));
  initMap();

  $("origin").addEventListener("change", () => refresh(true));
  $("dest").addEventListener("change", () => refresh(true));
  $("cls").addEventListener("change", () => { setDwt($("cls").value, META.vessels[$("cls").value].typical); refresh(); });
  $("dwt").addEventListener("input", () => {
    const { cls, dwt } = dwtFromSlider(+$("dwt").value);
    $("cls").value = cls; $("dwtOut").textContent = int(dwt); $("dwtOut").dataset.dwt = dwt;
    paintSlider(); dwtNote(); refresh();
  });
  refresh(true);
}

let timer, seq = 0;
function refresh(routeChanged = false) {
  clearTimeout(timer);
  $("forecast").setAttribute("aria-busy", "true");
  timer = setTimeout(() => load(routeChanged), 220);
}
async function load(routeChanged) {
  const id = ++seq, o = $("origin").value, d = $("dest").value;
  const q = new URLSearchParams({ origin: o, dest: d, cls: $("cls").value, dwt: $("dwtOut").dataset.dwt });
  const sq = new URLSearchParams(q);
  Object.entries(SHOCK).forEach(([k, v]) => { if (k !== "fx" && v) sq.set("s_" + k, v); });
  const scen = sq.toString() !== q.toString();
  try {
    const [base, sc, lim] = await Promise.all([
      fetch(API + "/api/forecast?" + q).then((x) => x.json()),
      scen ? fetch(API + "/api/forecast?" + sq).then((x) => x.json()) : Promise.resolve(null),
      routeChanged ? fetch(`${API}/api/limits?origin=${o}&dest=${d}`).then((x) => x.json()) : Promise.resolve(null),
    ]);
    if (id !== seq) return;
    const r = sc || base;
    if (base.error || r.error) throw new Error(base.error || r.error);
    if (lim) { LIMITS = lim; renderTicks(); dwtNote(); }
    BASE = base;
    LAST = r;
    selectedDay = r.best_day;
    render(r, routeChanged);
  } catch (e) {
    if (id === seq) showError("Couldn't price this voyage", e.message + ". Check the server window, then change any input to retry.");
  } finally {
    if (id === seq) $("forecast").setAttribute("aria-busy", "false");
  }
}
function showError(title, text) {
  $("verdict").innerHTML = `<div class="error"><h3>${esc(title)}</h3><p>${esc(text)}</p></div>`;
  $("ladder").innerHTML = "";
  $("forecast").setAttribute("aria-busy", "false");
}

function render(r, routeChanged) {
  renderWhatIf();
  renderVerdict(r); renderLadder(r); renderPie(r); renderFit(r); renderKeel(r); renderLegs(r);
  renderVesselFit(r); renderMarket(r); renderRisks(r); renderIdle(r); renderMap(r, routeChanged);
  // annotate class options with what this route allows
  r.vessel_fit.forEach((f) => {
    const opt = $("cls").querySelector(`option[value="${f.cls}"]`);
    const note = { permitted: "", part_cargo: " (part cargo)", forbidden: " (not permitted)", not_viable: " (not viable)" }[f.status];
    opt.textContent = META.vessels[f.cls].name + note;
  });
}

// ---------- decision band
function renderVerdict(r) {
  const p = r.permission, v = r.vessel, now = r.horizons[0];
  const meta = [`<li class="chip"><b>${v.name}</b> ${int(v.dwt)} DWT</li>`];
  if (p.status === "permitted") meta.push(`<li class="chip chip--ok">Full cargo ${int(p.cargo)} t</li>`);
  if (p.status === "part_cargo") meta.push(`<li class="chip chip--no">Part cargo ${int(p.cargo)} t of ${int(p.intake)} t</li>`);
  meta.push(`<li class="chip">Priced on <b>${v.index_name}</b></li>`);
  meta.push(`<li class="chip">${int(r.route.total_nm)} nm by sea</li>`);
  if (p.status === "forbidden" || p.status === "not_viable") {
    const alt = r.best_fit && r.best_fit !== r.inputs.cls ? META.vessels[r.best_fit] : null;
    $("verdict").innerHTML = `
      <h3 class="verdict__head verdict__head--no">${p.status === "forbidden" ? "Not permitted" : "Not viable"}</h3>
      <p class="verdict__save">${esc(p.reason)}.${p.status === "not_viable" ? ` It could load only ${int(p.cargo)} t.` : ""}</p>
      <ul class="verdict__meta">${meta.slice(0, 1).join("")}<li class="chip chip--no">${p.status === "forbidden" ? "Fails port limits" : "Under 40% of intake"}</li></ul>
      ${alt ? `<button class="btn" type="button" data-use="${r.best_fit}">Switch to ${alt.name}, the cheapest class that fits</button>` : ""}`;
    bindUse($("verdict"));
    return;
  }
  const c0 = r.curve[0], bd = r.curve[r.best_day];
  let save;
  if (r.best_day > 0) {
    save = `Saves <b>${money(r.best_saving)}</b> against fixing today, ${pct(r.best_saving / c0.total, 1)} of the voyage cost.`;
  } else {
    const next = r.curve.slice(1).reduce((a, b) => (b.total < a.total ? b : a));
    save = `Waiting costs more: the cheapest later date (${fmtDate(next.date)}) is <b>${money(next.total - c0.total)}</b> dearer.`;
  }
  const overlap = r.best_day > 0 && bd.hi > c0.total;
  $("verdict").innerHTML = `
    <h3 class="verdict__head">${icon("i-plimsoll")}${r.best_day ? "Fix on " + fmtDate(bd.date) : "Fix today"}</h3>
    <p class="verdict__when">${r.best_day === 0 ? "The cheapest day in the next 60"
      : r.best_day === 60 ? "The last day of the 60-day window: rates are still easing there, so recheck nearer the date"
      : `${r.best_day} days from today, the cheapest day in the next 60`}</p>
    <p class="verdict__save">${save}${overlap ? ` The forecast range still overlaps today's price, so treat it as likely, not certain.` : ""}</p>
    <dl class="verdict__facts">
      <div><dt>Estimated voyage</dt><dd>${bd.days.total.toFixed(1)} days</dd><dd class="sub">${bd.days.sea.toFixed(1)} at sea, ${(bd.days.port + bd.days.wait).toFixed(1)} in port and waiting</dd></div>
      <div><dt>Estimated price</dt><dd>${money(bd.total)}</dd><dd class="sub">${bd.accuracy == null ? "today's market rate" : `${(bd.accuracy * 100).toFixed(0)}% forecast accuracy`}</dd></div>
    </dl>
    <ul class="verdict__meta">${meta.join("")}</ul>
    <div class="contract"><h3>${esc(r.contract.kind)}</h3><p>${esc(r.contract.text)}</p></div>`;
}
function bindUse(root) {
  root.querySelectorAll("[data-use]").forEach((b) => b.addEventListener("click", () => {
    const c = b.dataset.use;
    setDwt(c, META.vessels[c].typical); refresh();
    document.getElementById("forecast").scrollIntoView({ block: "start" });
  }));
}

// ---------- fixing windows: today, +7, +30, +60 and the recommended day (from the daily curve)
function renderLadder(r) {
  const p = r.permission;
  if (p.status === "forbidden" || p.status === "not_viable") {
    const fails = p.checks.filter((c) => !c.ok && (p.status === "not_viable" || c.item !== "Draft"));
    $("ladder").innerHTML = `<div class="refusal" role="list" aria-label="Limits this vessel fails">${fails.map((c, i) =>
      `<div class="refusal__row${i === 0 ? " refusal__row--bind" : ""}" role="listitem"><span>${c.item} at ${esc(c.port)}</span><b>${c.vessel} ${c.unit}</b><span>limit ${c.limit} ${c.unit}</span></div>`).join("")}</div>`;
    return;
  }
  const cv = r.curve, best = r.best_day;
  const days = [...new Set([0, 7, 30, 60, best])].sort((a, b) => a - b);
  const when = (d) => (d === 0 ? "Today" : `+${d} days`);
  const lo = Math.min(...days.map((d) => cv[d].lo)), hi = Math.max(...days.map((d) => cv[d].hi));
  const x = (v) => ((v - lo) / (hi - lo || 1)) * 100;
  $("ladder").innerHTML = `<div class="rungs">${days.map((d) => {
    const c = cv[d], isBest = d === best;
    return `<button type="button" class="rung${isBest ? " rung--best" : ""}" data-day="${d}" aria-pressed="${d === selectedDay}"
      aria-label="${when(d)}, fix ${fmtDate(c.date)}: ${money(c.total)} total, ${per(c.per_t)} per tonne${isBest ? ", recommended" : ""}">
      <span class="rung__when">${when(d)}<small>fix ${fmtDate(c.date)}</small><small>${c.days.total.toFixed(1)} days voyage</small></span>
      <span class="rung__cost">${money(c.total)}${isBest ? `<span class="tag tag--ok">Recommended</span>` : ""}
        <small class="rung__acc">${c.accuracy == null ? "today's market rate" : `${(c.accuracy * 100).toFixed(0)}% forecast accuracy`}</small></span>
      <span class="rung__per"><b>${per(c.per_t)}/t</b>hire ${money(c.hire, 1)}/day</span>
      <span class="rung__band" aria-hidden="true"><i style="left:${x(c.lo)}%;width:${x(c.hi) - x(c.lo)}%"></i><em style="left:${x(c.total)}%"></em></span>
    </button>`;
  }).join("")}</div>
  <p class="rungs__note">The recommended date is the cheapest of every day from today to +60. Voyage time runs from fixing to discharge: loading, sea passage, waiting and discharge. Forecast accuracy is 100% minus the model's average error on past ${r.vessel.index_name} forecasts made the same distance ahead; it covers the freight rate, not port delays or weather. Bars show each date's 80% range. Select a row for its cost breakdown.</p>`;
  $("ladder").querySelectorAll(".rung").forEach((b) => b.addEventListener("click", () => {
    selectedDay = +b.dataset.day;
    $("ladder").querySelectorAll(".rung").forEach((o) => o.setAttribute("aria-pressed", o === b));
    renderPie(LAST);
  }));
}

// ---------- cost split
function renderPie(r) {
  const h = r.curve[selectedDay] || r.curve[0];
  const vals = COST_PARTS.map(([k]) => h.cost[k]);
  charts.pie.data.datasets[0].data = vals;
  charts.pie.update();
  const blocked = r.permission.status === "forbidden" || r.permission.status === "not_viable";
  $("costSub").textContent = blocked
    ? `Hypothetical only: this vessel can't work the route. Shown for comparison, fixing ${fmtDate(h.date)}.`
    : `Fixing on ${fmtDate(h.date)}${h.day === r.best_day ? " (recommended)" : ""}, ${h.days.total.toFixed(1)} days on charter. Select another row to compare.`;
  $("costTable").innerHTML = COST_PARTS.map(([k, label, col]) =>
    `<tr><td><span class="swatch" style="background:${col}"></span>${label}</td><td>${money(h.cost[k])}</td><td>${pct(h.cost[k] / h.total)}</td></tr>`).join("")
    + `<tr><td>Total voyage cost</td><td>${money(h.total)}</td><td>${per(h.per_t)}/t</td></tr>`;
}

// ---------- fit: limit checks
function renderFit(r) {
  const p = r.permission, v = r.vessel;
  const words = { permitted: "Fits both ports and the lane with a full cargo.", part_cargo: `Draft-limited at ${p.limiting}: loads ${int(p.cargo)} t of a ${int(p.intake)} t intake.`,
    forbidden: "Fails a hard limit: " + p.reason + ".", not_viable: `Draft limit at ${p.limiting} leaves only ${int(p.cargo)} t.` };
  $("fitSub").textContent = words[p.status];
  const mark = (c) => c.ok ? icon("i-ok", "mark mark--ok") : (c.item === "Draft" ? icon("i-part", "mark mark--part") : icon("i-no", "mark mark--no"));
  $("checks").innerHTML = `<tr><th></th><th>Where</th><th>Vessel</th><th>Limit</th></tr>` + p.checks.map((c) =>
    `<tr><td>${mark(c)}</td><td>${c.item} at ${esc(c.port)}</td><td>${c.vessel} ${c.unit}</td><td>${c.limit} ${c.unit}</td></tr>`).join("");
}

// ---------- keel chart: the laden keel across every discharge port (from the ship/ prototype)
function renderKeel(r) {
  const dims = r.vessel, sel = r.inputs.dest, ports = META.destinations, names = Object.keys(ports);
  const W = 960, H = 340, top = 70, left = 96, scale = 10.2;
  const colW = (W - left - 8) / names.length, y = (m) => top + m * scale, keelY = y(dims.draft);
  let s = `<defs><pattern id="seabed" width="8" height="8" patternUnits="userSpaceOnUse"><rect width="8" height="8" fill="var(--k-buff)"/><circle cx="2" cy="2" r="1" fill="var(--k-buff-ink)" opacity=".35"/></pattern>
    <pattern id="touch" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="7" height="7" fill="var(--k-paper)"/><rect width="3" height="7" fill="var(--k-magenta)"/></pattern></defs>`;
  [0, 5, 10, 15, 20].filter((m) => Math.abs(m - dims.draft) > 3.2).forEach((m) => {
    s += `<g class="depth-tick"><line x1="${left - 10}" x2="${left - 4}" y1="${y(m)}" y2="${y(m)}"/><text x="${left - 14}" y="${y(m) + 5}" text-anchor="end">${m} m</text></g>`;
  });
  names.forEach((code, i) => {
    const p = ports[code], x = left + i * colW + 6, w = colW - 12, bed = y(p.draft);
    const fit = { draft: dims.draft <= p.draft, loa: dims.loa <= p.loa, beam: dims.beam <= p.beam };
    const ok = fit.draft && fit.loa && fit.beam, isSel = code === sel;
    const label = p.name.replace("Syama Prasad Mookerjee (Haldia)", "SMP Haldia").replace(" (Ennore)", "");
    s += `<g class="port-col${isSel ? " is-selected" : ""}${ok ? "" : " is-blocked"}" data-port="${code}" role="button" tabindex="0" aria-pressed="${isSel}"
      aria-label="${esc(p.name)}, depth limit ${p.draft} metres, ${ok ? "vessel fits" : "vessel does not fit"}">
      <rect class="hit" x="${x - 6}" y="0" width="${colW}" height="${H}"/>
      <text class="port-name" x="${x + w / 2}" y="${top - 40}" text-anchor="middle">${esc(label)}</text>
      <rect x="${x}" y="${top}" width="${w}" height="${bed - top}" fill="var(--k-shoal)"/>
      <rect x="${x}" y="${bed}" width="${w}" height="${H - 34 - bed}" fill="url(#seabed)"/>
      ${fit.draft ? "" : `<rect x="${x}" y="${bed}" width="${w}" height="${keelY - bed}" fill="url(#touch)"/>`}
      <text class="sounding" x="${x + w / 2}" y="${Math.max(bed + 22, fit.draft ? 0 : keelY + 22)}" text-anchor="middle">${p.draft.toFixed(1)} m</text>
      ${isSel ? `<path class="hull" d="M${x + w * .14},${top - 14} H${x + w * .86} L${x + w * .82},${keelY - 14} Q${x + w * .78},${keelY} ${x + w * .64},${keelY} H${x + w * .36} Q${x + w * .22},${keelY} ${x + w * .18},${keelY - 14} Z"/>
        <rect class="sel-frame" x="${x - 3}" y="${top - 62}" width="${w + 6}" height="${H - 30 - top + 62}"/>` : ""}
      ${!fit.loa || !fit.beam ? `<text class="limit-note" x="${x + w / 2}" y="${H - 12}" text-anchor="middle">${!fit.loa ? `LOA over ${p.loa} m` : `Beam over ${p.beam} m`}</text>` : ""}
    </g>`;
  });
  s += `<line class="waterline" x1="${left - 4}" x2="${W - 4}" y1="${top}" y2="${top}"/>`;
  s += `<line class="keel" x1="${left - 4}" x2="${W - 4}" y1="${keelY}" y2="${keelY}"/>`;
  s += `<text class="keel-label" x="4" y="${keelY - 6}">keel ${dims.draft.toFixed(1)} m</text>`;
  $("keel").innerHTML = s;
  $("keel").querySelectorAll(".port-col").forEach((g) => {
    const choose = () => { if (g.dataset.port !== $("dest").value) { $("dest").value = g.dataset.port; refresh(true); } };
    g.addEventListener("click", choose);
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(); } });
  });
}

// ---------- legs
const bnCls = (b) => (b >= 7 ? "bn--gale" : b >= 5 ? "bn--fresh" : "bn--calm");
function renderLegs(r) {
  const legs = r.route.legs, now = r.horizons[0];
  const live = legs.filter((l) => l.source === "live forecast").length;
  $("legsSub").textContent = `${legs.length} legs for a ship fixed today. Speed loss from wind and sea state (Kwon's method) and ocean current set each leg's speed. ` +
    (live ? `${live} legs use Open-Meteo forecasts; later legs use seasonal norms.` : "Weather from seasonal norms (live forecast unavailable).");
  const rows = legs.map((l) => `<tr>
    <td>${esc(l.frm)} <span class="leg__to">to ${esc(l.to)}</span></td>
    <td class="num">${int(l.nm)}</td><td class="num">${String(l.heading).padStart(3, "0")}°</td>
    <td><span class="bn ${bnCls(l.bn)}" title="Beaufort ${l.bn}">${l.bn}</span> ${l.wind_kn} kn from ${String(l.wind_from).padStart(3, "0")}°</td>
    <td class="num">${l.wave_m == null ? "–" : l.wave_m.toFixed(1) + " m"}</td>
    <td class="num">${l.loss_pct.toFixed(1)}%</td><td class="num">${l.speed_kn.toFixed(1)}</td><td class="num">${l.days.toFixed(2)}</td>
    <td class="wrap">${l.zones.length ? `<span class="zone-flag">${l.zones.map(esc).join(", ")}</span>` : ""} <span class="src">${l.source === "live forecast" ? "live" : "norm"}</span></td></tr>`).join("");
  $("legs").innerHTML = `<thead><tr><th>Leg</th><th class="num">nm</th><th class="num">Course</th><th>Wind</th><th class="num">Wave</th><th class="num">Speed loss</th><th class="num">Knots</th><th class="num">Days</th><th>Hazards, source</th></tr></thead>
    <tbody>${rows}</tbody>
    <tfoot><tr><td>At sea</td><td class="num">${int(r.route.total_nm)}</td><td></td><td colspan="4">Plus ${now.days.port.toFixed(1)} days loading and discharging, ${now.days.wait.toFixed(1)} days waiting${now.days.tidal ? ` (incl. ${now.days.tidal * 24} h for tides)` : ""}</td><td class="num">${now.days.sea.toFixed(1)}</td><td></td></tr></tfoot>`;
}

// ---------- vessel fit
function renderVesselFit(r) {
  const st = { permitted: "Permitted", part_cargo: "Part cargo", forbidden: "Not permitted", not_viable: "Not viable" };
  $("fitTable").innerHTML = `<thead><tr><th>Class</th><th class="num">Typical DWT</th><th>Status</th><th>Binding limit</th><th class="num">Cargo t</th><th class="num">Cost today</th><th class="num">$/t</th><th></th></tr></thead><tbody>` +
    r.vessel_fit.map((f) => `<tr class="${f.cls === r.best_fit ? "is-best" : ""} ${f.cls === r.inputs.cls ? "is-current" : ""}">
      <td>${f.name}${f.cls === r.best_fit ? `<span class="tag tag--ok">Recommended</span>` : ""}</td><td class="num">${int(f.dwt)}</td>
      <td class="status status--${f.status}">${st[f.status]}</td><td class="wrap">${esc(f.reason)}</td>
      <td class="num">${f.cargo ? int(f.cargo) : "–"}</td><td class="num">${f.total ? money(f.total) : "–"}</td>
      <td class="num">${f.per_t ? per(f.per_t) : "–"}</td>
      <td>${f.cls !== r.inputs.cls && f.per_t ? `<button type="button" class="btn btn--quiet" data-use="${f.cls}">Use ${f.name}</button>` : ""}</td></tr>`).join("") + "</tbody>";
  bindUse($("fitTable"));
}

// ---------- market
function initCharts() {
  Chart.defaults.font.family = "Noto Sans, system-ui, sans-serif";
  Chart.defaults.font.size = 12;
  Chart.defaults.color = C["ink-2"];
  Chart.defaults.borderColor = C["rule-soft"];
  Chart.defaults.animation.duration = 350;
  Chart.defaults.plugins.legend.labels.boxWidth = 12;
  charts.pie = new Chart($("pie"), {
    type: "pie",
    data: { labels: COST_PARTS.map((p) => p[1]), datasets: [{ data: [], backgroundColor: COST_PARTS.map((p) => p[2]), borderColor: "#F5F6F6", borderWidth: 2 }] },
    options: { plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => ` ${c.label}: ${money(c.raw)}` } } } },
  });
  const line = (id, color) => (charts[id] = new Chart($(id), {
    type: "line", data: { labels: [], datasets: [{ data: [], borderColor: color, borderWidth: 1.6, pointRadius: 0, tension: 0.2 }] },
    options: { maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { display: false }, y: { ticks: { maxTicksLimit: 3 }, grid: { display: false } } } },
  }));
  line("mPmi", C.hull); line("mIip", C.hull); line("mIiv", C.hull); line("mTrade", C.hull);
}
const addDays = (iso, d) => { const t = new Date(iso + "T00:00:00Z"); t.setUTCDate(t.getUTCDate() + d); return t.toISOString().slice(0, 10); };
function renderMarket(r) {
  const m = r.market, n = m.dates.length;
  const fut = m.forecast.slice(1).map((f) => addDays(m.last_date, f.days));
  const labels = [...m.dates, ...fut], pad = (arr) => [...Array(n - 1).fill(null), ...arr];
  $("idxCap").textContent = `${m.index} (${r.vessel.name} index) and BDI, two years weekly, with the 7, 30 and 60-day forecast and its 80% range`;
  $("marketSub").textContent = `The model reads the ${m.index} first and uses the BDI only to fill gaps. It relearns every week, weighting recent weeks more.`;
  const ds = [
    { label: m.index, data: m.series, borderColor: C.hull, borderWidth: 2, pointRadius: 0, tension: 0.15 },
    { label: "BDI", data: m.bdi, borderColor: "#8BA3BA", borderWidth: 1.4, borderDash: [5, 4], pointRadius: 0, tension: 0.15 },
    { label: "Forecast range low", data: pad(m.forecast.map((f) => f.lo)), borderColor: "transparent", pointRadius: 0, fill: false },
    { label: "80% range", data: pad(m.forecast.map((f) => f.hi)), borderColor: "transparent", backgroundColor: "rgba(168,50,42,.14)", pointRadius: 0, fill: "-1" },
    { label: "Forecast", data: pad(m.forecast.map((f) => f.mid)), borderColor: C.antifoul, borderWidth: 2.4, pointRadius: 3, pointBackgroundColor: C.antifoul, tension: 0 },
  ];
  if (!charts.idx) {
    charts.idx = new Chart($("idxChart"), { type: "line", data: { labels, datasets: ds }, options: {
      maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { position: "top", align: "end", labels: { filter: (i) => !i.text.includes("low") } },
        tooltip: { callbacks: { label: (c) => c.raw == null ? null : ` ${c.dataset.label}: ${int(c.raw)}` } } },
      scales: { x: { ticks: { maxTicksLimit: 9, callback(v) { const d = this.getLabelForValue(v); return d.slice(0, 7); } }, grid: { display: false } } } } });
  } else { charts.idx.data.labels = labels; charts.idx.data.datasets = ds; charts.idx.update(); }

  const bt = { labels: m.backtest.map((b) => b.date), datasets: [
    { label: "Actual", data: m.backtest.map((b) => b.actual), borderColor: C.hull, borderWidth: 2, pointRadius: 0 },
    { label: "Model, 4 weeks ahead", data: m.backtest.map((b) => b.predicted), borderColor: C.antifoul, borderWidth: 1.6, borderDash: [4, 3], pointRadius: 0 }] };
  if (!charts.bt) charts.bt = new Chart($("btChart"), { type: "line", data: bt, options: { maintainAspectRatio: false, plugins: { legend: { position: "top", align: "end" } },
    scales: { x: { ticks: { maxTicksLimit: 6, callback(v) { return this.getLabelForValue(v).slice(0, 7); } }, grid: { display: false } } } } });
  else { charts.bt.data = bt; charts.bt.update(); }

  const w = [...m.weights].sort((a, b) => Math.abs(b.w) - Math.abs(a.w)).slice(0, 8);
  const wd = { labels: w.map((x) => x.name), datasets: [{ data: w.map((x) => x.w), backgroundColor: w.map((x) => (x.w >= 0 ? C.hull : C.antifoul)), barThickness: 12 }] };
  if (!charts.w) charts.w = new Chart($("wChart"), { type: "bar", data: wd, options: { indexAxis: "y", maintainAspectRatio: false,
    plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => ` weight ${c.raw.toFixed(3)} (${c.raw >= 0 ? "pushes up" : "pushes down"})` } } },
    scales: { y: { grid: { display: false } }, x: { ticks: { maxTicksLimit: 5 } } } } });
  else { charts.w.data = wd; charts.w.update(); }

  const better = m.mape < m.naive_mape;
  $("stats").innerHTML = `
    <h3 class="boxhead boxhead--light">Model check</h3>
    <div class="stat"><div class="stat__v">${int(m.series[n - 1])}</div><div class="stat__k">${m.index} on ${fmtDate(m.last_date)}</div></div>
    <div class="stat"><div class="stat__v">${pct(m.mape, 1)}</div><div class="stat__k">Average 4-week error, vs ${pct(m.naive_mape, 1)} for "no change" ${better ? "(model ahead)" : "(level with no-change)"}</div></div>
    <div class="stat"><div class="stat__v">${pct(m.vol)}</div><div class="stat__k">8-week volatility, annualised</div></div>`;
  [["mPmi", m.pmi], ["mIip", m.iip], ["mIiv", m.iiv], ["mTrade", m.trade]].forEach(([id, data]) => {
    charts[id].data.labels = m.dates; charts[id].data.datasets[0].data = data; charts[id].update();
  });
}

// ---------- risks & idle
function renderNews(r) {
  const lvl = { high: "Alert", watch: "Watch", info: "Note" };
  const items = r.risks.length ? r.risks : [{ level: "info", title: "No warnings", detail: "Nothing on this voyage needs attention." }];
  const html = items.map((x) => `<li><b>${lvl[x.level]}:</b>${esc(x.title)}. ${esc(x.detail)}</li>`).join("");
  $("newsList").innerHTML = html + html.replace(/<li>/g, '<li aria-hidden="true">'); // doubled for a seamless loop
  $("newsList").style.setProperty("--ticker-s", Math.max(25, items.length * 9) + "s");
}
function renderRisks(r) {
  renderNews(r);
  const lvl = { high: "High", watch: "Watch", info: "Note" };
  $("risks").innerHTML = r.risks.length ? r.risks.map((x) =>
    `<li class="risk"><span class="risk__lvl risk__lvl--${x.level}">${lvl[x.level]}</span><div><b>${esc(x.title)}</b><span>${esc(x.detail)}</span></div></li>`).join("")
    : `<li><p class="empty">No warnings for this voyage and fixing window.</p></li>`;
}
function renderIdle(r) {
  const i = r.idle, o = META.origins[r.inputs.origin];
  $("idleSub").textContent = `After discharge the ship ballasts ${i.back_days} days back to ${o.name}: about ${money(i.back_cost)} of hire and fuel with no cargo aboard.`;
  $("advice").innerHTML = i.advice.map((a) => `<li>${esc(a.replace(/\$([\d,]+)/g, (_, d) => money(+d.replace(/,/g, ""))))}</li>`).join("");
  $("repos").innerHTML = `<thead><tr><th>Nearest loading areas from ${esc(META.destinations[r.inputs.dest].name)}</th><th>Country</th><th class="num">Ballast days</th></tr></thead><tbody>` +
    i.reposition.map((p) => `<tr><td>${esc(p.port)}</td><td>${esc(p.country)}</td><td class="num">${p.days.toFixed(1)}</td></tr>`).join("") + "</tbody>";
}

// ---------- map
function portCard(code, p, kind) {
  return `<h4>${esc(p.name)}</h4><p>${kind === "dest" ? "Discharge port" : "Load port"}, ${esc(p.country)}</p>
    <dl><dt>Max LOA</dt><dd>${p.loa} m</dd><dt>Max beam</dt><dd>${p.beam} m</dd><dt>Max draft</dt><dd>${p.draft} m</dd>
    <dt>Tidal range</dt><dd>${p.tidal_range} m</dd><dt>Handling rate</dt><dd>${int(p.rate)} t/day</dd><dt>Typical wait</dt><dd>${p.congestion} days</dd></dl>
    <p class="avail">${esc(p.availability)}</p>`;
}
function initMap() {
  map = L.map("map", { scrollWheelZoom: false, worldCopyJump: true, minZoom: 2 }).setView([5, 95], 3);
  // maritime view: Esri ocean basemap (depth shading) with OpenSeaMap sea marks; street map as an alternative
  const ocean = L.layerGroup([
    L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}", { maxZoom: 10, attribution: "Ocean basemap &copy; Esri, GEBCO, NOAA, Garmin" }),
    L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Reference/MapServer/tile/{z}/{y}/{x}", { maxZoom: 10 }),
  ]).addTo(map);
  const street = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 9, className: "tiles-muted", attribution: "&copy; OpenStreetMap contributors" });
  const seamarks = L.tileLayer("https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "Sea marks &copy; OpenSeaMap" }).addTo(map);
  L.control.layers({ "Ocean chart": ocean, "Street map": street }, { "Sea marks (buoys, lights, lanes)": seamarks }, { position: "topright" }).addTo(map);
  map.on("focus", () => map.scrollWheelZoom.enable());
  map.on("blur", () => map.scrollWheelZoom.disable());
  layers.zones = L.layerGroup().addTo(map);
  META.zones.forEach((z) => {
    L.polygon(z.poly, z.active
      ? { color: C.antifoul, weight: 1.2, fillColor: C.antifoul, fillOpacity: 0.16 }
      : { color: "#7F93A6", weight: 1, dashArray: "4 4", fillOpacity: 0.04 })
      .bindTooltip(`${esc(z.name)}<br>${z.active ? "Active this month" : "Season: " + z.months.map((m) => new Date(2000, m - 1).toLocaleString("en", { month: "short" })).join(", ")}`, { sticky: true })
      .addTo(layers.zones);
  });
  layers.corr = L.layerGroup().addTo(map);
  META.corridors.forEach((c) => L.polyline(c.coords, { color: "#5A6F84", weight: 1.2, opacity: 0.45, interactive: false }).addTo(layers.corr));
  layers.route = L.layerGroup().addTo(map);
  layers.ports = L.layerGroup().addTo(map);
  let card;
  const addPort = (code, p, kind) => {
    const mk = L.circleMarker([p.lat, p.lon], { radius: 6, color: "#fff", weight: 2, fillColor: kind === "dest" ? C.hull : C.brass, fillOpacity: 1 })
      .bindTooltip(esc(p.name), { direction: "top", offset: [0, -6] }).addTo(layers.ports);
    mk.on("click", (e) => {
      L.DomEvent.stopPropagation(e);
      if (card) map.removeLayer(card);
      card = L.tooltip({ direction: "bottom", permanent: true, interactive: true, className: "portcard", offset: [0, 10] })
        .setLatLng([p.lat, p.lon]).setContent(portCard(code, p, kind)).addTo(map);
    });
    mk.on("keypress", (e) => mk.fire("click", e));
  };
  Object.entries(META.origins).forEach(([k, p]) => addPort(k, p, "origin"));
  Object.entries(META.destinations).forEach(([k, p]) => addPort(k, p, "dest"));
  map.on("click", () => { if (card) { map.removeLayer(card); card = null; } });
  $("mapLegend").innerHTML = `
    <div><i style="border-color:${C.hull};border-top-width:5px"></i>Selected route</div>
    <div><i style="border-color:#5A6F84;opacity:.6"></i>Other corridors</div>
    <div><i style="border-color:${C.antifoul};border-top-width:8px;opacity:.5"></i>Weather hazard, active now</div>
    <div><i style="border-color:#7F93A6;border-top-style:dashed"></i>Hazard, out of season</div>
    <div><i style="border-color:${C.brass};border-top-width:8px;width:8px;border-radius:50%"></i>Load ports <i style="border-color:${C.hull};border-top-width:8px;width:8px;border-radius:50%;margin-left:8px"></i>Discharge</div>`;
}
function renderMap(r, routeChanged) {
  layers.route.clearLayers();
  L.polyline(r.route.coords, { color: "#fff", weight: 8, opacity: 0.9, interactive: false }).addTo(layers.route);
  L.polyline(r.route.coords, { color: C.hull, weight: 4.5, interactive: false }).addTo(layers.route);
  r.route.legs.forEach((l) => {
    const col = l.bn >= 7 ? C.antifoul : l.bn >= 5 ? C.brass : "#5A8FB0";
    L.circleMarker(l.mid, { radius: 4, color: "#fff", weight: 1.5, fillColor: col, fillOpacity: 1 })
      .bindTooltip(`${esc(l.frm)} to ${esc(l.to)}<br>Beaufort ${l.bn}, ${l.wind_kn} kn from ${l.wind_from}°<br>Speed loss ${l.loss_pct}%, ${l.source}`, { className: "wxtip", direction: "top" })
      .addTo(layers.route);
  });
  if (routeChanged) map.fitBounds(L.polyline(r.route.coords).getBounds(), { padding: [30, 30], maxZoom: 5 });
}

// display settings: text size and high contrast (remembered per browser)
function initDisplay() {
  const root = document.documentElement, get = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
  const put = (k, v) => { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch {} };
  const setSize = (s) => {
    root.dataset.size = s; put("ll-size", s);
    document.querySelectorAll(".seg:not(.cur) button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.size === s));
    if (LAST) renderLadder(LAST);
  };
  const setContrast = (on) => { on ? (root.dataset.contrast = "") : delete root.dataset.contrast; put("ll-contrast", on ? "1" : null); $("contrast").setAttribute("aria-pressed", on); };
  document.querySelectorAll(".seg:not(.cur) button").forEach((b) => b.addEventListener("click", () => setSize(b.dataset.size)));
  $("contrast").addEventListener("click", () => setContrast(!("contrast" in root.dataset)));
  setSize(get("ll-size") || "m");
  setContrast(get("ll-contrast") === "1");
}

// currency: daily USD to INR reference rate via /api/fx; INR stays off if it can't be fetched
async function initCurrency() {
  const btns = document.querySelectorAll(".cur button"), note = $("fxNote");
  const set = setCurrency = (code) => {
    CUR.code = code;
    btns.forEach((b) => b.setAttribute("aria-pressed", b.dataset.cur === code));
    try { localStorage.setItem("ll-cur", code); } catch {}
    if (LAST) render(LAST, false);
  };
  btns.forEach((b) => b.addEventListener("click", () => set(b.dataset.cur)));
  const inr = document.querySelector('.cur button[data-cur="INR"]');
  inr.disabled = true;
  try {
    const j = await (await fetch(API + "/api/fx")).json();
    if (!j.rate) throw new Error("no rate");
    CUR.rate = FX_LIVE = j.rate;
    $("wi-fx").disabled = false;
    inr.disabled = false;
    note.textContent = `₹${CUR.rate.toFixed(2)} per US$, reference rate of ${fmtDate(j.date)}`;
    let saved = null; try { saved = localStorage.getItem("ll-cur"); } catch {}
    if (saved === "INR") set("INR");
  } catch {
    note.textContent = "Rupee rate unavailable offline; showing US$";
  }
}

// ---------- what-if scenarios
const WHATIF = [
  ["bdi", "BDI"], ["bci", "BCI (Capesize)"], ["bpi", "BPI (Panamax)"], ["bsi", "BSI (Supramax)"], ["bhsi", "BHSI (Handysize)"],
  ["china_pmi", "China PMI"], ["india_iip", "India IIP"], ["iron_ore_usd", "IIV: iron-ore price"], ["world_trade_idx", "World trade volume"], ["fx", "US$ to ₹ rate"],
];
const WI_LIM = { bdi: .5, bci: .5, bpi: .5, bsi: .5, bhsi: .5, china_pmi: 5, india_iip: .3, iron_ore_usd: .5, world_trade_idx: .3, fx: .2 }; // matches model.SHOCK_LIMITS
const SHOCK = {};
const wiBase = (k) => (k === "fx" ? FX_LIVE : BASE?.latest?.[k]);
const wiFmt = (k, v) => v == null ? "–" : k === "fx" ? "₹" + v.toFixed(2) : k === "iron_ore_usd" ? "$" + v.toFixed(1) + "/t" : v >= 1000 ? int(v) : v.toFixed(1);
const wiShifted = (k, b) => (k === "china_pmi" ? b + (SHOCK[k] || 0) : b * (1 + (SHOCK[k] || 0)));
function initWhatIf() {
  $("wiGrid").innerHTML = WHATIF.map(([k, label]) => `
    <div class="wi">
      <label class="wi__label" for="wi-${k}">${label}</label>
      <p class="wi__vals"><span id="wb-${k}">–</span><span class="wi__to" id="ws-${k}"></span></p>
      <input type="range" class="wi__range" id="wi-${k}" data-k="${k}" min="-100" max="100" step="1" value="0"${k === "fx" ? " disabled" : ""}
        aria-describedby="wc-${k}">
      <p class="wi__chg" id="wc-${k}">Today's value</p>
    </div>`).join("");
  let t;
  $("wiGrid").querySelectorAll(".wi__range").forEach((el) => {
    paintWi(el);
    el.addEventListener("input", () => {
      const k = el.dataset.k, v = (+el.value / 100) * WI_LIM[k];
      if (v) SHOCK[k] = +v.toFixed(4); else delete SHOCK[k];
      paintWi(el); wiLabels();
      if (k === "fx") {
        CUR.rate = FX_LIVE * (1 + (SHOCK.fx || 0));
        if (SHOCK.fx && CUR.code === "USD") setCurrency("INR"); else if (LAST) render(LAST, false);
        return;
      }
      clearTimeout(t); t = setTimeout(() => refresh(), 250);
    });
    el.addEventListener("dblclick", () => { el.value = 0; el.dispatchEvent(new Event("input")); }); // double-click: back to today
  });
  $("wiReset").addEventListener("click", () => {
    const fx = "fx" in SHOCK;
    Object.keys(SHOCK).forEach((k) => delete SHOCK[k]);
    $("wiGrid").querySelectorAll(".wi__range").forEach((el) => { el.value = 0; paintWi(el); });
    if (fx) CUR.rate = FX_LIVE;
    wiLabels(); refresh();
  });
}
function paintWi(el) {
  const p = (+el.value + 100) / 2;
  el.style.setProperty("--a", Math.min(50, p) + "%"); el.style.setProperty("--b", Math.max(50, p) + "%");
}
function wiLabels() {
  WHATIF.forEach(([k]) => {
    const b = wiBase(k), s = SHOCK[k];
    $("wb-" + k).textContent = wiFmt(k, b);
    $("ws-" + k).textContent = s && b != null ? "→ " + wiFmt(k, wiShifted(k, b)) : "";
    $("wc-" + k).textContent = !s ? "Today's value" : k === "china_pmi" ? `${s > 0 ? "+" : ""}${s.toFixed(1)} points` : `${s > 0 ? "+" : ""}${(s * 100).toFixed(0)}%`;
    $("wc-" + k).className = "wi__chg" + (s ? (s > 0 ? " is-up" : " is-down") : "");
  });
  $("wiReset").disabled = !Object.keys(SHOCK).length;
}
function renderWhatIf() {
  wiLabels();
  const active = Object.keys(SHOCK).length > 0;
  $("h-result").textContent = active ? "Recommended fixing window: your scenario" : "Recommended fixing window";
  if (!active || !BASE || !LAST) { $("wiCompare").innerHTML = `<p class="wi-empty">Move a slider to compare.</p>`; return; }
  const inr = CUR.code === "INR", baseRate = FX_LIVE || 1;
  const fmt = (v) => inr ? "₹" + (Math.round(v / 100) * 100).toLocaleString("en-IN") : "$" + (Math.round(v / 100) * 100).toLocaleString("en-US");
  const rows = LAST.horizons.map((h, i) => {
    const b = BASE.horizons[i].total * (inr ? baseRate : 1), s = h.total * (inr ? CUR.rate : 1), d = s - b;
    return `<tr${h.key === LAST.best ? ' class="is-best"' : ""}><td>${HZ_LABEL[h.key]}</td><td class="num">${fmt(b)}</td><td class="num">${fmt(s)}</td>
      <td class="num ${d > 0 ? "up" : d < 0 ? "down" : ""}">${d > 0 ? "+" : d < 0 ? "−" : ""}${fmt(Math.abs(d))}</td></tr>`;
  }).join("");
  $("wiCompare").innerHTML = `<table class="data data--compact wi-table"><thead><tr><th>Fix</th><th class="num">Today's market</th><th class="num">Scenario</th><th class="num">Change</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="wi-best">Cheapest fixing date: <b>${fmtDate(BASE.curve[BASE.best_day].date)}</b> today, <b>${fmtDate(LAST.curve[LAST.best_day].date)}</b> in your scenario${BASE.best_day === LAST.best_day ? "." : ". The advice changes."}</p>`;
}

function initTraffic() {
  const src = new URLSearchParams({ zoom: 4, lat: 12, lon: 86, width: "100%", height: 560, names: false, track: false,
    fleet: false, fleet_name: false, fleet_hide_old_positions: false, clicktoact: true, store_pos: false, ra: location.href });
  $("vf").src = "https://www.vesselfinder.com/aismap?" + src;
}

initDisplay();
initWhatIf();
initTraffic();
initCurrency();
init();
