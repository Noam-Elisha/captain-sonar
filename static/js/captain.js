/* ============================================================
   Captain Sonar — captain.js
   Captain sees own health, controls movement + weapons.
   Weapon charges are hidden (ask first mate).
   Enemy health is NOT shown to captain.
   ============================================================ */

// ── Globals (injected by template) ──────────────────────────
// GAME_ID, MY_NAME, MY_TEAM, MAP_ROWS, MAP_COLS, SECTOR_SZ, ISLANDS, COL_LABELS

const CELL_PX  = 32;
const MAP_PAD  = 16;
const ENEMY_TEAM = MY_TEAM === 'blue' ? 'red' : 'blue';

// ── State ────────────────────────────────────────────────────
let placementDone  = false;
let myPosition     = null;
let myTrail        = [];
let myMines        = [];
let myHealth       = 4;
let isMyTurn       = false;
let hasMoved       = false;
let isSurfaced     = false;
let engineerDone   = false;
let firstMateDone  = false;
let lastDirection  = null;

let targetMode       = null;   // 'torpedo' | 'mine' | null
let stealthDirection = null;
let stealthSteps     = 1;

const ISLAND_SET = new Set(ISLANDS.map(([r,c]) => `${r},${c}`));

// ── Socket ───────────────────────────────────────────────────
const socket = io();

socket.on('connect', () => {
  socket.emit('join_room',  {game_id: GAME_ID});
  socket.emit('join_game',  {game_id: GAME_ID, name: MY_NAME});
});

socket.on('game_state', state => { updateFromState(state); });

socket.on('game_started', () => { renderMap(); });

socket.on('game_phase', data => {
  document.getElementById('placement-overlay').style.display = 'none';
  placementDone = true;
  isMyTurn = (data.current_team === MY_TEAM);
  updateLock();
  logEvent(`Game started! ${data.current_team === MY_TEAM ? 'YOUR TURN' : data.current_team + ' goes first'}`, 'highlight');
});

socket.on('sub_placed', data => { logEvent(`${data.team} team placed their submarine`); });

socket.on('moved_self', data => {
  myPosition    = {row: data.row, col: data.col};
  myTrail       = data.trail.map(([r,c]) => ({row:r, col:c}));
  hasMoved      = true;
  lastDirection = data.direction || null;
  renderTrail();
  renderSubMarker();
  updateEndTurnBtn();
});

socket.on('turn_start', data => {
  isMyTurn      = (data.team === MY_TEAM);
  hasMoved      = false;
  isSurfaced    = false;
  engineerDone  = false;
  firstMateDone = false;
  lastDirection = null;
  updateLock();
  updateEndTurnBtn();
  if (isMyTurn) logEvent('YOUR TURN', 'highlight');
  else          logEvent(`${data.team} team's turn`);
});

socket.on('direction_announced', data => {
  if (data.team !== MY_TEAM) logEvent(`Enemy moved: ${data.direction.toUpperCase()}`);
});

socket.on('surface_announced', data => {
  const msg = data.team === MY_TEAM
    ? `You surfaced in sector ${data.sector} (−1 HP)`
    : `Enemy surfaced in sector ${data.sector}!`;
  logEvent(msg, data.team === MY_TEAM ? 'danger' : 'highlight');
  if (data.team === MY_TEAM) {
    myHealth   = data.health;
    isSurfaced = true;
    hasMoved   = true;
    renderHealth();
    showDiveBtn(true);
    updateLock();
    updateEndTurnBtn();
  }
});

socket.on('dive_ack', () => {
  isSurfaced = false;
  showDiveBtn(false);
  updateLock();
});

socket.on('torpedo_fired', data => {
  if (data.team !== MY_TEAM) {
    logEvent(`⚠ Enemy fired torpedo at row ${data.row+1}, col ${COL_LABELS[data.col]}!`, 'danger');
  } else {
    logEvent(`Torpedo fired at row ${data.row+1}, col ${COL_LABELS[data.col]}`);
  }
  showExplosion(data.row, data.col);
});

socket.on('mine_detonated', data => {
  logEvent(`💥 Mine detonated at row ${data.row+1}, col ${COL_LABELS[data.col]}`, 'danger');
  showExplosion(data.row, data.col);
});

socket.on('damage', data => {
  if (data.team === MY_TEAM) {
    myHealth = data.health;
    renderHealth();
    const cause = data.cause === 'system_failure' ? '⚡ System failure! ' : '💥 ';
    logEvent(`${cause}We took ${data.amount} damage! (${data.health} HP left)`, 'danger');
    if (data.health <= 0) setTimeout(() => showToast('GAME OVER — RED team wins!', true), 400);
  } else {
    const cause = data.cause === 'system_failure' ? '⚡ Enemy system failure ' : '💥 Enemy took ';
    logEvent(`${cause}${data.amount} damage`);
  }
});

socket.on('mine_placed_ack', data => {
  myMines = (data.mines || []).map(([r,c]) => ({row:r, col:c}));
  renderMines();
  logEvent('Mine placed!');
});

socket.on('stealth_announced', data => {
  if (data.team !== MY_TEAM)
    logEvent(`👻 Enemy used stealth (${data.steps} step${data.steps!==1?'s':''})`);
});
socket.on('sonar_announced', data => {
  if (data.team !== MY_TEAM) logEvent('Enemy used sonar on us');
});
socket.on('drone_announced', data => {
  if (data.team !== MY_TEAM) logEvent(`Enemy scanned sector ${data.sector} with drone`);
});

socket.on('game_over', data => {
  const won = (data.winner === MY_TEAM);
  showToast(won ? '🏆 YOU WIN!' : '💀 You were sunk…', !won);
  logEvent(`GAME OVER — ${data.winner} team wins!`, 'highlight');
  setLock(true, won ? 'Victory!' : 'Defeat');
});

socket.on('error', data => { showToast(data.msg, true); });

socket.on('bot_chat', data => {
  const icons = {captain:'🤖🎖', first_mate:'🤖⚙', engineer:'🤖🔧', radio_operator:'🤖📡'};
  logEvent(`${icons[data.role]||'🤖'} [${data.name}]: ${data.msg}`, 'bot');
});

// ── Update from full game state ──────────────────────────────
function updateFromState(state) {
  if (!state || !state.submarines) return;

  const mySub = state.submarines[MY_TEAM];
  if (mySub) {
    myHealth = mySub.health;
    if (mySub.position) myPosition = {row: mySub.position[0], col: mySub.position[1]};
    if (mySub.trail)    myTrail    = mySub.trail.map(([r,c]) => ({row:r, col:c}));
    if (mySub.mines)    myMines    = mySub.mines.map(([r,c]) => ({row:r, col:c}));
  }

  isMyTurn      = (state.current_team === MY_TEAM);
  const ts      = state.turn_state || {};
  hasMoved      = ts.moved          || false;
  engineerDone  = ts.engineer_done  || false;
  firstMateDone = ts.first_mate_done || false;
  lastDirection = ts.direction      || null;

  if (state.phase === 'placement' && !placementDone) {
    setLock(false);
    document.getElementById('placement-overlay').style.display = 'flex';
  } else if (state.phase === 'playing') {
    document.getElementById('placement-overlay').style.display = 'none';
    placementDone = true;
  }

  renderHealth();
  renderTrail();
  renderSubMarker();
  renderMines();
  updateLock();
  updateEndTurnBtn();
}

// ── Map Rendering ────────────────────────────────────────────
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
      cell.id        = `cell-${r}-${c}`;
      if (ISLAND_SET.has(`${r},${c}`)) {
        cell.classList.add('island-cell');
        cell.title = 'Island';
      } else {
        cell.addEventListener('click', () => onCellClick(r, c));
      }
      grid.appendChild(cell);
    }
  }
  renderSectors();
}

function renderSectors() {
  const grid    = document.getElementById('map-grid');
  const sPerRow = Math.ceil(MAP_ROWS / SECTOR_SZ);
  const sPerCol = Math.ceil(MAP_COLS / SECTOR_SZ);
  for (let sr = 0; sr < sPerRow; sr++) {
    for (let sc = 0; sc < sPerCol; sc++) {
      const box    = document.createElement('div');
      box.className = 'sector-box';
      const startR = sr * SECTOR_SZ, startC = sc * SECTOR_SZ;
      const endR   = Math.min(startR + SECTOR_SZ, MAP_ROWS);
      const endC   = Math.min(startC + SECTOR_SZ, MAP_COLS);
      box.style.position = 'absolute';
      box.style.left     = (MAP_PAD + 24 + startC * CELL_PX) + 'px';
      box.style.top      = (MAP_PAD + 24 + startR * CELL_PX) + 'px';
      box.style.width    = ((endC - startC) * CELL_PX) + 'px';
      box.style.height   = ((endR - startR) * CELL_PX) + 'px';
      const lblEl        = document.createElement('div');
      lblEl.className    = 'sector-label';
      lblEl.textContent  = sr * sPerCol + sc + 1;
      box.appendChild(lblEl);
      grid.parentElement.appendChild(box);
    }
  }
}

// ── Cell click handler ───────────────────────────────────────
function onCellClick(r, c) {
  if (!placementDone) {
    if (ISLAND_SET.has(`${r},${c}`)) return;
    socket.emit('place_sub', {game_id: GAME_ID, name: MY_NAME, row: r, col: c});
    document.getElementById('placement-overlay').innerHTML =
      '<div class="placement-card"><h2>Submarine placed!</h2><p>Waiting for enemy…</p></div>';
    return;
  }
  if (!isMyTurn) return;
  if (targetMode === 'torpedo') confirmTorpedo(r, c);
  else if (targetMode === 'mine') confirmMine(r, c);
}

// ── Trail & Sub rendering ─────────────────────────────────────
function renderTrail() {
  document.querySelectorAll('.trail-line').forEach(e => e.remove());
  if (myTrail.length < 2) return;
  for (let i = 1; i < myTrail.length; i++)
    drawTrailLine(myTrail[i-1].row, myTrail[i-1].col, myTrail[i].row, myTrail[i].col);
}

function drawTrailLine(fr, fc, tr, tc) {
  const grid = document.getElementById('map-grid');
  const line = document.createElement('div');
  line.className    = 'trail-line';
  line.style.position = 'absolute';
  const x1 = MAP_PAD + 24 + fc * CELL_PX + CELL_PX / 2;
  const y1 = MAP_PAD + 24 + fr * CELL_PX + CELL_PX / 2;
  const x2 = MAP_PAD + 24 + tc * CELL_PX + CELL_PX / 2;
  const y2 = MAP_PAD + 24 + tr * CELL_PX + CELL_PX / 2;
  if (fr === tr) {
    line.style.left = Math.min(x1,x2)+'px'; line.style.top = (y1-3)+'px';
    line.style.width = Math.abs(x2-x1)+'px'; line.style.height = '6px';
  } else {
    line.style.left = (x1-3)+'px'; line.style.top = Math.min(y1,y2)+'px';
    line.style.width = '6px'; line.style.height = Math.abs(y2-y1)+'px';
  }
  grid.parentElement.appendChild(line);
}

function renderSubMarker() {
  document.querySelectorAll('.sub-marker').forEach(e => e.remove());
  if (!myPosition) return;
  const cell = document.getElementById(`cell-${myPosition.row}-${myPosition.col}`);
  if (!cell) return;
  const marker = document.createElement('div');
  marker.className = 'sub-marker';
  const dot = document.createElement('div');
  dot.className = 'sub-dot';
  marker.appendChild(dot);
  cell.appendChild(marker);
}

function renderMines() {
  document.querySelectorAll('.mine-icon').forEach(e => e.remove());
  myMines.forEach((m, idx) => {
    const cell = document.getElementById(`cell-${m.row}-${m.col}`);
    if (!cell) return;
    const icon       = document.createElement('div');
    icon.className   = 'mine-icon';
    icon.textContent = '💣';
    icon.title       = 'Click to detonate';
    icon.style.cursor = 'pointer';
    icon.addEventListener('click', e => { e.stopPropagation(); detonateMine(idx); });
    cell.appendChild(icon);
  });

  const panel = document.getElementById('mine-det-panel');
  const list  = document.getElementById('mine-list');
  if (myMines.length > 0) {
    panel.style.display = 'block';
    list.innerHTML = '';
    myMines.forEach((m, idx) => {
      const entry = document.createElement('div');
      entry.className = 'mine-entry';
      entry.innerHTML = `<span>💣 Row ${m.row+1} / ${COL_LABELS[m.col]}</span>`;
      const btn       = document.createElement('button');
      btn.className   = 'mine-det-btn';
      btn.textContent = 'Detonate';
      btn.onclick     = () => detonateMine(idx);
      entry.appendChild(btn);
      list.appendChild(entry);
    });
  } else {
    panel.style.display = 'none';
  }
}

// ── Health rendering ─────────────────────────────────────────
function renderHealth() {
  const el = document.getElementById('own-health');
  if (!el) return;
  el.innerHTML = '';
  for (let i = 0; i < 4; i++) {
    const h = document.createElement('span');
    h.className   = `health-heart ${i < myHealth ? '' : 'empty'}`;
    h.textContent = i < myHealth ? '❤️' : '🖤';
    el.appendChild(h);
  }
}

// ── Movement ─────────────────────────────────────────────────
function doMove(direction) {
  if (!isMyTurn || hasMoved) return;
  socket.emit('captain_move', {game_id: GAME_ID, name: MY_NAME, direction});
  hasMoved = true;
  updateEndTurnBtn();
}

function doSurface() {
  if (!isMyTurn || hasMoved) return;
  if (!confirm('Surface your submarine? You will take 1 damage and reveal your sector.')) return;
  socket.emit('captain_surface', {game_id: GAME_ID, name: MY_NAME});
}

function doDive() { socket.emit('captain_dive', {game_id: GAME_ID, name: MY_NAME}); }

function showDiveBtn(show) {
  const btn = document.getElementById('btn-dive');
  if (show) btn.classList.remove('hidden'); else btn.classList.add('hidden');
  document.getElementById('move-panel').style.opacity       = show ? '0.4' : '1';
  document.getElementById('move-panel').style.pointerEvents = show ? 'none' : 'auto';
  if (show) {
    btn.style.display      = 'block';
    btn.style.pointerEvents = 'auto';
    btn.style.opacity       = '1';
  }
}

function doEndTurn() {
  if (!isMyTurn || !hasMoved) return;
  const needWait = (lastDirection !== null);
  if (needWait && (!engineerDone || !firstMateDone)) return;
  socket.emit('captain_end_turn', {game_id: GAME_ID, name: MY_NAME});
  isMyTurn = false; hasMoved = false; engineerDone = false;
  firstMateDone = false; lastDirection = null;
  updateLock(); updateEndTurnBtn();
}

// ── Torpedo / Mine targeting ──────────────────────────────────
function enterTargetMode(mode) {
  targetMode = mode;
  for (let r = 0; r < MAP_ROWS; r++) {
    for (let c = 0; c < MAP_COLS; c++) {
      if (ISLAND_SET.has(`${r},${c}`)) continue;
      const cell = document.getElementById(`cell-${r}-${c}`);
      if (!cell) continue;
      if (mode === 'torpedo') {
        const dist = myPosition ? Math.abs(r - myPosition.row) + Math.abs(c - myPosition.col) : 999;
        if (dist <= 4 && dist > 0) cell.classList.add('torpedo-target');
      } else if (mode === 'mine') {
        // Chebyshev distance 1 = all 8 adjacent cells (includes diagonals)
        const dr = myPosition ? Math.abs(r - myPosition.row) : 999;
        const dc = myPosition ? Math.abs(c - myPosition.col) : 999;
        if (Math.max(dr, dc) === 1) cell.classList.add('mine-target');
      }
    }
  }
  showToast(mode === 'torpedo' ? 'Click a target (range 4)' : 'Click cell to place mine (diagonals OK)');
}

function clearTargetMode() {
  targetMode = null;
  document.querySelectorAll('.torpedo-target').forEach(e => e.classList.remove('torpedo-target'));
  document.querySelectorAll('.mine-target').forEach(e => e.classList.remove('mine-target'));
}

function confirmTorpedo(r, c) {
  clearTargetMode();
  socket.emit('captain_torpedo', {game_id: GAME_ID, name: MY_NAME, row: r, col: c});
}
function confirmMine(r, c) {
  clearTargetMode();
  socket.emit('captain_mine_place', {game_id: GAME_ID, name: MY_NAME, row: r, col: c});
}
function detonateMine(idx) {
  socket.emit('captain_mine_det', {game_id: GAME_ID, name: MY_NAME, mine_index: idx});
}

function showExplosion(r, c) {
  const cell = document.getElementById(`cell-${r}-${c}`);
  if (!cell) return;
  const ring = document.createElement('div');
  ring.className = 'explosion-ring';
  cell.appendChild(ring);
  setTimeout(() => ring.remove(), 700);
}

// ── Stealth ───────────────────────────────────────────────────
function openStealth() {
  stealthDirection = null; stealthSteps = 1;
  renderStealthUI();
  document.getElementById('stealth-modal').classList.remove('hidden');
}
function closeStealth() { document.getElementById('stealth-modal').classList.add('hidden'); }
function setStealthDir(dir)  { stealthDirection = dir;  renderStealthUI(); }
function setStealthSteps(n)  { stealthSteps = n;        renderStealthUI(); }
function renderStealthUI() {
  ['north','south','west','east'].forEach(d => {
    const btn = document.getElementById(`sdir-${d}`);
    if (btn) btn.classList.toggle('active', d === stealthDirection);
  });
  [0,1,2,3,4].forEach(n => {
    const btn = document.getElementById(`ssteps-${n}`);
    if (btn) btn.classList.toggle('active', n === stealthSteps);
  });
  const prev = document.getElementById('stealth-preview');
  if (prev) {
    if (stealthSteps === 0) prev.textContent = 'Stay in place (0 steps)';
    else if (!stealthDirection) prev.textContent = 'Select a direction →';
    else {
      const labels = {north:'↑ North', south:'↓ South', west:'← West', east:'→ East'};
      prev.textContent = `Move ${stealthSteps} step${stealthSteps!==1?'s':''} ${labels[stealthDirection]}`;
    }
  }
  const execBtn = document.getElementById('btn-execute-stealth');
  if (execBtn) execBtn.disabled = (!stealthDirection && stealthSteps > 0);
}
function submitStealth() {
  const dir = stealthSteps === 0 ? 'north' : stealthDirection;
  closeStealth();
  socket.emit('captain_stealth', {game_id: GAME_ID, name: MY_NAME, direction: dir, steps: stealthSteps});
}

// ── UI helpers ────────────────────────────────────────────────
function setLock(locked, msg) {
  const overlay = document.getElementById('lock-overlay');
  if (locked) {
    overlay.classList.remove('hidden');
    document.getElementById('lock-msg').textContent = msg || 'Waiting…';
  } else {
    overlay.classList.add('hidden');
  }
}

function updateLock() {
  if (!placementDone) return;
  if (!isMyTurn || isSurfaced)
    setLock(!isSurfaced && !isMyTurn, isSurfaced ? '' : 'Waiting for enemy team…');
  else
    setLock(false);
}

function updateEndTurnBtn() {
  const btn      = document.getElementById('btn-end-turn');
  const needWait = (lastDirection !== null);
  const canEnd   = isMyTurn && hasMoved && (!needWait || engineerDone) && (!needWait || firstMateDone);
  btn.disabled   = !canEnd;
  updateRoleWaitStatus();
}

function updateRoleWaitStatus() {
  const el = document.getElementById('role-wait-status');
  if (!el) return;
  if (!isMyTurn || !hasMoved || lastDirection === null) { el.innerHTML = ''; return; }
  const engIcon = engineerDone  ? '✅' : '⏳';
  const fmIcon  = firstMateDone ? '✅' : '⏳';
  el.innerHTML = `<span class="wait-label">${engIcon} Engineer</span><span class="wait-label">${fmIcon} First Mate</span>`;
}

function logEvent(msg, cls) {
  const log   = document.getElementById('event-log');
  const entry = document.createElement('div');
  entry.className  = 'log-entry' + (cls ? ' ' + cls : '');
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
  renderHealth();
  setLock(true, 'Waiting for game to start…');
});
