/* Icon set — Lucide-style 24x24 line icons, defined once as primitives so the
   DOM (SVG markup) and the map (canvas Path2D) render identical shapes. */

const ICONS = {
  radar: [
    { arc: [12, 12, 9, -0.6, 3.7] },
    { arc: [12, 12, 5, -0.3, 4.0] },
    { line: [12, 12, 19, 6] },
    { dot: [12, 12, 1.4] },
  ],
  'chevron-down': [{ d: 'M6 9.5 L12 15.5 L18 9.5' }],
  refresh: [
    { arc: [12, 12, 8, -1.2, 3.6] },
    { d: 'M20 4 L20 10 L14 10' },
  ],
  bell: [
    { d: 'M18 15.5 A6 6 0 0 0 18 9.5 A6 6 0 0 0 6 9.5 A6 6 0 0 0 6 15.5 Z' },
    { d: 'M4.5 16.5 L19.5 16.5' },
    { d: 'M10 19.5 A2.2 2.2 0 0 0 14 19.5' },
  ],
  moon: [{ d: 'M20 14.5 A8.5 8.5 0 1 1 10 4 A6.6 6.6 0 0 0 20 14.5 Z' }],
  sun: [
    { dot: [12, 12, 4], hollow: true },
    { line: [12, 1.8, 12, 4.2] }, { line: [12, 19.8, 12, 22.2] },
    { line: [1.8, 12, 4.2, 12] }, { line: [19.8, 12, 22.2, 12] },
    { line: [4.9, 4.9, 6.6, 6.6] }, { line: [17.4, 17.4, 19.1, 19.1] },
    { line: [19.1, 4.9, 17.4, 6.6] }, { line: [6.6, 17.4, 4.9, 19.1] },
  ],
  settings: [
    { line: [4, 7, 20, 7] }, { line: [4, 12, 20, 12] }, { line: [4, 17, 20, 17] },
    { dot: [9, 7, 2], hollow: true, solidFill: true },
    { dot: [15, 12, 2], hollow: true, solidFill: true },
    { dot: [9, 17, 2], hollow: true, solidFill: true },
  ],
  search: [{ dot: [10.5, 10.5, 6.5], hollow: true }, { line: [15.2, 15.2, 20.5, 20.5] }],
  x: [{ line: [6, 6, 18, 18] }, { line: [18, 6, 6, 18] }],
  plus: [{ line: [12, 5.5, 12, 18.5] }, { line: [5.5, 12, 18.5, 12] }],
  minus: [{ line: [5.5, 12, 18.5, 12] }],
  maximize: [
    { d: 'M4 9 L4 4 L9 4' }, { d: 'M15 4 L20 4 L20 9' },
    { d: 'M20 15 L20 20 L15 20' }, { d: 'M9 20 L4 20 L4 15' },
  ],
  check: [{ d: 'M5 12.5 L10 17.5 L19 6.5' }],
  download: [
    { d: 'M12 3.5 L12 14.5 M7.5 10.5 L12 15 L16.5 10.5' },
    { d: 'M4 16.5 L4 20 L20 20 L20 16.5' },
  ],
  image: [
    { rect: [3, 4.5, 18, 15, 2.5] },
    { dot: [8.5, 10, 1.6], hollow: true },
    { d: 'M4 17 L9.5 12 L14 15.5 L17 13 L20 15.5' },
  ],
  file: [
    { d: 'M6 3.5 L14 3.5 L19 8.5 L19 20.5 L6 20.5 Z' },
    { d: 'M14 3.5 L14 8.5 L19 8.5' },
    { line: [9, 13, 16, 13] }, { line: [9, 16.5, 14, 16.5] },
  ],
  'arrow-in': [{ dot: [12, 12, 8.5], hollow: true }, { d: 'M12 8 L12 16 M8.5 12.5 L12 16 L15.5 12.5' }],
  'arrow-out': [{ dot: [12, 12, 8.5], hollow: true }, { d: 'M12 16 L12 8 M8.5 11.5 L12 8 L15.5 11.5' }],
  alert: [
    { d: 'M12 4.2 L21.2 19.4 L2.8 19.4 Z' },
    { line: [12, 9.5, 12, 13.5] }, { dot: [12, 16.4, 0.9], fill: true },
  ],

  /* Window controls (Windows 11 style: thin, square) */
  'win-min': [{ line: [3, 12, 21, 12] }],
  'win-max': [{ rect: [3.5, 3.5, 17, 17, 2] }],
  'win-restore': [{ rect: [3.5, 7, 13.5, 13.5, 2] }, { d: 'M7.5 7 L7.5 3.5 L20.5 3.5 L20.5 16.5 L17 16.5' }],
  'win-close': [{ line: [4, 4, 20, 20] }, { line: [20, 4, 4, 20] }],

  /* Device types */
  router: [
    { rect: [2.5, 12.5, 19, 8, 2.5] },
    { dot: [7, 16.5, 0.9], fill: true }, { dot: [10.5, 16.5, 0.9], fill: true },
    { line: [16, 16.5, 19, 16.5] },
    { d: 'M7.5 9.5 A6.4 6.4 0 0 1 16.5 9.5' },
    { d: 'M10 7 A3 3 0 0 1 14 7' },
  ],
  phone: [{ rect: [6.5, 2.5, 11, 19, 2.6] }, { line: [10.5, 18.6, 13.5, 18.6] }],
  laptop: [{ rect: [4, 5, 16, 11, 2] }, { line: [2, 19, 22, 19] }],
  desktop: [{ rect: [3, 4, 18, 12.5, 2] }, { line: [12, 16.5, 12, 20] }, { line: [8, 20, 16, 20] }],
  tv: [{ rect: [2.5, 7, 19, 13, 2.5] }, { d: 'M8 3.5 L12 7 L16 3.5' }],
  iot: [
    { rect: [7, 7, 10, 10, 2] },
    { line: [10, 3.5, 10, 7] }, { line: [14, 3.5, 14, 7] },
    { line: [10, 17, 10, 20.5] }, { line: [14, 17, 14, 20.5] },
    { line: [3.5, 10, 7, 10] }, { line: [3.5, 14, 7, 14] },
    { line: [17, 10, 20.5, 10] }, { line: [17, 14, 20.5, 14] },
  ],
  camera: [
    { d: 'M3 8.5 L16.5 5 L18 10.5 L4.5 14 Z' },
    { line: [7, 13.2, 8.5, 18.5] },
    { d: 'M4 20.5 L12 20.5' },
    { line: [18.4, 8, 21, 7.3] },
  ],
  printer: [
    { d: 'M7 8.5 L7 3.5 L17 3.5 L17 8.5' },
    { rect: [3, 8.5, 18, 7.5, 2] },
    { rect: [7, 14, 10, 6.5, 1.2] },
  ],
  unknown: [{ dot: [12, 12, 8.5], hollow: true }, { d: 'M9.6 9.6 A2.5 2.5 0 1 1 12 13 L12 14.4' }, { dot: [12, 17.3, 0.9], fill: true }],
};

const DEVICE_ICON = {
  router: 'router', phone: 'phone', laptop: 'laptop', desktop: 'desktop',
  tv: 'tv', iot: 'iot', camera: 'camera', printer: 'printer', unknown: 'unknown',
};

function iconSvg(name, size = 24) {
  const parts = ICONS[name] || ICONS.unknown;
  let inner = '';
  for (const p of parts) {
    if (p.d) inner += `<path d="${p.d}" />`;
    else if (p.line) inner += `<path d="M${p.line[0]} ${p.line[1]} L${p.line[2]} ${p.line[3]}" />`;
    else if (p.rect) {
      const [x, y, w, h, r] = p.rect;
      inner += `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r || 0}" />`;
    } else if (p.dot) {
      const [cx, cy, r] = p.dot;
      const fill = p.fill || p.solidFill ? 'currentColor' : 'none';
      inner += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${fill}" />`;
    } else if (p.arc) {
      inner += `<path d="${arcPath(...p.arc)}" />`;
    }
  }
  return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none"
    stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;
}

function arcPath(cx, cy, r, a0, a1) {
  const x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0);
  const x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
  const large = Math.abs(a1 - a0) > Math.PI ? 1 : 0;
  return `M${x0.toFixed(2)} ${y0.toFixed(2)} A${r} ${r} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

/* Canvas: cached Path2D per icon, drawn in the 24x24 icon space. */
const _pathCache = new Map();

function iconPath(name) {
  if (_pathCache.has(name)) return _pathCache.get(name);
  const parts = ICONS[name] || ICONS.unknown;
  const stroke = new Path2D();
  const fill = new Path2D();
  for (const p of parts) {
    if (p.d) stroke.addPath(new Path2D(p.d));
    else if (p.line) stroke.addPath(new Path2D(`M${p.line[0]} ${p.line[1]} L${p.line[2]} ${p.line[3]}`));
    else if (p.rect) {
      const [x, y, w, h, r] = p.rect;
      const sub = new Path2D();
      sub.roundRect(x, y, w, h, r || 0);
      stroke.addPath(sub);
    } else if (p.dot) {
      const [cx, cy, r] = p.dot;
      const sub = new Path2D();
      sub.arc(cx, cy, r, 0, Math.PI * 2);
      (p.fill || p.solidFill ? fill : stroke).addPath(sub);
    } else if (p.arc) {
      stroke.addPath(new Path2D(arcPath(...p.arc)));
    }
  }
  const entry = { stroke, fill };
  _pathCache.set(name, entry);
  return entry;
}

/* Replace every [data-icon] placeholder in the DOM. */
function hydrateIcons(root = document) {
  root.querySelectorAll('[data-icon]').forEach((el) => {
    el.innerHTML = iconSvg(el.dataset.icon);
  });
}
