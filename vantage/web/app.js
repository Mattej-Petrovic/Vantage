/* App state, panels, and the bridge to Python (pywebview JsApi). */

const DEFAULT_SETTINGS = {
  scan_interval: '30',
  theme: 'system',
  alert_delivery: 'in_app',
  port_scan: 'on',
  toast_new_port: 'off',
  toast_risk_raised: 'off',
  close_to_tray: 'on',
};

const state = {
  devices: [],
  byMac: new Map(),
  gateway: null,
  status: {},
  alerts: [],
  unread: 0,
  selected: null,
  filter: 'all',
  query: '',
  settings: { ...DEFAULT_SETTINGS },
  settingsLoaded: false,
  settingsLoading: false,
  ready: false,
  booting: false,
};

const $ = (id) => document.getElementById(id);
const api = () => (window.pywebview && window.pywebview.api) || null;

/* A frontend error must never fail silently: without a console this window is
   a black box, and a dead UI over a healthy backend looks identical to a
   backend that never started. */
window.addEventListener('error', (e) => {
  const bar = document.getElementById('titlebar-title');
  if (bar) bar.textContent = `JS error: ${e.message} @ ${(e.filename || '').split('/').pop()}:${e.lineno}`;
});
window.addEventListener('unhandledrejection', (e) => {
  const bar = document.getElementById('titlebar-title');
  if (bar) bar.textContent = `Promise rejected: ${e.reason && e.reason.message ? e.reason.message : e.reason}`;
});

/* ---------- boot ---------- */

window.addEventListener('DOMContentLoaded', () => {
  hydrateIcons();
  bindUI();
  NetworkMap.init($('map'), { onSelect: selectDevice, onHover: showTooltip });
  boot();
});

window.addEventListener('pywebviewready', boot);

/* The bridge can be late, and a single failed handshake must never leave the
   UI stuck on its empty state — keep trying until it answers. */
let bootAttempts = 0;
const bootTimer = setInterval(() => {
  if (state.ready) return clearInterval(bootTimer);
  if (++bootAttempts > 60) return clearInterval(bootTimer); // ~30s, then give up
  if (api()) boot();
}, 500);

async function boot() {
  if (state.ready || state.booting) return;
  if (!$('map') || !$('status-chip')) return;
  state.booting = true;

  state.ready = true;
  state.booting = false;
  clearInterval(bootTimer);

  // Startup must not wait on Python. The bridge can load settings later.
  state.toastAvailable = false;
  applyTheme(state.settings.theme || 'system');

  try {
    applySnapshot({
      devices: [],
      gateway: null,
      status: {},
      alerts: [],
      unread_alerts: 0,
    });
    hintOnce('Drag to pan · scroll to zoom · click a node for details');
  } catch (err) {
    console.error('boot render failed', err);
  }
}

async function loadInitialFromBackend() {
  if (state.settingsLoaded || state.settingsLoading || !api()) return;
  state.settingsLoading = true;
  try {
    const initial = await api().get_initial();
    state.settings = { ...DEFAULT_SETTINGS, ...(initial.settings || {}) };
    state.toastAvailable = !!initial.toast_available;
    $('set-dbpath').textContent = initial.db_path || '';
    state.settingsLoaded = true;
  } catch (err) {
    console.error('get_initial failed', err);
  } finally {
    state.settingsLoading = false;
  }
}

/* Python pushes here via evaluate_js. */
window.vantage = {
  event(evt) {
    switch (evt.type) {
      case 'scan_complete':
        applySnapshot(evt.snapshot);
        break;
      case 'status':
        state.status = evt.status;
        renderStatus();
        break;
      case 'device_joined':
        NetworkMap.flashJoin(evt.device.mac);
        toast(
          evt.is_new ? 'new' : 'join',
          evt.is_new ? `New device: ${evt.device.name}` : `${evt.device.name} came online`
        );
        break;
      case 'device_left':
        toast('leave', `${evt.device.name} went offline`);
        break;
      case 'new_port':
        toast('new', `${evt.name} opened port ${evt.port.port} (${evt.port.service || 'unknown'})`);
        break;
      case 'risk_raised':
        toast('risk', evt.message);
        break;
      case 'error':
        toast('new', evt.message);
        break;
    }
  },
};

/* ---------- data ---------- */

function applySnapshot(snapshot) {
  if (!snapshot) return;
  state.devices = snapshot.devices || [];
  state.byMac = new Map(state.devices.map((d) => [d.mac, d]));
  state.gateway = snapshot.gateway;
  state.status = snapshot.status || {};
  state.alerts = snapshot.alerts || [];
  state.unread = snapshot.unread_alerts || 0;

  // "New" comes from the backend baseline (appeared after Vantage started
  // watching); the amber highlight decays after a day.
  const dayAgo = Date.now() / 1000 - 86400;
  for (const d of state.devices) d.is_new_today = !!d.is_new && d.first_seen > dayAgo;

  NetworkMap.setDevices(state.devices, state.gateway);
  renderStatus();
  renderInterfaces();
  renderList();
  renderAlerts();
  if (state.selected) renderDetail(state.byMac.get(state.selected));

  const hasDevices = state.devices.length > 0;
  $('map-empty').classList.toggle('is-hidden', hasDevices);
  if (hasDevices && !applySnapshot.fitted) {
    applySnapshot.fitted = true;
    setTimeout(() => NetworkMap.fit(), 700);
  }
}

/* ---------- top bar ---------- */

function renderStatus() {
  const s = state.status || {};
  const chip = $('status-chip');
  const text = $('status-text');

  let mode = 'idle';
  if (s.error) mode = 'error';
  else if (s.scanning) mode = 'scanning';
  else if (s.paused) mode = 'paused';
  chip.dataset.state = mode;

  if (s.error) text.textContent = s.error;
  else if (s.scanning) text.textContent = 'Scanning…';
  else if (s.paused) text.textContent = 'Paused';
  else if (s.last_scan_ts) text.textContent = `Scanned ${timeAgo(s.last_scan_ts)}`;
  else text.textContent = 'Idle';

  const empty = $('map-empty');
  if (empty) {
    empty.classList.toggle('is-scanning', !!s.scanning);
    const title = empty.querySelector('h2');
    const copy = empty.querySelector('p');
    if (title) title.textContent = s.scanning ? 'Mapping your network' : 'Ready to map your network';
    if (copy) {
      copy.textContent = s.scanning
        ? 'Pinging every host on the subnet. Devices appear the moment they answer.'
        : 'Click Scan now when you want Vantage to discover devices on this subnet.';
    }
  }

  const online = state.devices.filter((d) => d.online).length;
  $('count-online').textContent = online;
  $('count-total').textContent = state.devices.length;

  const bell = $('btn-alerts');
  bell.querySelector('.badge-dot')?.remove();
  if (state.unread > 0) {
    const badge = document.createElement('span');
    badge.className = 'badge-dot';
    badge.textContent = state.unread > 9 ? '9+' : state.unread;
    bell.appendChild(badge);
  }
}

function renderInterfaces() {
  const select = $('iface-select');
  const list = state.status.interfaces || [];
  const current = state.status.interface;
  if (select.dataset.count === String(list.length) && select.value) return;
  select.dataset.count = String(list.length);
  select.innerHTML = list
    .map(
      (i) =>
        `<option value="${i.id}" ${current && i.id === current.id ? 'selected' : ''}>` +
        `${escapeHtml(i.name)} — ${i.cidr}</option>`
    )
    .join('');
}

/* ---------- sidebar ---------- */

function visibleDevices() {
  const q = state.query.trim().toLowerCase();
  return state.devices.filter((d) => {
    if (state.filter === 'online' && !d.online) return false;
    if (state.filter === 'offline' && d.online) return false;
    if (state.filter === 'new' && !d.is_new_today) return false;
    if (state.filter === 'risk' && d.risk_band !== 'risk' && d.risk_band !== 'watch') return false;
    if (!q) return true;
    return [d.name, d.ip, d.mac, d.vendor, d.device_type, d.hostname, d.model]
      .filter(Boolean)
      .some((v) => String(v).toLowerCase().includes(q));
  });
}

function renderList() {
  const list = $('device-list');
  const items = visibleDevices();
  $('list-count').textContent = items.length
    ? `${items.length}${items.length !== state.devices.length ? ` / ${state.devices.length}` : ''}`
    : '';

  if (!items.length) {
    list.innerHTML = `<div class="list-empty">${
      state.devices.length ? 'No devices match this filter.' : 'No devices discovered yet.'
    }</div>`;
    return;
  }

  list.innerHTML = items
    .map((d) => {
      const presence = d.online ? (d.is_new_today ? 'is-new' : '') : 'is-off';
      // When the name already is the IP (no vendor, randomized MAC), the
      // subtitle should carry something else useful.
      const role = d.is_local ? ' · This PC' : d.is_gateway ? ' · Router' : '';
      const sub = d.name === d.ip ? (d.randomized_mac ? 'Randomized MAC' : d.mac) : d.ip || d.mac;
      return `
      <button class="device-row ${d.mac === state.selected ? 'is-selected' : ''}
              ${d.online ? '' : 'is-offline'} ${d.is_gateway ? 'is-router' : ''}" data-mac="${d.mac}">
        <span class="device-icon">${iconSvg(DEVICE_ICON[d.device_type] || 'unknown', 17)}</span>
        <span class="device-meta">
          <span class="device-name">${escapeHtml(d.name)}</span>
          <span class="device-sub mono">${escapeHtml(sub)}${role}</span>
        </span>
        ${
          d.risk_band === 'risk' || d.risk_band === 'watch'
            ? `<span class="risk-pip" data-band="${d.risk_band}" title="${
                d.risk_band === 'risk' ? 'Needs attention' : 'Worth a look'
              }"></span>`
            : ''
        }
        <span class="presence ${presence}"></span>
      </button>`;
    })
    .join('');

  list.querySelectorAll('.device-row').forEach((row) => {
    row.addEventListener('click', () => selectDevice(row.dataset.mac));
  });
}

/* ---------- detail panel ---------- */

function selectDevice(mac) {
  state.selected = mac && state.byMac.has(mac) ? mac : null;
  NetworkMap.select(state.selected);
  $('app').classList.toggle('detail-open', !!state.selected);
  $('detail').classList.toggle('is-open', !!state.selected);
  renderList();
  if (state.selected) renderDetail(state.byMac.get(state.selected));
}

function renderDetail(d) {
  if (!d) return;
  // Every scan re-renders this panel. Without holding the scroll position, the
  // panel jumps back to the top every 30 seconds while you are reading it.
  const body = $('detail-body');
  const scrollTop = body.scrollTop;

  $('detail-icon').innerHTML = iconSvg(DEVICE_ICON[d.device_type] || 'unknown', 22);
  $('detail-name').textContent = d.name;
  // The name often *is* the vendor; then the subtitle should add something.
  $('detail-vendor').textContent =
    d.name === d.vendor ? d.ip || d.mac : d.vendor || (d.randomized_mac ? 'Randomized MAC' : 'Unknown vendor');

  const pills = [];
  pills.push(
    d.online
      ? '<span class="pill pill-ok">Online</span>'
      : '<span class="pill pill-neutral">Offline</span>'
  );
  if (d.is_gateway) pills.push('<span class="pill pill-neutral">Router</span>');
  if (d.is_local) pills.push('<span class="pill pill-neutral">This PC</span>');
  if (d.is_new_today) pills.push('<span class="pill pill-watch">New today</span>');
  if (d.randomized_mac) pills.push('<span class="pill pill-neutral">Randomized MAC</span>');
  if (d.trust_status === 'trusted') pills.push('<span class="pill pill-ok">Trusted</span>');
  if (d.trust_status === 'blocked') pills.push('<span class="pill pill-risk">Blocked</span>');
  $('detail-pills').innerHTML = pills.join('');

  const rename = $('detail-rename');
  if (document.activeElement !== rename) rename.value = d.custom_name || '';
  rename.placeholder = d.name;

  $('detail-trust').querySelectorAll('button').forEach((b) => {
    b.classList.toggle('is-active', b.dataset.trust === (d.trust_status || 'unknown'));
  });

  const rows = [
    ['IP', `<span class="mono">${escapeHtml(d.ip || '—')}</span>`],
    ['MAC', `<span class="mono">${escapeHtml(d.mac)}</span>`],
    ['Type', escapeHtml(capitalize(d.device_type || 'unknown'))],
  ];
  // Only show where a name came from when there is a discovered name to explain.
  if (d.hostname) {
    rows.push([
      'Hostname',
      `${escapeHtml(d.hostname)}${
        d.name_source ? ` <span class="port-banner" style="display:inline">via ${escapeHtml(d.name_source)}</span>` : ''
      }`,
    ]);
  }
  if (d.model && d.model !== d.hostname) rows.push(['Model', escapeHtml(d.model)]);
  rows.push(
    ['Latency', `<span class="mono">${d.rtt_ms != null ? d.rtt_ms.toFixed(1) + ' ms' : 'no reply'}</span>`],
    ['First seen', formatTime(d.first_seen)],
    ['Last seen', formatTime(d.last_seen)]
  );
  $('detail-kv').innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('');

  $('btn-wake').disabled = !d.mac || d.is_local;
  loadSparkline(d.mac);
  loadDeviceDetail(d);
  body.scrollTop = scrollTop;
}

/* ---------- device detail data (ports, risk, timeline) ---------- */

/* The panel re-renders on every scan, but almost nothing in it changes between
   scans. Writing innerHTML anyway makes the list visibly flicker and drops any
   text the user was selecting — so each section only touches the DOM when the
   data it renders actually differs from what is already on screen. */
const rendered = new Map();
function renderIfChanged(key, data, render) {
  const signature = JSON.stringify(data);
  if (rendered.get(key) === signature) return;
  rendered.set(key, signature);
  render();
}

async function loadDeviceDetail(device) {
  const mac = device.mac;

  if (!device.port_scanned) {
    renderIfChanged(`ports:${mac}`, device.online, () => {
      $('detail-ports').innerHTML = `<div class="ports-note">${
        device.online
          ? 'Not scanned yet — Vantage works through devices a few at a time.'
          : 'Never scanned while online.'
      }</div>`;
    });
  }

  let info;
  try {
    info = await api().get_device(mac);
  } catch (err) {
    $('detail-ports').innerHTML = '<div class="ports-note">Could not load device details.</div>';
    return;
  }
  if (state.selected !== mac) return; // selection moved on while we waited

  if (device.port_scanned) {
    renderIfChanged(`ports:${mac}`, info.ports || [], () => renderPorts(info.ports || []));
  }
  renderIfChanged(`risk:${mac}`, info.risk || {}, () => renderRisk(info.risk, device));
  renderIfChanged(`timeline:${mac}`, info.timeline || [], () =>
    renderTimeline(info.timeline || [])
  );
}

function renderPorts(ports) {
  const host = $('detail-ports');
  if (!ports.length) {
    host.innerHTML =
      '<div class="ports-note">No open ports found. The device answers ping but exposes no services we probe for.</div>';
    return;
  }
  host.innerHTML = ports
    .map(
      (p) => `
      <div class="port-row ${p.status === 'open' ? '' : 'is-closed'}">
        <span class="port-num">${p.port}</span>
        <span>
          <span class="port-service">${escapeHtml(p.service || 'unknown')}${
            p.status === 'open' ? '' : ' · closed'
          }</span>
          ${p.banner ? `<div class="port-banner">${escapeHtml(p.banner)}</div>` : ''}
        </span>
      </div>`
    )
    .join('');
}

/* ---------- risk ---------- */

const RISK_HEADLINE = {
  ok: 'Nothing risky in the scanned ports',
  watch: 'Worth a look',
  risk: 'Needs attention',
  unknown: 'Not assessed yet',
};

function renderRisk(risk, device) {
  const host = $('detail-risk');
  const band = (risk && risk.band) || 'unknown';
  const findings = (risk && risk.findings) || [];

  $('detail-risk-score').textContent = band === 'unknown' ? '' : `${risk.score} / 100`;

  const banner = `
    <div class="risk-banner" data-band="${band}">
      ${iconSvg(band === 'ok' ? 'check' : band === 'unknown' ? 'unknown' : 'alert', 15)}
      <span>${RISK_HEADLINE[band]}</span>
    </div>`;

  if (band === 'unknown') {
    host.innerHTML =
      banner +
      `<div class="risk-note">${
        device.online
          ? 'This device has not been port-scanned yet, so there is nothing to score. Use “Scan now” below to do it immediately.'
          : 'This device has never been scanned while online.'
      }</div>`;
    return;
  }

  const rows = findings
    .map(
      (f) => `
      <div class="risk-finding">
        <span class="risk-sev" data-sev="${f.severity}">${f.severity}${
          f.port ? `<br>:${f.port}` : ''
        }</span>
        <span>
          <div class="risk-title">${escapeHtml(f.title)}</div>
          <div class="risk-why">${escapeHtml(f.detail)}</div>
        </span>
      </div>`
    )
    .join('');

  // A clean result has to state its own limits. "No findings" across ~110
  // probed ports is not the same claim as "this device is safe", and a posture
  // panel that lets the user confuse the two is worse than none at all.
  const note =
    '<div class="risk-note">Scored from ~110 common TCP ports. A clean result ' +
    'means nothing risky was found in what was probed — not that the device is ' +
    'secure.</div>';

  host.innerHTML = banner + rows + note;
}

/* ---------- presence timeline ---------- */

function renderTimeline(segments) {
  const host = $('detail-timeline');
  const label = $('detail-timeline-label');

  if (!segments.length) {
    host.innerHTML = '';
    label.textContent = 'No observations recorded yet.';
    return;
  }

  // The bar spans what was actually observed, not a fixed week. Drawing a full
  // 7 days after four hours of watching renders the six unobserved days as
  // empty track — visually identical to "offline", which is a claim the data
  // does not support. The window grows as the history does.
  const now = Date.now() / 1000;
  const start = Math.max(now - 7 * 86400, segments[0].start);
  const span = Math.max(now - start, 1);

  // Every segment is at least a hair wide: a device that appeared for one scan
  // is exactly the event this view exists to show, and rounding it to zero
  // pixels would hide it.
  host.innerHTML = segments
    .filter((s) => s.online && s.end >= start)
    .map((s) => {
      const left = ((Math.max(s.start, start) - start) / span) * 100;
      const width = Math.max(0.6, ((s.end - Math.max(s.start, start)) / span) * 100);
      return `<span class="timeline-seg" style="left:${left.toFixed(3)}%;width:${width.toFixed(
        3
      )}%"></span>`;
    })
    .join('');

  const onlineSecs = segments
    .filter((s) => s.online)
    .reduce((total, s) => total + (s.end - s.start), 0);
  const observed = segments[segments.length - 1].end - segments[0].start;
  const share = observed > 0 ? Math.round((onlineSecs / observed) * 100) : 0;
  label.innerHTML =
    `<span>watched since ${formatTime(start)}</span>` +
    `<span>online ${share}% of that</span>`;
}

async function loadSparkline(mac) {
  if (!api()) return;
  const history = await api().get_history(mac, 60);
  const svg = $('detail-spark');
  const points = history.filter((h) => h.online && h.rtt_ms != null).reverse();

  if (points.length < 2) {
    svg.innerHTML = '';
    $('detail-spark-label').textContent = points.length
      ? 'Not enough samples yet.'
      : 'No round-trip samples yet.';
    return;
  }

  const w = 100, h = 40, max = Math.max(...points.map((p) => p.rtt_ms), 5);
  const path = points
    .map((p, i) => {
      const x = (i / (points.length - 1)) * w;
      const y = h - 3 - (p.rtt_ms / max) * (h - 8);
      return `${i ? 'L' : 'M'}${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(' ');

  svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
  svg.innerHTML = `
    <path d="${path} L${w} ${h} L0 ${h} Z" fill="var(--accent-soft)" stroke="none" />
    <path d="${path}" fill="none" stroke="var(--accent)" stroke-width="1.4"
          vector-effect="non-scaling-stroke" stroke-linejoin="round" />`;

  const avg = points.reduce((a, p) => a + p.rtt_ms, 0) / points.length;
  $('detail-spark-label').textContent =
    `${points.length} samples · avg ${avg.toFixed(1)} ms · peak ${max.toFixed(1)} ms`;
}

/* ---------- alerts ---------- */

function renderAlerts() {
  const body = $('alerts-body');
  if (!state.alerts.length) {
    body.innerHTML = '<div class="list-empty">Nothing has changed yet.</div>';
    return;
  }
  body.innerHTML = state.alerts
    .map(
      (a) => `
      <div class="alert-row">
        <span class="alert-icon">${iconSvg('alert', 14)}</span>
        <div>
          <div class="alert-text">${escapeHtml(a.detail)}</div>
          <div class="alert-time">${timeAgo(a.ts)}</div>
        </div>
      </div>`
    )
    .join('');
}

/* ---------- tooltip + toasts ---------- */

function showTooltip(node, screenPos) {
  const el = $('tooltip');
  if (!node || !screenPos) return el.classList.remove('is-visible');
  const region = $('map-region').getBoundingClientRect();
  const canvasRect = $('map').getBoundingClientRect();
  $('tooltip-name').textContent = node.name;
  $('tooltip-sub').textContent = [node.ip, node.rtt == null ? 'no reply' : `${node.rtt.toFixed(1)} ms`]
    .filter(Boolean)
    .join('  ·  ');
  el.style.left = `${screenPos[0] + (canvasRect.left - region.left)}px`;
  el.style.top = `${screenPos[1] + (canvasRect.top - region.top) - node.r}px`;
  el.classList.add('is-visible');
}

function toast(kind, message) {
  const stack = $('toast-stack');
  const el = document.createElement('div');
  const icon =
    kind === 'leave' ? 'arrow-out'
    : kind === 'new' || kind === 'risk' ? 'alert'
    : kind === 'info' ? 'download'
    : 'arrow-in';
  el.className = `toast toast-${kind}`;
  el.innerHTML = `${iconSvg(icon, 16)}<span>${escapeHtml(message)}</span>`;
  stack.appendChild(el);
  setTimeout(() => {
    el.classList.add('is-out');
    setTimeout(() => el.remove(), 240);
  }, 3600);
}

function hintOnce(text) {
  const el = $('map-hint');
  el.textContent = text;
  el.classList.add('is-visible');
  setTimeout(() => el.classList.remove('is-visible'), 5200);
}

/* ---------- theme ---------- */

function applyTheme(choice) {
  const dark =
    choice === 'dark' ||
    (choice === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  $('btn-theme').innerHTML = iconSvg(dark ? 'sun' : 'moon');
  $('set-theme')?.querySelectorAll('button').forEach((b) => {
    b.classList.toggle('is-active', b.dataset.themeChoice === choice);
  });
  NetworkMap.themeChanged();
}

function applyPortScanSetting(value) {
  $('set-portscan')?.querySelectorAll('button').forEach((b) => {
    b.classList.toggle('is-active', b.dataset.portscan === value);
  });
}

function applySegmented(id, attribute, value) {
  $(id)?.querySelectorAll('button').forEach((b) => {
    b.classList.toggle('is-active', b.dataset[attribute] === value);
  });
}

function applyAlertSettings() {
  const delivery = state.settings.alert_delivery || 'in_app';
  applySegmented('set-delivery', 'delivery', delivery);
  $('set-toast-port').checked = state.settings.toast_new_port === 'on';
  $('set-toast-risk').checked = state.settings.toast_risk_raised === 'on';

  // The extra notification types only exist as Windows toasts, so the row is
  // meaningless unless a mode that produces toasts is selected.
  const toasts = delivery === 'toast' || delivery === 'both';
  $('row-toast-extras').classList.toggle('is-disabled', !toasts);
  $('set-delivery-hint').textContent = state.toastAvailable
    ? 'Where changes on the network are announced'
    : 'Windows notifications are unavailable on this system';
  $('set-delivery').querySelectorAll('[data-delivery="toast"], [data-delivery="both"]').forEach((b) => {
    b.disabled = !state.toastAvailable;
  });
}

/* ---------- events ---------- */

function bindUI() {
  /* Native window chrome owns movement and resizing in the packaged app. */
  $('win-min').addEventListener('click', () => api()?.window_minimize());
  $('win-close').addEventListener('click', () => api()?.window_close());
  $('win-max').addEventListener('click', async () => {
    const maximized = await api()?.window_toggle_maximize();
    $('win-max').innerHTML = iconSvg(maximized ? 'win-restore' : 'win-max');
  });

  $('search').addEventListener('input', (e) => {
    state.query = e.target.value;
    renderList();
  });

  $('filters').addEventListener('click', (e) => {
    const chip = e.target.closest('.chip');
    if (!chip) return;
    state.filter = chip.dataset.filter;
    $('filters').querySelectorAll('.chip').forEach((c) => c.classList.toggle('is-active', c === chip));
    renderList();
  });

  $('btn-rescan').addEventListener('click', () => api()?.rescan());
  $('btn-fit').addEventListener('click', () => NetworkMap.fit());
  $('btn-zoom-in').addEventListener('click', () => NetworkMap.zoomBy(1.25));
  $('btn-zoom-out').addEventListener('click', () => NetworkMap.zoomBy(1 / 1.25));
  $('btn-close-detail').addEventListener('click', () => selectDevice(null));
  $('btn-locate').addEventListener('click', () => state.selected && NetworkMap.centerOn(state.selected));

  $('btn-scan-ports').addEventListener('click', async () => {
    const mac = state.selected;
    if (!mac) return;
    const btn = $('btn-scan-ports');
    btn.disabled = true;
    btn.textContent = 'Scanning…';
    $('detail-ports').innerHTML = '<div class="ports-note">Probing ports…</div>';
    try {
      const result = await api().scan_ports(mac);
      if (state.selected !== mac) return;
      if (result && result.ok) renderPorts(result.ports || []);
      else $('detail-ports').innerHTML = `<div class="ports-note">${escapeHtml((result && result.error) || 'Scan failed.')}</div>`;
    } catch (err) {
      $('detail-ports').innerHTML = '<div class="ports-note">Scan failed.</div>';
    } finally {
      btn.disabled = false;
      btn.textContent = 'Scan now';
    }
  });

  $('btn-theme').addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    state.settings.theme = next;
    applyTheme(next);
    api()?.set_setting('theme', next);
  });

  $('btn-alerts').addEventListener('click', () => {
    const drawer = $('alerts-drawer');
    const open = drawer.classList.toggle('is-open');
    $('btn-alerts').classList.toggle('is-active', open);
    if (open && state.unread) {
      api()?.acknowledge_alerts();
      state.unread = 0;
      renderStatus();
    }
  });

  /* Export menu */
  $('btn-export').addEventListener('click', (e) => {
    e.stopPropagation();
    const open = $('export-menu').classList.toggle('is-open');
    $('btn-export').classList.toggle('is-active', open);
  });

  $('export-menu').addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-export]');
    if (!btn) return;
    closeExportMenu();
    const kind = btn.dataset.export;
    // The dialog and the file write both happen on the Python side, so the
    // button has to say it is busy — otherwise a slow save looks like a click
    // that did nothing.
    toast('info', kind === 'png' ? 'Capturing the map…' : 'Building the report…');
    const result = await api()?.export_snapshot(kind, NetworkMap.snapshot());
    if (!result || result.cancelled) return;
    if (result.ok) {
      toast('info', `Saved to ${result.path.split(/[\\/]/).pop()}`);
    } else {
      toast('risk', result.error || 'Export failed.');
    }
  });

  function closeExportMenu() {
    $('export-menu').classList.remove('is-open');
    $('btn-export').classList.remove('is-active');
  }
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.menu-wrap')) closeExportMenu();
  });

  $('btn-clear-alerts').addEventListener('click', () => {
    api()?.acknowledge_alerts();
    state.unread = 0;
    renderStatus();
  });

  $('iface-select').addEventListener('change', (e) => {
    api()?.select_interface(e.target.value);
    state.devices = [];
    renderList();
    $('map-empty').classList.remove('is-hidden');
  });

  $('detail-rename').addEventListener('change', (e) => {
    if (!state.selected) return;
    api()?.rename_device(state.selected, e.target.value.trim());
  });
  $('detail-rename').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') e.target.blur();
  });

  $('detail-trust').addEventListener('click', (e) => {
    const btn = e.target.closest('button');
    if (!btn || !state.selected) return;
    api()?.set_trust(state.selected, btn.dataset.trust);
    const device = state.byMac.get(state.selected);
    if (device) {
      device.trust_status = btn.dataset.trust;
      renderDetail(device);
      NetworkMap.setDevices(state.devices, state.gateway);
    }
  });

  /* Settings modal */
  $('btn-settings').addEventListener('click', async () => {
    await loadInitialFromBackend();
    const interval = Number(state.settings.scan_interval || 30);
    $('set-interval').value = interval;
    $('set-interval-value').textContent = `${interval}s`;
    applyTheme(state.settings.theme || 'system');
    applyPortScanSetting(state.settings.port_scan || 'on');
    applySegmented('set-tray', 'tray', state.settings.close_to_tray || 'on');
    applyAlertSettings();
    $('settings-modal').classList.add('is-open');
  });

  $('set-delivery').addEventListener('click', (e) => {
    const btn = e.target.closest('button');
    if (!btn || btn.disabled) return;
    state.settings.alert_delivery = btn.dataset.delivery;
    applyAlertSettings();
    api()?.set_setting('alert_delivery', btn.dataset.delivery);
  });

  $('set-tray').addEventListener('click', (e) => {
    const btn = e.target.closest('button');
    if (!btn) return;
    state.settings.close_to_tray = btn.dataset.tray;
    applySegmented('set-tray', 'tray', btn.dataset.tray);
    api()?.set_setting('close_to_tray', btn.dataset.tray);
  });

  for (const [id, key] of [['set-toast-port', 'toast_new_port'], ['set-toast-risk', 'toast_risk_raised']]) {
    $(id).addEventListener('change', (e) => {
      const value = e.target.checked ? 'on' : 'off';
      state.settings[key] = value;
      api()?.set_setting(key, value);
    });
  }

  $('btn-wake').addEventListener('click', async () => {
    const mac = state.selected;
    if (!mac) return;
    const btn = $('btn-wake');
    btn.disabled = true;
    try {
      const result = await api().wake(mac);
      // There is no reply to a magic packet, so the wording has to stop at
      // "sent". Claiming the device woke would be inventing a confirmation the
      // protocol never provides.
      toast(result && result.ok ? 'join' : 'new',
        result && result.ok
          ? 'Magic packet sent. The device wakes only if Wake-on-LAN is enabled in its firmware.'
          : `Could not send: ${(result && result.error) || 'unknown error'}`);
    } catch (err) {
      toast('new', 'Wake-on-LAN failed.');
    } finally {
      btn.disabled = false;
    }
  });

  $('set-portscan').addEventListener('click', (e) => {
    const btn = e.target.closest('button');
    if (!btn) return;
    state.settings.port_scan = btn.dataset.portscan;
    applyPortScanSetting(btn.dataset.portscan);
    api()?.set_setting('port_scan', btn.dataset.portscan);
  });
  $('btn-close-settings').addEventListener('click', () => $('settings-modal').classList.remove('is-open'));
  $('settings-modal').addEventListener('click', (e) => {
    if (e.target === $('settings-modal')) $('settings-modal').classList.remove('is-open');
  });

  $('set-interval').addEventListener('input', (e) => {
    $('set-interval-value').textContent = `${e.target.value}s`;
  });
  $('set-interval').addEventListener('change', (e) => {
    state.settings.scan_interval = e.target.value;
    api()?.set_setting('scan_interval', e.target.value);
  });

  $('set-theme').addEventListener('click', (e) => {
    const btn = e.target.closest('button');
    if (!btn) return;
    state.settings.theme = btn.dataset.themeChoice;
    applyTheme(btn.dataset.themeChoice);
    api()?.set_setting('theme', btn.dataset.themeChoice);
  });

  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if ((state.settings.theme || 'system') === 'system') applyTheme('system');
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      // Dismiss the shallowest thing that is open, so one Escape does not
      // close a menu and the detail panel behind it in the same keystroke.
      if ($('export-menu').classList.contains('is-open')) return closeExportMenu();
      $('settings-modal').classList.remove('is-open');
      $('alerts-drawer').classList.remove('is-open');
      selectDevice(null);
    }
    if (e.key === '/' && document.activeElement !== $('search')) {
      e.preventDefault();
      $('search').focus();
    }
    if (e.key === 'r' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      api()?.rescan();
    }
  });

  // Keep "scanned N ago" honest without waiting for the next scan.
  setInterval(renderStatus, 15000);
}

/* ---------- helpers ---------- */

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])
  );
}

const capitalize = (s) => String(s || '').charAt(0).toUpperCase() + String(s || '').slice(1);

function timeAgo(ts) {
  if (!ts) return 'never';
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (secs < 10) return 'just now';
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

function formatTime(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return sameDay ? `Today ${time}` : `${d.toLocaleDateString()} ${time}`;
}
