/* ============================================================
   Captain Sonar — engineer.js
   Engineering board: 4 direction columns × 6 node rows,
   with SVG circuit lines connecting nodes across all 4 directions.
   ============================================================ */

const ENEMY_TEAM = MY_TEAM === 'blue' ? 'red' : 'blue';

// Engineering board layout — mirrors game_state.py ENGINEERING_LAYOUT exactly
// Indices 0-2: circuit nodes (C1=red, C2=green, C3=yellow) one per direction
// Indices 3-4: extra non-circuit Central Circuit nodes
// Index  5:   radiation (reactor)
const ENG_LAYOUT = {
  west:  [
    {color:'red',       circuit:1},    // 0  mine/torpedo  C1
    {color:'green',     circuit:2},    // 1  sonar/drone   C2
    {color:'yellow',    circuit:3},    // 2  stealth        C3
    {color:'yellow',    circuit:null}, // 3  stealth (extra)
    {color:'red',       circuit:null}, // 4  mine/torpedo (extra)
    {color:'radiation', circuit:null}, // 5  reactor
  ],
  north: [
    {color:'red',       circuit:1},
    {color:'green',     circuit:2},
    {color:'yellow',    circuit:3},
    {color:'red',       circuit:null},
    {color:'green',     circuit:null},
    {color:'radiation', circuit:null},
  ],
  south: [
    {color:'red',       circuit:1},
    {color:'green',     circuit:2},
    {color:'yellow',    circuit:3},
    {color:'green',     circuit:null},
    {color:'yellow',    circuit:null},
    {color:'radiation', circuit:null},
  ],
  east:  [
    {color:'red',       circuit:1},
    {color:'green',     circuit:2},
    {color:'yellow',    circuit:3},
    {color:'yellow',    circuit:null},
    {color:'red',       circuit:null},
    {color:'radiation', circuit:null},
  ],
};

const DIR_ORDER   = ['west', 'north', 'south', 'east'];
const DIR_LABELS  = {west: '← W', north: '↑ N', south: '↓ S', east: 'E →'};
const CIRCUIT_COLORS = {1: '#f97316', 2: '#06b6d4', 3: '#ec4899'};

const COLOR_LABELS = {
  red:       'Mine / Torpedo',
  green:     'Sonar / Drone',
  yellow:    'Stealth',
  radiation: 'Radiation (reactor)',
};

let board       = null;
let activeDir   = null;
let canMark     = false;
let myHealth    = 4;
let enemyHealth = 4;

const socket = io();

socket.on('connect', () => {
  socket.emit('join_room',  {game_id: GAME_ID});
  socket.emit('join_game',  {game_id: GAME_ID, name: MY_NAME});
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

  const isMyTurn = (state.current_team === MY_TEAM);
  const moved    = state.turn_state?.moved;
  const engDone  = state.turn_state?.engineer_done;
  const dir      = state.turn_state?.direction;

  activeDir = (isMyTurn && moved && !engDone && dir) ? dir : null;
  canMark   = !!activeDir;

  renderAll();
  updateStatus();
});

socket.on('direction_to_mark', data => {
  activeDir = data.direction;
  canMark   = true;
  updateStatus();
  renderBoard();
  logEvent(`⚡ Mark a node in the ${data.direction.toUpperCase()} column!`, 'highlight');
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
  logEvent(data.team === MY_TEAM ? '🔔 OUR TURN — wait for captain to move' : `${data.team} team's turn`);
});

socket.on('damage', data => {
  if (data.team === MY_TEAM) myHealth    = data.health;
  else                        enemyHealth = data.health;
  renderHealth();
  if (data.team === MY_TEAM) {
    if (data.cause === 'direction_damage' && data.direction) flashDir(data.direction);
    logEvent(`💥 Engineering damage! −${data.amount} HP (${data.health} left)`, 'danger');
  } else {
    logEvent(`💥 Enemy took ${data.amount} damage`);
  }
});

socket.on('circuit_cleared', data => {
  if (data.team === MY_TEAM) {
    logEvent(`✅ Circuit C${data.circuit} self-repaired!`, 'highlight');
    renderBoard();
  }
});

socket.on('surface_announced', data => {
  if (data.team === MY_TEAM) { myHealth = data.health; renderHealth(); }
  else { enemyHealth = data.health; renderHealth(); }
  logEvent(`🌊 ${data.team} surfaced in sector ${data.sector}`);
});

socket.on('game_over', data => {
  const won = data.winner === MY_TEAM;
  logEvent(`🏁 GAME OVER — ${data.winner} wins!`, 'highlight');
  showToast(won ? '🏆 Victory!' : '💀 Defeat…', !won);
});

socket.on('error', data => showToast(data.msg, true));

socket.on('bot_chat', data => {
  const icons = {captain:'🤖🎖', first_mate:'🤖⚙', engineer:'🤖🔧', radio_operator:'🤖📡'};
  logEvent(`${icons[data.role]||'🤖'} [${data.name}]: ${data.msg}`, 'bot');
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
    s.className = 'health-heart' + (i < hp ? '' : ' empty');
    s.textContent = i < hp ? '❤️' : '🖤';
    el.appendChild(s);
  }
}

// ── Board render ──────────────────────────────────────────────
function renderBoard() {
  const container = document.getElementById('eng-board');
  container.innerHTML = '';

  const wrap = document.createElement('div');
  wrap.className = 'eng-wrap';
  wrap.id        = 'eng-wrap';

  // Direction column headers
  const headers = document.createElement('div');
  headers.className = 'eng-col-headers';
  DIR_ORDER.forEach(dir => {
    const h = document.createElement('div');
    h.className = `eng-dir-header${activeDir === dir ? ' active' : ''}`;
    h.textContent = DIR_LABELS[dir];
    headers.appendChild(h);
  });
  wrap.appendChild(headers);

  // Node columns container
  const colsWrap = document.createElement('div');
  colsWrap.className = 'eng-cols';
  colsWrap.id        = 'eng-cols';

  DIR_ORDER.forEach(dir => {
    const col = document.createElement('div');
    col.className  = `eng-col${activeDir === dir ? ' active-col' : ''}`;
    col.dataset.dir = dir;

    const serverNodes = board?.[dir];

    ENG_LAYOUT[dir].forEach((def, idx) => {
      // Insert REACTOR divider between index 2 and 3
      if (idx === 3) {
        const divider = document.createElement('div');
        divider.className = 'reactor-divider-inline';
        divider.innerHTML = '<span>⚛</span>';
        col.appendChild(divider);
      }

      const marked      = serverNodes?.[idx]?.marked ?? false;
      const isActive    = (dir === activeDir);
      const isClickable = canMark && isActive && !marked;

      const node = document.createElement('div');
      node.id        = `node-${dir}-${idx}`;
      node.className = [
        'eng-node',
        def.color,
        def.circuit ? `circuit-${def.circuit}` : 'no-circuit',
        idx < 3 ? 'cc-zone' : 'reactor-zone',
        marked      ? 'marked'    : '',
        isClickable ? 'clickable' : '',
      ].filter(Boolean).join(' ');

      node.dataset.dir = dir;
      node.dataset.idx = idx;
      node.title = `${dir.toUpperCase()} [${idx}] — ${COLOR_LABELS[def.color] || def.color}`
        + (def.circuit ? ` · Circuit C${def.circuit}` : '');

      if (def.color === 'radiation') {
        const sym = document.createElement('span');
        sym.className   = 'rad-sym';
        sym.textContent = '☢';
        node.appendChild(sym);
      }

      if (def.circuit !== null) {
        const badge = document.createElement('span');
        badge.className   = `circuit-badge c${def.circuit}`;
        badge.textContent = `C${def.circuit}`;
        node.appendChild(badge);
      }

      if (isClickable) node.addEventListener('click', () => markNode(dir, idx));
      col.appendChild(node);
    });

    colsWrap.appendChild(col);
  });

  wrap.appendChild(colsWrap);
  container.appendChild(wrap);

  // Draw SVG circuit lines after DOM is rendered
  requestAnimationFrame(drawCircuitLines);
}

// ── SVG circuit lines ─────────────────────────────────────────
function drawCircuitLines() {
  const wrap = document.getElementById('eng-wrap');
  if (!wrap) return;
  wrap.querySelectorAll('.circuit-svg').forEach(s => s.remove());

  const wrapRect = wrap.getBoundingClientRect();
  if (wrapRect.width === 0) return;

  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.className = 'circuit-svg';
  svg.setAttribute('width',  wrapRect.width);
  svg.setAttribute('height', wrapRect.height);

  [1, 2, 3].forEach(cid => {
    const nodeIdx = cid - 1;
    const pts = DIR_ORDER.map(dir => {
      const el = document.getElementById(`node-${dir}-${nodeIdx}`);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return {
        x: +(r.left - wrapRect.left + r.width  / 2).toFixed(1),
        y: +(r.top  - wrapRect.top  + r.height / 2).toFixed(1),
      };
    }).filter(Boolean);

    if (pts.length < 2) return;

    const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    poly.setAttribute('points',         pts.map(p => `${p.x},${p.y}`).join(' '));
    poly.setAttribute('stroke',         CIRCUIT_COLORS[cid]);
    poly.setAttribute('stroke-width',   '3.5');
    poly.setAttribute('stroke-opacity', '0.60');
    poly.setAttribute('fill',           'none');
    poly.setAttribute('stroke-linecap', 'round');
    poly.setAttribute('stroke-linejoin','round');
    svg.appendChild(poly);

    // Small glow dots at node centres
    pts.forEach(p => {
      const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      c.setAttribute('cx',           p.x);
      c.setAttribute('cy',           p.y);
      c.setAttribute('r',            '6');
      c.setAttribute('fill',         CIRCUIT_COLORS[cid]);
      c.setAttribute('fill-opacity', '0.35');
      svg.appendChild(c);
    });
  });

  wrap.appendChild(svg);
}

function flashDir(dir) {
  ENG_LAYOUT[dir].forEach((_, idx) => {
    const node = document.getElementById(`node-${dir}-${idx}`);
    if (node) {
      node.classList.add('damage-flash');
      setTimeout(() => node.classList.remove('damage-flash'), 900);
    }
  });
}

function markNode(dir, idx) {
  if (!canMark || dir !== activeDir) return;
  canMark = false;
  socket.emit('engineer_mark', {game_id: GAME_ID, name: MY_NAME, direction: dir, index: idx});
  renderBoard();
}

function updateStatus() {
  const el = document.getElementById('eng-status');
  if (!el) return;
  if (canMark && activeDir) {
    el.textContent = `⚡ Mark a node in the ${activeDir.toUpperCase()} column`;
    el.style.color = 'var(--accent)';
  } else {
    el.textContent = 'Waiting for captain to move…';
    el.style.color = 'var(--text-muted)';
  }
}

function logEvent(msg, cls) {
  const log   = document.getElementById('event-log');
  const entry = document.createElement('div');
  entry.className = 'log-entry' + (cls ? ' ' + cls : '');
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
