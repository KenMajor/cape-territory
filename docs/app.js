/* Cape Territory — static field map. No build step, no keys. */
(function () {
  'use strict';

  // ---------- config ----------
  const DATA = 'data/';
  const RAMP = ['#c6dbef', '#6baed6', '#2171b5', '#08519c', '#062a5c']; // single hue, pale -> deep
  const PIN_ZOOM = 16;
  const LS_KEY = 'ct_knocked_v1';
  const LS_LAYER = 'ct_layer';
  const LS_BASE = 'ct_base';
  const LS_VIEW = 'ct_view';

  const LAYERS = {
    score:  { name: 'Attack Score',      prop: 's',  breaksKey: 'score',                  fmt: v => v.toFixed(0),          unit: '',    sub: 'Composite 0–100 (owner-occ, roof size, sales, density, solar)' },
    owner:  { name: 'Owner-occupied %',  prop: 'po', breaksKey: 'pct_owner_occ',          fmt: v => v.toFixed(0) + '%',    unit: '%',   sub: 'Share of single-family homes with owner living there' },
    size:   { name: 'Home size',         prop: 'mf', breaksKey: 'median_footprint_sqft',  fmt: v => fmtInt(v) + ' ft²',    unit: 'ft²', sub: 'Median building footprint (roof) per hex' },
    sales:  { name: 'Recent sales',      prop: 'pr', breaksKey: 'pct_recent_sale',        fmt: v => v.toFixed(0) + '%',    unit: '%',   sub: 'Sold since Sept 2023. Pins at street zoom', pins: 'sales' },
    roofs:  { name: 'Big roofs',         prop: 'mf', breaksKey: 'median_footprint_sqft',  fmt: v => fmtInt(v) + ' ft²',    unit: 'ft²', sub: 'Top-15% roof homes as pins at street zoom', pins: 'bigroof' },
    solar:  { name: 'Solar',             prop: 'sp', breaksKey: 'solar_penetration_pct',  fmt: v => v.toFixed(1) + '%',    unit: '%',   sub: 'Town-level estimate: installs ÷ homes (MassCEC)', townLevel: true },
  };

  // ---------- state ----------
  let manifest = null, scorecard = null;
  let layerKey = 'score';
  const hexData = {};      // slug -> GeoJSON
  const hexLayers = {};    // slug -> L.geoJSON
  const pinData = {};      // 'sales:slug' -> GeoJSON
  const pinLayers = {};
  const loading = new Set();
  let knocked = loadKnocked();
  let selected = null;     // feature props of tapped hex
  let selectedLayer = null;
  let meMarker = null, meCircle = null;

  // ---------- map ----------
  const map = L.map('map', { zoomControl: false, attributionControl: true, tap: true, maxZoom: 19 })
    .setView([41.68, -70.25], 11);
  const renderer = L.svg({ padding: 0.3 });
  map.addLayer(renderer);
  injectHatchPattern(renderer);

  const baseOSM = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
  });
  const baseSat = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    maxZoom: 19, attribution: 'Imagery &copy; Esri, Maxar, Earthstar Geographics, and the GIS User Community'
  });
  let sat = localStorage.getItem(LS_BASE) === 'sat';
  (sat ? baseSat : baseOSM).addTo(map);
  applyBaseTheme();

  const townOutline = L.geoJSON(null, { style: { color: '#ffb703', weight: 1.5, fill: false, opacity: 0.8 }, interactive: false, renderer });
  townOutline.addTo(map);
  const knockedLayer = L.geoJSON(null, { style: { fillColor: 'url(#hatch)', fillOpacity: 0.85, color: '#111', weight: 1, opacity: 0.8 }, interactive: false, renderer });
  const pinGroup = L.layerGroup().addTo(map);
  const hexGroup = L.layerGroup().addTo(map);
  knockedLayer.addTo(map);

  // ---------- boot ----------
  layerKey = localStorage.getItem(LS_LAYER) in LAYERS ? localStorage.getItem(LS_LAYER) : 'score';
  fetch(DATA + 'manifest.json').then(r => r.json()).then(m => {
    manifest = m;
    const saved = loadView();
    if (saved) map.setView(saved.c, saved.z);
    else map.fitBounds(unionBounds(m.towns.map(t => t.bounds)), { padding: [10, 10] });
    fetch(DATA + 'towns.geojson').then(r => r.json()).then(g => townOutline.addData(g));
    fetch(DATA + 'town_scorecard.json').then(r => r.json()).then(s => { scorecard = s; });
    setLayer(layerKey);
    loadVisible();
  }).catch(e => toast('Could not load data: ' + e.message));

  map.on('moveend zoomend', () => { loadVisible(); saveView(); });
  function saveView() { try { localStorage.setItem(LS_VIEW, JSON.stringify({ c: map.getCenter(), z: map.getZoom() })); } catch (e) { /* ignore */ } }
  function loadView() { try { const v = JSON.parse(localStorage.getItem(LS_VIEW)); return v && v.c && v.z ? v : null; } catch (e) { return null; } }

  // ---------- data loading (per town, lazy) ----------
  function townsInView() {
    if (!manifest) return [];
    const vb = map.getBounds();
    return manifest.towns.filter(t => vb.intersects(L.latLngBounds([[t.bounds[1], t.bounds[0]], [t.bounds[3], t.bounds[2]]])));
  }
  function loadVisible() {
    const towns = townsInView();
    for (const t of towns) {
      if (!hexData[t.slug]) loadHexes(t.slug);
    }
    const pins = LAYERS[layerKey].pins;
    if (pins && map.getZoom() >= PIN_ZOOM) {
      for (const t of towns) ensurePins(pins, t.slug);
    } else {
      pinGroup.clearLayers();
      for (const k in pinLayers) delete pinLayers[k];
    }
  }
  function showLoading(on) { document.getElementById('loading').hidden = !on; }
  function loadHexes(slug) {
    loading.add(slug); showLoading(true);
    fetch(`${DATA}hexes_${slug}.geojson`).then(r => r.json()).then(g => {
      hexData[slug] = g;
      buildHexLayer(slug);
      refreshKnocked();
    }).catch(e => toast('Load failed: ' + slug)).finally(() => { loading.delete(slug); showLoading(loading.size > 0); });
  }
  function buildHexLayer(slug) {
    if (hexLayers[slug]) { hexGroup.removeLayer(hexLayers[slug]); }
    const lyr = L.geoJSON(hexData[slug], {
      renderer,
      style: f => hexStyle(f.properties),
      onEachFeature: (f, l) => { l.on('click', () => selectHex(f.properties, l)); }
    });
    hexLayers[slug] = lyr;
    hexGroup.addLayer(lyr);
  }
  function ensurePins(kind, slug) {
    const key = kind + ':' + slug;
    if (pinLayers[key]) return;
    const build = g => {
      if (pinLayers[key] || LAYERS[layerKey].pins !== kind) return;
      const lyr = L.geoJSON(g, {
        renderer,
        pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 7, color: '#fff', weight: 2, fillColor: kind === 'sales' ? '#ffb703' : '#d62828', fillOpacity: 1 }),
        onEachFeature: (f, l) => {
          const p = f.properties;
          l.bindPopup(kind === 'sales' ? 'Sold ' + fmtMonth(p.d) : fmtInt(p.f) + ' ft² roof', { closeButton: false, offset: [0, -4] });
        }
      });
      pinLayers[key] = lyr; pinGroup.addLayer(lyr);
    };
    if (pinData[key]) { build(pinData[key]); return; }
    fetch(`${DATA}${kind}_${slug}.geojson`).then(r => r.json()).then(g => { pinData[key] = g; build(g); });
  }

  // ---------- styling ----------
  function breaks() { return (manifest && manifest.breaks[LAYERS[layerKey].breaksKey]) || [20, 40, 60, 80]; }
  function classOf(v) {
    if (v == null || isNaN(v)) return -1;
    const b = breaks(); let i = 0;
    while (i < b.length && v >= b[i]) i++;
    return Math.min(i, RAMP.length - 1);
  }
  function hexStyle(p) {
    const v = p[LAYERS[layerKey].prop];
    const c = classOf(v);
    const isSel = selected && selected.h === p.h;
    return {
      fillColor: c < 0 ? '#888' : RAMP[c], fillOpacity: 0.62,
      color: isSel ? '#ffb703' : '#ffffff', weight: isSel ? 4 : 1, opacity: isSel ? 1 : 0.7,
    };
  }
  function restyleAll() { for (const s in hexLayers) hexLayers[s].setStyle(f => hexStyle(f.properties)); }

  function setLayer(key) {
    layerKey = key; localStorage.setItem(LS_LAYER, key);
    document.getElementById('layer-name').textContent = LAYERS[key].name;
    restyleAll(); renderLegend(); loadVisible();
  }

  // ---------- legend ----------
  const legendEl = document.getElementById('legend');
  let legendOpen = false;
  legendEl.addEventListener('click', () => { legendOpen = !legendOpen; renderLegend(); });
  function renderLegend() {
    const L_ = LAYERS[layerKey]; const b = breaks();
    document.getElementById('legend-title').textContent = L_.name;
    document.getElementById('legend-swatches').innerHTML = RAMP.map(c => `<i style="background:${c}"></i>`).join('');
    const det = document.getElementById('legend-detail');
    det.hidden = !legendOpen;
    document.querySelector('.legend-caret').textContent = legendOpen ? '▴' : '▾';
    if (!legendOpen) return;
    const labels = [];
    if (L_.townLevel && b.length && b.length <= 5) {
      // one class per town value
      const vals = b.slice();
      det.innerHTML = vals.map(v => { const c = classOf(v); return `<div class="row"><i style="background:${RAMP[c]}"></i>${L_.fmt(v)} — ${townForSolar(v)}</div>`; }).join('')
        + `<div class="row hatch"><i></i>Knocked</div><div class="note">${L_.sub}</div>`;
      return;
    }
    for (let i = 0; i < RAMP.length; i++) {
      let t;
      if (i === 0) t = '< ' + L_.fmt(b[0]);
      else if (i === RAMP.length - 1) t = '≥ ' + L_.fmt(b[b.length - 1]);
      else t = L_.fmt(b[i - 1]) + ' – ' + L_.fmt(b[i]);
      labels.push(`<div class="row"><i style="background:${RAMP[i]}"></i>${t}</div>`);
    }
    labels.push('<div class="row hatch"><i></i>Knocked</div>');
    det.innerHTML = labels.join('') + `<div class="note">${L_.sub}</div>`;
  }
  function townForSolar(v) {
    if (!scorecard) return '';
    return scorecard.towns.filter(t => t.solar_penetration_pct != null && Math.abs(t.solar_penetration_pct - v) < 0.05).map(t => t.town).join(', ');
  }

  // ---------- sheet ----------
  const sheet = document.getElementById('sheet');
  const sheetBody = document.getElementById('sheet-body');
  const sheetTitle = document.getElementById('sheet-title');
  document.getElementById('sheet-close').addEventListener('click', closeSheet);
  function openSheet(title, html) { sheetTitle.textContent = title; sheetBody.innerHTML = html; sheet.hidden = false; }
  function closeSheet() {
    sheet.hidden = true;
    if (selected) { selected = null; if (selectedLayer) selectedLayer.setStyle(hexStyle(selectedLayer.feature.properties)); selectedLayer = null; }
  }

  document.getElementById('btn-layers').addEventListener('click', () => {
    const html = '<div class="layer-list">' + Object.keys(LAYERS).map(k =>
      `<button class="layer-btn ${k === layerKey ? 'on' : ''}" data-k="${k}"><span><div class="name">${LAYERS[k].name}</div><div class="sub">${LAYERS[k].sub}</div></span></button>`).join('') + '</div>';
    openSheet('Layer', html);
    sheetBody.querySelectorAll('.layer-btn').forEach(b => b.addEventListener('click', () => { setLayer(b.dataset.k); closeSheet(); }));
  });

  function selectHex(p, layer) {
    if (selectedLayer) selectedLayer.setStyle(hexStyle(selectedLayer.feature.properties));
    selected = p; selectedLayer = layer;
    layer.setStyle(hexStyle(p)); layer.bringToFront();
    renderHexCard();
    // keep the tapped hex visible above the sheet
    const sh = sheet.getBoundingClientRect().height;
    const pt = map.latLngToContainerPoint(layer.getBounds().getCenter());
    const visibleH = map.getSize().y - sh;
    if (pt.y > visibleH - 30) map.panBy([0, pt.y - visibleH / 2], { animate: true });
  }
  function renderHexCard() {
    const p = selected; if (!p) return;
    const k = knocked[p.h];
    const html = `
      <div class="hex-meta">${p.t} · hex ${p.h.slice(-6)}</div>
      <div class="stats">
        <div class="stat big"><div class="num">${p.s == null ? '–' : Math.round(p.s)}</div><div class="lab">ATTACK SCORE</div></div>
        <div class="stat"><div class="num">${p.n}</div><div class="lab">HOMES</div></div>
        <div class="stat"><div class="num">${p.po == null ? '–' : Math.round(p.po)}<small>%</small></div><div class="lab">OWNER-OCC</div></div>
        <div class="stat"><div class="num">${p.ml == null ? '–' : fmtInt(p.ml)}</div><div class="lab">MED FT²</div></div>
        <div class="stat"><div class="num">${p.rs}</div><div class="lab">RECENT SALES</div></div>
        <div class="stat"><div class="num">${p.mf == null ? '–' : fmtInt(p.mf)}</div><div class="lab">MED ROOF FT²</div></div>
        <div class="stat"><div class="num">${p.br}</div><div class="lab">BIG ROOFS</div></div>
      </div>
      <button id="btn-knock" class="knock ${k ? 'on' : ''}">${k ? 'Knocked ' + k.knocked_date + ' — tap to undo' : 'Mark knocked'}</button>`;
    openSheet(p.v || p.t, html);
    document.getElementById('btn-knock').addEventListener('click', () => {
      if (knocked[p.h]) delete knocked[p.h];
      else knocked[p.h] = { town: p.t, knocked_date: new Date().toISOString().slice(0, 10) };
      saveKnocked(); refreshKnocked(); renderHexCard();
    });
  }

  // ---------- knocked state ----------
  function loadKnocked() { try { return JSON.parse(localStorage.getItem(LS_KEY) || '{}'); } catch (e) { return {}; } }
  function saveKnocked() { localStorage.setItem(LS_KEY, JSON.stringify(knocked)); }
  function refreshKnocked() {
    knockedLayer.clearLayers();
    const feats = [];
    for (const s in hexData) for (const f of hexData[s].features) if (knocked[f.properties.h]) feats.push(f);
    if (feats.length) knockedLayer.addData({ type: 'FeatureCollection', features: feats });
  }
  document.getElementById('btn-copy').addEventListener('click', () => {
    const list = Object.keys(knocked).sort().map(h => ({ hex_id: h, town: knocked[h].town, knocked_date: knocked[h].knocked_date }));
    const txt = JSON.stringify(list);
    const done = () => toast(`Copied ${list.length} knocked hex${list.length === 1 ? '' : 'es'}`);
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(txt).then(done, () => fallbackCopy(txt, done));
    else fallbackCopy(txt, done);
  });
  function fallbackCopy(txt, done) {
    openSheet('Progress JSON', `<p style="font-size:14px">Select all and copy:</p><textarea style="width:100%;height:40vh;font-size:13px" readonly>${txt}</textarea>`);
    const ta = sheetBody.querySelector('textarea'); ta.focus(); ta.select();
  }

  // ---------- scorecard ----------
  document.getElementById('btn-scorecard').addEventListener('click', () => {
    if (!scorecard) { toast('Scorecard still loading'); return; }
    const rows = [
      ['Single-family homes', 'n_sfh'], ['Target doors (owner-occ)', 'target_doors'], ['Owner-occ % (parcel method)', 'pct_owner_occ_parcel_method'],
      ['Absentee out of state', 'n_absentee_out_of_state'], ['Median living ft²', 'median_living_sqft'], ['Median roof ft²', 'median_footprint_sqft'],
      ['Big-roof homes', 'n_big_roof'], ['Sold since Sept 2023', 'n_recent_sale'], ['New owner (12 mo)', 'n_new_owner_12mo'],
      ['Solar installs (res.)', 'solar_installs_res'], ['Solar in prior 24 mo', 'solar_installs_24mo'], ['Solar penetration %', 'solar_penetration_pct'],
      ['Census owner-occ %', 'census_pct_owner_occupied'], ['Census seasonal-vacant %', 'census_pct_seasonal_vacant'], ['Census renter %', 'census_pct_renter'],
      ['Assessor FY', 'fy_vintage'], ['Latest sale in data', 'latest_sale_date'],
    ];
    const html = scorecard.towns.map(t => {
      const tot = (manifest.towns.find(m => m.name === t.town) || {}).n_hex || t.n_hex || 0;
      const kn = Object.values(knocked).filter(k => k.town === t.town).length;
      const pct = tot ? Math.round(100 * kn / tot) : 0;
      return `<div class="sc-town"><h3>${t.town}</h3>
        <div class="sc-progress"><span class="num">${kn} / ${tot}</span><span class="lab">HEXES KNOCKED · ${pct}%</span></div>
        <div class="sc-bar"><i style="width:${pct}%"></i></div>
        <table class="sc">${rows.map(([lab, k]) => `<tr><td>${lab}</td><td>${fmtVal(t[k], k)}</td></tr>`).join('')}</table></div>`;
    }).join('') + `<div class="sc-note">Generated ${scorecard.generated_at}. Solar: MassCEC PTS as of ${scorecard.meta.pts_as_of || 'n/a'}. Census: ${scorecard.meta.census_status}.</div>`;
    openSheet('Town scorecard', html);
  });
  function fmtVal(v, k) {
    if (v == null) return 'pending';
    if (k === 'fy_vintage' || k === 'latest_sale_date') return String(v);
    if (typeof v === 'number') return k.startsWith('pct') || k.includes('pct') ? v.toFixed(1) + '%' : fmtInt(v);
    return String(v);
  }

  // ---------- basemap + locate ----------
  document.getElementById('btn-base').addEventListener('click', () => {
    sat = !sat; localStorage.setItem(LS_BASE, sat ? 'sat' : 'map');
    if (sat) { map.removeLayer(baseOSM); baseSat.addTo(map); } else { map.removeLayer(baseSat); baseOSM.addTo(map); }
    applyBaseTheme();
  });
  function applyBaseTheme() {
    document.body.classList.toggle('sat', sat);
    document.getElementById('map').style.background = sat ? '#0b1220' : '#dde3ea';
    document.getElementById('btn-base').textContent = sat ? 'MAP' : 'SAT';
  }
  document.getElementById('btn-locate').addEventListener('click', () => {
    if (!navigator.geolocation) { toast('No GPS in this browser'); return; }
    toast('Locating…');
    map.locate({ setView: true, maxZoom: 16, enableHighAccuracy: true, timeout: 12000 });
  });
  map.on('locationfound', e => {
    if (meMarker) { meMarker.setLatLng(e.latlng); meCircle.setLatLng(e.latlng).setRadius(e.accuracy / 2); }
    else {
      meCircle = L.circle(e.latlng, { radius: e.accuracy / 2, color: '#1d4ed8', weight: 1, fillOpacity: 0.12, interactive: false }).addTo(map);
      meMarker = L.circleMarker(e.latlng, { radius: 9, className: 'me', fillOpacity: 1, weight: 3, interactive: false }).addTo(map);
    }
  });
  map.on('locationerror', e => toast('Location unavailable: ' + e.message));

  window.__ct = { map, hexLayers, get knocked() { return knocked; } };

  // ---------- helpers ----------
  function injectHatchPattern(r) {
    const svg = r._container; if (!svg) return;
    const ns = 'http://www.w3.org/2000/svg';
    const defs = document.createElementNS(ns, 'defs');
    const pat = document.createElementNS(ns, 'pattern');
    pat.setAttribute('id', 'hatch'); pat.setAttribute('patternUnits', 'userSpaceOnUse');
    pat.setAttribute('width', '10'); pat.setAttribute('height', '10'); pat.setAttribute('patternTransform', 'rotate(45)');
    const bg = document.createElementNS(ns, 'rect'); bg.setAttribute('width', '10'); bg.setAttribute('height', '10'); bg.setAttribute('fill', '#ffffff'); bg.setAttribute('fill-opacity', '0.55');
    const ln = document.createElementNS(ns, 'rect'); ln.setAttribute('width', '4'); ln.setAttribute('height', '10'); ln.setAttribute('fill', '#111111');
    pat.appendChild(bg); pat.appendChild(ln); defs.appendChild(pat); svg.insertBefore(defs, svg.firstChild);
  }
  function unionBounds(list) {
    let b = null;
    for (const x of list) { const lb = L.latLngBounds([[x[1], x[0]], [x[3], x[2]]]); b = b ? b.extend(lb) : lb; }
    return b;
  }
  function fmtInt(v) { return Math.round(v).toLocaleString('en-US'); }
  function fmtMonth(ym) { if (!ym) return '?'; const [y, m] = ym.split('-'); return ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][+m - 1] + ' ' + y; }
  let toastT = null;
  function toast(msg) {
    const el = document.getElementById('toast'); el.textContent = msg; el.hidden = false;
    clearTimeout(toastT); toastT = setTimeout(() => { el.hidden = true; }, 2200);
  }
})();
