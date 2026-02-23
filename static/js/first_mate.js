/* ============================================================
   Captain Sonar — first_mate.js
   FM charges systems and activates green systems (sonar/drone).
   ============================================================ */

// GAME_ID, MY_NAME, MY_TEAM, MAP_ROWS, MAP_COLS injected by template

const ENEMY_TEAM = MY_TEAM === 'blue' ? 'red' : 'blue';

const SYS_DEF = {
  torpedo: {label:'🚀 Torpedo', max:6, color:'red',    desc:'Captain fires — range 4'},
  mine:    {label:'💣 Mine',    max:6, color:'red',    desc:'Captain places N/S/E/W adjacent (after moving)'},
  sonar:   {label:'📡 Sonar',  max:6, color:'green',  desc:'YOU activate — ask row/col/sector'},
  drone:   {label:'🛸 Drone',  max:6, color:'green',  desc:'YOU activate — confirm sector'},
  stealth: {label:'👻 Stealth',max:4, color:'yellow', desc:'Captain moves silently 0–4 steps'},
};

let systems       = {torpedo:{charge:0}, mine:{charge:0}, sonar:{charge:0}, drone:{charge:0}, stealth:{charge:0}};
let myHealth      = 4;
let canCharge     = false;
let isMyTurn      = false;
let systemUsed    = false;
let movedThisTurn = false;  // RULEBOOK TBT: systems activate after course announcement

const socket = io();

socket.on('connect', () => {
  socket.emit('join_room', {game_id: GAME_ID});
  socket.emit('join_game', {game_id: GAME_ID, name: MY_NAME});
});

socket.on('game_state', state => {
  if (!state || !state.submarines) return;
  const mySub = state.submarines[MY_TEAM];
  if (mySub) { myHealth = mySub.health; systems = mySub.systems || systems; }

  isMyTurn      = (state.current_team === MY_TEAM);
  const ts      = state.turn_state || {};
  const moved      = ts.moved || false;
  const fmDone     = ts.first_mate_done || false;
  const dir        = ts.direction;
  const stealthDir = ts.stealth_direction; // private — only own team's FM sees this
  systemUsed    = ts.system_used || false;
  movedThisTurn = moved;
  // canCharge on normal move OR stealth move (but NOT after surfacing)
  canCharge     = isMyTurn && moved && !fmDone && !!(dir || stealthDir);

  renderAll();
});

socket.on('systems_update', data => {
  systems = data.systems;
  renderSystems();
  // Only log a charge message when a charge actually happened (not on system consumption)
  if (data.reason === 'charge' && data.system) {
    logEvent(`⚡ ${SYS_DEF[data.system]?.label || data.system} charged +1`, 'highlight');
  }
});

socket.on('can_charge', data => {
  canCharge = true;
  renderSystems();
  const msg = (data && data.is_stealth)
    ? '👻 Stealth move — charge a system now!'
    : 'Captain moved — charge a system now!';
  logEvent(msg, 'highlight');
  document.getElementById('charge-overlay').classList.add('hidden');
});

socket.on('turn_start', data => {
  canCharge     = false;
  systemUsed    = false;
  movedThisTurn = false;
  isMyTurn      = (data.team === MY_TEAM);
  renderSystems();
  if (data.team !== MY_TEAM) {
    document.getElementById('charge-overlay').classList.remove('hidden');
    // Don't clutter the log with "X team's turn" every turn
  } else {
    document.getElementById('charge-overlay').classList.add('hidden');
    logEvent('OUR TURN — wait for captain to move, then charge', 'highlight');
  }
});

socket.on('damage', data => {
  if (data.team === MY_TEAM) {
    myHealth = data.health;
    renderHealth();
    const cause = data.cause === 'system_failure' ? '⚡ System failure! '
                : data.cause === 'surface'         ? '🌊 Surfaced! '      : '💥 ';
    logEvent(`${cause}We took ${data.amount} damage (${data.health} HP)`, 'danger');
  } else {
    const cause = data.cause === 'system_failure' ? '⚡ Enemy system failure '
                : data.cause === 'surface'         ? '🌊 Enemy surfaced! '  : '💥 Enemy took ';
    logEvent(`${cause}${data.amount} damage`, 'danger');
  }
});

socket.on('surface_announced', data => {
  // RULEBOOK: surfacing costs 1 HP (announced via damage event), enemy gets 3 bonus turns
  if (data.team === MY_TEAM) {
    logEvent(`You surfaced in sector ${data.sector} — trail + engineering cleared. Enemy gets 3 bonus turns!`, 'warning');
  } else {
    logEvent(`Enemy surfaced in sector ${data.sector}! We get 3 bonus turns!`, 'highlight');
  }
});

socket.on('sonar_announced', data => {
  if (data.team === MY_TEAM) {
    logEvent('📡 Sonar activated — waiting for enemy captain to respond…');
  } else {
    logEvent('📡 Enemy used sonar on us — our captain must respond');
  }
});

// RULEBOOK sonar_result: 2 pieces of info from enemy captain (1 true, 1 false)
// Broadcast to whole room — use data.target to determine if we are the activating team
socket.on('sonar_result', data => {
  const fmtVal = (type, val) => {
    if (type === 'row') return `Row ${val + 1}`;
    if (type === 'col') return `Col ${val + 1}`;
    return `Sector ${val}`;
  };
  const info1 = fmtVal(data.type1, data.val1);
  const info2 = fmtVal(data.type2, data.val2);
  if (data.target === MY_TEAM) {
    // Our sonar — enemy captain responded with these values
    showToast(`Sonar: "${info1}" and "${info2}" — 1 is true, 1 is false!`);
    logEvent(`📡 Sonar: enemy said "${info1}" AND "${info2}" (one is true, one is false — deduce!)`, 'highlight');
    systemUsed = true;
    renderSystems();
  } else {
    // Enemy sonar — they reported these values (both teams hear the result)
    logEvent(`📡 Enemy sonar result: they reported "${info1}" and "${info2}"`);
  }
});

// RULEBOOK blackout: no valid moves → auto-surface
socket.on('blackout_announced', data => {
  if (data.team === MY_TEAM) {
    logEvent('⚠ BLACKOUT — no valid moves, sub surfaced automatically!', 'danger');
  } else {
    logEvent('⚠ Enemy blackout — they surfaced automatically! We get 3 bonus turns!', 'highlight');
  }
});

// Circuit cleared event
socket.on('circuit_cleared', data => {
  if (data.team === MY_TEAM) {
    logEvent(`🔄 Circuit C${data.circuit} completed — nodes cleared (no damage)`, 'good');
  }
});

// Broadcast to whole room — use data.target to determine if we are the activating team
socket.on('drone_result', data => {
  if (data.target === MY_TEAM) {
    // Our drone result
    showToast(data.in_sector ? `🛸 Drone: Enemy IS in sector ${data.ask_sector}! 🎯` : `🛸 Drone: Enemy NOT in sector ${data.ask_sector}`);
    logEvent(`🛸 Drone sector ${data.ask_sector}: ${data.in_sector ? 'YES — CONTACT! 🎯' : 'NO — clear'}`, 'highlight');
    systemUsed = true;
    renderSystems();
  } else {
    // Enemy drone (we hear the result too — both teams hear in physical game)
    logEvent(`🛸 Enemy drone sector ${data.ask_sector}: ${data.in_sector ? 'contact on us!' : 'clear'}`);
  }
});

socket.on('game_over', data => {
  const won = data.winner === MY_TEAM;
  logEvent(`GAME OVER — ${data.winner} wins!`, 'highlight');
  showToast(won ? '🏆 YOU WIN!' : '💀 Defeat…', !won);
});

socket.on('error', data => showToast(data.msg, true));

socket.on('bot_chat', data => {
  const icons = {captain:'🤖🎖', first_mate:'🤖⚙', engineer:'🤖🔧', radio_operator:'🤖📡'};
  logEvent(`${icons[data.role]||'🤖'} [${data.name}]: ${data.msg}`, 'bot');
});

// ── Render ────────────────────────────────────────────────────
function renderAll() { renderHealth(); renderSystems(); }

function renderHealth() {
  const el = document.getElementById('own-health');
  if (!el) return;
  el.innerHTML = '';
  for (let i = 0; i < 4; i++) {
    const s = document.createElement('span');
    s.className   = 'health-heart' + (i < myHealth ? '' : ' empty');
    s.textContent = i < myHealth ? '❤️' : '🖤';
    el.appendChild(s);
  }
}

function renderSystems() {
  const panel = document.getElementById('systems-panel');
  panel.innerHTML = '';
  Object.entries(SYS_DEF).forEach(([sys, meta]) => {
    const s     = systems[sys] || {charge:0, max:meta.max, ready:false};
    const cur   = s.charge || 0;
    const max   = s.max    || meta.max;
    const ready = s.ready  || (cur >= max);
    const isGreen     = (sys === 'sonar' || sys === 'drone');
    // RULEBOOK TBT: FM can only activate systems AFTER the captain has announced a course
    const canActivate = isGreen && ready && isMyTurn && !systemUsed && movedThisTurn;

    const card     = document.createElement('div');
    card.className = `sys-card sys-${sys}${canCharge && !ready ? ' can-charge' : ''}${ready ? ' is-ready' : ''}${canActivate ? ' can-activate' : ''}`;

    // Badge
    if (canActivate) {
      const badge     = document.createElement('div');
      badge.className = 'sys-act-badge';
      badge.textContent = '▶ READY TO ACTIVATE';
      card.appendChild(badge);
    } else if (ready) {
      const badge     = document.createElement('div');
      badge.className = 'sys-ready-badge';
      badge.textContent = isGreen ? '✓ READY' : '✓ READY';
      card.appendChild(badge);
    }

    // Header
    const hdr = document.createElement('div');
    hdr.className = 'sys-card-header';
    hdr.innerHTML = `<span class="sys-card-name">${meta.label}</span><span class="sys-card-cost">${cur}/${max}</span>`;
    card.appendChild(hdr);

    // Dots
    const dotsRow = document.createElement('div');
    dotsRow.className = 'charge-dots';
    for (let i = 0; i < max; i++) {
      const dot = document.createElement('div');
      dot.className = 'c-dot' + (i < cur ? ' filled' : '');
      dotsRow.appendChild(dot);
    }
    card.appendChild(dotsRow);

    // Desc
    const desc = document.createElement('div');
    desc.className   = 'sys-desc';
    desc.textContent = meta.desc;
    card.appendChild(desc);

    // Charge button
    const btn     = document.createElement('button');
    btn.className = 'btn-charge';
    btn.textContent = ready ? 'Fully Charged' : 'Charge +1';
    btn.disabled    = !canCharge || ready;
    btn.onclick     = () => chargeSystem(sys);
    card.appendChild(btn);

    // Activate button for green systems
    if (isGreen) {
      const actBtn     = document.createElement('button');
      actBtn.className = 'btn-activate';
      actBtn.textContent = sys === 'sonar' ? '📡 Use Sonar' : '🛸 Use Drone';
      actBtn.disabled    = !canActivate;
      actBtn.onclick     = () => activateSystem(sys);
      card.appendChild(actBtn);
    }

    panel.appendChild(card);
  });
}

function chargeSystem(sys) {
  if (!canCharge) return;
  socket.emit('first_mate_charge', {game_id: GAME_ID, name: MY_NAME, system: sys});
  canCharge = false;
  renderSystems();
}

function activateSystem(sys) {
  if (sys === 'sonar') openSonar();
  else if (sys === 'drone') openDrone();
}

// ── Sonar (RULEBOOK: just activate — enemy captain responds) ──────────────────
function openSonar()  { document.getElementById('sonar-modal').classList.remove('hidden'); }
function closeSonar() { document.getElementById('sonar-modal').classList.add('hidden');    }
function submitSonar() {
  closeSonar();
  // RULEBOOK: no ask params — enemy captain provides 2 pieces of info interactively
  socket.emit('first_mate_sonar', {game_id: GAME_ID, name: MY_NAME});
}

// ── Drone ─────────────────────────────────────────────────────
function openDrone() {
  document.getElementById('drone-modal').classList.remove('hidden');
  renderDroneMap();
}

function renderDroneMap() {
  const canvas = document.getElementById('drone-map-canvas');
  if (!canvas) return;

  const MINI_PX = 10; // pixels per map cell in the mini-map
  const totalW  = MAP_COLS * MINI_PX;
  const totalH  = MAP_ROWS * MINI_PX;
  canvas.width  = totalW;
  canvas.height = totalH;
  canvas.style.width  = Math.min(totalW, 280) + 'px';
  canvas.style.height = Math.round(totalH * Math.min(totalW, 280) / totalW) + 'px';
  canvas.style.imageRendering = 'pixelated';
  canvas.style.cursor = 'pointer';
  canvas.style.border = '1px solid rgba(148,163,184,.3)';
  canvas.style.borderRadius = '4px';
  canvas.style.display = 'block';
  canvas.style.margin = '0 auto .5rem';

  const ctx = canvas.getContext('2d');
  const spr = Math.ceil(MAP_COLS / SECTOR_SZ);  // sectors per row
  const spc = Math.ceil(MAP_ROWS / SECTOR_SZ);  // sectors per col
  const islandSet = (typeof ISLANDS !== 'undefined')
    ? new Set(ISLANDS.map(([r, c]) => `${r},${c}`))
    : new Set();

  // Draw water/island cells
  for (let r = 0; r < MAP_ROWS; r++) {
    for (let c = 0; c < MAP_COLS; c++) {
      ctx.fillStyle = islandSet.has(`${r},${c}`) ? '#475569' : '#0f172a';
      ctx.fillRect(c * MINI_PX, r * MINI_PX, MINI_PX, MINI_PX);
    }
  }

  // Sector color palette
  const PALETTE = [
    'rgba(99,102,241,0.30)', 'rgba(34,197,94,0.30)',
    'rgba(239,68,68,0.30)',  'rgba(245,158,11,0.30)',
  ];

  // Draw sector overlays + sector number labels
  for (let sr = 0; sr < spc; sr++) {
    for (let sc = 0; sc < spr; sc++) {
      const secNum = sr * spr + sc + 1;
      const x = sc * SECTOR_SZ * MINI_PX;
      const y = sr * SECTOR_SZ * MINI_PX;
      const w = Math.min(SECTOR_SZ, MAP_COLS - sc * SECTOR_SZ) * MINI_PX;
      const h = Math.min(SECTOR_SZ, MAP_ROWS - sr * SECTOR_SZ) * MINI_PX;

      // Sector tint
      ctx.fillStyle = PALETTE[(secNum - 1) % PALETTE.length];
      ctx.fillRect(x, y, w, h);

      // Sector border
      ctx.strokeStyle = 'rgba(148,163,184,0.55)';
      ctx.lineWidth = 1.5;
      ctx.strokeRect(x + 0.75, y + 0.75, w - 1.5, h - 1.5);

      // Sector number (large, centred)
      const fontSize = Math.min(w, h) * 0.45;
      ctx.font      = `bold ${fontSize}px sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = 'rgba(255,255,255,0.90)';
      ctx.fillText(String(secNum), x + w / 2, y + h / 2);
    }
  }

  // Click: determine which sector was clicked and submit
  canvas.onclick = (e) => {
    const rect  = canvas.getBoundingClientRect();
    const scaleX = canvas.width  / rect.width;
    const scaleY = canvas.height / rect.height;
    const col = Math.floor((e.clientX - rect.left) * scaleX / MINI_PX);
    const row = Math.floor((e.clientY - rect.top)  * scaleY / MINI_PX);
    if (col < 0 || col >= MAP_COLS || row < 0 || row >= MAP_ROWS) return;
    const secR   = Math.floor(row / SECTOR_SZ);
    const secC   = Math.floor(col / SECTOR_SZ);
    const secNum = secR * spr + secC + 1;
    submitDrone(secNum);
  };
}

function closeDrone()        { document.getElementById('drone-modal').classList.add('hidden'); }
function submitDrone(sector) {
  closeDrone();
  socket.emit('first_mate_drone', {game_id: GAME_ID, name: MY_NAME, sector});
}

// ── Helpers ───────────────────────────────────────────────────
function logEvent(msg, cls) {
  const log   = document.getElementById('event-log');
  const entry = document.createElement('div');
  entry.className   = 'log-entry' + (cls ? ' ' + cls : '');
  entry.textContent = msg;
  log.prepend(entry);
  while (log.children.length > 50) log.lastChild.remove();
}

function showToast(msg, isError) {
  const t       = document.getElementById('result-toast');
  t.textContent = msg;
  t.className   = 'result-toast' + (isError ? ' error' : '');
  setTimeout(() => t.classList.add('hidden'), 4000);
}

document.addEventListener('DOMContentLoaded', () => { renderAll(); });
