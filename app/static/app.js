"use strict";

/* Test console for the Geospatial File Measurement API. Plain JS, no build step.
   Everything talks to the same origin that served this page. */

const $ = (s, r = document) => r.querySelector(s);
const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nf = (n, d = 2) => Number(n).toLocaleString("en-US", { maximumFractionDigits: d });
const badge = (cls, text) => `<span class="badge ${esc(cls)}">${esc(text ?? cls)}</span>`;

const SAMPLE_TYPES = { "survey.kml": "application/vnd.google-earth.kml+xml", "parcels_utm43.zip": "application/zip" };
const POLYGONS = new Set(["Polygon", "MultiPolygon"]);
const LINES = new Set(["LineString", "MultiLineString"]);
const POINTS = new Set(["Point", "MultiPoint"]);

let chosen = null; // the File the user picked
let rows = []; // merged feature + measurement rows of the last upload

/* ---------- network ------------------------------------------------------------------- */

async function call(method, path, body) {
  const t0 = performance.now();
  try {
    const res = await fetch(path, { method, body });
    const raw = await res.text();
    let json = null;
    try { json = JSON.parse(raw); } catch { /* not JSON */ }
    return { status: res.status, ok: res.ok, body: json ?? raw, ms: Math.round(performance.now() - t0) };
  } catch (err) {
    return { status: 0, ok: false, body: null, ms: 0, error: String(err) };
  }
}

async function fetchAll(path) {
  const results = [];
  let first = null;
  let offset = 0;
  let total = Infinity;
  while (offset < total) {
    const r = await call("GET", `${path}?limit=1000&offset=${offset}`);
    if (!r.ok) throw new Error(`GET ${path} returned HTTP ${r.status}`);
    first ??= r.body;
    results.push(...r.body.results);
    total = r.body.total;
    if (!r.body.results.length) break;
    offset += 1000;
  }
  return { first, results };
}

function upload(file) {
  const fd = new FormData();
  fd.append("file", file, file.name);
  return call("POST", "/api/files/", fd);
}

async function sampleFile(name) {
  const res = await fetch(`/samples/${name}`);
  if (!res.ok) throw new Error(`could not load sample ${name} (HTTP ${res.status})`);
  return new File([await res.blob()], name, { type: SAMPLE_TYPES[name] });
}

/* ---------- independent geodesic maths (spherical, no projection) ---------------------- */

const R = 6371008.8;
const RAD = Math.PI / 180;

function ringArea(ring) {
  let s = 0;
  for (let i = 0; i < ring.length - 1; i++) {
    const [l1, p1] = ring[i];
    const [l2, p2] = ring[i + 1];
    s += (l2 - l1) * RAD * (2 + Math.sin(p1 * RAD) + Math.sin(p2 * RAD));
  }
  return Math.abs((s * R * R) / 2);
}

function polygonArea(rings) {
  return ringArea(rings[0]) - rings.slice(1).reduce((sum, hole) => sum + ringArea(hole), 0);
}

function haversine([l1, p1], [l2, p2]) {
  const dp = (p2 - p1) * RAD;
  const dl = (l2 - l1) * RAD;
  const a = Math.sin(dp / 2) ** 2 + Math.cos(p1 * RAD) * Math.cos(p2 * RAD) * Math.sin(dl / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

function lineLength(coords) {
  let sum = 0;
  for (let i = 0; i < coords.length - 1; i++) sum += haversine(coords[i], coords[i + 1]);
  return sum;
}

function referenceValue(g) {
  switch (g.type) {
    case "Polygon": return { kind: "area", value: polygonArea(g.coordinates) };
    case "MultiPolygon": return { kind: "area", value: g.coordinates.reduce((s, p) => s + polygonArea(p), 0) };
    case "LineString": return { kind: "length", value: lineLength(g.coordinates) };
    case "MultiLineString": return { kind: "length", value: g.coordinates.reduce((s, l) => s + lineLength(l), 0) };
    default: return null;
  }
}

function browserCheck(row) {
  if (row.status !== "MEASURED" || !row.geometry) return { state: "skip", text: "-" };
  if (row.crs !== "EPSG:4326") return { state: "skip", text: "n/a (file is not lon/lat)" };
  const ref = referenceValue(row.geometry);
  if (!ref) return { state: "skip", text: "-" };
  const api = ref.kind === "area" ? row.m.area_sq_m : row.m.length_m;
  const diff = ((api - ref.value) / ref.value) * 100;
  return { state: Math.abs(diff) < 1 ? "pass" : "fail", text: `${diff >= 0 ? "+" : ""}${diff.toFixed(2)}% vs sphere`, diff };
}

/* ---------- rendering helpers ---------------------------------------------------------- */

function setStatus(text, kind = "") {
  const s = $("#status");
  s.textContent = text;
  s.className = `status ${kind}`;
}

function show(id, on = true) {
  $(id).hidden = !on;
}

function shortCrs(p) {
  if (!p) return "-";
  const m = /^\+proj=laea \+lat_0=(-?[\d.]+) \+lon_0=(-?[\d.]+)/.exec(p);
  return m ? `Equal-area (LAEA) at ${m[1]}, ${m[2]}` : p;
}

function metric(row) {
  if (row.m?.area_sq_m !== undefined) return `${nf(row.m.area_sq_m)} m²<span class="note">${nf(row.m.area_hectares, 4)} ha</span>`;
  if (row.m?.length_m !== undefined) return `${nf(row.m.length_m)} m<span class="note">${nf(row.m.length_km, 4)} km</span>`;
  return "-";
}

function featureName(props) {
  return props?.Name ?? props?.name ?? props?.NAME ?? props?.parcel_id ?? props?.id ?? "";
}

/* ---------- 2. file information vs the expected example ------------------------------- */

const EXAMPLE = { id: "abc123", filename: "survey.kml", feature_count: 120, crs: "EPSG:4326", status: "COMPLETED" };

function specChecks(info, file) {
  const typeOk = {
    id: typeof info.id === "string" && info.id.length > 0,
    filename: info.filename === file.name,
    feature_count: Number.isInteger(info.feature_count) && info.feature_count >= 0,
    crs: typeof info.crs === "string" && info.crs.length > 0,
    status: info.status === "COMPLETED",
  };
  return Object.keys(EXAMPLE).map((field) => ({
    field,
    example: JSON.stringify(EXAMPLE[field]),
    actual: field in info ? JSON.stringify(info[field]) : "(missing)",
    ok: field in info && typeOk[field],
  }));
}

function renderInfo(res, file) {
  show("#info");
  const info = res.body;
  $("#info-http").innerHTML = `HTTP ${esc(res.status)} · ${esc(res.ms)} ms`;
  $("#info-json").textContent = typeof info === "string" ? info : JSON.stringify(info, null, 2);
  const tbody = $("#info-checks tbody");
  if (!info || typeof info !== "object" || !("id" in info)) {
    tbody.innerHTML = `<tr><td colspan="4">${badge("fail", "No file record in the response")}</td></tr>`;
    return;
  }
  const checks = specChecks(info, file);
  const extras = Object.keys(info).filter((k) => !(k in EXAMPLE));
  tbody.innerHTML =
    checks
      .map((c) => `<tr><td><code>${esc(c.field)}</code></td><td>${esc(c.example)}</td><td>${esc(c.actual)}</td><td>${badge(c.ok ? "pass" : "fail", c.ok ? "OK" : "Check")}</td></tr>`)
      .join("") +
    `<tr><td colspan="4" class="muted">Extra fields returned (allowed): ${extras.map((k) => `<code>${esc(k)}</code>`).join(" ") || "none"}</td></tr>`;
}

/* ---------- 3. summary ----------------------------------------------------------------- */

function renderSummary(info, summary) {
  show("#summary");
  const statuses = Object.entries(summary.by_status).map(([k, v]) => `${esc(k)} ${v}`).join(" · ");
  const tiles = [
    ["Features", nf(summary.feature_count, 0), statuses],
    ["Total area", `${nf(summary.total_area_hectares, 4)} ha`, `${nf(summary.total_area_sq_m)} m²`],
    ["Total length", `${nf(summary.total_length_km, 4)} km`, `${nf(summary.total_length_m)} m`],
    ["File CRS", info.crs ?? "-", `status ${info.status}`],
  ];
  $("#tiles").innerHTML = tiles
    .map(([label, value, sub]) => `<div class="tile"><span>${esc(label)}</span><b>${esc(value)}</b><span class="sub2">${sub}</span></div>`)
    .join("");
}

/* ---------- 4. validation of the measurements ------------------------------------------ */

function validationChecks(info, summary) {
  const out = [];
  const add = (state, title, detail) => out.push({ state, title, detail });

  const counts = [info.feature_count, summary.feature_count, rows.length];
  add(counts.every((c) => c === counts[0]) ? "pass" : "fail", "Feature counts agree everywhere",
    `file info ${counts[0]}, summary ${counts[1]}, features returned ${counts[2]}`);

  const measured = rows.filter((r) => r.status === "MEASURED");
  const missing = measured.filter((r) =>
    (POLYGONS.has(r.type) && !(r.m?.area_sq_m > 0)) || (LINES.has(r.type) && !(r.m?.length_m > 0)));
  add(missing.length === 0 ? "pass" : "fail", "Every measured polygon has an area and every line a length",
    `${measured.length} measured features, ${missing.length} without a positive value`);

  const metricCrs = (p) => /^\+proj=laea /.test(p ?? "") || /^EPSG:(326|327)\d\d$/.test(p ?? "");
  const nonMetric = measured.filter((r) => !metricCrs(r.projected));
  const laea = measured.filter((r) => /^\+proj=laea/.test(r.projected ?? "")).length;
  add(nonMetric.length === 0 ? "pass" : "fail", "Measured in metres, never in degrees",
    `${laea} polygon(s) in a local equal-area (LAEA) projection, ${measured.length - laea - nonMetric.length} line(s) in a UTM zone, ${nonMetric.length} in a non-metric CRS`);

  const validStatus = new Set(["MEASURED", "NOT_APPLICABLE", "UNSUPPORTED", "NO_GEOMETRY", "ERROR"]);
  const bad = rows.filter((r) => !validStatus.has(r.status) || (r.status !== "MEASURED" && r.m));
  const mix = Object.entries(summary.by_status).map(([k, v]) => `${v} ${k}`).join(", ");
  add(bad.length === 0 ? "pass" : "fail", "Points, unsupported and empty features are reported, not crashed", mix);

  const sumArea = rows.reduce((s, r) => s + (r.m?.area_sq_m ?? 0), 0);
  const sumLen = rows.reduce((s, r) => s + (r.m?.length_m ?? 0), 0);
  const tol = 0.002 * Math.max(1, rows.length);
  const okTotals = Math.abs(sumArea - summary.total_area_sq_m) <= tol && Math.abs(sumLen - summary.total_length_m) <= tol;
  add(okTotals ? "pass" : "fail", "Summary totals equal the sum of the features",
    `area ${nf(sumArea)} vs ${nf(summary.total_area_sq_m)} m², length ${nf(sumLen)} vs ${nf(summary.total_length_m)} m`);

  const eligible = rows.map((r) => r.check).filter((c) => c.state !== "skip");
  if (eligible.length === 0) {
    add("skip", "Independent browser calculation",
      "Skipped: it needs geometry in longitude/latitude (EPSG:4326), and this file uses a projected CRS.");
  } else {
    const worst = Math.max(...eligible.map((c) => Math.abs(c.diff)));
    const fails = eligible.filter((c) => c.state === "fail").length;
    add(fails === 0 ? "pass" : "fail", "Independent browser calculation agrees with the API (within 1%)",
      `${eligible.length - fails} of ${eligible.length} features agree; largest difference ${worst.toFixed(2)}% (the browser uses a sphere, the API an ellipsoid, so a small gap is expected)`);
  }

  const noGeom = rows.filter((r) => r.status !== "NO_GEOMETRY" && (!r.crs || (!r.geometry && r.type)));
  add(noGeom.length === 0 ? "pass" : "fail", "Each feature returns its geometry, CRS and properties",
    `${rows.filter((r) => r.geometry).length} geometries, ${rows.filter((r) => r.crs).length} CRS labels, ${rows.filter((r) => r.props && Object.keys(r.props).length).length} with properties`);
  return out;
}

function renderChecks(info, summary) {
  show("#checks");
  $("#check-list").innerHTML = validationChecks(info, summary)
    .map((c) => `<li>${badge(c.state, c.state === "pass" ? "PASS" : c.state === "fail" ? "FAIL" : "SKIP")}<div>${esc(c.title)}<small>${esc(c.detail)}</small></div></li>`)
    .join("");
}

/* ---------- 5. map preview ------------------------------------------------------------- */

function eachCoord(g, fn) {
  if (!g) return;
  if (g.type === "GeometryCollection") return g.geometries.forEach((x) => eachCoord(x, fn));
  const walk = (c) => (typeof c[0] === "number" ? fn(c) : c.forEach(walk));
  walk(g.coordinates);
}

function shapeClass(row, g) {
  if (row.status === "UNSUPPORTED" || row.status === "ERROR" || g.type === "GeometryCollection") return "other";
  if (POLYGONS.has(g.type)) return "poly";
  if (LINES.has(g.type)) return "line";
  return "point";
}

function renderMap() {
  const drawable = rows.filter((r) => r.geometry);
  if (!drawable.length) return show("#mapcard", false);
  const byCrs = {};
  drawable.forEach((r) => (byCrs[r.crs] = (byCrs[r.crs] ?? 0) + 1));
  const crs = Object.keys(byCrs).sort((a, b) => byCrs[b] - byCrs[a])[0];
  const items = drawable.filter((r) => r.crs === crs);
  const geo = crs === "EPSG:4326";

  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
  items.forEach((r) => eachCoord(r.geometry, ([x, y]) => {
    minx = Math.min(minx, x); maxx = Math.max(maxx, x); miny = Math.min(miny, y); maxy = Math.max(maxy, y);
  }));
  const kx = geo ? Math.cos(((miny + maxy) / 2) * RAD) : 1;
  const w = Math.max((maxx - minx) * kx, 1e-9);
  const h = Math.max(maxy - miny, 1e-9);
  const VW = 1000, VH = 420, pad = 36;
  const s = Math.min((VW - 2 * pad) / w, (VH - 2 * pad) / h);
  const ox = (VW - w * s) / 2, oy = (VH - h * s) / 2;
  const P = ([x, y]) => `${(ox + (x - minx) * kx * s).toFixed(1)} ${(oy + (maxy - y) * s).toFixed(1)}`;
  const path = (c, close) => `M${c.map(P).join("L")}${close ? "Z" : ""}`;

  const draw = (g, cls, key) => {
    const attr = `class="${cls}" data-key="${esc(key)}"`;
    switch (g.type) {
      case "Polygon": return `<path ${attr} d="${g.coordinates.map((r) => path(r, true)).join("")}"/>`;
      case "MultiPolygon": return `<path ${attr} d="${g.coordinates.flat().map((r) => path(r, true)).join("")}"/>`;
      case "LineString": return `<path ${attr} d="${path(g.coordinates)}"/>`;
      case "MultiLineString": return `<path ${attr} d="${g.coordinates.map((l) => path(l)).join("")}"/>`;
      case "Point": { const [px, py] = P(g.coordinates).split(" "); return `<circle ${attr} cx="${px}" cy="${py}" r="7"/>`; }
      case "MultiPoint": return g.coordinates.map((c) => { const [px, py] = P(c).split(" "); return `<circle ${attr} cx="${px}" cy="${py}" r="7"/>`; }).join("");
      case "GeometryCollection": return g.geometries.map((x) => draw(x, "other", key)).join("");
      default: return "";
    }
  };

  const svg = $("#map");
  svg.setAttribute("viewBox", `0 0 ${VW} ${VH}`);
  svg.innerHTML = items.map((r) => draw(r.geometry, shapeClass(r, r.geometry), r.key)).join("");
  show("#mapcard");
  $("#map-note").textContent = `drawn in ${crs}${items.length < drawable.length ? ` (${items.length} of ${drawable.length} shapes; other CRSs are not mixed)` : ""} · click a table row to highlight a shape`;
}

/* ---------- 6. table ------------------------------------------------------------------- */

function fillSelect(sel, values, label) {
  sel.innerHTML = `<option value="">${esc(label)}</option>` + [...new Set(values)].sort().map((v) => `<option>${esc(v)}</option>`).join("");
}

function renderTable() {
  const type = $("#f-type").value;
  const status = $("#f-status").value;
  const q = $("#f-search").value.trim().toLowerCase();
  const list = rows.filter((r) =>
    (!type || (r.type ?? "None") === type) && (!status || r.status === status) &&
    (!q || `${r.layer} ${featureName(r.props)}`.toLowerCase().includes(q)));
  $("#count").textContent = `${list.length} of ${rows.length} features`;
  $("#tbl tbody").innerHTML = list.map((r) => {
    const notes = [r.message, ...r.warnings].filter(Boolean).join(" · ");
    const bc = r.check.state === "skip" ? (r.check.text === "-" ? "-" : esc(r.check.text)) : `${badge(r.check.state, r.check.state === "pass" ? "OK" : "DIFF")} <span class="note">${esc(r.check.text)}</span>`;
    return `<tr data-key="${esc(r.key)}"><td>${esc(r.layer)}</td><td>${esc(r.index)}</td><td>${esc(featureName(r.props))}</td>` +
      `<td>${esc(r.type ?? "None")}</td><td>${badge(r.status)}</td><td class="num">${metric(r)}</td>` +
      `<td>${esc(shortCrs(r.projected))}</td><td>${bc}</td><td>${esc(notes)}</td></tr>`;
  }).join("");
}

function exportCsv() {
  const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const head = ["layer", "feature_index", "name", "geometry_type", "status", "area_sq_m", "area_hectares", "length_m", "length_km", "projected_crs", "crs", "notes"];
  const lines = rows.map((r) => [r.layer, r.index, featureName(r.props), r.type, r.status, r.m?.area_sq_m, r.m?.area_hectares,
    r.m?.length_m, r.m?.length_km, r.projected, r.crs, [r.message, ...r.warnings].filter(Boolean).join(" | ")].map(q).join(","));
  const url = URL.createObjectURL(new Blob([[head.join(","), ...lines].join("\n")], { type: "text/csv" }));
  Object.assign(document.createElement("a"), { href: url, download: "measurements.csv" }).click();
  URL.revokeObjectURL(url);
}

/* ---------- main flow ------------------------------------------------------------------ */

function resetResults() {
  ["#info", "#summary", "#checks", "#mapcard", "#tablecard"].forEach((id) => show(id, false));
  rows = [];
}

async function processFile(file) {
  resetResults();
  $("#go").disabled = true;
  setStatus(`Uploading ${file.name} and processing it...`, "busy");
  const res = await upload(file);
  renderInfo(res, file);

  if (res.status === 0) return setStatus(`Could not reach the API: ${res.error}`, "err");
  const info = res.body;
  if (res.status === 422 && info?.status === "FAILED") {
    setStatus(`The file was accepted but could not be processed: ${info.error}`, "err");
    return afterRun();
  }
  if (res.status !== 201) {
    setStatus(`Upload rejected (HTTP ${res.status}): ${info?.detail ?? "unexpected response"}`, "err");
    return afterRun();
  }

  try {
    const [m, f] = await Promise.all([
      fetchAll(`/api/files/${info.id}/measurements/`),
      fetchAll(`/api/files/${info.id}/features/`),
    ]);
    const feats = new Map(f.results.map((x) => [`${x.layer}#${x.feature_index}`, x]));
    rows = m.results.map((x) => {
      const key = `${x.layer}#${x.feature_index}`;
      const ft = feats.get(key) ?? {};
      const row = {
        key, layer: x.layer, index: x.feature_index, type: x.geometry_type, status: x.status, m: x.measurement,
        projected: x.projected_crs, message: x.message, warnings: x.warnings, geometry: ft.geometry ?? null,
        crs: ft.crs ?? null, props: ft.properties ?? {},
      };
      row.check = browserCheck(row);
      return row;
    });
    renderSummary(info, m.first.summary);
    renderChecks(info, m.first.summary);
    renderMap();
    fillSelect($("#f-type"), rows.map((r) => r.type ?? "None"), "All types");
    fillSelect($("#f-status"), rows.map((r) => r.status), "All statuses");
    $("#f-search").value = "";
    renderTable();
    show("#tablecard");
    setStatus(`Done: ${info.feature_count} features processed in ${res.ms} ms.`, "ok");
  } catch (err) {
    setStatus(`Processed, but reading the results failed: ${err.message}`, "err");
  }
  afterRun();
}

function afterRun() {
  $("#go").disabled = !chosen;
}

function choose(file) {
  chosen = file;
  $("#drop").classList.toggle("has", !!file);
  $("#drop-title").textContent = file ? file.name : "Drop a .zip (Shapefile) or a .kml here";
  $("#drop-sub").textContent = file ? `${nf(file.size / 1024, 1)} KB · click to choose a different file` : "or click to choose a file";
  $("#go").disabled = !file;
}

/* ---------- API self-test ------------------------------------------------------------- */

const near = (value, target, relTol) => Math.abs(value - target) <= Math.abs(target) * relTol;

async function runSelfTest() {
  show("#selftest-card");
  const tbody = $("#selftest-tbl tbody");
  tbody.innerHTML = "";
  $("#selftest-sum").textContent = "running...";
  let passed = 0;
  let total = 0;
  const ctx = {};

  const test = async (name, fn) => {
    const t0 = performance.now();
    let ok = false;
    let obs = "";
    try { ({ ok, obs } = await fn()); } catch (err) { obs = `Error: ${err.message}`; }
    total += 1;
    passed += ok ? 1 : 0;
    tbody.insertAdjacentHTML("beforeend",
      `<tr><td>${badge(ok ? "pass" : "fail", ok ? "PASS" : "FAIL")}</td><td>${esc(name)}</td><td>${esc(obs)}</td><td class="num">${Math.round(performance.now() - t0)}</td></tr>`);
  };
  const keysOk = (o) => ["id", "filename", "feature_count", "crs", "status"].every((k) => k in o);

  const kml = await sampleFile("survey.kml");
  const zip = await sampleFile("parcels_utm43.zip");

  await test("GET /health returns {status: ok}", async () => {
    const r = await call("GET", "/health");
    return { ok: r.status === 200 && r.body?.status === "ok", obs: `HTTP ${r.status} ${JSON.stringify(r.body)}` };
  });
  await test("OpenAPI lists the three required endpoints", async () => {
    const r = await call("GET", "/openapi.json");
    const paths = Object.keys(r.body?.paths ?? {});
    const need = ["/api/files/", "/api/files/{file_id}/", "/api/files/{file_id}/measurements/"];
    return { ok: need.every((p) => paths.includes(p)), obs: paths.join(", ") };
  });
  await test("POST /api/files/ with survey.kml returns 201 and the example shape", async () => {
    const r = await upload(kml);
    ctx.kml = r.body;
    const b = r.body ?? {};
    const ok = r.status === 201 && keysOk(b) && b.filename === "survey.kml" && b.feature_count === 7 && b.crs === "EPSG:4326" && b.status === "COMPLETED";
    return { ok, obs: `HTTP ${r.status} id=${b.id} filename=${b.filename} feature_count=${b.feature_count} crs=${b.crs} status=${b.status}` };
  });
  await test("GET /api/files/{id}/ returns the same record", async () => {
    const r = await call("GET", `/api/files/${ctx.kml.id}/`);
    return { ok: r.status === 200 && JSON.stringify(r.body) === JSON.stringify(ctx.kml), obs: `HTTP ${r.status}` };
  });
  await test("GET .../measurements/ returns 7 features with the expected status mix", async () => {
    const r = await call("GET", `/api/files/${ctx.kml.id}/measurements/`);
    ctx.meas = r.body;
    const s = r.body?.summary?.by_status ?? {};
    const ok = r.status === 200 && r.body.total === 7 && s.MEASURED === 4 && s.NOT_APPLICABLE === 1 && s.NO_GEOMETRY === 1 && s.UNSUPPORTED === 1;
    return { ok, obs: JSON.stringify(s) };
  });
  await test("Polygon area is in square metres (Plot A about 910,550 m²), via equal-area projection", async () => {
    const f = ctx.meas.results.find((x) => x.layer === "Plots" && x.feature_index === 0);
    const a = f?.measurement?.area_sq_m;
    return { ok: near(a, 910550, 0.005) && /^\+proj=laea/.test(f.projected_crs), obs: `${a} m² in ${shortCrs(f?.projected_crs)}` };
  });
  await test("Line length is in metres (Canal about 5,162 m), via the UTM zone", async () => {
    const f = ctx.meas.results.find((x) => x.layer === "Infrastructure" && x.feature_index === 0);
    const l = f?.measurement?.length_m;
    return { ok: near(l, 5161.9, 0.005) && f.projected_crs === "EPSG:32643", obs: `${l} m in ${f?.projected_crs}` };
  });
  await test("GeometryCollection is UNSUPPORTED and does not crash the file", async () => {
    const f = ctx.meas.results.find((x) => x.geometry_type === "GeometryCollection");
    return { ok: f?.status === "UNSUPPORTED", obs: f ? `${f.status}: ${f.message}` : "feature not found" };
  });
  await test("GET .../features/ returns geometry, CRS and properties", async () => {
    const r = await call("GET", `/api/files/${ctx.kml.id}/features/?geometry_type=polygon`);
    const f = r.body?.results?.[0];
    return { ok: r.status === 200 && r.body.total === 2 && !!f?.geometry && f.crs === "EPSG:4326" && f.properties?.owner === "Singh", obs: `total ${r.body?.total}, first: ${f?.properties?.Name}` };
  });
  await test("Pagination and the geometry_type filter work", async () => {
    const page = await call("GET", `/api/files/${ctx.kml.id}/measurements/?limit=2&offset=1`);
    const poly = await call("GET", `/api/files/${ctx.kml.id}/measurements/?geometry_type=Polygon`);
    const ok = page.body?.results?.length === 2 && page.body.total === 7 && poly.body?.total === 2 && poly.body.summary.feature_count === 7;
    return { ok, obs: `limit=2 gave ${page.body?.results?.length} of ${page.body?.total}; Polygon filter total ${poly.body?.total}` };
  });
  await test("Projected Shapefile zip (EPSG:32643): measured on the ground, not on the grid", async () => {
    const r = await upload(zip);
    const b = r.body ?? {};
    const m = r.status === 201 ? await call("GET", `/api/files/${b.id}/measurements/`) : null;
    const p1 = m?.body?.results?.find((x) => x.layer === "parcels" && x.feature_index === 0)?.measurement?.area_sq_m;
    // the parcel is 200 m x 150 m = 30,000 m2 on the UTM grid; on the ground it is about 29,989 m2
    const ok = r.status === 201 && b.crs === "EPSG:32643" && b.feature_count === 3 && near(p1, 29989.4, 0.0005);
    return { ok, obs: `HTTP ${r.status}, crs ${b.crs}, ${b.feature_count} features, parcel P-001 = ${p1} m² (grid area would be 30,000)` };
  });
  await test("Unknown id returns 404", async () => {
    const r = await call("GET", "/api/files/does-not-exist/");
    return { ok: r.status === 404, obs: `HTTP ${r.status} ${JSON.stringify(r.body)}` };
  });
  await test("Wrong file type (.txt) returns 415", async () => {
    const r = await upload(new File(["hello"], "notes.txt"));
    return { ok: r.status === 415, obs: `HTTP ${r.status}` };
  });
  await test("Empty .kml returns 400", async () => {
    const r = await upload(new File([], "empty.kml"));
    return { ok: r.status === 400, obs: `HTTP ${r.status}` };
  });
  await test("Corrupt zip: 422 + FAILED record, GET shows FAILED, measurements return 409", async () => {
    const r = await upload(new File(["not a zip"], "broken.zip"));
    const id = r.body?.id;
    const info = id ? await call("GET", `/api/files/${id}/`) : null;
    const meas = id ? await call("GET", `/api/files/${id}/measurements/`) : null;
    const ok = r.status === 422 && r.body?.status === "FAILED" && info?.body?.status === "FAILED" && meas?.status === 409;
    return { ok, obs: `POST ${r.status} ${r.body?.status}, GET ${info?.body?.status}, measurements HTTP ${meas?.status}` };
  });
  await test("Invalid pagination (limit=0) returns 422", async () => {
    const r = await call("GET", `/api/files/${ctx.kml.id}/measurements/?limit=0`);
    return { ok: r.status === 422, obs: `HTTP ${r.status}` };
  });

  $("#selftest-sum").innerHTML = `${badge(passed === total ? "pass" : "fail", `${passed} of ${total} passed`)}`;
}

/* ---------- wiring --------------------------------------------------------------------- */

async function checkHealth() {
  const pill = $("#health");
  const r = await call("GET", "/health");
  const up = r.status === 200 && r.body?.status === "ok";
  pill.textContent = up ? "API online" : "API unreachable";
  pill.className = `pill ${up ? "pill-ok" : "pill-bad"}`;
}

async function runSample(name) {
  try {
    const file = await sampleFile(name);
    choose(file);
    await processFile(file);
  } catch (err) {
    setStatus(err.message, "err");
  }
}

function init() {
  $("#origin").textContent = location.origin;
  const drop = $("#drop");
  $("#file").addEventListener("change", (e) => e.target.files[0] && choose(e.target.files[0]));
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => e.dataTransfer.files[0] && choose(e.dataTransfer.files[0]));
  $("#go").addEventListener("click", () => chosen && processFile(chosen));
  $("#sample-kml").addEventListener("click", () => runSample("survey.kml"));
  $("#sample-zip").addEventListener("click", () => runSample("parcels_utm43.zip"));
  $("#selftest").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try { await runSelfTest(); } finally { e.target.disabled = false; }
  });
  ["#f-type", "#f-status"].forEach((id) => $(id).addEventListener("change", renderTable));
  $("#f-search").addEventListener("input", renderTable);
  $("#csv").addEventListener("click", exportCsv);
  $("#tbl tbody").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-key]");
    if (!tr) return;
    const on = !tr.classList.contains("sel");
    document.querySelectorAll("#tbl tr.sel").forEach((x) => x.classList.remove("sel"));
    document.querySelectorAll("#map .hl").forEach((x) => x.classList.remove("hl"));
    if (on) {
      tr.classList.add("sel");
      document.querySelectorAll(`#map [data-key="${CSS.escape(tr.dataset.key)}"]`).forEach((x) => x.classList.add("hl"));
    }
  });
  checkHealth();

  // ?sample=kml|zip and ?selftest=1 run on load (handy for demos and automated checks)
  const params = new URLSearchParams(location.search);
  if (params.get("sample") === "kml") runSample("survey.kml");
  else if (params.get("sample") === "zip") runSample("parcels_utm43.zip");
  if (params.get("selftest")) $("#selftest").click();
}

init();
