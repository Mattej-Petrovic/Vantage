/* The live map: d3-force physics, canvas rendering.

   Node pulse frequency is driven by measured ICMP round-trip time — a fast
   responder pulses often, a slow one rarely. It is never a stand-in for
   bandwidth, which we cannot see on a switched network. */

const NetworkMap = (() => {
  const PULSE_LIFE = 1800;
  const MIN_PULSE_GAP = 950;
  const MAX_PULSE_GAP = 3600;
  const SWEEP_PERIOD = 7000;   // radar sweep revolution, ms

  let canvas, ctx, dpr = 1, cw = 0, ch = 0;
  let sim = null;
  let nodes = [];
  let byMac = new Map();
  let colors = {};
  let cam = { x: 0, y: 0, k: 1 };
  let hovered = null;
  let selected = null;
  let dragging = null;
  let panning = null;
  let reducedMotion = false;
  let onSelect = () => {};
  let onHover = () => {};
  let running = false;
  let resizeObserver = null;

  const NODE_R = { router: 34, normal: 21 };

  function readColors() {
    const s = getComputedStyle(document.documentElement);
    const v = (name) => s.getPropertyValue(name).trim();
    colors = {
      wash: v('--map-wash'),
      grid: v('--map-grid'),
      ring: v('--map-ring'),
      edge: v('--map-edge'),
      node: v('--map-node'),
      node2: v('--map-node-2'),
      nodeBorder: v('--map-node-border'),
      label: v('--map-label'),
      offline: v('--map-offline'),
      accent: v('--accent'),
      accent2: v('--accent-2'),
      ok: v('--ok'),
      watch: v('--watch'),
      risk: v('--risk'),
      text: v('--text'),
      secondary: v('--text-2'),
      surface: v('--surface'),
      isDark: document.documentElement.dataset.theme === 'dark',
    };
  }

  function init(canvasEl, handlers = {}) {
    canvas = canvasEl;
    ctx = canvas.getContext('2d');
    onSelect = handlers.onSelect || onSelect;
    onHover = handlers.onHover || onHover;
    reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    readColors();
    resize();
    window.addEventListener('resize', resize);
    resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(canvas.parentElement || canvas);
    setTimeout(resize, 0);
    setTimeout(resize, 250);
    setTimeout(resize, 1000);

    sim = d3
      .forceSimulation([])
      .force('charge', d3.forceManyBody().strength((d) => (d.isGateway ? -1100 : -380)))
      .force('link', d3.forceLink([]).id((d) => d.mac).distance(linkDistance).strength(0.5))
      .force('collide', d3.forceCollide(collideRadius))
      .force('x', d3.forceX(0).strength(0.03))
      .force('y', d3.forceY(0).strength(0.03))
      .alphaDecay(0.018)
      .velocityDecay(0.45);

    sim.stop(); // ticked manually inside the render loop

    bindPointer();
    running = true;
    requestAnimationFrame(frame);
  }

  /* Reserve room for the label chip, not just the disc, so names never
     stack on top of each other. */
  function collideRadius(d) {
    return Math.max(d.r + 34, (d.labelHalf || 0) + 16);
  }

  function linkDistance(link) {
    // Slow responders sit further out — distance carries information too.
    const rtt = link.target.rtt;
    const base = 165;
    if (rtt == null) return base + 60;
    return base + Math.min(95, rtt * 6);
  }

  function resize() {
    dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    cw = rect.width;
    ch = rect.height;
    canvas.width = Math.max(1, Math.round(cw * dpr));
    canvas.height = Math.max(1, Math.round(ch * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function themeChanged() { readColors(); }

  /* ---------- data ---------- */

  function setDevices(devices, gatewayIp) {
    resize();
    const now = performance.now();
    const seen = new Set();

    for (const d of devices) {
      seen.add(d.mac);
      let node = byMac.get(d.mac);
      const isGateway = d.is_gateway || (gatewayIp && d.ip === gatewayIp);

      if (!node) {
        const angle = Math.random() * Math.PI * 2;
        const dist = isGateway ? 0 : 260 + Math.random() * 90;
        node = {
          mac: d.mac,
          x: Math.cos(angle) * dist,
          y: Math.sin(angle) * dist,
          appear: 0,
          hoverAmt: 0,
          pulses: [],
          nextPulse: now + Math.random() * 900,
          joinFlash: d.online ? now : 0,
          r: 0,
        };
        byMac.set(d.mac, node);
      }

      Object.assign(node, {
        name: d.name,
        ip: d.ip,
        type: d.device_type || 'unknown',
        vendor: d.vendor,
        online: !!d.online,
        rtt: d.rtt_ms,
        trust: d.trust_status,
        risk: d.risk_band,
        isGateway: !!isGateway,
        isLocal: !!d.is_local,
        isNew: !!d.is_new_today,
        r: isGateway ? NODE_R.router : NODE_R.normal,
      });
      // ~6.1px per character at the 12px label size, plus chip padding.
      node.labelHalf = (truncate(node.name || node.ip || node.mac, 20).length * 6.1) / 2 + 8;

      if (isGateway) { node.fx = 0; node.fy = 0; }
    }

    for (const mac of [...byMac.keys()]) {
      if (!seen.has(mac)) byMac.delete(mac);
    }

    nodes = [...byMac.values()];
    const gateway = nodes.find((n) => n.isGateway);
    const links = gateway
      ? nodes.filter((n) => n !== gateway).map((n) => ({ source: gateway, target: n }))
      : [];

    sim.nodes(nodes);
    sim.force('link').links(links);
    sim.alpha(0.55);
  }

  function flashJoin(mac) {
    const node = byMac.get(mac);
    if (node) node.joinFlash = performance.now();
  }

  function select(mac) { selected = mac; }

  function centerOn(mac) {
    const node = byMac.get(mac);
    if (node) animateCamera({ x: node.x, y: node.y, k: Math.max(cam.k, 1.2) });
  }

  /* ---------- camera ---------- */

  let camAnim = null;

  function animateCamera(target, duration = 480) {
    camAnim = { from: { ...cam }, to: target, start: performance.now(), duration };
  }

  function stepCamera(now) {
    if (!camAnim) return;
    const t = Math.min(1, (now - camAnim.start) / camAnim.duration);
    const e = 1 - Math.pow(1 - t, 3);
    cam.x = camAnim.from.x + (camAnim.to.x - camAnim.from.x) * e;
    cam.y = camAnim.from.y + (camAnim.to.y - camAnim.from.y) * e;
    cam.k = camAnim.from.k + (camAnim.to.k - camAnim.from.k) * e;
    if (t >= 1) camAnim = null;
  }

  function fit() {
    if (!nodes.length) return animateCamera({ x: 0, y: 0, k: 1 });
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const n of nodes) {
      minX = Math.min(minX, n.x - n.r); maxX = Math.max(maxX, n.x + n.r);
      minY = Math.min(minY, n.y - n.r); maxY = Math.max(maxY, n.y + n.r);
    }
    const pad = 110;
    const k = Math.min(
      cw / Math.max(1, maxX - minX + pad * 2),
      ch / Math.max(1, maxY - minY + pad * 2),
      1.4
    );
    animateCamera({ x: (minX + maxX) / 2, y: (minY + maxY) / 2, k: Math.max(0.25, k) });
  }

  function zoomBy(factor) {
    animateCamera({ x: cam.x, y: cam.y, k: clamp(cam.k * factor, 0.25, 3) }, 240);
  }

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const toScreen = (x, y) => [cw / 2 + (x - cam.x) * cam.k, ch / 2 + (y - cam.y) * cam.k];
  const toWorld = (sx, sy) => [cam.x + (sx - cw / 2) / cam.k, cam.y + (sy - ch / 2) / cam.k];

  /* ---------- interaction ---------- */

  function nodeAt(sx, sy) {
    const [wx, wy] = toWorld(sx, sy);
    let best = null, bestDist = Infinity;
    for (const n of nodes) {
      const d = Math.hypot(n.x - wx, n.y - wy);
      if (d < n.r + 8 && d < bestDist) { best = n; bestDist = d; }
    }
    return best;
  }

  function bindPointer() {
    canvas.addEventListener('pointerdown', (e) => {
      canvas.setPointerCapture(e.pointerId);
      const node = nodeAt(e.offsetX, e.offsetY);
      if (node) {
        dragging = { node, moved: false };
        node.fx = node.x;
        node.fy = node.y;
        sim.alphaTarget(0.25);
      } else {
        panning = { sx: e.offsetX, sy: e.offsetY, camX: cam.x, camY: cam.y, moved: false };
        canvas.classList.add('is-dragging');
      }
    });

    canvas.addEventListener('pointermove', (e) => {
      if (dragging) {
        const [wx, wy] = toWorld(e.offsetX, e.offsetY);
        dragging.node.fx = wx;
        dragging.node.fy = wy;
        dragging.moved = true;
        return;
      }
      if (panning) {
        cam.x = panning.camX - (e.offsetX - panning.sx) / cam.k;
        cam.y = panning.camY - (e.offsetY - panning.sy) / cam.k;
        panning.moved = true;
        camAnim = null;
        return;
      }
      const node = nodeAt(e.offsetX, e.offsetY);
      canvas.classList.toggle('is-over-node', !!node);
      hovered = node;
      onHover(node, node ? toScreen(node.x, node.y) : null);
    });

    const endPointer = () => {
      if (dragging) {
        const { node, moved } = dragging;
        if (!moved) {
          if (!node.isGateway) { node.fx = null; node.fy = null; }
          onSelect(node.mac);
        }
        sim.alphaTarget(0);
        dragging = null;
      } else if (panning) {
        if (!panning.moved) onSelect(null);
        panning = null;
        canvas.classList.remove('is-dragging');
      }
    };
    canvas.addEventListener('pointerup', endPointer);
    canvas.addEventListener('pointercancel', endPointer);

    canvas.addEventListener('dblclick', (e) => {
      const node = nodeAt(e.offsetX, e.offsetY);
      if (node && !node.isGateway) { node.fx = null; node.fy = null; sim.alpha(0.3); }
    });

    canvas.addEventListener('wheel', (e) => {
      e.preventDefault();
      camAnim = null;
      const [wx, wy] = toWorld(e.offsetX, e.offsetY);
      const k = clamp(cam.k * (e.deltaY < 0 ? 1.12 : 1 / 1.12), 0.25, 3);
      cam.x = wx - (e.offsetX - cw / 2) / k;
      cam.y = wy - (e.offsetY - ch / 2) / k;
      cam.k = k;
    }, { passive: false });

    canvas.addEventListener('pointerleave', () => {
      hovered = null;
      onHover(null, null);
    });
  }

  /* ---------- render ---------- */

  function frame(now) {
    if (!running) return;
    stepCamera(now);
    sim.tick();
    draw(now);
    requestAnimationFrame(frame);
  }

  function draw(now) {
    ctx.clearRect(0, 0, cw, ch);

    const gateway = nodes.find((n) => n.isGateway);
    const origin = gateway ? toScreen(gateway.x, gateway.y) : [cw / 2, ch / 2];

    drawAmbient(origin);
    drawGrid();
    if (gateway) {
      drawRangeRings(origin, now);
      for (const n of nodes) if (n !== gateway) drawEdge(gateway, n, now);
    }

    for (const n of nodes) drawPulses(n, now);
    for (const n of nodes) if (n !== hovered && n.mac !== selected) drawNode(n, now);
    // Focused nodes paint last so their glow is never clipped by a neighbour.
    for (const n of nodes) if (n === hovered || n.mac === selected) drawNode(n, now);
  }

  /* A soft accent wash centred on the router: gives the scene a light source. */
  function drawAmbient([ox, oy]) {
    const radius = Math.max(cw, ch) * 0.62;
    const grad = ctx.createRadialGradient(ox, oy, 0, ox, oy, radius);
    grad.addColorStop(0, colors.wash);
    grad.addColorStop(1, 'transparent');
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, cw, ch);
  }

  function drawGrid() {
    const step = 40 * cam.k;
    if (step < 16) return;
    const ox = ((-cam.x * cam.k + cw / 2) % step + step) % step;
    const oy = ((-cam.y * cam.k + ch / 2) % step + step) % step;
    ctx.fillStyle = colors.grid;
    const r = cam.k > 1.4 ? 1.2 : 1;
    for (let x = ox; x < cw; x += step) {
      for (let y = oy; y < ch; y += step) {
        ctx.beginPath();
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }

  /* Concentric range rings + a slow sweep — the defender's radar metaphor. */
  function drawRangeRings([ox, oy], now) {
    ctx.save();
    ctx.strokeStyle = colors.ring;
    ctx.lineWidth = 1;
    for (let i = 1; i <= 4; i++) {
      ctx.beginPath();
      ctx.arc(ox, oy, i * 130 * cam.k, 0, Math.PI * 2);
      ctx.stroke();
    }

    if (!reducedMotion) {
      const angle = ((now % SWEEP_PERIOD) / SWEEP_PERIOD) * Math.PI * 2;
      const reach = 4 * 130 * cam.k;
      const grad = ctx.createConicGradient
        ? ctx.createConicGradient(angle - 0.55, ox, oy)
        : null;
      if (grad) {
        grad.addColorStop(0, 'transparent');
        grad.addColorStop(0.06, colors.wash);
        grad.addColorStop(0.09, 'transparent');
        ctx.globalAlpha = 0.55;
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(ox, oy, reach, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.restore();
  }

  function drawEdge(a, b, now) {
    const [x1, y1] = toScreen(a.x, a.y);
    const [x2, y2] = toScreen(b.x, b.y);
    const dx = x2 - x1, dy = y2 - y1;
    const len = Math.hypot(dx, dy) || 1;
    const bow = Math.min(30, len * 0.1) * cam.k;
    const mx = (x1 + x2) / 2 - (dy / len) * bow;
    const my = (y1 + y2) / 2 + (dx / len) * bow;

    const dimmed = selected && selected !== b.mac && selected !== a.mac;
    const focused = selected === b.mac || hovered === b;

    ctx.save();
    ctx.globalAlpha = (b.online ? 0.9 : 0.3) * b.appear * (dimmed ? 0.3 : 1);

    // Bright at the router, fading toward the device: direction without arrows.
    const grad = ctx.createLinearGradient(x1, y1, x2, y2);
    grad.addColorStop(0, focused ? colors.accent : colors.edge);
    grad.addColorStop(1, 'transparent');
    ctx.strokeStyle = grad;
    ctx.lineWidth = (focused ? 2 : b.online ? 1.3 : 0.9) * Math.min(1.3, cam.k);
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.quadraticCurveTo(mx, my, x2, y2);
    ctx.stroke();

    // A packet drifting toward the router: quiet proof the link is live.
    if (b.online && !reducedMotion && cam.k > 0.45 && !dimmed) {
      const period = 3000;
      const t = ((now + hash(b.mac) * 2000) % period) / period;
      const u = 1 - t;
      const px = u * u * x2 + 2 * u * t * mx + t * t * x1;
      const py = u * u * y2 + 2 * u * t * my + t * t * y1;
      const fade = Math.sin(Math.PI * t);
      ctx.globalAlpha = 0.7 * fade * b.appear;
      ctx.fillStyle = colors.accent;
      ctx.shadowColor = colors.accent;
      ctx.shadowBlur = 8;
      ctx.beginPath();
      ctx.arc(px, py, 2.1 * Math.min(1.3, cam.k), 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  function pulseGap(rtt) {
    if (rtt == null) return null; // no RTT measured -> no pulse, honestly
    return MIN_PULSE_GAP + clamp(rtt / 40, 0, 1) * (MAX_PULSE_GAP - MIN_PULSE_GAP);
  }

  function drawPulses(n, now) {
    if (n.online && !reducedMotion) {
      const gap = pulseGap(n.rtt);
      if (gap != null && now >= n.nextPulse) {
        n.pulses.push({ born: now });
        n.nextPulse = now + gap;
      }
    }
    n.pulses = n.pulses.filter((p) => now - p.born < PULSE_LIFE);
    if (!n.pulses.length) return;

    const [sx, sy] = toScreen(n.x, n.y);
    const color = nodeAccent(n);
    const dimmed = selected && selected !== n.mac;

    for (const p of n.pulses) {
      const t = (now - p.born) / PULSE_LIFE;
      const e = 1 - Math.pow(1 - t, 2.4);
      const radius = (n.r + 3 + e * (n.isGateway ? 96 : 58)) * cam.k;
      ctx.save();
      ctx.globalAlpha = Math.pow(1 - t, 1.7) * 0.5 * n.appear * (dimmed ? 0.3 : 1);
      ctx.strokeStyle = color;
      ctx.lineWidth = (2.2 * (1 - t) + 0.3) * Math.min(1.4, cam.k);
      ctx.beginPath();
      ctx.arc(sx, sy, radius, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }
  }

  function nodeAccent(n) {
    if (!n.online) return colors.offline;
    if (n.trust === 'blocked') return colors.risk;
    if (n.isGateway) return colors.accent;
    if (n.trust === 'trusted') return colors.ok;
    if (n.isNew) return colors.watch; // amber means "showed up recently", nothing more
    return colors.nodeBorder;
  }

  function drawNode(n, now) {
    n.appear = Math.min(1, n.appear + 0.05);
    const isSelected = selected === n.mac;
    const isHovered = hovered === n;
    const target = isHovered || isSelected ? 1 : 0;
    n.hoverAmt += (target - n.hoverAmt) * 0.18;

    const grow = easeOutBack(n.appear) * (1 + n.hoverAmt * 0.07);
    const [sx, sy] = toScreen(n.x, n.y);
    const r = n.r * cam.k * grow;
    if (sx < -160 || sx > cw + 160 || sy < -160 || sy > ch + 160) return;

    const accent = nodeAccent(n);
    const dimmed = selected && !isSelected;

    ctx.save();
    ctx.globalAlpha = dimmed ? 0.4 : 1;

    // Join flash: one amber ring that expands and dissolves.
    const flashAge = now - (n.joinFlash || 0);
    if (flashAge < 2000 && !reducedMotion) {
      const t = flashAge / 2000;
      ctx.save();
      ctx.globalAlpha = Math.pow(1 - t, 2) * 0.6;
      ctx.strokeStyle = colors.watch;
      ctx.lineWidth = 2.5 * (1 - t) + 0.5;
      ctx.beginPath();
      ctx.arc(sx, sy, r + 6 + easeOutCubic(t) * 52, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }

    // Ambient glow — stronger for the router, on hover, and on selection.
    if (n.online) {
      // Light surfaces need far less bloom before it reads as smudge.
      const glowScale = colors.isDark ? 1 : 0.45;
      const glow = ((n.isGateway ? 0.5 : 0.22) + n.hoverAmt * 0.4) * glowScale;
      const halo = ctx.createRadialGradient(sx, sy, r * 0.6, sx, sy, r * 2.5);
      halo.addColorStop(0, withAlpha(accent, glow * 0.55));
      halo.addColorStop(1, 'transparent');
      ctx.fillStyle = halo;
      ctx.beginPath();
      ctx.arc(sx, sy, r * 2.5, 0, Math.PI * 2);
      ctx.fill();
    }

    if (isSelected) {
      ctx.strokeStyle = withAlpha(colors.accent, 0.45);
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 5]);
      ctx.beginPath();
      ctx.arc(sx, sy, r + 11, 0, Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // Posture is a ring around the node, never the node's own colour.
    // The fill already carries role and recency; overloading it with risk would
    // make an amber node ambiguous between "new here" and "worth a look".
    if (n.online && (n.risk === 'risk' || n.risk === 'watch')) {
      ctx.strokeStyle = n.risk === 'risk' ? colors.risk : colors.watch;
      ctx.lineWidth = (n.risk === 'risk' ? 2 : 1.5) * Math.min(1.4, cam.k);
      ctx.beginPath();
      ctx.arc(sx, sy, r + 4.5, 0, Math.PI * 2);
      ctx.stroke();
    }

    // Body: a subtle top-lit gradient reads as a physical disc, not a flat dot.
    const body = ctx.createLinearGradient(sx, sy - r, sx, sy + r);
    if (n.isGateway && n.online) {
      body.addColorStop(0, colors.accent);
      body.addColorStop(1, colors.accent2);
    } else {
      body.addColorStop(0, colors.node);
      body.addColorStop(1, colors.node2);
    }

    ctx.beginPath();
    ctx.arc(sx, sy, r, 0, Math.PI * 2);
    ctx.fillStyle = body;
    ctx.shadowColor = colors.isDark ? 'rgba(0, 0, 0, 0.6)' : 'rgba(16, 24, 56, 0.22)';
    ctx.shadowBlur = (10 + n.hoverAmt * 12) * cam.k;
    ctx.shadowOffsetY = 3 * cam.k;
    ctx.fill();
    ctx.shadowColor = 'transparent';
    ctx.shadowBlur = 0;
    ctx.shadowOffsetY = 0;

    // Ring
    if (!(n.isGateway && n.online)) {
      ctx.lineWidth = (isSelected || n.isNew ? 2.2 : 1.6) * Math.min(1.4, cam.k);
      ctx.strokeStyle = n.online ? accent : colors.offline;
      ctx.globalAlpha = (dimmed ? 0.4 : 1) * (n.online ? 0.9 : 0.5);
      ctx.stroke();
      ctx.globalAlpha = dimmed ? 0.4 : 1;
    }

    // Icon
    const iconSize = (n.isGateway ? 32 : 21) * cam.k * grow;
    if (iconSize > 8) {
      const paths = iconPath(DEVICE_ICON[n.type] || 'unknown');
      const s = iconSize / 24;
      const tint = n.isGateway && n.online ? '#FFFFFF' : n.online ? colors.text : colors.offline;
      ctx.save();
      ctx.translate(sx - iconSize / 2, sy - iconSize / 2);
      ctx.scale(s, s);
      ctx.globalAlpha = (dimmed ? 0.4 : 1) * (n.online ? 0.95 : 0.5);
      ctx.strokeStyle = tint;
      ctx.fillStyle = tint;
      ctx.lineWidth = 1.85;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.stroke(paths.stroke);
      ctx.fill(paths.fill);
      ctx.restore();
    }

    // Label on a chip, so text stays readable over rings, edges and glow.
    if (cam.k > 0.4) {
      const label = truncate(n.name || n.ip || n.mac, 20);
      const fontSize = Math.round(12 * Math.min(1.2, Math.max(0.85, cam.k)));
      ctx.font = `${n.isGateway ? 640 : 540} ${fontSize}px Inter, system-ui, sans-serif`;
      const w = ctx.measureText(label).width;
      const padX = 7, chipH = fontSize + 10;
      const cx = sx - w / 2 - padX;
      const cy = sy + r + 9;

      ctx.globalAlpha = (dimmed ? 0.35 : 1) * (n.online ? 1 : 0.65);
      ctx.beginPath();
      ctx.roundRect(cx, cy, w + padX * 2, chipH, chipH / 2);
      ctx.fillStyle = withAlpha(colors.surface, colors.isDark ? 0.72 : 0.82);
      ctx.fill();
      if (isSelected || isHovered) {
        ctx.strokeStyle = withAlpha(accent, 0.5);
        ctx.lineWidth = 1;
        ctx.stroke();
      }

      ctx.fillStyle = isSelected || isHovered ? colors.text : colors.label;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(label, sx, cy + chipH / 2 + 0.5);

      if ((isHovered || isSelected) && n.ip && cam.k > 0.65) {
        ctx.font = `500 ${Math.round(10.5 * Math.min(1.2, cam.k))}px 'JetBrains Mono', ui-monospace, monospace`;
        ctx.fillStyle = colors.secondary;
        ctx.globalAlpha = 0.9;
        ctx.textBaseline = 'top';
        ctx.fillText(n.ip, sx, cy + chipH + 5);
      }
    }
    ctx.restore();
  }

  /* Colors arrive as hex or rgb() from CSS; both need an alpha variant. */
  function withAlpha(color, alpha) {
    color = String(color).trim();
    if (color.startsWith('#')) {
      let hex = color.slice(1);
      if (hex.length === 3) hex = hex.split('').map((c) => c + c).join('');
      const num = parseInt(hex, 16);
      return `rgba(${(num >> 16) & 255}, ${(num >> 8) & 255}, ${num & 255}, ${alpha})`;
    }
    const nums = color.match(/[\d.]+/g);
    if (!nums) return color;
    return `rgba(${nums[0]}, ${nums[1]}, ${nums[2]}, ${alpha})`;
  }

  function easeOutBack(t) {
    const c = 1.70158, c3 = c + 1;
    return 1 + c3 * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2);
  }
  const easeOutCubic = (t) => 1 - Math.pow(1 - t, 3);

  function truncate(s, n) {
    s = String(s || '');
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  }

  function hash(str) {
    let h = 0;
    for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) % 1000;
    return h / 1000;
  }

  /* A PNG of the map as it stands.

     The live canvas is transparent — the page paints the backdrop behind it —
     so exporting it directly gives nodes floating on nothing, which turns into
     black or white depending on what opens the file. Compositing onto the same
     background the user is looking at makes the export match the screen, which
     is the only thing that makes it worth calling a snapshot. */
  function snapshot() {
    if (!canvas || !canvas.width) return null;
    const out = document.createElement('canvas');
    out.width = canvas.width;
    out.height = canvas.height;
    const octx = out.getContext('2d');
    const bg = getComputedStyle(document.getElementById('map-region')).backgroundColor;
    octx.fillStyle = bg && bg !== 'rgba(0, 0, 0, 0)' ? bg : colors.surface;
    octx.fillRect(0, 0, out.width, out.height);
    octx.drawImage(canvas, 0, 0);
    return out.toDataURL('image/png');
  }

  return {
    init, setDevices, flashJoin, select, centerOn, fit, zoomBy,
    themeChanged, resize, snapshot,
  };
})();
