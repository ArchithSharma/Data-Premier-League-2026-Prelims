const charts = {};
function killChart(id){ if(charts[id]){ try{ charts[id].destroy(); }catch(e){} delete charts[id]; } }
// Wrap every chart creation so a blocked/failed Chart.js CDN load (ad-blockers commonly
// block anything with "chart" in the URL) degrades gracefully instead of throwing and
// halting all the JS that runs after it (which is what breaks the whole page).
function safeChart(id, canvasId, config){
  try{
    if(typeof Chart === 'undefined') throw new Error('Chart.js failed to load (blocked by network/ad-blocker?)');
    const el = document.getElementById(canvasId);
    if(!el) return;
    charts[id] = new Chart(el, config);
  }catch(e){
    console.warn('Chart skipped:', canvasId, e.message);
    const el = document.getElementById(canvasId);
    if(el && el.parentElement) el.parentElement.innerHTML =
      '<p class="muted">⚠️ Chart could not load (likely blocked by an ad-blocker or network filter) — the numbers below are unaffected.</p>' + (el.parentElement.querySelector('table') ? '' : '');
  }
}
function safeRun(fn, label){ try{ fn(); }catch(e){ console.warn('Render skipped:', label, e.message); } }
function fmt(x){ return x===null||x===undefined ? '–' : (Math.round(x*100)/100).toLocaleString(); }
function colorFor(v){ return v>=0 ? 'rgba(63,178,127,.75)' : 'rgba(226,86,79,.75)'; }
function years(obj){ return Object.keys(obj||{}).sort(); }

// ---------- tabs ----------
document.querySelectorAll('.tab').forEach(t=>{
  t.onclick = ()=>{
    document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));
    t.classList.add('active');
    document.getElementById(t.dataset.panel).classList.add('active');
  };
});

// ============================================================ PLAYERS
const playerNames = Object.keys(DATA.players).sort();
document.getElementById('playerList').innerHTML = playerNames.map(n=>`<option value="${n}">`).join('');
const searchEl = document.getElementById('playerSearch');
searchEl.addEventListener('change', ()=> renderPlayer(searchEl.value));
searchEl.addEventListener('input', ()=>{ if(DATA.players[searchEl.value]) renderPlayer(searchEl.value); });

function statCard(label, value, cls){ return `<div class="card"><div class="v ${cls||''}">${value}</div><div class="l">${label}</div></div>`; }

function renderPlayer(name){
  const p = DATA.players[name];
  const c = document.getElementById('playerContent');
  if(!p){ c.innerHTML=''; return; }
  const logo = p.team && LOGOS[p.team] ? `<img class="logo" src="${LOGOS[p.team]}">` : '';
  let html = `<div class="namebar" style="margin:10px 0"><h2 style="margin:0">${name}</h2>${logo}<span class="muted">${p.team||''}</span></div>`;
  const roles = [];
  if(p.batting) roles.push('batting'); if(p.bowling) roles.push('bowling');
  html += `<div class="tabs" id="roleTabs">${roles.map((r,i)=>`<div class="tab role-tab ${i===0?'active':''}" data-role="${r}">${r[0].toUpperCase()+r.slice(1)}</div>`).join('')}</div>`;
  roles.forEach((r,i)=> html += `<div class="role-panel" data-role="${r}" style="display:${i===0?'block':'none'}"></div>`);
  c.innerHTML = html;
  document.querySelectorAll('.role-tab').forEach(t=> t.onclick=()=>{
    document.querySelectorAll('.role-tab').forEach(x=>x.classList.remove('active'));
    t.classList.add('active');
    document.querySelectorAll('.role-panel').forEach(x=> x.style.display = x.dataset.role===t.dataset.role?'block':'none');
  });
  roles.forEach(r=> renderRole(r, p[r], name));
}

function renderRole(role, d, playerName){
  const el = document.querySelector(`.role-panel[data-role="${role}"]`);
  const mean = d.mean_impact_per_ball;
  let html = `<div class="cards">
    ${statCard('Impact vs mean / ball', fmt(d.impact_vs_mean), d.impact_vs_mean>=0?'pos':'neg')}
    ${statCard('Role mean / ball', fmt(mean))}
    ${statCard('Percentile', d.percentile? d.percentile.toFixed(0)+'th':'–')}
    ${statCard('Balls', d.career_balls)}
    ${statCard(role==='batting'?'Runs':'Runs conceded', d.career_runs)}
  </div>`;
  html += `<div class="section"><h3>Per-game Impact vs role mean / ball (bubble size = venue factor)</h3><div class="chart-wrap"><canvas id="gameChart-${role}"></canvas></div>
    <p class="note">Venue factor &gt;1 = hitter-friendly ground that game; &lt;1 = bowler-friendly. Bigger/brighter bubble = further from a neutral (1.0) venue.</p></div>`;

  if(role==='batting'){
    html += yearBlock('shotBlock', d.shot_types, 'Shot type mix & Impact vs median by year', 'shot');
    html += yearBlock('wagonBlock', d.wagon_zones, 'Wagon-zone share & Impact vs median by year (batter\'s-eye view)', 'zone', true);
  } else {
    html += `<div class="section"><h3>Line/Length grid — % of deliveries & Impact vs median</h3><div id="llGrid"></div></div>`;
    html += yearBlock('speedBlock', d.speed, 'Bowl speed mix & Impact vs median by year', 'speed');
    html += `<div class="section"><h3>Delivery-to-delivery variation</h3><div id="varBlock"></div>
      <p class="note">Distance = how different each ball was from the bowler's own previous delivery on the length/line grid (0 = repeated the same ball). Compared to the league average that year.</p></div>`;
  }
  el.innerHTML = html;

  // per-game chart
  killChart('gameChart-'+role);
  const games = d.games;
  safeChart('gameChart-'+role, 'gameChart-'+role, {
    type:'bubble',
    data:{ datasets:[{
      label:'Impact',
      data: games.map((g,i)=>({x:i, y:g.balls ? g.impact/g.balls-mean : null, r: g.venue_factor? Math.min(14, 4+Math.abs(g.venue_factor-1)*40) : 5, meta:g})),
        backgroundColor: games.map(g=>colorFor(g.balls ? g.impact/g.balls-mean : 0)),
    }]},
    options:{ responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{display:false},
        tooltip:{ callbacks:{ label:(ctx)=>{ const g=ctx.raw.meta;
          return `${g.date} vs ${g.opponent||'?'} @ ${g.ground}: ${fmt(g.impact/g.balls-mean)} vs mean/ball (${g.balls} balls)`+
                 (g.venue_factor? `, venue factor ${g.venue_factor}`:''); } } } },
      scales:{ x:{ title:{display:true,text:'Match # (chronological)'}, ticks:{display:false} },
               y:{ title:{display:true,text:'Impact vs role mean / ball'} } } }
  });

  if(role==='batting'){ setupYearBlock('shotBlock', d.shot_types, 'shot', 'Shot', mean); setupYearBlock('wagonBlock', d.wagon_zones, 'zone', 'Zone', mean); }
  else {
    renderLLGrid('llGrid', d.line_length, mean);
    setupYearBlock('speedBlock', d.speed, 'speed', 'Speed (kph)', mean);
    renderVariation('varBlock', d.variation, mean);
  }
}

function yearBlock(id, obj, title, key, isZone){
  return `<div class="section"><h3>${title}</h3>
    <div class="row"><select id="${id}-year"></select></div>
    <div class="chart-wrap"><canvas id="${id}-chart"></canvas></div></div>`;
}
function setupYearBlock(id, obj, key, keyLabel, mean){
  const yrs = years(obj);
  const sel = document.getElementById(id+'-year');
  sel.innerHTML = yrs.map(y=>`<option value="${y}">${y}</option>`).join('');
  sel.onchange = ()=> drawYearChart(id, obj[sel.value], key, keyLabel, mean);
  if(yrs.length) drawYearChart(id, obj[yrs[yrs.length-1]], key, keyLabel, mean);
}
function drawYearChart(id, rows, key, keyLabel, mean=0){
  killChart(id+'-chart');
  rows = (rows||[]).slice().sort((a,b)=> (key==='zone') ? a.zone-b.zone : b.pct-a.pct).slice(0, 14);
  const labels = rows.map(r=> key==='zone' ? 'Zone '+r.zone : r[key]);
  safeChart(id+'-chart', id+'-chart', {
    data:{ labels, datasets:[
      { type:'bar', label:'% of deliveries', data: rows.map(r=>r.pct), backgroundColor:'rgba(91,140,255,.55)', yAxisID:'y' },
      { type:'line', label:'Impact vs role mean / ball', data: rows.map(r=>r.impact_vs_mean!=null?r.impact_vs_mean:r.impact-mean), borderColor:'#f2b84b', backgroundColor:'#f2b84b', yAxisID:'y1', tension:.2 },
    ]},
    options:{ responsive:true, maintainAspectRatio:false,
      scales:{ x:{ ticks:{ autoSkip:false, maxRotation:40, minRotation:20 } },
               y:{ position:'left', title:{display:true,text:'% of deliveries'} },
               y1:{ position:'right', title:{display:true,text:'Impact vs mean / ball'}, grid:{drawOnChartArea:false} } } }
  });
}

const LENGTH_ORDER = ['SHORT','SHORT_OF_A_GOOD_LENGTH','GOOD_LENGTH','FULL','YORKER','FULL_TOSS'];
const LINE_ORDER = ['DOWN_LEG','ON_THE_STUMPS','OUTSIDE_OFFSTUMP','WIDE_OUTSIDE_OFFSTUMP'];
function renderLLGrid(id, obj, mean=0){
  const yrs = years(obj);
  let html = `<div class="row"><select id="${id}-year">${yrs.map(y=>`<option value="${y}">${y}</option>`).join('')}</select></div><div id="${id}-tbl"></div>`;
  document.getElementById(id).innerHTML = html;
  const sel = document.getElementById(id+'-year');
  const draw = ()=>{
    const rows = obj[sel.value]||[];
    const map = {}; rows.forEach(r=> map[r.length+'|'+r.line]=r);
    const relativeValue = r=>r.impact_vs_mean!=null?r.impact_vs_mean:r.impact-mean;
    const maxAbs = Math.max(0.5, ...rows.map(r=>Math.abs(relativeValue(r))));
    let t = '<table class="grid-heat"><tr><th></th>'+LINE_ORDER.map(l=>`<th>${l.replace(/_/g,' ')}</th>`).join('')+'</tr>';
    LENGTH_ORDER.forEach(len=>{
      t += `<tr><th>${len.replace(/_/g,' ')}</th>`;
      LINE_ORDER.forEach(line=>{
        const r = map[len+'|'+line];
        if(!r){ t+='<td>–</td>'; return; }
        const relativeImpact = relativeValue(r);
        const alpha = Math.min(1, Math.abs(relativeImpact)/maxAbs);
        const bg = relativeImpact>=0 ? `rgba(63,178,127,${0.15+0.6*alpha})` : `rgba(226,86,79,${0.15+0.6*alpha})`;
        t += `<td style="background:${bg}">${relativeImpact.toFixed(2)}<br><span class="muted">${r.pct.toFixed(1)}%</span></td>`;
      });
      t += '</tr>';
    });
    t += '</table>';
    document.getElementById(id+'-tbl').innerHTML = t;
  };
  sel.onchange = draw;
  if(yrs.length) draw();
}

function renderVariation(id, obj, mean=0){
  const yrs = years(obj);
  let html = '<div class="cards">';
  yrs.forEach(y=>{
    const v = obj[y], lg = (DATA.league_variation||{})[y];
    html += `<div class="card"><div class="l">${y} avg distance</div><div class="v">${v.mean_dist!=null?v.mean_dist.toFixed(2):'–'}</div>
      <div class="muted">league: ${lg?lg.mean_dist.toFixed(2):'–'}</div></div>`;
    const relativeImpact = v.mean_impact-mean;
    html += `<div class="card"><div class="l">${y} bowl impact vs mean / ball</div><div class="v ${relativeImpact>=0?'pos':'neg'}">${fmt(relativeImpact)}</div>
      <div class="muted">role mean: ${fmt(mean)}</div></div>`;
  });
  html += '</div>';
  document.getElementById(id).innerHTML = html || '<p class="muted">No variation data.</p>';
}

// ============================================================ TEAMS
const teamSel = document.getElementById('teamSelect');
teamSel.innerHTML = DATA.team_names.map(t=>`<option value="${t}">${t}</option>`).join('');
teamSel.onchange = ()=> renderTeam(teamSel.value);
renderTeam(DATA.team_names[0]);

function renderTeam(team){
  const t = DATA.teams[team];
  const c = document.getElementById('teamContent');
  const logo = LOGOS[team] ? `<img class="logo" style="height:64px;width:64px" src="${LOGOS[team]}">` : '';
  let html = `<div class="namebar" style="margin:10px 0">${logo}<h2 style="margin:0">${team}</h2></div>`;
  html += `<div class="section"><h3>Batting & bowling Impact vs role mean / 100 balls, by year</h3><div class="chart-wrap"><canvas id="teamPer100"></canvas></div></div>`;
  html += yearBlock('tShotBlock', t.shot_types, 'Shot type mix & Impact vs batting mean', 'shot');
  html += yearBlock('tWagonBlock', t.wagon_zones, 'Wagon-zone share & Impact vs batting mean', 'zone');
  html += `<div class="section"><h3>Line/Length grid — % & Impact vs bowling mean</h3><div id="tLLGrid"></div></div>`;
  html += yearBlock('tSpeedBlock', t.speed, 'Bowl speed mix & Impact vs bowling mean', 'speed');
  html += `<div class="section"><h3>Delivery-to-delivery variation</h3><div id="tVarBlock"></div></div>`;
  c.innerHTML = html;

  killChart('teamPer100');
  const yrs = Array.from(new Set([...Object.keys(t.batting_per100), ...Object.keys(t.bowling_per100)])).sort();
  safeChart('teamPer100', 'teamPer100', {
    type:'bar',
    data:{ labels: yrs, datasets:[
      { label:'Batting vs mean /100', data: yrs.map(y=>t.batting_per100[y]-DATA.role_means.batting*100), backgroundColor:'rgba(91,140,255,.7)' },
      { label:'Bowling vs mean /100', data: yrs.map(y=>t.bowling_per100[y]-DATA.role_means.bowling*100), backgroundColor:'rgba(242,184,75,.8)' },
    ]},
    options:{ responsive:true, maintainAspectRatio:false, scales:{ y:{ title:{display:true,text:'Impact vs role mean / 100 balls'} } } }
  });

  setupYearBlock('tShotBlock', t.shot_types, 'shot', 'Shot', DATA.role_means.batting);
  setupYearBlock('tWagonBlock', t.wagon_zones, 'zone', 'Zone', DATA.role_means.batting);
  renderLLGrid('tLLGrid', t.line_length, DATA.role_means.bowling);
  setupYearBlock('tSpeedBlock', t.speed, 'speed', 'Speed', DATA.role_means.bowling);
  renderVariation('tVarBlock', t.variation, DATA.role_means.bowling);
}

// ============================================================ IMPACT PLAYER
const ipYears = Array.from(new Set(DATA.impact_player.map(e=>e.year))).sort();
document.getElementById('ipYear').innerHTML += ipYears.map(y=>`<option value="${y}">${y}</option>`).join('');
document.getElementById('ipTeam').innerHTML += DATA.team_names.map(t=>`<option value="${t}">${t}</option>`).join('');
['ipYear','ipTeam','ipSort'].forEach(id=> document.getElementById(id).onchange = renderImpactPlayer);
function renderImpactPlayer(){
  const yr = document.getElementById('ipYear').value, tm = document.getElementById('ipTeam').value, sort = document.getElementById('ipSort').value;
  let rows = DATA.impact_player.filter(e=> (yr==='all'||e.year==yr) && (tm==='all'||e.team===tm));
  if(sort==='vs_mean') rows = rows.slice().sort((a,b)=>b.substitution_effect-a.substitution_effect);
  else if(sort==='vs_mean_worst') rows = rows.slice().sort((a,b)=>a.substitution_effect-b.substitution_effect);
  else rows = rows.slice().sort((a,b)=> a.date.localeCompare(b.date));
  const top = rows.slice(0, 40);
  killChart('ipChart');
  safeChart('ipChart', 'ipChart', {
    type:'bar',
    data:{ labels: top.map(e=> `${e.player_in} in (${e.date.slice(0,10)})`),
      datasets:[{ label:'Substitution effect', data: top.map(e=>e.substitution_effect), backgroundColor: top.map(e=>colorFor(e.substitution_effect)) }] },
    options:{ responsive:true, maintainAspectRatio:false, indexAxis:'y',
      plugins:{ legend:{display:false}, tooltip:{ callbacks:{ label:(ctx)=>{ const e=top[ctx.dataIndex];
        return `${e.team} vs ${e.opponent}: ${e.player_out} → ${e.player_in} (${e.slot_role}), `+
            `SE ${fmt(e.substitution_effect)} (bat ${fmt(e.batting_vs_mean)}, bowl ${fmt(e.bowling_vs_mean)})`; } } } },
          scales:{ x:{ title:{display:true,text:'Substitution effect (runs)'} } } }
  });
  let t = '<tr><th>Date</th><th>Team</th><th>Opp</th><th>Out → In</th><th>Role</th><th>Substitution effect</th><th>Batting vs mean</th><th>Bowling vs mean</th></tr>';
  rows.slice(0,150).forEach(e=>{
    t += `<tr><td>${e.date.slice(0,10)}</td><td>${e.team}</td><td>${e.opponent}</td>
      <td>${e.player_out} → ${e.player_in}</td><td>${e.slot_role}</td>
      <td class="${e.substitution_effect>=0?'pos':'neg'}">${fmt(e.substitution_effect)}</td>
      <td class="${e.batting_vs_mean>=0?'pos':'neg'}">${fmt(e.batting_vs_mean)}</td>
      <td class="${e.bowling_vs_mean>=0?'pos':'neg'}">${fmt(e.bowling_vs_mean)}</td></tr>`;
  });
  document.getElementById('ipTable').innerHTML = t;
  const distRows = ipYears.map(y=>{
    const values = DATA.impact_player.filter(e=>e.year===y).map(e=>e.substitution_effect).filter(Number.isFinite).sort((a,b)=>a-b);
    return distributionRow(y, values);
  }).join('');
  document.getElementById('ipDistTable').innerHTML = distributionHeader('Year') + distRows;
  renderImpactDistributionChart();
}
renderImpactPlayer();

function renderImpactDistributionChart(){
  const values = DATA.impact_player.map(e=>e.substitution_effect).filter(Number.isFinite);
  if(!values.length) return;
  const min = Math.floor(Math.min(...values)), max = Math.ceil(Math.max(...values));
  const binCount = 12, width = Math.max(1, (max-min)/binCount);
  const labels = Array.from({length:binCount}, (_,i)=>fmt(min+(i+.5)*width));
  const groups = [
    {year:2023, role:'Batted 1st', color:'#5b8cff'},
    {year:2023, role:'Batted 2nd', color:'#f2b84b'},
    {year:2024, role:'Batted 1st', color:'#3fb27f'},
    {year:2024, role:'Batted 2nd', color:'#e2564f'},
  ];
  const datasets = groups.map(g=>{
    const counts = Array(binCount).fill(0);
    const groupRows = DATA.impact_player.filter(e=>e.year===g.year && e.team_role===g.role && Number.isFinite(e.substitution_effect));
    if(groupRows.length < 10) return null;
    groupRows.forEach(e=>{
      const index = Math.min(binCount-1, Math.max(0, Math.floor((e.substitution_effect-min)/width)));
      counts[index]++;
    });
    return {label:`${g.year} ${g.role}`, data:counts, backgroundColor:g.color+'99', borderColor:g.color, borderWidth:1};
  }).filter(Boolean);
  safeChart('ipDistChart', 'ipDistChart', {
    type:'bar', data:{labels, datasets},
    options:{responsive:true, maintainAspectRatio:false,
      plugins:{tooltip:{callbacks:{label:ctx=>`${ctx.dataset.label}: ${ctx.raw} events`}}},
      scales:{x:{title:{display:true,text:'Impact vs role mean (runs)'}}, y:{beginAtZero:true,title:{display:true,text:'Substitution events'}}}}
  });
}

// ============================================================ AUCTION
const aucYears = Array.from(new Set(DATA.auction.map(a=>a.year))).sort();
document.getElementById('aucYear').innerHTML += aucYears.map(y=>`<option value="${y}">${y}</option>`).join('');
document.getElementById('aucYear').onchange = renderAuction;
const YEAR_COLORS = {}; aucYears.forEach((y,i)=> YEAR_COLORS[y] = ['#5b8cff','#f2b84b','#3fb27f','#e2564f'][i%4]);
function renderAuction(){
  const yr = document.getElementById('aucYear').value;
  const rows = DATA.auction.filter(a=> yr==='all'||a.year==yr);
  killChart('aucChart');
  const byYear = {};
  rows.forEach(r=>{ (byYear[r.year]=byYear[r.year]||[]).push(r); });
  const datasets = Object.keys(byYear).map(y=>({
    label:String(y), data: byYear[y].map(r=>({x:r.price_cr, y:r.impact_vs_mean, meta:r})),
    backgroundColor: YEAR_COLORS[y]+'cc',
  }));
  safeChart('aucChart', 'aucChart', {
    type:'scatter', data:{ datasets },
    options:{ responsive:true, maintainAspectRatio:false,
      plugins:{ tooltip:{ callbacks:{ label:(ctx)=>{ const r=ctx.raw.meta;
        return `${r.player} (${r.team||'?'}, ${r.year}, ${r.role}): ₹${r.price_cr}cr, Impact vs mean/ball ${fmt(r.impact_vs_mean)}, ${fmt(r.impact_per_cr)}/cr`; } } } },
      scales:{ x:{ title:{display:true,text:'Price (₹ crore)'} }, y:{ title:{display:true,text:'Impact vs role mean / ball'} } } }
  });
  const ranked = rows.slice().sort((a,b)=>b.impact_vs_mean-a.impact_vs_mean);
  let t = '<tr><th>Year</th><th>Player</th><th>Role</th><th>Team</th><th>Balls</th><th>Price (cr)</th><th>Impact vs mean / ball</th><th>Impact / cr</th></tr>';
  ranked.forEach(r=>{
    t += `<tr><td>${r.year}</td><td>${r.player}</td><td>${r.role||'?'}</td><td>${r.team||'?'}</td><td>${r.balls}</td><td>${fmt(r.price_cr)}</td>`+
      `<td class="${r.impact_vs_mean>=0?'pos':'neg'}">${fmt(r.impact_vs_mean)}</td><td>${fmt(r.impact_per_cr)}</td></tr>`;
  });
  document.getElementById('aucTable').innerHTML = t;
}
renderAuction();

function fmtStat(value){ return value == null || !Number.isFinite(value) ? '–' : fmt(value); }
function distributionHeader(firstLabel){
  return `<tr><th>${firstLabel}</th><th>N</th><th>Mean</th><th>SD</th><th>P25</th><th>Median</th><th>P75</th><th>Min</th><th>Max</th></tr>`;
}
function distributionRow(label, values){
  if(!values.length) return `<tr><td>${label}</td><td colspan="8">–</td></tr>`;
  const mean = values.reduce((sum,value)=>sum+value,0)/values.length;
  const variance = values.reduce((sum,value)=>sum+(value-mean)**2,0)/values.length;
  const quantile = p=> values[Math.min(values.length-1, Math.floor((values.length-1)*p))];
  return `<tr><td>${label}</td><td>${values.length}</td><td>${fmtStat(mean)}</td><td>${fmtStat(Math.sqrt(variance))}</td>`+
    `<td>${fmtStat(quantile(.25))}</td><td>${fmtStat(quantile(.5))}</td><td>${fmtStat(quantile(.75))}</td>`+
    `<td>${fmtStat(values[0])}</td><td>${fmtStat(values[values.length-1])}</td></tr>`;
}