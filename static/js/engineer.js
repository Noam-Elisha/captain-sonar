/* ============================================================
   Captain Sonar — engineer.js
   Engineering board: 2×2 section layout (WEST/NORTH/SOUTH/EAST)
   SVG overlay draws circuits (C1/C2/C3):
     Intra-panel: chain the 3 circuit nodes within each non-EAST section.
     Inter-panel: ONE exit line per section → its designated EAST hub node.
       SOUTH bundle (C3, pink):   S[0]─S[1]─S[2] → E[0] (yellow, left)
       WEST  bundle (C1, orange): W[0]─W[1]─W[2] → E[1] (red,    mid)
       NORTH bundle (C2, cyan):   N[0]─N[1]─N[2] → E[2] (green,  right)
   ============================================================ */

const ENEMY_TEAM = MY_TEAM === 'blue' ? 'red' : 'blue';

// All four direction IDs used for per-section updates
const DIRS = ['west', 'north', 'south', 'east'];

// Circuit colours: index = node position within section's main-nodes row
//   0 → C1 (orange), 1 → C2 (cyan), 2 → C3 (pink)
const CIRCUIT_COLORS = { 0: '#f97316', 1: '#06b6d4', 2: '#ec4899' };

// Each non-EAST direction bundles its 3 circuit nodes (idx 0–2) into one chain,
// then exits with ONE inter-panel line to the designated EAST hub node.
// SOUTH → E[0] (yellow, left — adjacent to SOUTH, no crossover)
// WEST  → E[1] (red,    mid)
// NORTH → E[2] (green,  right — straight down from NORTH)
const CIRCUIT_BUNDLES = [
  { dir: 'south', hubIdx: 0, color: CIRCUIT_COLORS[2] },
  { dir: 'west',  hubIdx: 1, color: CIRCUIT_COLORS[0] },
  { dir: 'north', hubIdx: 2, color: CIRCUIT_COLORS[1] },
];

let board       = null;
let activeDir   = null;
let canMark     = false;
let myHealth    = 4;
let enemyHealth = 4;

const socket = io();

socket.on('connect', () => {
  socket.emit('join_room', { game_id: GAME_ID });
  socket.emit('join_game', { game_id: GAME_ID, name: MY_NAME });
});

socket.on('game_state', state => {
  if (!state || !state.submarines) return;
  const mySub    = state.submarines[MY_TEAM];
  const enemySub = state.submarines[ENEMY_TEAM];
  if (mySub) {
    myHealth = mySub.health;
    if (mySub.engineering) board = mySub.engineering;
  }
  if (enemySub) enemyHealth = enemySub.health;

  const isMyTurn   = (state.current_team === MY_TEAM);
  const moved      = state.turn_state?.moved;
  const engDone    = state.turn_state?.engineer_done;
  const dir        = state.turn_state?.direction;
  const stealthDir = state.turn_state?.stealth_direction; // only own team sees this

  // Use public direction; fall back to private stealth direction
  const effectiveDir = dir || stealthDir || null;
  activeDir = (isMyTurn && moved && !engDone && effectiveDir) ? effectiveDir : null;
  canMark   = !!activeDir;

  renderAll();
  updateStatus();
});

socket.on('direction_to_mark', data => {
  activeDir = data.direction;
  canMark   = true;
  updateStatus();
  renderBoard();
  const label = data.is_stealth
    ? `👻 STEALTH move — mark a node in the ${data.direction.toUpperCase()} section (secret!)`
    : `⚡ Mark a node in the ${data.direction.toUpperCase()} section!`;
  logEvent(label, 'highlight');
});

socket.on('board_update', data => {
  board     = data.board;
  canMark   = false;
  activeDir = null;
  renderBoard();
  updateStatus();
  logEvent('Board updated');
});

socket.on('turn_start', data => {
  canMark   = false;
  activeDir = null;
  renderBoard();
  updateStatus();
  if (data.team === MY_TEAM) {
    logEvent('🔔 OUR TURN — wait for captain to move', 'highlight');
  }
});

socket.on('damage', data => {
  if (data.team === MY_TEAM) myHealth    = data.health;
  else                        enemyHealth = data.health;
  renderHealth();
  if (data.team === MY_TEAM) {
    if (data.cause === 'direction_damage' && data.direction) flashDir(data.direction);
    const causeMsg = data.cause === 'surface' ? '🌊 Surfaced! −' : '💥 Engineering damage! −';
    logEvent(`${causeMsg}${data.amount} HP (${data.health} left)`, 'danger');
  } else {
    const causeMsg = data.cause === 'surface' ? '🌊 Enemy surfaced! ' : '';
    logEvent(`${causeMsg}💥 Enemy took ${data.amount} damage`);
  }
});

socket.on('circuit_cleared', data => {
  if (data.team === MY_TEAM) {
    logEvent(`✅ Circuit C${data.circuit} self-repaired!`, 'highlight');
    renderBoard();
  }
});

socket.on('sonar_result', data => {
  if (data.target === MY_TEAM) {
    logEvent('📡 Sonar complete — result reported to captain & first mate', 'good');
  }
});

socket.on('drone_result', data => {
  const result = data.in_sector ? 'YES — CONTACT! 🎯' : 'NO — clear';
  if (data.target === MY_TEAM) {
    logEvent(`🛸 Drone sector ${data.ask_sector}: ${result}`, 'highlight');
  } else {
    logEvent(`🛸 Enemy drone sector ${data.ask_sector}: ${result}`);
  }
});

socket.on('surface_announced', data => {
  if (data.team === MY_TEAM) { myHealth = data.health; renderHealth(); }
  else                        { enemyHealth = data.health; renderHealth(); }
  logEvent(`🌊 ${data.team} surfaced in sector ${data.sector}`);
});

socket.on('game_over', data => {
  const won = data.winner === MY_TEAM;
  logEvent(`🏁 GAME OVER — ${data.winner} wins!`, 'highlight');
  showToast(won ? '🏆 Victory!' : '💀 Defeat…', !won);
});

socket.on('error', data => showToast(data.msg, true));

socket.on('bot_chat', data => {
  const icons = { captain: '🤖🎖', first_mate: '🤖⚙', engineer: '🤖🔧', radio_operator: '🤖📡' };
  logEvent(`${icons[data.role] || '🤖'} [${data.name}]: ${data.msg}`, 'bot');
});

// ── Render all ────────────────────────────────────────────────
function renderAll() { renderHealth(); renderBoard(); }

function renderHealth() {
  renderHearts('own-health',   myHealth,    4);
  renderHearts('enemy-health', enemyHealth, 4);
}

function renderHearts(id, hp, max) {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerHTML = '';
  for (let i = 0; i < max; i++) {
    const s = document.createElement('span');
    s.className   = 'health-heart' + (i < hp ? '' : ' empty');
    s.textContent = i < hp ? '❤️' : '🖤';
    el.appendChild(s);
  }
}

// ── Board render — updates existing static HTML, no DOM rebuild ──
function renderBoard() {
  DIRS.forEach(dir => {
    const section = document.getElementById(`section-${dir}`);
    if (!section) return;

    // Highlight the active direction section
    section.classList.toggle('active-section', activeDir === dir);

    const serverNodes = board?.[dir];

    for (let idx = 0; idx < 6; idx++) {
      const node = document.getElementById(`node-${dir}-${idx}`);
      if (!node) continue;

      const marked      = serverNodes?.[idx]?.marked ?? false;
      const isActive    = (dir === activeDir);
      const isClickable = canMark && isActive && !marked;

      node.classList.toggle('marked',    marked);
      node.classList.toggle('clickable', isClickable);

      // Replace onclick each render to avoid stale closures
      node.onclick = isClickable ? (() => { const d = dir, i = idx; return () => markNode(d, i); })() : null;
    }
  });

  requestAnimationFrame(drawCircuitLines);
}

// ── SVG circuit bundles ───────────────────────────────────────
// Each non-EAST direction: chain its 3 circuit nodes (0→1→2) inside the panel,
// then ONE line from the last node → the designated EAST hub node.
function drawCircuitLines() {
  const svg  = document.getElementById('eng-circuit-svg');
  const wrap = document.getElementById('eng-board');
  if (!svg || !wrap) return;
  svg.innerHTML = '';

  const wrapRect = wrap.getBoundingClientRect();
  if (wrapRect.width === 0) return;

  svg.setAttribute('width',  wrapRect.width);
  svg.setAttribute('height', wrapRect.height);

  function nodeCenter(dir, idx) {
    const el = document.getElementById(`node-${dir}-${idx}`);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {
      x: +(r.left - wrapRect.left + r.width  / 2).toFixed(1),
      y: +(r.top  - wrapRect.top  + r.height / 2).toFixed(1),
    };
  }

  function svgLine(x1, y1, x2, y2, color, opacity, width) {
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1',             x1);
    line.setAttribute('y1',             y1);
    line.setAttribute('x2',             x2);
    line.setAttribute('y2',             y2);
    line.setAttribute('stroke',         color);
    line.setAttribute('stroke-width',   width);
    line.setAttribute('stroke-opacity', opacity);
    line.setAttribute('stroke-linecap', 'round');
    svg.appendChild(line);
  }

  function svgDot(cx, cy, color, r, opacity) {
    const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    c.setAttribute('cx',           cx);
    c.setAttribute('cy',           cy);
    c.setAttribute('r',            r);
    c.setAttribute('fill',         color);
    c.setAttribute('fill-opacity', opacity);
    svg.appendChild(c);
  }

  CIRCUIT_BUNDLES.forEach(({ dir, hubIdx, color }) => {
    // Centres of the 3 circuit nodes within this direction's panel
    const pts = [0, 1, 2].map(i => nodeCenter(dir, i));
    if (pts.some(p => !p)) return;

    // ── Intra-panel: chain node 0 → 1 → 2 ──────────────────
    svgLine(pts[0].x, pts[0].y, pts[1].x, pts[1].y, color, '0.42', '1.5');
    svgLine(pts[1].x, pts[1].y, pts[2].x, pts[2].y, color, '0.42', '1.5');

    // ── Inter-panel: exit from node 2 → EAST hub ───────────
    const hub = nodeCenter('east', hubIdx);
    if (!hub) return;
    svgLine(pts[2].x, pts[2].y, hub.x, hub.y, color, '0.62', '2');

    // ── Glow dots at each circuit node + EAST hub ───────────
    pts.forEach(p => svgDot(p.x, p.y, color, '5', '0.22'));
    svgDot(hub.x, hub.y, color, '6', '0.30');
  });
}

function flashDir(dir) {
  for (let idx = 0; idx < 6; idx++) {
    const node = document.getElementById(`node-${dir}-${idx}`);
    if (node) {
      node.classList.add('damage-flash');
      setTimeout(() => node.classList.remove('damage-flash'), 900);
    }
  }
}

function markNode(dir, idx) {
  if (!canMark || dir !== activeDir) return;
  canMark = false;
  socket.emit('engineer_mark', { game_id: GAME_ID, name: MY_NAME, direction: dir, index: idx });
  renderBoard();
}

function updateStatus() {
  const el = document.getElementById('eng-status');
  if (!el) return;
  if (canMark && activeDir) {
    el.textContent = `⚡ Mark a node in the ${activeDir.toUpperCase()} section`;
    el.style.color = 'var(--accent)';
  } else {
    el.textContent = 'Waiting for captain to move…';
    el.style.color = 'var(--text-muted)';
  }
}

function logEvent(msg, cls) {
  const log   = document.getElementById('event-log');
  const entry = document.createElement('div');
  entry.className   = 'log-entry' + (cls ? ' ' + cls : '');
  entry.textContent = msg;
  log.prepend(entry);
  while (log.children.length > 50) log.lastChild.remove();
}

function showToast(msg, isError) {
  const t = document.getElementById('result-toast');
  t.textContent = msg;
  t.className   = 'result-toast' + (isError ? ' error' : '');
  setTimeout(() => t.classList.add('hidden'), 4000);
}

document.addEventListener('DOMContentLoaded', () => {
  renderHealth();
  renderBoard();
  updateStatus();
});
