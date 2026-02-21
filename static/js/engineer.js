/* ============================================================
   Captain Sonar — engineer.js
   ============================================================ */

// GAME_ID, MY_NAME, MY_TEAM injected by template

const ENEMY_TEAM = MY_TEAM === 'blue' ? 'red' : 'blue';

// Engineering board layout (mirrors game_state.py)
const ENG_LAYOUT = {
  west:  [
    {color:'yellow', circuit:1}, {color:'red',    circuit:1},
    {color:'green',  circuit:1}, {color:'green',  circuit:null},
    {color:'radiation', circuit:null}, {color:'radiation', circuit:null},
  ],
  north: [
    {color:'red',    circuit:2}, {color:'red',    circuit:null},
    {color:'green',  circuit:null}, {color:'yellow', circuit:2},
    {color:'yellow', circuit:2}, {color:'radiation', circuit:null},
  ],
  south: [
    {color:'red',    circuit:3}, {color:'red',    circuit:null},
    {color:'green',  circuit:3}, {color:'yellow', circuit:3},
    {color:'yellow', circuit:null}, {color:'radiation', circuit:null},
  ],
  east: [
    {color:'yellow', circuit:3}, {color:'red',    circuit:1},
    {color:'green',  circuit:2}, {color:'green',  circuit:null},
    {color:'radiation', circuit:null}, {color:'radiation', circuit:null},
  ],
};

const DIRECTIONS  = ['west','north','south','east'];
const DIR_ARROWS  = {west:'←', north:'↑', south:'↓', east:'→'};

let board         = null;   // will be set from server
let activeDir     = null;   // direction we need to mark this turn
let canMark       = false;
let myHealth      = 4;
let enemyHealth   = 4;

const socket = io();

socket.on('connect', () => {
  socket.emit('join_room', {game_id: GAME_ID});
  socket.emit('join_game', {game_id: GAME_ID, name: MY_NAME});
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
  const moved    = state.turn_state && state.turn_state.moved;
  const engDone  = state.turn_state && state.turn_state.engineer_done;
  const dir      = state.turn_state && state.turn_state.direction;

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
  logEvent(`Mark a node in the ${data.direction.toUpperCase()} section!`, 'highlight');
});

socket.on('board_update', data => {
  board   = data.board;
  canMark = false;
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
  logEvent(data.team === MY_TEAM ? 'OUR TURN — wait for captain to move' : `${data.team} team's turn`);
});

socket.on('damage', data => {
  if (data.team === MY_TEAM) myHealth    = data.health;
  else                        enemyHealth = data.health;
  renderHealth();

  if (data.team === MY_TEAM) {
    // Flash the relevant direction column if applicable
    if (data.cause === 'direction_damage' && data.direction) {
      flashDir(data.direction);
    }
    logEvent(`💥 Engineering damage! −${data.amount} HP (${data.health} left)`, 'danger');
  } else {
    logEvent(`💥 Enemy took ${data.amount} damage`);
  }
});

socket.on('surface_announced', data => {
  if (data.team === MY_TEAM) { myHealth = data.health; renderHealth(); }
  else { enemyHealth = data.health; renderHealth(); }
  logEvent(`${data.team} surfaced in sector ${data.sector}`);
});

socket.on('game_over', data => {
  const won = data.winner === MY_TEAM;
  logEvent(`GAME OVER — ${data.winner} wins!`, 'highlight');
  showToast(won ? '🏆 Victory!' : '💀 Defeat…', !won);
});

socket.on('error', data => showToast(data.msg, true));

socket.on('bot_chat', data => {
  const icons = {captain:'🤖🎖', first_mate:'🤖⚙', engineer:'🤖🔧', radio_operator:'🤖📡'};
  const icon = icons[data.role] || '🤖';
  logEvent(`${icon} [${data.name}]: ${data.msg}`, 'bot');
});

// ── Render ────────────────────────────────────────────────────
function renderAll() {
  renderHealth();
  renderBoard();
}

function renderHealth() {
  renderHearts('own-health',   myHealth,    4);
  renderHearts('enemy-health', enemyHealth, 4);
}

function renderHearts(id, hp, max) {
  const el = document.getElementById(id);
  el.innerHTML = '';
  for (let i = 0; i < max; i++) {
    const s = document.createElement('span');
    s.className = 'health-heart' + (i < hp ? '' : ' empty');
    s.textContent = i < hp ? '❤️' : '🖤';
    el.appendChild(s);
  }
}

// Node grid positions in the CSS grid (0-indexed [row, col]):
//   North arm : row 0, cols 1-6  → CSS grid-row 1, grid-col 2-7
//   West arm  : rows 1-6, col 0  → CSS grid-row 2-7, grid-col 1
//   East arm  : rows 1-6, col 7  → CSS grid-row 2-7, grid-col 8
//   South arm : row 7, cols 1-6  → CSS grid-row 8, grid-col 2-7
const NODE_GRID = {
  north: (i) => ({ row: 1,   col: i + 2 }),  // i=0..5 → col 2-7
  south: (i) => ({ row: 8,   col: i + 2 }),
  west:  (i) => ({ row: i + 2, col: 1 }),     // i=0..5 → row 2-7
  east:  (i) => ({ row: i + 2, col: 8 }),
};

function renderBoard() {
  const container = document.getElementById('eng-board');
  container.innerHTML = '';

  // Wrap in a centred flex div
  const wrap = document.createElement('div');
  wrap.className = 'eng-board-wrap';

  // Outer CSS grid (8 cols × 8 rows)
  const grid = document.createElement('div');
  grid.className = 'eng-cross';
  grid.id = 'eng-cross';

  // Helper: place an element at a CSS grid position
  function place(el, row, col) {
    el.style.gridRow    = row;
    el.style.gridColumn = col;
    grid.appendChild(el);
  }

  // ── Corner / direction labels ──
  const CORNER_LABELS = [
    { row:1, col:1, text:'←W', dir:'west'  },
    { row:1, col:8, text:'E→', dir:'east'  },
    { row:8, col:1, text:'←W', dir:'west'  },
    { row:8, col:8, text:'E→', dir:'east'  },
  ];
  // North header row (row 0 in grid = row 1 in CSS)
  const nHdr = document.createElement('div');
  nHdr.style.gridRow = 1; nHdr.style.gridColumn = '2 / 8';
  nHdr.className = 'dir-corner' + (activeDir==='north' ? ' active-arm' : '');
  nHdr.textContent = '↑ NORTH';
  grid.appendChild(nHdr);

  // South header row
  const sHdr = document.createElement('div');
  sHdr.style.gridRow = 8; sHdr.style.gridColumn = '2 / 8';
  sHdr.className = 'dir-corner' + (activeDir==='south' ? ' active-arm' : '');
  sHdr.textContent = '↓ SOUTH';
  grid.appendChild(sHdr);

  CORNER_LABELS.forEach(({row, col, text, dir}) => {
    const lbl = document.createElement('div');
    lbl.className = 'dir-corner' + (activeDir===dir ? ' active-arm' : '');
    lbl.textContent = text;
    place(lbl, row, col);
  });

  // ── Centre area ──
  const center = document.createElement('div');
  center.className = 'eng-center';
  center.style.gridRow    = '2 / 8';
  center.style.gridColumn = '2 / 8';

  const rose = document.createElement('div');
  rose.className = 'compass-rose';
  rose.textContent = '🧭';

  const clabel = document.createElement('div');
  clabel.className = 'center-label';
  clabel.textContent = 'ENGINEERING';

  const cstatus = document.createElement('div');
  cstatus.className = 'center-status';
  cstatus.id = 'center-status-text';
  cstatus.textContent = activeDir
    ? `Mark a node in ${activeDir.toUpperCase()} →`
    : 'Waiting…';

  // Circuit key inside centre
  const ckey = document.createElement('div');
  ckey.className = 'circuit-legend';
  [['c1','#f97316'],['c2','#06b6d4'],['c3','#ec4899']].forEach(([cls, col]) => {
    const d = document.createElement('div');
    d.className = 'circ-dot';
    d.style.background = col;
    d.style.borderRadius = '2px';
    d.title = cls.toUpperCase();
    ckey.appendChild(d);
  });

  center.appendChild(rose);
  center.appendChild(clabel);
  center.appendChild(cstatus);
  center.appendChild(ckey);
  grid.appendChild(center);

  // ── Arm nodes ──
  DIRECTIONS.forEach(dir => {
    const layout     = ENG_LAYOUT[dir];
    const serverNodes = board && board[dir];

    layout.forEach((def, idx) => {
      const gp      = NODE_GRID[dir](idx);
      const marked  = serverNodes ? serverNodes[idx].marked : false;
      const isActive = (dir === activeDir);

      const node = document.createElement('div');
      node.id = `node-${dir}-${idx}`;
      node.className = 'eng-node ' + def.color
        + (def.circuit ? ` circuit-${def.circuit}` : '')
        + (marked       ? ' marked'     : '')
        + (isActive     ? ' active-arm' : '');

      node.title = `${dir.toUpperCase()} [${idx}] — ${def.color}`
        + (def.circuit ? ` · Circuit ${def.circuit}` : '');

      if (def.color === 'radiation') node.textContent = '☢';

      // Circuit badge
      if (def.circuit !== null) {
        const badge = document.createElement('div');
        badge.className = `circuit-badge c${def.circuit}`;
        badge.textContent = 'C' + def.circuit;
        node.appendChild(badge);
      }

      // Clickable only when it's the active direction and not yet marked
      if (canMark && isActive && !marked) {
        node.classList.add('clickable');
        node.addEventListener('click', () => markNode(dir, idx));
      }

      place(node, gp.row, gp.col);
    });
  });

  wrap.appendChild(grid);
  container.appendChild(wrap);
}

function flashDir(dir) {
  const layout = ENG_LAYOUT[dir];
  layout.forEach((_, idx) => {
    const node = document.getElementById(`node-${dir}-${idx}`);
    if (node) {
      node.classList.add('damage-flash');
      setTimeout(() => node.classList.remove('damage-flash'), 900);
    }
  });
}

function markNode(direction, index) {
  if (!canMark || direction !== activeDir) return;
  canMark = false;   // optimistic lock
  socket.emit('engineer_mark', {game_id: GAME_ID, name: MY_NAME, direction, index});
  renderBoard();     // re-render without clickable
}

function updateStatus() {
  const el = document.getElementById('eng-status');
  if (canMark && activeDir) {
    el.textContent = `Mark a node in the ${activeDir.toUpperCase()} section ↓`;
    el.style.color = 'var(--accent)';
  } else {
    el.textContent = 'Waiting for captain to move…';
    el.style.color = 'var(--text-muted)';
  }
}

// ── Helpers ───────────────────────────────────────────────────
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
