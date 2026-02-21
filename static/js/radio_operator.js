/* ============================================================
   Captain Sonar — radio_operator.js
   ============================================================ */

// GAME_ID, MY_NAME, MY_TEAM, MAP_ROWS, MAP_COLS, SECTOR_SZ, ISLANDS, COL_LABELS

const ENEMY_TEAM = MY_TEAM === 'blue' ? 'red' : 'blue';
const CELL_PX    = 32;
const ISLAND_SET = new Set(ISLANDS.map(([r,c]) => `${r},${c}`));

let myHealth     = 4;
let enemyHealth  = 4;
let moveCount    = 0;

// Drawing state
let currentTool  = 'draw';   // 'draw' | 'erase'
let isDrawing    = false;
let lastX        = 0, lastY = 0;
let canvas, ctx;

const socket = io();

socket.on('connect', () => {
  socket.emit('join_room', {game_id: GAME_ID});
  socket.emit('join_game', {game_id: GAME_ID, name: MY_NAME});
});

socket.on('game_state', state => {
  if (!state || !state.submarines) return;
  const mySub    = state.submarines[MY_TEAM];
  const enemySub = state.submarines[ENEMY_TEAM];
  if (mySub)    myHealth    = mySub.health;
  if (enemySub) enemyHealth = enemySub.health;
  renderHealth();
});

// Enemy direction announcements — RO tracks these
socket.on('direction_announced', data => {
  if (data.team === ENEMY_TEAM) {
    logMove(data.direction, 'move');
    logEvent(`Enemy moved: ${data.direction.toUpperCase()}`, 'highlight');
    document.getElementById('ro-hint').textContent =
      `Enemy moved ${data.direction.toUpperCase()} — mark on grid!`;
  }
});

socket.on('surface_announced', data => {
  if (data.team === ENEMY_TEAM) {
    logMove(`SURFACE (sector ${data.sector})`, 'surface');
    logEvent(`⚠ Enemy surfaced in sector ${data.sector}!`, 'highlight');
    document.getElementById('ro-hint').textContent =
      `Enemy surfaced in sector ${data.sector}!`;
  }
  if (data.team === MY_TEAM) { myHealth = data.health; renderHealth(); }
  else { enemyHealth = data.health; renderHealth(); }
});

socket.on('stealth_announced', data => {
  if (data.team === ENEMY_TEAM) {
    logMove(`STEALTH (${data.steps} step${data.steps!==1?'s':''})`, 'stealth');
    logEvent(`👻 Enemy used stealth — ${data.steps} silent step(s)`, 'highlight');
    document.getElementById('ro-hint').textContent =
      `Enemy used stealth — ${data.steps} silent steps, direction unknown`;
  }
});

socket.on('torpedo_fired', data => {
  if (data.team === ENEMY_TEAM) {
    logEvent(`⚠ Enemy fired torpedo!`, 'danger');
  }
});

socket.on('sonar_announced', data => {
  if (data.team === ENEMY_TEAM) logEvent('Enemy used sonar on us');
});
socket.on('drone_announced', data => {
  if (data.team === ENEMY_TEAM) logEvent(`Enemy scanned sector ${data.sector} with drone`);
});

socket.on('damage', data => {
  if (data.team === MY_TEAM) myHealth    = data.health;
  else                        enemyHealth = data.health;
  renderHealth();
  logEvent(`💥 ${data.team === MY_TEAM ? 'We' : 'Enemy'} took ${data.amount} damage`, 'danger');
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

// ── Map Rendering ─────────────────────────────────────────────
function renderMap() {
  const grid = document.getElementById('map-grid');
  grid.innerHTML = '';
  grid.style.gridTemplateColumns = `24px repeat(${MAP_COLS}, ${CELL_PX}px)`;
  grid.style.gridTemplateRows    = `24px repeat(${MAP_ROWS}, ${CELL_PX}px)`;

  const corner = document.createElement('div');
  corner.className = 'map-label';
  grid.appendChild(corner);

  COL_LABELS.forEach(l => {
    const el = document.createElement('div');
    el.className = 'map-label';
    el.textContent = l;
    grid.appendChild(el);
  });

  for (let r = 0; r < MAP_ROWS; r++) {
    const rl = document.createElement('div');
    rl.className = 'map-label';
    rl.textContent = r + 1;
    grid.appendChild(rl);

    for (let c = 0; c < MAP_COLS; c++) {
      const cell = document.createElement('div');
      cell.className = 'map-cell';
      if (ISLAND_SET.has(`${r},${c}`)) cell.classList.add('island-cell');
      grid.appendChild(cell);
    }
  }

  // Sectors
  const wrapper = document.querySelector('.map-wrapper');
  const sPerRow = Math.ceil(MAP_ROWS / SECTOR_SZ);
  const sPerCol = Math.ceil(MAP_COLS / SECTOR_SZ);
  for (let sr = 0; sr < sPerRow; sr++) {
    for (let sc = 0; sc < sPerCol; sc++) {
      const box = document.createElement('div');
      box.className = 'sector-box';
      const startR = sr * SECTOR_SZ, startC = sc * SECTOR_SZ;
      const endR   = Math.min(startR + SECTOR_SZ, MAP_ROWS);
      const endC   = Math.min(startC + SECTOR_SZ, MAP_COLS);
      box.style.position = 'absolute';
      box.style.left     = (1*16 + 24 + startC * CELL_PX) + 'px';
      box.style.top      = (1*16 + 24 + startR * CELL_PX) + 'px';
      box.style.width    = ((endC - startC) * CELL_PX) + 'px';
      box.style.height   = ((endR - startR) * CELL_PX) + 'px';
      const lblEl = document.createElement('div');
      lblEl.className   = 'sector-label';
      lblEl.textContent = sr * sPerCol + sc + 1;
      box.appendChild(lblEl);
      wrapper.appendChild(box);
    }
  }
}

// ── Canvas Drawing ────────────────────────────────────────────
function initCanvas() {
  canvas = document.getElementById('draw-canvas');
  ctx    = canvas.getContext('2d');

  const totalW = 24 + MAP_COLS * CELL_PX;
  const totalH = 24 + MAP_ROWS * CELL_PX;
  canvas.width  = totalW;
  canvas.height = totalH;

  canvas.addEventListener('mousedown',  startDraw);
  canvas.addEventListener('mousemove',  draw);
  canvas.addEventListener('mouseup',    stopDraw);
  canvas.addEventListener('mouseleave', stopDraw);

  // Touch support
  canvas.addEventListener('touchstart',  e => { e.preventDefault(); startDraw(e.touches[0]); }, {passive:false});
  canvas.addEventListener('touchmove',   e => { e.preventDefault(); draw(e.touches[0]); },      {passive:false});
  canvas.addEventListener('touchend',    stopDraw);
}

function getPos(e) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: (e.clientX - rect.left) * (canvas.width  / rect.width),
    y: (e.clientY - rect.top)  * (canvas.height / rect.height),
  };
}

function startDraw(e) {
  isDrawing = true;
  const p = getPos(e);
  lastX = p.x; lastY = p.y;
}

function draw(e) {
  if (!isDrawing) return;
  const p = getPos(e);

  ctx.beginPath();
  ctx.moveTo(lastX, lastY);
  ctx.lineTo(p.x,  p.y);

  if (currentTool === 'draw') {
    ctx.globalCompositeOperation = 'source-over';
    ctx.strokeStyle = ENEMY_TEAM === 'red' ? '#f87171aa' : '#60a5faaa';
    ctx.lineWidth   = 4;
    ctx.lineCap     = 'round';
  } else {
    ctx.globalCompositeOperation = 'destination-out';
    ctx.lineWidth = 20;
    ctx.lineCap   = 'round';
  }

  ctx.stroke();
  lastX = p.x; lastY = p.y;
}

function stopDraw() { isDrawing = false; }

function setTool(tool) {
  currentTool = tool;
  ['draw','erase'].forEach(t => {
    document.getElementById('tool-'+t).classList.toggle('active', t === tool);
  });
}

function clearAll() {
  if (!confirm('Clear all drawings?')) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
}

// ── Health ────────────────────────────────────────────────────
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

// ── Movement log ──────────────────────────────────────────────
function logMove(dir, type) {
  moveCount++;
  const log   = document.getElementById('movement-log');
  const entry = document.createElement('div');
  entry.className = `move-entry ${type}`;
  entry.innerHTML = `<span class="move-n">#${moveCount}</span><span class="move-d">${dir.toUpperCase()}</span>`;
  log.prepend(entry);
  while (log.children.length > 60) log.lastChild.remove();
}

// ── Event log ─────────────────────────────────────────────────
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

// ── Init ──────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  renderMap();
  initCanvas();
  renderHealth();
});
