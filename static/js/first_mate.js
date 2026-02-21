/* ============================================================
   Captain Sonar — first_mate.js
   ============================================================ */

// GAME_ID, MY_NAME, MY_TEAM injected by template

const ENEMY_TEAM = MY_TEAM === 'blue' ? 'red' : 'blue';

const SYS_DEF = {
  torpedo: {label:'🚀 Torpedo', max:3, color:'red',    desc:'Fires at targets within range 4'},
  mine:    {label:'💣 Mine',    max:3, color:'red',    desc:'Place on adjacent cell, detonate anytime'},
  sonar:   {label:'📡 Sonar',  max:3, color:'green',  desc:'Ask row / col / sector'},
  drone:   {label:'🛸 Drone',  max:4, color:'green',  desc:'Confirm if enemy is in a sector'},
  stealth: {label:'👻 Stealth',max:5, color:'yellow', desc:'Move silently 0–4 steps'},
};

let systems    = {torpedo:{charge:0}, mine:{charge:0}, sonar:{charge:0}, drone:{charge:0}, stealth:{charge:0}};
let myHealth   = 4;
let enemyHealth= 4;
let canCharge  = false;   // true after captain moves, before FM charges

const socket = io();

socket.on('connect', () => {
  socket.emit('join_room', {game_id: GAME_ID});
  socket.emit('join_game', {game_id: GAME_ID, name: MY_NAME});
});

socket.on('game_state', state => {
  if (!state || !state.submarines) return;
  const mySub    = state.submarines[MY_TEAM];
  const enemySub = state.submarines[ENEMY_TEAM];
  if (mySub)    { myHealth    = mySub.health; systems = mySub.systems || systems; }
  if (enemySub)   enemyHealth = enemySub.health;

  const isMyTurn = (state.current_team === MY_TEAM);
  const moved    = state.turn_state && state.turn_state.moved;
  const fmDone   = state.turn_state && state.turn_state.first_mate_done;
  const dir      = state.turn_state && state.turn_state.direction;
  canCharge = isMyTurn && moved && !fmDone && !!dir;

  renderAll();
});

socket.on('systems_update', data => {
  systems   = data.systems;
  canCharge = false;        // reset; server will send can_charge again on next move
  renderAll();
  logEvent('System charged!', 'highlight');
});

socket.on('can_charge', () => {
  canCharge = true;
  renderSystems();
  logEvent('Captain moved — charge a system now!', 'highlight');
  document.getElementById('charge-overlay').classList.add('hidden');
});

socket.on('turn_start', data => {
  canCharge = false;
  renderSystems();
  if (data.team !== MY_TEAM) {
    document.getElementById('charge-overlay').classList.remove('hidden');
    logEvent(`${data.team} team's turn`);
  } else {
    document.getElementById('charge-overlay').classList.add('hidden');
    logEvent('OUR TURN — wait for captain to move', 'highlight');
  }
});

socket.on('damage', data => {
  if (data.team === MY_TEAM) myHealth    = data.health;
  else                        enemyHealth = data.health;
  renderHealth();
  logEvent(`💥 ${data.team === MY_TEAM ? 'We' : 'Enemy'} took ${data.amount} damage (${data.health} HP)`, 'danger');
});

socket.on('surface_announced', data => {
  if (data.team === MY_TEAM) myHealth    = data.health;
  else                        enemyHealth = data.health;
  renderHealth();
  logEvent(`${data.team} surfaced in sector ${data.sector}`, data.team === MY_TEAM ? 'danger' : '');
});

socket.on('game_over', data => {
  const won = data.winner === MY_TEAM;
  logEvent(`GAME OVER — ${data.winner} wins!`, 'highlight');
  showToast(won ? '🏆 YOU WIN!' : '💀 Defeat…', !won);
});

socket.on('error', data => showToast(data.msg, true));

// ── Render ────────────────────────────────────────────────────
function renderAll() {
  renderHealth();
  renderSystems();
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

function renderSystems() {
  const panel = document.getElementById('systems-panel');
  panel.innerHTML = '';
  Object.entries(SYS_DEF).forEach(([sys, meta]) => {
    const s     = systems[sys] || {charge:0, max:meta.max, ready:false};
    const cur   = s.charge || 0;
    const max   = s.max    || meta.max;
    const ready = s.ready  || (cur >= max);

    const card  = document.createElement('div');
    card.className = `sys-card sys-${sys}${canCharge && !ready ? ' can-charge' : ''}${ready ? ' is-ready' : ''}`;

    // Ready badge
    const badge = document.createElement('div');
    badge.className = 'sys-ready-badge' + (ready ? '' : ' hidden');
    badge.textContent = '✓ READY';
    card.appendChild(badge);

    // Header
    const hdr = document.createElement('div');
    hdr.className = 'sys-card-header';
    hdr.innerHTML = `
      <span class="sys-card-name">${meta.label}</span>
      <span class="sys-card-cost">${cur}/${max}</span>
    `;
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

    // Charge button
    const btn = document.createElement('button');
    btn.className = 'btn-charge';
    btn.textContent = ready ? 'Fully Charged' : 'Charge';
    btn.disabled = !canCharge || ready;
    btn.onclick  = () => chargeSystem(sys);
    card.appendChild(btn);

    panel.appendChild(card);
  });
}

function chargeSystem(sys) {
  if (!canCharge) return;
  socket.emit('first_mate_charge', {game_id: GAME_ID, name: MY_NAME, system: sys});
  canCharge = false;   // optimistic: prevent double-charge until server updates
  renderSystems();
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
  renderAll();
});
