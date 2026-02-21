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

function renderBoard() {
  const container = document.getElementById('eng-board');
  container.innerHTML = '';

  DIRECTIONS.forEach(dir => {
    const col = document.createElement('div');
    col.className = 'dir-col' + (dir === activeDir ? ' active-dir' : '');
    col.id = `dir-col-${dir}`;

    const lbl = document.createElement('div');
    lbl.className = 'dir-label';
    lbl.textContent = `${DIR_ARROWS[dir]} ${dir.toUpperCase()}`;
    col.appendChild(lbl);

    const layout = ENG_LAYOUT[dir];
    const serverNodes = board && board[dir];

    // Group: circuit nodes first, then divider, then non-circuit
    const circuitNodes    = layout.filter(n => n.circuit !== null);
    const nonCircuitNodes = layout.filter(n => n.circuit === null);

    [...circuitNodes, {divider:true}, ...nonCircuitNodes].forEach((def, i) => {
      if (def.divider) {
        const hr = document.createElement('hr');
        hr.className = 'node-divider';
        col.appendChild(hr);
        return;
      }

      // Find actual index in full layout
      const idx = layout.indexOf(def);
      const marked = serverNodes ? serverNodes[idx].marked : false;

      const node = document.createElement('div');
      node.className = `eng-node ${def.color}${marked ? ' marked' : ''}`;
      node.title     = `${def.color}${def.circuit ? ' (circuit '+def.circuit+')' : ''}`;

      if (def.color === 'radiation') node.textContent = '☢';

      if (def.circuit !== null) {
        const tag = document.createElement('div');
        tag.className   = 'circuit-tag';
        tag.textContent = 'C'+def.circuit;
        node.appendChild(tag);
      }

      if (canMark && dir === activeDir && !marked) {
        node.classList.add('clickable');
        node.addEventListener('click', () => markNode(dir, idx));
      }

      col.appendChild(node);
    });

    container.appendChild(col);
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

function flashDir(dir) {
  const col = document.getElementById(`dir-col-${dir}`);
  if (col) {
    col.classList.add('damage-flash');
    setTimeout(() => col.classList.remove('damage-flash'), 900);
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
