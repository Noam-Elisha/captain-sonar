/* ============================================================
   Admiral Radar — spectator.js
   Full-visibility observer: sees both submarines, all systems,
   all engineering boards, and watches radio operators' drawings.
   ============================================================ */

// ── Constants ─────────────────────────────────────────────────────────────────
const CELL_PX   = 32;
const MAP_LABEL = 24;
const ISLAND_SET = new Set(ISLANDS.map(([r, c]) => `${r},${c}`));

const SYSTEM_MAX    = { torpedo: 3, mine: 3, sonar: 3, drone: 4, stealth: 5 };
const SYSTEM_LABELS = { torpedo: '🚀 Torpedo', mine: '💠 Mine', sonar: '📡 Sonar', drone: '🛸 Drone', stealth: '✨ Silent Running' };
const SYSTEM_COLOR  = { torpedo: 'col-red', mine: 'col-red', sonar: 'col-green', drone: 'col-green', stealth: 'col-yellow' };

// ── State ─────────────────────────────────────────────────────────────────────
let lastBlue = null, lastRed = null;

// Radio operator canvas layers (one per team)
let roCanvases = {};   // { blue: {canvas, ctx}, red: {canvas, ctx} }

// ── Socket ────────────────────────────────────────────────────────────────────
const socket = io();

socket.on('connect', () => {
  socket.emit('join_room',         { game_id: GAME_ID });
  socket.emit('join_as_spectator', { game_id: GAME_ID, name: MY_NAME });
});

socket.on('spectator_ack', () => {
  logEvent('👁 Connected as spectator', 'highlight');
  document.getElementById('spec-status').textContent = 'Watching live game';
});

socket.on('game_state', state => {
  if (!state || !state.submarines) return;
  lastBlue = state.submarines.blue;
  lastRed  = state.submarines.red;
  updateHeader(state);
  updateTeamPanel('blue', lastBlue);
  updateTeamPanel('red',  lastRed);
  redrawSVG(lastBlue, lastRed);
});

socket.on('direction_announced', data => {
  const t = data.team;
  logEvent(`${t === 'blue' ? '🔵' : '🔴'} [${t.toUpperCase()}] moved ${data.direction.toUpperCase()}`, t);
  addMoveTag(t, data.direction.toUpperCase().slice(0, 1), t);
});

socket.on('surface_announced', data => {
  const t = data.team;
  logEvent(`${t === 'blue' ? '🔵' : '🔴'} [${t.toUpperCase()}] SURFACED in sector ${data.sector} ⚠`, 'danger');
  addMoveTag(t, '🛸', 'surface');
  setSurfaced(t, true);
});

socket.on('dive_announced', data => {
  logEvent(`${data.team === 'blue' ? '🔵' : '🔴'} [${data.team.toUpperCase()}] dived`);
  setSurfaced(data.team, false);
});

socket.on('torpedo_fired', data => {
  logEvent(`🚀 [${data.team.toUpperCase()}] fired torpedo → (${data.row + 1}, ${data.col + 1})`, 'danger');
  flashExplosion(data.row, data.col, '#f97316');
});

socket.on('mine_detonated', data => {
  logEvent(`💥 [${data.team.toUpperCase()}] detonated mine at (${data.row + 1}, ${data.col + 1})`, 'danger');
  flashExplosion(data.row, data.col, '#ef4444');
});

socket.on('stealth_announced', data => {
  const t = data.team;
  logEvent(`✨ [${t.toUpperCase()}] used SILENT RUNNING — ${data.steps} silent step(s)`, 'stealth');
  addMoveTag(t, '✨', 'stealth');
});

socket.on('damage', data => {
  const cause = data.cause === 'system_failure' ? ' (system failure)'
              : data.cause === 'surface'         ? ' (surfaced)'      : '';
  logEvent(`💥 [${data.team.toUpperCase()}] took ${data.amount} damage${cause} (${data.health} health remain)`, 'danger');
});

socket.on('sonar_announced', data => logEvent(`📡 [${data.team.toUpperCase()}] used sonar`));
socket.on('drone_announced', data => logEvent(`🛸 [${data.team.toUpperCase()}] drone → sector ${data.sector}`));

socket.on('sonar_result', data => {
  const fmtVal = (type, val) => {
    if (type === 'row') return `Row ${val + 1}`;
    if (type === 'col') return `Col ${val + 1}`;
    return `Sector ${val}`;
  };
  const t = data.target;
  logEvent(
    `📡 [${t.toUpperCase()}] sonar: enemy replied "${fmtVal(data.type1, data.val1)}" & "${fmtVal(data.type2, data.val2)}" (1 true, 1 false)`,
    t === 'blue' ? 'blue' : 'red'
  );
});

socket.on('drone_result', data => {
  const t = data.target;
  const result = data.in_sector ? 'YES 🎯' : 'NO — clear';
  logEvent(`🛸 [${t.toUpperCase()}] drone sector ${data.ask_sector}: ${result}`, t === 'blue' ? 'blue' : 'red');
});

// Radio operator pan relay — sync the radio operator's pan position onto their canvas overlay
socket.on('ro_pan', data => {
  const layer = roCanvases[data.team];
  if (!layer || !layer.canvas) return;
  const dx = data.ox * layer.canvas.width;
  const dy = data.oy * layer.canvas.height;
  layer.canvas.style.transform = `translate(${dx}px, ${dy}px)`;
});

socket.on('turn_start', data => { setTurnBadge(data.team); });

socket.on('game_phase', data => {
  if (data.current_team) setTurnBadge(data.current_team);
  document.getElementById('spec-status').textContent = 'Playing…';
});

socket.on('game_over', data => {
  logEvent(`🏆 GAME OVER — ${data.winner.toUpperCase()} WINS!`, 'highlight');
  const badge = document.getElementById('spec-turn');
  badge.textContent = `${data.winner.toUpperCase()} WINS!`;
  badge.className   = 'spec-turn-badge ended';
  document.getElementById('spec-status').textContent = `Game over — ${data.winner} wins`;
});

socket.on('sub_placed', data => {
  logEvent(`${data.team === 'blue' ? '🔵' : '🔴'} [${data.team.toUpperCase()}] submarine placed`, data.team);
});

socket.on('bot_chat', data => {
  const ROLE_TAG = {captain:'CAP', first_mate:'FM', engineer:'ENG', radio_operator:'RO'};
  const tag = ROLE_TAG[data.role] || 'BOT';
  const teamCls = data.team === 'blue' ? 'bot-blue' : data.team === 'red' ? 'bot-red' : 'bot';
  logEvent(`[${tag}] ${data.msg}`, teamCls);
});

socket.on('error', data => { logEvent(`⚠ ${data.msg}`, 'danger'); });

// ── Radio Operator canvas relay ──────────────────────────────────────────────
socket.on('ro_canvas_stroke', data => {
  const team = data.team;
  const layer = roCanvases[team];
  if (!layer || !layer.ctx) return;
  const { ctx, canvas } = layer;

  if (data.tool === 'clear') {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    return;
  }

  const x1 = data.x1 * canvas.width;
  const y1 = data.y1 * canvas.height;
  const x2 = data.x2 * canvas.width;
  const y2 = data.y2 * canvas.height;

  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);

  if (data.tool === 'draw') {
    ctx.globalCompositeOperation = 'source-over';
    ctx.strokeStyle = team === 'blue' ? '#60a5facc' : '#f87171cc';
    ctx.lineWidth   = 4;
    ctx.lineCap     = 'round';
  } else if (data.tool === 'erase') {
    ctx.globalCompositeOperation = 'destination-out';
    ctx.lineWidth = 20;
    ctx.lineCap   = 'round';
  }
  ctx.stroke();
  ctx.globalCompositeOperation = 'source-over';
});

// ── Header ────────────────────────────────────────────────────────────────────
function updateHeader(state) {
  const statusEl = document.getElementById('spec-status');
  if (statusEl) {
    if      (state.phase === 'placement') statusEl.textContent = 'Placement phase…';
    else if (state.phase === 'playing')   statusEl.textContent = `Turn ${(state.turn_index || 0) + 1}`;
    else if (state.phase === 'ended')     statusEl.textContent = `OVER — ${state.winner?.toUpperCase()} wins!`;
  }
  if (state.current_team) setTurnBadge(state.current_team);
}

function setTurnBadge(team) {
  const el = document.getElementById('spec-turn');
  if (!el) return;
  el.textContent = `${team.toUpperCase()} TEAM`;
  el.className   = `spec-turn-badge ${team}-turn`;
}

// ── Team panels ───────────────────────────────────────────────────────────────
function updateTeamPanel(team, sub) {
  if (!sub) return;
  renderHealth(team + '-health', sub.health, 4);
  renderSystems(team + '-systems', sub.systems);
  if (sub.engineering) renderEngBoard(team + '-eng', sub.engineering);
  setSurfaced(team, !!sub.surfaced);
}

function setSurfaced(team, on) {
  const el = document.getElementById(team + '-surfaced');
  if (el) el.classList.toggle('visible', on);
}

function renderHealth(id, hp, max) {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerHTML = '';
  for (let i = 0; i < max; i++) {
    const s = document.createElement('span');
    s.className   = 'health-heart' + (i < hp ? '' : ' empty');
    s.textContent = i < hp ? '🛡️' : '💔';
    el.appendChild(s);
  }
}

function renderSystems(id, systems) {
  const el = document.getElementById(id);
  if (!el || !systems) return;
  el.innerHTML = '';
  Object.entries(SYSTEM_MAX).forEach(([sys, maxVal]) => {
    const info    = systems[sys];
    const charge  = (typeof info === 'object' ? info.charge : info) || 0;
    const maxC    = (typeof info === 'object' ? info.max    : maxVal) || maxVal;
    const ready   = charge >= maxC;
    const colClass = SYSTEM_COLOR[sys] || '';

    const row  = document.createElement('div');
    row.className = 'sys-row';

    const lbl  = document.createElement('span');
    lbl.className   = 'sys-lbl';
    lbl.textContent = SYSTEM_LABELS[sys];

    const pips = document.createElement('span');
    pips.className = 'sys-pips';
    for (let i = 0; i < maxC; i++) {
      const pip = document.createElement('span');
      pip.className = 'sys-pip' + (i < charge ? ' charged ' + colClass : '');
      pips.appendChild(pip);
    }
    if (ready) {
      const rdy = document.createElement('span');
      rdy.className   = 'sys-ready-badge';
      rdy.textContent = '✓';
      pips.appendChild(rdy);
    }

    row.appendChild(lbl);
    row.appendChild(pips);
    el.appendChild(row);
  });
}

function renderEngBoard(id, board) {
  const el = document.getElementById(id);
  if (!el || !board) return;
  el.innerHTML = '';

  const DIR_ORDER_2X2 = [
    ['west', 'north'],
    ['south', 'east']
  ];
  const DIR_LABELS = { west: 'W', north: 'N', south: 'S', east: 'E' };
  const CIRCUIT_LABELS = ['C1', 'C2', 'C3'];
  const CIRCUIT_CLASSES = ['c1', 'c2', 'c3'];

  const grid = document.createElement('div');
  grid.className = 'mini-eng-2x2';

  DIR_ORDER_2X2.flat().forEach(dir => {
    const nodes = board[dir];
    if (!nodes) return;

    const section = document.createElement('div');
    section.className = 'mini-eng-section';

    // Direction label
    const lbl = document.createElement('div');
    lbl.className = 'mini-dir-label';
    lbl.textContent = DIR_LABELS[dir];
    section.appendChild(lbl);

    // Circuit nodes (0-2)
    const mainRow = document.createElement('div');
    mainRow.className = 'mini-node-row';
    for (let i = 0; i < 3; i++) {
      const node = nodes[i];
      const dot = document.createElement('div');
      dot.className = 'mini-node-2x2 ' + node.color + (node.marked ? ' marked' : '');
      const badge = document.createElement('span');
      badge.className = 'mini-circuit-badge ' + CIRCUIT_CLASSES[i];
      badge.textContent = CIRCUIT_LABELS[i];
      dot.appendChild(badge);
      mainRow.appendChild(dot);
    }
    section.appendChild(mainRow);

    // Divider
    const divider = document.createElement('div');
    divider.className = 'mini-divider';
    section.appendChild(divider);

    // Extra nodes (3-5)
    const extraRow = document.createElement('div');
    extraRow.className = 'mini-node-row';
    for (let i = 3; i < 6; i++) {
      const node = nodes[i];
      const dot = document.createElement('div');
      dot.className = 'mini-node-2x2 ' + node.color + (node.marked ? ' marked' : '');
      if (node.color === 'radiation') {
        const sym = document.createElement('span');
        sym.className = 'mini-rad-sym';
        sym.textContent = '\u2622';
        dot.appendChild(sym);
      }
      extraRow.appendChild(dot);
    }
    section.appendChild(extraRow);

    grid.appendChild(section);
  });

  el.appendChild(grid);
}

// ── Move log ──────────────────────────────────────────────────────────────────
function addMoveTag(team, label, type) {
  const logEl = document.getElementById(team + '-moves');
  if (!logEl) return;
  const tag = document.createElement('span');
  tag.className   = 'move-tag ' + (type || team);
  tag.textContent = label;
  tag.title       = label;
  logEl.prepend(tag);
  while (logEl.children.length > 50) logEl.lastChild.remove();
}

// ── Event log ─────────────────────────────────────────────────────────────────
function logEvent(msg, cls) {
  const log = document.getElementById('spec-event-log');
  if (!log) return;
  const entry = document.createElement('div');
  entry.className   = 'log-entry' + (cls ? ' ' + cls : '');
  const t = new Date().toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
  entry.textContent = `[${t}] ${msg}`;
  log.prepend(entry);
  while (log.children.length > 120) log.lastChild.remove();
}

// ── Map rendering ─────────────────────────────────────────────────────────────
function renderMap() {
  const grid = document.getElementById('spec-map');
  if (!grid) return;
  grid.innerHTML = '';
  grid.style.gridTemplateColumns = `${MAP_LABEL}px repeat(${MAP_COLS}, ${CELL_PX}px)`;
  grid.style.gridTemplateRows    = `${MAP_LABEL}px repeat(${MAP_ROWS}, ${CELL_PX}px)`;

  const corner = document.createElement('div');
  corner.className = 'map-label';
  grid.appendChild(corner);

  COL_LABELS.forEach(l => {
    const el = document.createElement('div');
    el.className   = 'map-label';
    el.textContent = l;
    grid.appendChild(el);
  });

  for (let r = 0; r < MAP_ROWS; r++) {
    const rl = document.createElement('div');
    rl.className   = 'map-label';
    rl.textContent = r + 1;
    grid.appendChild(rl);
    for (let c = 0; c < MAP_COLS; c++) {
      const cell = document.createElement('div');
      cell.className = 'map-cell';
      if (ISLAND_SET.has(`${r},${c}`)) cell.classList.add('island-cell');
      grid.appendChild(cell);
    }
  }

  // Sector boxes
  const wrapper = document.getElementById('spec-wrapper');
  const secW = (typeof SECTOR_W !== 'undefined') ? SECTOR_W : SECTOR_SZ;
  const secH = (typeof SECTOR_H !== 'undefined') ? SECTOR_H : SECTOR_SZ;
  const sPerRow = Math.ceil(MAP_ROWS / secH);
  const sPerCol = Math.ceil(MAP_COLS / secW);
  for (let sr = 0; sr < sPerRow; sr++) {
    for (let sc = 0; sc < sPerCol; sc++) {
      const box    = document.createElement('div');
      box.className = 'sector-box';
      const startR = sr * secH, startC = sc * secW;
      const endR   = Math.min(startR + secH, MAP_ROWS);
      const endC   = Math.min(startC + secW, MAP_COLS);
      box.style.cssText = `position:absolute;`
        + `left:${MAP_LABEL + startC * CELL_PX}px;`
        + `top:${MAP_LABEL + startR * CELL_PX}px;`
        + `width:${(endC - startC) * CELL_PX}px;`
        + `height:${(endR - startR) * CELL_PX}px;`;
      const lblEl        = document.createElement('div');
      lblEl.className    = 'sector-label';
      lblEl.textContent  = sr * sPerCol + sc + 1;
      box.appendChild(lblEl);
      wrapper.appendChild(box);
    }
  }

  const totalW = MAP_LABEL + MAP_COLS * CELL_PX;
  const totalH = MAP_LABEL + MAP_ROWS * CELL_PX;

  const svg = document.getElementById('spec-svg');
  if (svg) { svg.setAttribute('width', totalW); svg.setAttribute('height', totalH); }

  // Initialise radio operator drawing canvas layers
  initROCanvases(totalW, totalH);
}

// ── Radio operator canvas layers (blue + red) ───────────────────────────────
function initROCanvases(w, h) {
  ['blue', 'red'].forEach(team => {
    const canvasEl = document.getElementById(`ro-canvas-${team}`);
    if (!canvasEl) return;
    canvasEl.width  = w;
    canvasEl.height = h;
    const ctx = canvasEl.getContext('2d');
    roCanvases[team] = { canvas: canvasEl, ctx };
  });
}

// ── SVG overlay for submarines, trails, mines ──────────────────────────────────
function cellCenter(row, col) {
  return {
    x: MAP_LABEL + col * CELL_PX + CELL_PX / 2,
    y: MAP_LABEL + row * CELL_PX + CELL_PX / 2,
  };
}

function redrawSVG(blue, red) {
  const svg = document.getElementById('spec-svg');
  if (!svg) return;
  const ns = 'http://www.w3.org/2000/svg';
  svg.innerHTML = '';

  drawTrail(svg, ns, blue?.trail, '#60a5fa', 0.55);
  drawTrail(svg, ns, red?.trail,  '#f87171', 0.55);

  (blue?.mines || []).forEach(([r, c]) => {
    const { x, y } = cellCenter(r, c);
    drawMine(svg, ns, x, y, '#60a5fa');
  });
  (red?.mines || []).forEach(([r, c]) => {
    const { x, y } = cellCenter(r, c);
    drawMine(svg, ns, x, y, '#f87171');
  });

  if (blue?.position) {
    const [r, c] = blue.position;
    const { x, y } = cellCenter(r, c);
    drawSub(svg, ns, x, y, '#2563eb', '#60a5fa');
  }
  if (red?.position) {
    const [r, c] = red.position;
    const { x, y } = cellCenter(r, c);
    drawSub(svg, ns, x, y, '#dc2626', '#f87171');
  }
}

function drawTrail(svg, ns, trail, color, opacity) {
  if (!trail || trail.length < 2) return;
  const pts = trail.map(([r, c]) => {
    const { x, y } = cellCenter(r, c);
    return `${x},${y}`;
  }).join(' ');
  const line = document.createElementNS(ns, 'polyline');
  line.setAttribute('points',          pts);
  line.setAttribute('stroke',          color);
  line.setAttribute('stroke-width',    '2.5');
  line.setAttribute('stroke-opacity',  String(opacity));
  line.setAttribute('fill',            'none');
  line.setAttribute('stroke-linecap',  'round');
  line.setAttribute('stroke-linejoin', 'round');
  svg.appendChild(line);
}

function drawSub(svg, ns, x, y, fillColor, glowColor) {
  const glow = document.createElementNS(ns, 'circle');
  glow.setAttribute('cx', x); glow.setAttribute('cy', y); glow.setAttribute('r', 15);
  glow.setAttribute('fill', fillColor); glow.setAttribute('fill-opacity', '0.18');
  svg.appendChild(glow);

  const c = document.createElementNS(ns, 'circle');
  c.setAttribute('cx', x); c.setAttribute('cy', y); c.setAttribute('r', 9);
  c.setAttribute('fill', fillColor); c.setAttribute('stroke', '#fff'); c.setAttribute('stroke-width', '2');
  svg.appendChild(c);

  const t = document.createElementNS(ns, 'text');
  t.setAttribute('x', x); t.setAttribute('y', y + 4);
  t.setAttribute('text-anchor', 'middle'); t.setAttribute('font-size', '10');
  t.setAttribute('fill', '#fff'); t.setAttribute('pointer-events', 'none');
  t.textContent = '🚀';
  svg.appendChild(t);
}

function drawMine(svg, ns, x, y, color) {
  const c = document.createElementNS(ns, 'circle');
  c.setAttribute('cx', x); c.setAttribute('cy', y); c.setAttribute('r', 5);
  c.setAttribute('fill', color); c.setAttribute('fill-opacity', '0.65');
  c.setAttribute('stroke', color); c.setAttribute('stroke-width', '1');
  svg.appendChild(c);

  const t = document.createElementNS(ns, 'text');
  t.setAttribute('x', x); t.setAttribute('y', y + 3);
  t.setAttribute('text-anchor', 'middle'); t.setAttribute('font-size', '7');
  t.setAttribute('fill', '#fff'); t.setAttribute('pointer-events', 'none');
  t.textContent = '✕';
  svg.appendChild(t);
}

function flashExplosion(row, col, color) {
  const svg = document.getElementById('spec-svg');
  if (!svg) return;
  const ns = 'http://www.w3.org/2000/svg';
  const { x, y } = cellCenter(row, col);

  const ring = document.createElementNS(ns, 'circle');
  ring.setAttribute('cx', x); ring.setAttribute('cy', y); ring.setAttribute('r', '8');
  ring.setAttribute('fill', color); ring.setAttribute('fill-opacity', '0.85');
  ring.setAttribute('stroke', '#fff'); ring.setAttribute('stroke-width', '1.5');
  svg.appendChild(ring);

  let r = 8, op = 0.85;
  const anim = setInterval(() => {
    r  += 3.5;
    op -= 0.1;
    if (op <= 0) {
      clearInterval(anim);
      if (ring.parentNode) ring.parentNode.removeChild(ring);
      return;
    }
    ring.setAttribute('r', String(r));
    ring.setAttribute('fill-opacity', String(Math.max(op, 0)));
    ring.setAttribute('stroke-opacity', String(Math.max(op * 1.5, 0)));
  }, 40);
}

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  renderMap();
  renderHealth('blue-health', 4, 4);
  renderHealth('red-health',  4, 4);
  logEvent('👁 Observer view loaded — waiting for game state…', 'highlight');
});
