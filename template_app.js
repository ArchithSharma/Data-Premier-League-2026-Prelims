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
function ordinal(n){
  const r = Math.round(n);
  const mod100 = r % 100;
  if(mod100 >= 11 && mod100 <= 13) return r + 'th';
  switch(r % 10){ case 1: return r+'st'; case 2: return r+'nd'; case 3: return r+'rd'; default: return r+'th'; }
}
function colorFor(v){ return v>=0 ? 'rgba(63,178,127,.75)' : 'rgba(226,86,79,.75)'; }
function years(obj){ return Object.keys(obj||{}).sort(); }
// Default cell renderer for a column with no explicit `render`: numbers go through fmt()
// (2-decimal rounding), everything else (player/team/role names, etc.) is shown as-is.
// NOTE: this must NOT be fmtStat() -- fmtStat() blanks out anything that isn't a finite
// number, which silently wiped every plain-string column (Player, Team, Role, Opp) down
// to "–" the moment a column omitted its own `render`.
function defaultCell(v){
  if(v == null) return '–';
  return typeof v === 'number' ? fmt(v) : v;
}
// For genuinely-always-numeric stats (distribution summaries: mean/SD/quantiles) where a
// non-finite value really does mean "couldn't be computed" and should read as "–".
function fmtStat(value){ return value == null || !Number.isFinite(value) ? '–' : fmt(value); }
// Generic click-to-sort table renderer, used for every big row-per-record table (Impact
// Player log, Auction comparison, Biggest Movers). `columns` is
// [{label, get(row)->sort value (number|string|null), render(row)->cell HTML (optional,
// defaults to get()), cls(row)->td class (optional)}]. Sort state is remembered per
// tableId across re-renders (e.g. changing a filter dropdown keeps the column you sorted
// by), reset only when a fresh set of columns is passed in (role/filter switch).
// NOTE: defined here, near the top of the script (not further down, next to where it's
// used) specifically so `sortState` is initialized before renderImpactPlayer() /
// renderAuction() / renderMovers() call it a few dozen lines below -- referencing a
// later `const` before its declaration line has run throws a ReferenceError that used to
// abort the whole script partway through, which is why the last few tabs' tables stopped
// rendering at all. Keep this block above every renderSortableTable(...) call site.
const sortState = {};
function renderSortableTable(tableId, columns, rows){
  const sig = columns.map(c=>c.label).join('|');
  let state = sortState[tableId];
  if(!state || state.sig !== sig){ state = sortState[tableId] = {col:null, asc:true, sig}; }
  let sorted = rows;
  if(state.col!=null && columns[state.col]){
    const col = columns[state.col];
    sorted = rows.slice().sort((a,b)=>{
      const va = col.get(a), vb = col.get(b);
      if(va==null && vb==null) return 0;
      if(va==null) return 1;
      if(vb==null) return -1;
      if(typeof va === 'string') return va.localeCompare(vb);
      return va-vb;
    });
    if(!state.asc) sorted.reverse();
  }
  const head = '<tr>'+columns.map((c,i)=>{
    const arrow = state.col===i ? (state.asc?' \u25B2':' \u25BC') : '';
    return `<th class="sortable" data-i="${i}" title="Click to sort">${c.label}${arrow}</th>`;
  }).join('')+'</tr>';
  const body = sorted.length ? sorted.map(r=>{
    return '<tr>'+columns.map(c=>{
      const cls = c.cls ? c.cls(r) : '';
      const cell = c.render ? c.render(r) : defaultCell(c.get(r));
      return `<td${cls?` class="${cls}"`:''}>${cell}</td>`;
    }).join('')+'</tr>';
  }).join('') : `<tr><td colspan="${columns.length}" class="muted">No rows.</td></tr>`;
  const el = document.getElementById(tableId);
  el.innerHTML = head+body;
  el.querySelectorAll('th.sortable').forEach(th=>{
    th.onclick = ()=>{
      const i = +th.dataset.i;
      if(state.col===i) state.asc = !state.asc; else { state.col = i; state.asc = true; }
      renderSortableTable(tableId, columns, rows);
    };
  });
  return sorted;
}
function combineSe(seA, seB){
  if(seA!=null && seB!=null) return Math.sqrt(seA*seA + seB*seB);
  if(seA!=null) return seA;
  if(seB!=null) return seB;
  return null;
}
// Chart.js core has no built-in error-bar support, so we draw them ourselves: any line
// dataset carrying an `errorBars` array (one +/- half-width per point, in data units) gets
// a vertical whisker drawn through that point. This is what lets every shot/zone/speed/
// length category stay on the chart -- including low-sample ones that used to be dropped
// entirely -- while still being honest that a low-sample category's Impact estimate is
// noisier than a high-sample one.
//
// Chart.js scales its axes off each dataset's own `data` values, which never include the
// error-bar half-widths -- so a very noisy low-sample category (a big `err`) doesn't
// resize the axis, it just draws a whisker that can shoot straight through the rest of
// the chart. We clamp the whisker to the visible plot area and draw a small open arrow
// at any end that got clipped, so an oversized error bar reads as "this is bigger than
// the chart can show" instead of blowing out the whole scale.
const errorBarPlugin = {
  id: 'errorBarPlugin',
  afterDatasetsDraw(chart){
    const ctx = chart.ctx;
    const area = chart.chartArea;
    if(!area) return;
    chart.data.datasets.forEach((ds, dsIndex)=>{
      if(!ds.errorBars) return;
      const meta = chart.getDatasetMeta(dsIndex);
      if(!meta || meta.hidden) return;
      meta.data.forEach((point, i)=>{
        const err = ds.errorBars[i];
        const value = ds.data[i];
        if(err==null || !Number.isFinite(err) || value==null || !Number.isFinite(value)) return;
        const scale = chart.scales[meta.yAxisID];
        if(!scale) return;
        const x = point.x;
        const rawTop = scale.getPixelForValue(value+err);
        const rawBot = scale.getPixelForValue(value-err);
        const yTop = Math.max(area.top, Math.min(rawTop, rawBot));
        const yBot = Math.min(area.bottom, Math.max(rawTop, rawBot));
        const clippedTop = yTop > Math.min(rawTop, rawBot) + 0.5;
        const clippedBot = yBot < Math.max(rawTop, rawBot) - 0.5;
        ctx.save();
        ctx.strokeStyle = ds.borderColor || '#f2b84b';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(x, yTop); ctx.lineTo(x, yBot);
        // whisker caps -- only drawn at an end that wasn't clipped away
        if(!clippedTop){ ctx.moveTo(x-4, yTop); ctx.lineTo(x+4, yTop); }
        if(!clippedBot){ ctx.moveTo(x-4, yBot); ctx.lineTo(x+4, yBot); }
        ctx.stroke();
        // small open arrowhead marking a clipped end, so it's clear the true error bar
        // extends further than the chart can show rather than just stopping there
        if(clippedTop){
          ctx.beginPath();
          ctx.moveTo(x-4, yTop+5); ctx.lineTo(x, yTop); ctx.lineTo(x+4, yTop+5);
          ctx.stroke();
        }
        if(clippedBot){
          ctx.beginPath();
          ctx.moveTo(x-4, yBot-5); ctx.lineTo(x, yBot); ctx.lineTo(x+4, yBot-5);
          ctx.stroke();
        }
        ctx.restore();
      });
    });
  }
};
if(typeof Chart !== 'undefined'){ try{ Chart.register(errorBarPlugin); }catch(e){} }

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
// Show each season's percentile separately (rank within that season's own qualified
// pool) rather than one blended career number -- a player's 2023 and 2024 percentiles
// are relative to different pools, so keeping them side by side is more honest than
// averaging them into one figure.
function percentileCards(d){
  const pby = d.percentile_by_year || {};
  const yrs = Object.keys(pby).sort();
  if(!yrs.length) return statCard('Percentile', d.percentile!=null ? ordinal(d.percentile) : '–');
  return yrs.map(y => statCard(`${y} percentile`, pby[y]!=null ? ordinal(pby[y]) : '–')).join('');
}

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
    ${percentileCards(d)}
    ${statCard('Balls', d.career_balls)}
    ${statCard(role==='batting'?'Runs':'Runs conceded', d.career_runs)}
  </div>`;
  html += `<div class="section"><h3>Per-game Impact vs role mean / ball (bubble size = venue factor)</h3><div class="chart-wrap"><canvas id="gameChart-${role}"></canvas></div>
    <p class="note">Venue factor &gt;1 = hitter-friendly ground that game; &lt;1 = bowler-friendly. Bigger/brighter bubble = further from a neutral (1.0) venue.</p></div>`;

  if(role==='batting'){
    html += yearBlock('shotBlock', d.shot_types, 'Shot type mix — change between seasons', 'shot');
    html += yearBlock('wagonBlock', d.wagon_zones, 'Wagon-zone share — change between seasons (batter\'s-eye view)', 'zone', true);
  } else {
    const chg = bowlingChangeStats(d);
    if(chg){
      html += `<div class="section"><h3>Total bowling mix change, ${chg.yrA} → ${chg.yrB}</h3>
        <div class="row muted">Joint (length, line, speed) change index: <b>${chg.idx.toFixed(1)}</b>`+
        (chg.hasJoint ? '' : ' <span title="No per-cell speed breakdown for both seasons -- falling back to the average of the two marginal indices below.">(marginal fallback)</span>')+
        `</div>
        <div class="row muted" style="margin-top:2px">Line/length alone: ${chg.llIdx.toFixed(1)} · Speed alone: ${chg.speedIdx!=null?chg.speedIdx.toFixed(1):'–'}</div>
        <p class="note">0 = identical mix both seasons, 100 = totally different. The joint index is measured on (length, line, speed) cells together, so it also catches the bowler pairing the same lengths/lines with different speeds -- a change the two marginal numbers next to it can miss on their own.</p>
      </div>`;
    }
    html += `<div class="section"><h3>Line/Length grid — change between seasons</h3><div id="llGrid"></div></div>`;
    html += yearBlock('speedBlock', d.speed, 'Bowl speed mix — change between seasons', 'speed');
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

// A season dropdown used to force picking one year at a time to see how a player's mix
// changed. Instead, whenever two (or more) seasons exist we show ONE difference plot --
// the change in share of each category plus the change in its Impact payoff, first
// season vs. last season -- and a single "change index" number summarizing how much the
// whole mix moved. With only one season of data there's nothing to diff, so that single
// season is shown plain.
function yearBlock(id, obj, title, key, isZone){
  const yrs = years(obj);
  const sub = yrs.length >= 2 ? `Change, ${yrs[0]} → ${yrs[yrs.length-1]}` : (yrs[0] || 'No data');
  return `<div class="section"><h3>${title}</h3>
    <div class="row muted">${sub}</div>
    <div class="chart-wrap"><canvas id="${id}-chart"></canvas></div>
    <div id="${id}-idx" class="muted" style="margin-top:6px"></div></div>`;
}
function catLabel(r, key){ return key==='zone' ? 'Zone '+r.zone : r[key]; }
function relImpact(r, mean){ return r.impact_vs_mean!=null ? r.impact_vs_mean : r.impact-mean; }
// Merge two seasons' rows into one row per category (union of both years' categories,
// missing categories treated as a 0% share). Carries each season's standard error (se)
// through too, and a combined se for the delta, so every category can stay on the chart
// with an honest error bar instead of low-sample ones being dropped.
function mergeSeasons(rowsA, rowsB, key, mean){
  const map = {};
  (rowsA||[]).forEach(r=>{ const l=catLabel(r,key); map[l]=map[l]||{label:l}; map[l].pctA=r.pct; map[l].implA=relImpact(r,mean); map[l].seA=r.se; });
  (rowsB||[]).forEach(r=>{ const l=catLabel(r,key); map[l]=map[l]||{label:l}; map[l].pctB=r.pct; map[l].implB=relImpact(r,mean); map[l].seB=r.se; });
  return Object.values(map).map(r=>({
    label:r.label, pctA:r.pctA||0, pctB:r.pctB||0, deltaPct:(r.pctB||0)-(r.pctA||0),
    implA: r.implA!=null?r.implA:null, implB: r.implB!=null?r.implB:null,
    deltaImpl: (r.implA!=null && r.implB!=null) ? r.implB-r.implA : null,
    seA: r.seA!=null?r.seA:null, seB: r.seB!=null?r.seB:null,
    deltaSe: combineSe(r.seA, r.seB),
  }));
}
// Total variation distance between the two seasons' share distributions: half the sum of
// absolute percentage-point changes. 0 = identical mix both seasons; 100 = completely
// different mix (no category shared at all). This is the "change index".
function changeIndex(merged){ return merged.reduce((s,r)=>s+Math.abs(r.deltaPct),0)/2; }
function drawYearChart(id, rowsA, rowsB, key, keyLabel, mean, yrA, yrB){
  killChart(id+'-chart');
  const idxBox = document.getElementById(id+'-idx');
  if(yrB===undefined){
    // only one season available -- nothing to diff, show it plain. Every category is
    // kept (no top-N cutoff); low-sample ones simply carry a wider error bar.
    let rows = (rowsA||[]).slice().sort((a,b)=> (key==='zone') ? a.zone-b.zone : b.pct-a.pct);
    const labels = rows.map(r=>catLabel(r,key));
    safeChart(id+'-chart', id+'-chart', {
      data:{ labels, datasets:[
        { type:'bar', label:'% of deliveries', data: rows.map(r=>r.pct), backgroundColor:'rgba(91,140,255,.55)', yAxisID:'y' },
        { type:'line', label:'Impact vs role mean / ball', data: rows.map(r=>relImpact(r,mean)), borderColor:'#f2b84b', backgroundColor:'#f2b84b', yAxisID:'y1', tension:.2, errorBars: rows.map(r=>r.se) },
      ]},
      options:{ responsive:true, maintainAspectRatio:false,
        plugins:{ tooltip:{ callbacks:{ label:(ctx)=>{ const r = rows[ctx.dataIndex];
          return ctx.dataset.yAxisID==='y'
            ? `${catLabel(r,key)}: ${fmt(r.pct)}% of deliveries (${r.balls} balls)`
            : `${catLabel(r,key)}: Impact vs mean ${fmt(relImpact(r,mean))}` + (r.se!=null ? ` ± ${fmt(r.se)}` : ''); } } } },
        scales:{ x:{ ticks:{ autoSkip:false, maxRotation:40, minRotation:20 } },
                 y:{ position:'left', title:{display:true,text:'% of deliveries'} },
                 y1:{ position:'right', title:{display:true,text:'Impact vs mean / ball'}, grid:{drawOnChartArea:false} } } }
    });
    if(idxBox) idxBox.textContent = '';
    return;
  }
  let merged = mergeSeasons(rowsA, rowsB, key, mean);
  const idx = changeIndex(merged);
  // Every category from either season is kept (no top-14 cutoff) -- sorted so the
  // biggest movers are still easy to spot at a glance, but nothing is hidden.
  merged = merged.slice().sort((a,b)=>Math.abs(b.deltaPct)-Math.abs(a.deltaPct));
  const labels = merged.map(r=>r.label);
  safeChart(id+'-chart', id+'-chart', {
    data:{ labels, datasets:[
      { type:'bar', label:`Δ % of deliveries (${yrA}→${yrB})`, data: merged.map(r=>r.deltaPct), backgroundColor: merged.map(r=>colorFor(r.deltaPct)), yAxisID:'y' },
      { type:'line', label:'Δ Impact vs role mean / ball', data: merged.map(r=>r.deltaImpl), borderColor:'#f2b84b', backgroundColor:'#f2b84b', yAxisID:'y1', tension:.2, errorBars: merged.map(r=>r.deltaSe) },
    ]},
    options:{ responsive:true, maintainAspectRatio:false,
      plugins:{ tooltip:{ callbacks:{ label:(ctx)=>{ const r = merged[ctx.dataIndex];
        return ctx.dataset.yAxisID==='y'
          ? `${r.label}: ${yrA} ${fmt(r.pctA)}% → ${yrB} ${fmt(r.pctB)}% (Δ ${fmt(r.deltaPct)}pp)`
          : `${r.label}: Δ Impact vs mean ${fmt(r.deltaImpl)}` + (r.deltaSe!=null ? ` ± ${fmt(r.deltaSe)}` : ''); } } } },
      scales:{ x:{ ticks:{ autoSkip:false, maxRotation:40, minRotation:20 } },
               y:{ position:'left', title:{display:true,text:'Δ % of deliveries (pp)'} },
               y1:{ position:'right', title:{display:true,text:'Δ Impact vs role mean / ball'}, grid:{drawOnChartArea:false} } } }
  });
  if(idxBox) idxBox.innerHTML = `<b>Change index: ${idx.toFixed(1)}</b> — ${idx.toFixed(1)}% of ${keyLabel.toLowerCase()} share moved to a different category from ${yrA} to ${yrB} (0 = no change in mix, 100 = totally different mix). Error bars on the Δ Impact line show combined standard error across both seasons.`;
}
function setupYearBlock(id, obj, key, keyLabel, mean){
  const yrs = years(obj);
  if(yrs.length >= 2) drawYearChart(id, obj[yrs[0]], obj[yrs[yrs.length-1]], key, keyLabel, mean, yrs[0], yrs[yrs.length-1]);
  else if(yrs.length === 1) drawYearChart(id, obj[yrs[0]], undefined, key, keyLabel, mean);
  else killChart(id+'-chart');
}

const LENGTH_ORDER = ['SHORT','SHORT_OF_A_GOOD_LENGTH','GOOD_LENGTH','FULL','YORKER','FULL_TOSS'];
const LINE_ORDER = ['DOWN_LEG','ON_THE_STUMPS','OUTSIDE_OFFSTUMP','WIDE_OUTSIDE_OFFSTUMP'];
// Same idea as the year-diff charts above, applied to the line/length grid: with two
// seasons, show one grid of the *change* in each cell (plus a change index) instead of a
// dropdown that only ever shows one season's grid at a time.
function renderLLGrid(id, obj, mean=0){
  const yrs = years(obj);
  document.getElementById(id).innerHTML = `<div id="${id}-tbl"></div><div id="${id}-idx" class="muted" style="margin-top:6px"></div>`;
  if(yrs.length >= 2) drawLLDiffGrid(id, obj[yrs[0]], obj[yrs[yrs.length-1]], mean, yrs[0], yrs[yrs.length-1]);
  else if(yrs.length === 1) drawLLSingleGrid(id, obj[yrs[0]], mean, yrs[0]);
}
function drawLLSingleGrid(id, rows, mean, yrLabel){
  rows = rows||[];
  const map = {}; rows.forEach(r=> map[r.length+'|'+r.line]=r);
  const maxAbs = Math.max(0.5, ...rows.map(r=>Math.abs(relImpact(r,mean))));
  let t = `<div class="muted" style="margin-bottom:4px">${yrLabel} (only one season of data)</div>`;
  t += '<table class="grid-heat"><tr><th></th>'+LINE_ORDER.map(l=>`<th>${l.replace(/_/g,' ')}</th>`).join('')+'</tr>';
  LENGTH_ORDER.forEach(len=>{
    t += `<tr><th>${len.replace(/_/g,' ')}</th>`;
    LINE_ORDER.forEach(line=>{
      const r = map[len+'|'+line];
      if(!r){ t+='<td>–</td>'; return; }
      const rv = relImpact(r, mean);
      const alpha = Math.min(1, Math.abs(rv)/maxAbs);
      const bg = rv>=0 ? `rgba(63,178,127,${0.15+0.6*alpha})` : `rgba(226,86,79,${0.15+0.6*alpha})`;
      const seTxt = r.se!=null ? ` ±${r.se.toFixed(2)}` : '';
      t += `<td style="background:${bg}">${rv.toFixed(2)}${seTxt}<br><span class="muted">${r.pct.toFixed(1)}% (${r.balls}b)</span></td>`;
    });
    t += '</tr>';
  });
  t += '</table>';
  document.getElementById(id+'-tbl').innerHTML = t;
  const box = document.getElementById(id+'-idx'); if(box) box.textContent = '';
}
// Merges two seasons' line/length rows into one row per (length, line) cell -- every
// cell is kept (no more `balls >= 8` filter dropping sparse ones), with a combined
// standard error attached so low-sample cells can show an honest error range instead of
// disappearing. Shared by the diff grid and the "biggest movers" leaderboard.
function llCells(rowsA, rowsB, mean, keyFn){
  keyFn = keyFn || (r=>r.length+'|'+r.line);
  const mapA={}; (rowsA||[]).forEach(r=>mapA[keyFn(r)]=r);
  const mapB={}; (rowsB||[]).forEach(r=>mapB[keyFn(r)]=r);
  const keys = new Set([...Object.keys(mapA), ...Object.keys(mapB)]);
  const cells = [];
  keys.forEach(k=>{
    const a=mapA[k], b=mapB[k];
    cells.push({
      key:k, pctA:a?a.pct:0, pctB:b?b.pct:0, deltaPct:(b?b.pct:0)-(a?a.pct:0),
      deltaImpl: (a && b && mean!=null) ? relImpact(b,mean)-relImpact(a,mean) : null,
      deltaSe: combineSe(a?a.se:null, b?b.se:null),
    });
  });
  return cells;
}
function llChangeIndex(cells){ return cells.reduce((s,c)=>s+Math.abs(c.deltaPct),0)/2; }
// Same (length, line) grid as llCells, but with speed folded in as a third key
// component (length|line|speed) instead of tracked as a separate marginal breakdown.
// A bowler who bowls the same lengths/lines overall and the same speeds overall, but
// pairs them differently (e.g. moved their slower balls from full to short), shows 0
// change on each 2-D marginal but a real change here -- this is the joint distribution,
// not the two 1-D projections of it averaged together.
function llSpeedCells(rowsA, rowsB, mean){
  return llCells(rowsA, rowsB, mean, r=>r.length+'|'+r.line+'|'+r.speed);
}
// Shared by the player page (single-player "Total mix change" stat) and Biggest Movers
// (whole-league table): first vs last season, joint (length,line,speed) grid change
// index, with the two marginal indices (line/length alone, speed alone) alongside for
// reference, and the old averaged-marginals number as a fallback when a player has no
// speed-tagged deliveries in one of the two seasons being compared.
function bowlingChangeStats(rd){
  const llObj = rd.line_length;
  const yrs = llObj ? Object.keys(llObj).sort() : [];
  if(yrs.length < 2) return null;
  const yrA = yrs[0], yrB = yrs[yrs.length-1];
  const llIdx = llChangeIndex(llCells(llObj[yrA], llObj[yrB], rd.mean_impact_per_ball));
  let speedIdx = null;
  const spObj = rd.speed;
  if(spObj && spObj[yrA] && spObj[yrB]){
    speedIdx = changeIndex(mergeSeasons(spObj[yrA], spObj[yrB], 'speed', rd.mean_impact_per_ball));
  }
  const llsObj = rd.line_length_speed;
  let idx, hasJoint = false;
  if(llsObj && llsObj[yrA] && llsObj[yrB]){
    idx = llChangeIndex(llSpeedCells(llsObj[yrA], llsObj[yrB], rd.mean_impact_per_ball));
    hasJoint = true;
  } else {
    idx = speedIdx!=null ? (llIdx+speedIdx)/2 : llIdx;
  }
  return { yrA, yrB, llIdx, speedIdx, idx, hasJoint };
}
function drawLLDiffGrid(id, rowsA, rowsB, mean, yrA, yrB){
  const cells = llCells(rowsA, rowsB, mean);
  const maxAbs = Math.max(3, ...cells.map(c=>Math.abs(c.deltaPct)));
  const cellMap={}; cells.forEach(c=>cellMap[c.key]=c);
  let t = `<div class="muted" style="margin-bottom:4px">Δ share of deliveries, ${yrA} → ${yrB} (green = bowled there more, red = less)</div>`;
  t += '<table class="grid-heat"><tr><th></th>'+LINE_ORDER.map(l=>`<th>${l.replace(/_/g,' ')}</th>`).join('')+'</tr>';
  LENGTH_ORDER.forEach(len=>{
    t += `<tr><th>${len.replace(/_/g,' ')}</th>`;
    LINE_ORDER.forEach(line=>{
      const c = cellMap[len+'|'+line];
      if(!c){ t+='<td>–</td>'; return; }
      const alpha = Math.min(1, Math.abs(c.deltaPct)/maxAbs);
      const bg = c.deltaPct>=0 ? `rgba(63,178,127,${0.15+0.6*alpha})` : `rgba(226,86,79,${0.15+0.6*alpha})`;
      const implTxt = c.deltaImpl!=null ? `<br><span class="muted">Δimpact ${fmt(c.deltaImpl)}${c.deltaSe!=null?' ±'+fmt(c.deltaSe):''}</span>` : '';
      t += `<td style="background:${bg}">${c.deltaPct>=0?'+':''}${c.deltaPct.toFixed(1)}pp${implTxt}</td>`;
    });
    t += '</tr>';
  });
  t += '</table>';
  document.getElementById(id+'-tbl').innerHTML = t;
  const idx = llChangeIndex(cells);
  const box = document.getElementById(id+'-idx');
  if(box) box.innerHTML = `<b>Change index: ${idx.toFixed(1)}</b> — ${idx.toFixed(1)}% of deliveries moved to a different length/line cell from ${yrA} to ${yrB} (0 = identical grid, 100 = totally different). Δimpact figures show ± combined standard error where both seasons have data.`;
}

function renderVariation(id, obj, mean=0, overall=null, leagueOverall=null){
  const yrs = years(obj);
  let html = '';
  if(overall){
    const lg = leagueOverall;
    const relImpact = overall.mean_impact-mean;
    html += '<p class="note" style="margin-bottom:4px"><b>All seasons combined</b> (not split by year — every delivery pooled together):</p>';
    html += '<div class="cards">';
    html += `<div class="card"><div class="l">Avg distance (all seasons)</div><div class="v">${overall.mean_dist!=null?overall.mean_dist.toFixed(2):'–'}</div>
      <div class="muted">league: ${lg&&lg.mean_dist!=null?lg.mean_dist.toFixed(2):'–'}</div></div>`;
    html += `<div class="card"><div class="l">Bowl impact vs mean / ball (all seasons)</div><div class="v ${relImpact>=0?'pos':'neg'}">${fmt(relImpact)}</div>
      <div class="muted">role mean: ${fmt(mean)}</div></div>`;
    html += `<div class="card"><div class="l">Corr(variation, impact) (all seasons)</div><div class="v">${overall.corr_dist_impact!=null?overall.corr_dist_impact.toFixed(3):'–'}</div>
      <div class="muted">league: ${lg&&lg.corr_dist_impact!=null?lg.corr_dist_impact.toFixed(3):'–'}, n=${overall.balls}</div></div>`;
    html += '</div>';
  }
  if(yrs.length) html += '<p class="note" style="margin-bottom:4px"><b>By season</b>:</p>';
  html += '<div class="cards">';
  yrs.forEach(y=>{
    const v = obj[y], lg = (DATA.league_variation||{})[y];
    html += `<div class="card"><div class="l">${y} avg distance</div><div class="v">${v.mean_dist!=null?v.mean_dist.toFixed(2):'–'}</div>
      <div class="muted">league: ${lg?lg.mean_dist.toFixed(2):'–'}</div></div>`;
    const relativeImpact = v.mean_impact-mean;
    html += `<div class="card"><div class="l">${y} bowl impact vs mean / ball</div><div class="v ${relativeImpact>=0?'pos':'neg'}">${fmt(relativeImpact)}</div>
      <div class="muted">role mean: ${fmt(mean)}</div></div>`;
    const corr = v.corr_dist_impact, lgCorr = lg ? lg.corr_dist_impact : null;
    html += `<div class="card"><div class="l">${y} corr(variation, impact)</div><div class="v">${corr!=null?corr.toFixed(3):'–'}</div>
      <div class="muted">league: ${lgCorr!=null?lgCorr.toFixed(3):'–'}</div></div>`;
  });
  html += '</div>';
  if(yrs.length){
    html += `<p class="note">Correlation is the Pearson r between how far each delivery sat from the bowler's own previous ball on the length/line grid and that ball's bowling Impact, within that season. Positive = mixing it up more often coincided with better outcomes that year; negative = more predictable deliveries actually worked better. Shown as "–" for a season with too few deliveries to estimate reliably.</p>`;
  }
  document.getElementById(id).innerHTML = html || '<p class="muted">No variation data.</p>';
}

// ============================================================ TEAMS
const teamSel = document.getElementById('teamSelect');
teamSel.innerHTML = DATA.team_names.map(t=>`<option value="${t}">${t}</option>`).join('');
teamSel.onchange = ()=> renderTeam(teamSel.value);
safeRun(()=>renderTeam(DATA.team_names[0]), 'teams');

function renderTeam(team){
  const t = DATA.teams[team];
  const c = document.getElementById('teamContent');
  const logo = LOGOS[team] ? `<img class="logo" style="height:64px;width:64px" src="${LOGOS[team]}">` : '';
  let html = `<div class="namebar" style="margin:10px 0">${logo}<h2 style="margin:0">${team}</h2></div>`;
  html += `<div class="section"><h3>Batting & bowling Impact vs role mean / 100 balls, by year</h3><div class="chart-wrap"><canvas id="teamPer100"></canvas></div></div>`;
  html += yearBlock('tShotBlock', t.shot_types, 'Shot type mix — change between seasons', 'shot');
  html += yearBlock('tWagonBlock', t.wagon_zones, 'Wagon-zone share — change between seasons', 'zone');
  html += `<div class="section"><h3>Line/Length grid — change between seasons</h3><div id="tLLGrid"></div></div>`;
  html += yearBlock('tSpeedBlock', t.speed, 'Bowl speed mix — change between seasons', 'speed');
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
  renderVariation('tVarBlock', t.variation, DATA.role_means.bowling, t.variation_overall, DATA.league_variation_overall);
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
  renderSortableTable('ipTable', [
    {label:'Date', get:e=>e.date, render:e=>e.date.slice(0,10)},
    {label:'Team', get:e=>e.team},
    {label:'Opp', get:e=>e.opponent},
    {label:'Out → In', get:e=>e.player_in, render:e=>`${e.player_out} → ${e.player_in}`},
    {label:'Role', get:e=>e.slot_role},
    {label:'Substitution effect', get:e=>e.substitution_effect, render:e=>fmt(e.substitution_effect), cls:e=>e.substitution_effect>=0?'pos':'neg'},
    {label:'Batting vs mean', get:e=>e.batting_vs_mean, render:e=>fmt(e.batting_vs_mean), cls:e=>e.batting_vs_mean>=0?'pos':'neg'},
    {label:'Bowling vs mean', get:e=>e.bowling_vs_mean, render:e=>fmt(e.bowling_vs_mean), cls:e=>e.bowling_vs_mean>=0?'pos':'neg'},
  ], rows.slice(0,150));
  const distRows = impactGroups().map(g=>{
    const values = DATA.impact_player.filter(e=>e.year===g.year && e.team_role===g.role)
      .map(e=>e.substitution_effect).filter(Number.isFinite).sort((a,b)=>a-b);
    return distributionRow(`${g.year} ${g.role}`, values);
  }).join('');
  document.getElementById('ipDistTable').innerHTML = distributionHeader('Year / Innings') + distRows;
  renderImpactDistributionChart();
}
safeRun(renderImpactPlayer, 'impact player');

// Every (year, innings-role) combination present in the data, so this generalizes past
// just 2023/2024 and 'Batted 1st'/'Batted 2nd' if the dataset grows.
function impactGroups(){
  const roles = Array.from(new Set(DATA.impact_player.map(e=>e.team_role))).filter(Boolean).sort();
  const palette = ['#5b8cff','#f2b84b','#3fb27f','#e2564f','#b06ee0','#3ecbd9'];
  const groups = [];
  let i = 0;
  ipYears.forEach(y=> roles.forEach(role=>{ groups.push({year:y, role, color: palette[i % palette.length]}); i++; }));
  return groups;
}

function renderImpactDistributionChart(){
  const values = DATA.impact_player.map(e=>e.substitution_effect).filter(Number.isFinite);
  if(!values.length) return;
  const min = Math.floor(Math.min(...values)), max = Math.ceil(Math.max(...values));
  const binCount = 12, width = Math.max(1, (max-min)/binCount);
  const labels = Array.from({length:binCount}, (_,i)=>fmt(min+(i+.5)*width));
  const groups = impactGroups();
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
  renderSortableTable('aucTable', [
    {label:'Year', get:r=>r.year},
    {label:'Player', get:r=>r.player},
    {label:'Role', get:r=>r.role||'?'},
    {label:'Team', get:r=>r.team||'?'},
    {label:'Balls', get:r=>r.balls},
    {label:'Price (cr)', get:r=>r.price_cr, render:r=>fmt(r.price_cr)},
    {label:'Impact vs mean / ball', get:r=>r.impact_vs_mean, render:r=>fmt(r.impact_vs_mean), cls:r=>r.impact_vs_mean>=0?'pos':'neg'},
    {label:'Impact / cr', get:r=>r.impact_per_cr, render:r=>fmt(r.impact_per_cr)},
  ], ranked);
}
safeRun(renderAuction, 'auction');

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

// ============================================================ BIGGEST MOVERS
// Ranks every player by how much their shot selection (batting) or line/length mix
// (bowling) changed between their first and last season, and shows where that change
// index ranks among all players, plus whether their Impact percentile actually improved.
function percentileRanks(items, valueFn){
  const sorted = items.slice().sort((a,b)=>valueFn(a)-valueFn(b));
  const n = sorted.length;
  const out = new Map();
  let i = 0;
  while(i < n){
    let j = i;
    while(j < n && valueFn(sorted[j])===valueFn(sorted[i])) j++;
    const pct = (i+1+j)/2/n*100; // average-rank percentile, ties share the same rank
    for(let k=i;k<j;k++) out.set(sorted[k], pct);
    i = j;
  }
  return out;
}
function buildMoversRows(role){
  const rows = [];
  playerNames.forEach(name=>{
    const p = DATA.players[name];
    const rd = p[role];
    if(!rd) return;
    let idx, yrA, yrB, llIdx=null, speedIdx=null;
    if(role==='batting'){
      const obj = rd.shot_types;
      const yrs = obj ? Object.keys(obj).sort() : [];
      if(yrs.length < 2) return;
      yrA = yrs[0]; yrB = yrs[yrs.length-1];
      idx = changeIndex(mergeSeasons(obj[yrA], obj[yrB], 'shot', rd.mean_impact_per_ball));
    } else {
      // Bowling "mix change" is measured on the JOINT (length, line, speed) grid -- WHERE
      // a bowler bowls and HOW FAST folded into one set of cells -- rather than averaging
      // two separate marginal indices (line/length alone, speed alone). The marginal
      // indices are still kept below (llChangeIndex/speedChangeIndex columns) for
      // reference/comparison, but they no longer feed the combined index: a joint grid
      // picks up a bowler re-pairing the same lengths/lines with different speeds even
      // when neither marginal looks like it moved. Falls back to averaging the two
      // marginals if a player's per-cell speed breakdown isn't available for both seasons.
      const stats = bowlingChangeStats(rd);
      if(!stats) return;
      yrA = stats.yrA; yrB = stats.yrB; llIdx = stats.llIdx; speedIdx = stats.speedIdx; idx = stats.idx;
    }
    const pby = rd.percentile_by_year || {};
    const pA = pby[yrA], pB = pby[yrB];
    rows.push({
      name, team: p.team, changeIndex: idx, llChangeIndex: llIdx, speedChangeIndex: speedIdx,
      yrA, yrB, pctA: pA, pctB: pB,
      deltaPct: (pA!=null && pB!=null) ? pB-pA : null,
    });
  });
  const ranks = percentileRanks(rows, r=>r.changeIndex);
  rows.forEach(r=> r.changeIndexPercentile = ranks.get(r));
  rows.sort((a,b)=> b.changeIndex-a.changeIndex);
  return rows;
}
const moversRoleSel = document.getElementById('moversRole');
moversRoleSel.onchange = renderMovers;

// Pearson correlation coefficient between two equal-length numeric arrays.
function pearsonR(xs, ys){
  const n = xs.length;
  if(n < 2) return null;
  const mx = xs.reduce((s,v)=>s+v,0)/n, my = ys.reduce((s,v)=>s+v,0)/n;
  let sxy=0, sxx=0, syy=0;
  for(let i=0;i<n;i++){ const dx=xs[i]-mx, dy=ys[i]-my; sxy+=dx*dy; sxx+=dx*dx; syy+=dy*dy; }
  if(sxx===0 || syy===0) return null;
  return sxy/Math.sqrt(sxx*syy);
}
function corrStrengthLabel(r){
  const a = Math.abs(r);
  if(a < 0.1) return 'essentially no';
  if(a < 0.3) return 'a weak';
  if(a < 0.5) return 'a moderate';
  return 'a strong';
}
function renderMoversCorrelation(rows, role){
  killChart('moversCorrChart');
  const pts = rows.filter(r=> r.deltaPct!=null && Number.isFinite(r.changeIndex));
  const metricLabel = role==='batting' ? 'Shot-selection change index' : 'Bowling mix change index (joint length/line/speed grid)';
  const note = document.getElementById('moversCorrNote');
  if(pts.length < 3){
    if(note) note.textContent = 'Not enough players with both a change index and a two-season percentile to check a relationship.';
    return;
  }
  const r = pearsonR(pts.map(p=>p.changeIndex), pts.map(p=>p.deltaPct));
  safeChart('moversCorrChart', 'moversCorrChart', {
    type:'scatter',
    data:{ datasets:[{
      label:`${metricLabel} vs Δ percentile`,
      data: pts.map(p=>({x:p.changeIndex, y:p.deltaPct, meta:p})),
      backgroundColor:'rgba(91,140,255,.75)',
    }]},
    options:{ responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{display:false},
        tooltip:{ callbacks:{ label:(ctx)=>{ const p=ctx.raw.meta;
          return `${p.name}: ${metricLabel.toLowerCase()} ${fmt(p.changeIndex)}, Δ percentile ${p.deltaPct>=0?'+':''}${fmt(p.deltaPct)}pp`; } } } },
      scales:{ x:{ title:{display:true,text:metricLabel} },
               y:{ title:{display:true,text:'Δ Impact percentile (last season − first season)'} } } }
  });
  if(note){
    note.innerHTML = `<b>Pearson r = ${r!=null?r.toFixed(3):'–'}</b> across ${pts.length} players — `+
      (r!=null ? `${corrStrengthLabel(r)} ${r>=0?'positive':'negative'} relationship between changing up their ${role==='batting'?'shot selection':'line and length'} and their Impact percentile moving.` : 'could not be computed.');
  }
}

function renderMovers(){
  const role = moversRoleSel.value;
  const rows = buildMoversRows(role);
  const metricLabel = role==='batting' ? 'Shot-selection change' : 'Bowling mix change';
  const pctFmt = v => v!=null ? (v>=0?'+':'')+v.toFixed(1)+' pp' : '–';
  const columns = [
    {label:'Player', get:r=>r.name},
    {label:'Team', get:r=>r.team||'–'},
    {label:'Seasons compared', get:r=>r.yrA, render:r=>`${r.yrA} → ${r.yrB}`},
  ];
  if(role==='bowling'){
    columns.push(
      {label:'Line/length change (marginal)', get:r=>r.llChangeIndex, render:r=>r.llChangeIndex!=null?r.llChangeIndex.toFixed(1):'–'},
      {label:'Speed change (marginal)', get:r=>r.speedChangeIndex, render:r=>r.speedChangeIndex!=null?r.speedChangeIndex.toFixed(1):'–'},
      {label:`${metricLabel} index (joint length/line/speed grid)`, get:r=>r.changeIndex, render:r=>r.changeIndex.toFixed(1)},
    );
  } else {
    columns.push({label:`${metricLabel} index`, get:r=>r.changeIndex, render:r=>r.changeIndex.toFixed(1)});
  }
  columns.push(
    {label:'Change-index percentile', get:r=>r.changeIndexPercentile, render:r=>ordinal(r.changeIndexPercentile)},
    {label:'First-season percentile', get:r=>r.pctA, render:r=>r.pctA!=null?ordinal(r.pctA):'–'},
    {label:'Last-season percentile', get:r=>r.pctB, render:r=>r.pctB!=null?ordinal(r.pctB):'–'},
    {label:'Δ percentile (benefit)', get:r=>r.deltaPct, render:r=>pctFmt(r.deltaPct), cls:r=>r.deltaPct==null?'':(r.deltaPct>=0?'pos':'neg')},
  );
  renderSortableTable('moversTable', columns, rows);
  renderMoversCorrelation(rows, role);
}
safeRun(renderMovers, 'movers');