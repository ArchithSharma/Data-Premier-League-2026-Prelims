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
        // whiskers on bars use the page text colour so they read on top of green/red/grey bars
        ctx.strokeStyle = meta.type==='bar' ? (getComputedStyle(document.documentElement).getPropertyValue('--text').trim() || '#e8eaf0') : (ds.borderColor || '#f2b84b');
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

// Draws the actual numbers on the chart (not just in the hover tooltip): any dataset carrying a
// `valueLabels` array (one string per point, e.g. '2.4→2.6') gets that text drawn at each point.
// Bars put it just beyond the bar's end (above positive, below negative); line points put it above
// the marker, flipping below when there's no room. Each label sits on a small pill in the card
// colour so it stays readable over gridlines / neighbouring marks, and colours follow the page theme.
const valueLabelPlugin = {
  id: 'valueLabelPlugin',
  afterDatasetsDraw(chart){
    const ctx = chart.ctx, area = chart.chartArea;
    if(!area) return;
    const css = getComputedStyle(document.documentElement);
    const cardBg = (css.getPropertyValue('--card') || '#171a21').trim();
    const textCol = (css.getPropertyValue('--text') || '#e8eaf0').trim();
    const placed = [];   // rects already drawn this pass, so labels from the bar and line datasets don't sit on top of each other
    chart.data.datasets.forEach((ds, di)=>{
      if(!ds.valueLabels) return;
      const meta = chart.getDatasetMeta(di);
      if(!meta || meta.hidden) return;
      const isBar = meta.type === 'bar';
      ctx.save();
      ctx.font = '600 9.5px -apple-system,Segoe UI,Roboto,Arial,sans-serif';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      meta.data.forEach((el, i)=>{
        const txt = ds.valueLabels[i], v = ds.data[i];
        if(!txt || v==null || !Number.isFinite(v) || !Number.isFinite(el.x) || !Number.isFinite(el.y)) return;
        const w = ctx.measureText(txt).width + 6, h = 12;
        const zeroY = isBar ? chart.scales[meta.yAxisID].getPixelForValue(0) : null;
        let up = isBar ? (el.y <= zeroY) : true;     // bar: label beyond the bar end; line: above the marker
        // a bar with an error whisker gets its label beyond the whisker's far end, not on top of it
        let anchor = el.y;
        const err = ds.errorBars ? ds.errorBars[i] : null;
        if(isBar && err!=null && Number.isFinite(err)){
          const sc = chart.scales[meta.yAxisID];
          const tip = sc.getPixelForValue(v + (up ? err : -err));
          anchor = up ? Math.max(area.top, tip) : Math.min(area.bottom, tip);
        }
        let y = up ? anchor - 4 - h/2 : anchor + 4 + h/2;
        if(y - h/2 < area.top - 14){ y = anchor + 4 + h/2; }       // no headroom above -> put it below
        if(y + h/2 > area.bottom + 14){ y = anchor - 4 - h/2; }
        const hit = yy => placed.some(r=> Math.abs(r.x - el.x) < (r.w + w)/2 && Math.abs(r.y - yy) < h);
        const step = up ? -(h+1) : (h+1);
        for(let n=0; n<4 && hit(y); n++) y += step;              // nudge away from an overlapping label
        y = Math.max(area.top - 14 + h/2, Math.min(area.bottom + 14 - h/2, y));
        placed.push({x:el.x, y, w});
        ctx.fillStyle = cardBg; ctx.globalAlpha = 0.82;
        ctx.fillRect(el.x - w/2, y - h/2, w, h);
        ctx.globalAlpha = 1;
        ctx.fillStyle = isBar ? textCol : (ds.borderColor || '#f2b84b');
        ctx.fillText(txt, el.x, y);
      });
      ctx.restore();
    });
  }
};
if(typeof Chart !== 'undefined'){ try{ Chart.register(valueLabelPlugin); }catch(e){} }

// Which Impact-Player metric the distribution (ridge + table) uses. Declared up here so it exists before the first render.
const ipMetricVal = e=> document.getElementById('ipMetric').value==='oe' ? e.oe : e.substitution_effect;

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
  const sub = yrs.length >= 2
    ? `Change, ${yrs[0]} → ${yrs[yrs.length-1]} — in each pair the grey bar is ${yrs[0]} and the coloured bar is ${yrs[yrs.length-1]} (green = went up, red = went down)`
    : (yrs[0] || 'No data');
  return `<div class="section"><h3>${title}</h3>
    <div class="row muted">${sub}</div>
    <div class="muted" style="margin:6px 0 2px"><b>How often</b> — share of deliveries (%)</div>
    <div class="chart-wrap"><canvas id="${id}-chart"></canvas></div>
    <div class="muted" style="margin:10px 0 2px"><b>How well</b> — Impact vs role mean / ball (± standard error)</div>
    <div class="chart-wrap"><canvas id="${id}-chart2"></canvas></div>
    <div id="${id}-idx" class="muted" style="margin-top:6px"></div>
    <div id="${id}-vals"></div></div>`;
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
// Signed number for deltas ('+0.20' / '-0.20'); '–' when missing.
function sgn2(v){ return v==null||!Number.isFinite(v) ? '–' : (v>=0?'+':'')+(Math.round(v*100)/100).toFixed(2); }
// 'A → B' text for a first-season / last-season value pair, so a delta is never shown without the values behind it.
function fromTo(a, b, f){ f = f||fmt; return `${a==null?'–':f(a)} → ${b==null?'–':f(b)}`; }
// Table of the raw per-season values behind the change chart (share of deliveries and Impact
// vs role mean / ball for both seasons, plus the deltas), tucked in a <details> so the
// chart stays the focus but the numbers are one click away.
function yearValuesTable(merged, yrA, yrB, keyLabel){
  const rows = merged.map(r=>`<tr><td style="text-align:left">${r.label}</td>
    <td>${r.pctA.toFixed(1)}%</td><td>${r.pctB.toFixed(1)}%</td><td class="${r.deltaPct>=0?'pos':'neg'}">${r.deltaPct>=0?'+':''}${r.deltaPct.toFixed(1)}pp</td>
    <td>${r.implA!=null?fmt(r.implA):'–'}</td><td>${r.implB!=null?fmt(r.implB):'–'}</td>
    <td class="${r.deltaImpl==null?'':(r.deltaImpl>=0?'pos':'neg')}">${sgn2(r.deltaImpl)}${r.deltaSe!=null?' <span class="muted">±'+fmt(r.deltaSe)+'</span>':''}</td></tr>`).join('');
  return `<details style="margin-top:6px"><summary class="muted" style="cursor:pointer">Show the underlying values (${yrA} and ${yrB})</summary>
    <div style="overflow-x:auto"><table class="grid-heat" style="margin-top:6px"><tr><th>${keyLabel}</th><th>% ${yrA}</th><th>% ${yrB}</th><th>Δ pp</th>
    <th>Impact ${yrA}</th><th>Impact ${yrB}</th><th>Δ Impact</th></tr>${rows}</table></div></details>`;
}
// Two separate plots (share of deliveries on top, Impact underneath) with grouped bars, one bar per
// season, and exactly ONE number printed on each bar. That keeps every plot to a single quantity
// -- no more "2.4→2.6" pairs stacked on a bar-plus-line combo -- while the change itself is carried
// by colour (the last-season bar is green if the value went up, red if it went down) and by the
// tooltip / values table, which spell out both values and the delta.
function groupedChart(canvasId, labels, datasets, yTitle, tipFn){
  safeChart(canvasId, canvasId, {
    type:'bar', data:{ labels, datasets },
    options:{ responsive:true, maintainAspectRatio:false, layout:{ padding:{ top:18, bottom:10 } },
      plugins:{ legend:{ display:false }, tooltip:{ callbacks:{ label: tipFn } } },
      scales:{ x:{ ticks:{ autoSkip:false, maxRotation:40, minRotation:20 } },
               y:{ title:{ display:true, text:yTitle } } } }
  });
}
const SEASON_A_COL = 'rgba(138,147,166,.75)';
function drawYearChart(id, rowsA, rowsB, key, keyLabel, mean, yrA, yrB){
  killChart(id+'-chart'); killChart(id+'-chart2');
  const idxBox = document.getElementById(id+'-idx');
  const vb = document.getElementById(id+'-vals');
  const pct1 = v => v==null ? null : +v.toFixed(1);
  if(yrB===undefined){
    // only one season available -- nothing to diff, show it plain. Every category is kept (no
    // top-N cutoff); low-sample ones simply carry a wider error bar.
    let rows = (rowsA||[]).slice().sort((a,b)=> (key==='zone') ? a.zone-b.zone : b.pct-a.pct);
    const labels = rows.map(r=>catLabel(r,key));
    const impl = rows.map(r=>relImpact(r,mean));
    groupedChart(id+'-chart', labels,
      [{ label:'% of deliveries', data: rows.map(r=>r.pct), backgroundColor:'rgba(91,140,255,.65)', valueLabels: rows.map(r=>r.pct.toFixed(1)) }],
      '% of deliveries', ctx=>{ const r = rows[ctx.dataIndex]; return `${catLabel(r,key)}: ${fmt(r.pct)}% of deliveries (${r.balls} balls)`; });
    groupedChart(id+'-chart2', labels,
      [{ label:'Impact vs role mean / ball', data: impl, backgroundColor: impl.map(v=>colorFor(v)), errorBars: rows.map(r=>r.se), valueLabels: impl.map(v=>v.toFixed(2)) }],
      'Impact vs mean / ball', ctx=>{ const r = rows[ctx.dataIndex]; return `${catLabel(r,key)}: Impact vs mean ${fmt(impl[ctx.dataIndex])}` + (r.se!=null ? ` ± ${fmt(r.se)}` : ''); });
    if(idxBox) idxBox.textContent = '';
    if(vb) vb.innerHTML = '';
    return;
  }
  let merged = mergeSeasons(rowsA, rowsB, key, mean);
  const idx = changeIndex(merged);
  // Every category from either season is kept (no top-14 cutoff) -- sorted so the biggest movers
  // are still easy to spot at a glance, but nothing is hidden. Both plots share this order.
  merged = merged.slice().sort((a,b)=>Math.abs(b.deltaPct)-Math.abs(a.deltaPct));
  const labels = merged.map(r=>r.label);
  // A category that one season never used has no bar (rather than a bar of 0) in that season.
  const pA = merged.map(r=> r.pctA>0 ? r.pctA : null), pB = merged.map(r=> r.pctB>0 ? r.pctB : null);
  groupedChart(id+'-chart', labels, [
    { label:`${yrA}`, data:pA, backgroundColor:SEASON_A_COL, valueLabels:pA.map(v=>v==null?null:v.toFixed(1)) },
    { label:`${yrB}`, data:pB, backgroundColor:merged.map(r=>colorFor(r.deltaPct)), valueLabels:pB.map(v=>v==null?null:v.toFixed(1)) },
  ], '% of deliveries', ctx=>{ const r = merged[ctx.dataIndex];
      return ctx.datasetIndex===0 ? `${r.label} ${yrA}: ${fmt(r.pctA)}% of deliveries`
                                  : `${r.label} ${yrB}: ${fmt(r.pctB)}% of deliveries (Δ ${r.deltaPct>=0?'+':''}${fmt(r.deltaPct)}pp vs ${yrA})`; });
  groupedChart(id+'-chart2', labels, [
    { label:`${yrA}`, data:merged.map(r=>r.implA), backgroundColor:SEASON_A_COL, errorBars:merged.map(r=>r.seA), valueLabels:merged.map(r=>r.implA==null?null:r.implA.toFixed(2)) },
    { label:`${yrB}`, data:merged.map(r=>r.implB), backgroundColor:merged.map(r=> r.deltaImpl==null ? 'rgba(91,140,255,.65)' : colorFor(r.deltaImpl)), errorBars:merged.map(r=>r.seB), valueLabels:merged.map(r=>r.implB==null?null:r.implB.toFixed(2)) },
  ], 'Impact vs mean / ball', ctx=>{ const r = merged[ctx.dataIndex];
      const own = ctx.datasetIndex===0 ? [r.implA, r.seA, yrA] : [r.implB, r.seB, yrB];
      let t = `${r.label} ${own[2]}: Impact vs mean ${fmt(own[0])}` + (own[1]!=null ? ` ± ${fmt(own[1])}` : '');
      if(ctx.datasetIndex===1 && r.deltaImpl!=null) t += ` (Δ ${sgn2(r.deltaImpl)}` + (r.deltaSe!=null ? ` ± ${fmt(r.deltaSe)}` : '') + ` vs ${yrA})`;
      return t; });
  if(vb) vb.innerHTML = yearValuesTable(merged, yrA, yrB, keyLabel);
  if(idxBox) idxBox.innerHTML = `<b>Change index: ${idx.toFixed(1)}</b> — ${idx.toFixed(1)}% of ${keyLabel.toLowerCase()} share moved to a different category from ${yrA} to ${yrB} (0 = no change in mix, 100 = totally different mix). Hover a bar for both seasons' values and the change; the table below lists every number. Whiskers on the Impact bars are each season's standard error, so low-sample categories read as noisy.`;
}
function setupYearBlock(id, obj, key, keyLabel, mean){
  const yrs = years(obj);
  if(yrs.length >= 2) drawYearChart(id, obj[yrs[0]], obj[yrs[yrs.length-1]], key, keyLabel, mean, yrs[0], yrs[yrs.length-1]);
  else if(yrs.length === 1) drawYearChart(id, obj[yrs[0]], undefined, key, keyLabel, mean);
  else { killChart(id+'-chart'); killChart(id+'-chart2'); }
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
      implA: (a && mean!=null) ? relImpact(a,mean) : null, implB: (b && mean!=null) ? relImpact(b,mean) : null,
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
      const implTxt = (c.implA!=null || c.implB!=null)
        ? `<br><span class="muted">Impact ${fromTo(c.implA, c.implB)}`+(c.deltaImpl!=null ? ` (Δ ${sgn2(c.deltaImpl)}${c.deltaSe!=null?' ±'+fmt(c.deltaSe):''})` : '')+`</span>` : '';
      t += `<td style="background:${bg}">${c.deltaPct>=0?'+':''}${c.deltaPct.toFixed(1)}pp<br><span class="muted">${c.pctA.toFixed(1)}% → ${c.pctB.toFixed(1)}%</span>${implTxt}</td>`;
    });
    t += '</tr>';
  });
  t += '</table>';
  document.getElementById(id+'-tbl').innerHTML = t;
  const idx = llChangeIndex(cells);
  const box = document.getElementById(id+'-idx');
  if(box) box.innerHTML = `<b>Change index: ${idx.toFixed(1)}</b> — ${idx.toFixed(1)}% of deliveries moved to a different length/line cell from ${yrA} to ${yrB} (0 = identical grid, 100 = totally different). Each cell shows the share and Impact in both seasons, then the change; ± is the combined standard error where both seasons have data.`;
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
['ipYear','ipTeam','ipSort','ipMetric'].forEach(id=> document.getElementById(id).onchange = renderImpactPlayer);
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
      .map(ipMetricVal).filter(Number.isFinite).sort((a,b)=>a-b);
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
  const cv = document.getElementById('ipRidge'); if(!cv) return;
  const groups = impactGroups().map(g=>{ const evs = DATA.impact_player.filter(e=>e.year===g.year && e.team_role===g.role && Number.isFinite(ipMetricVal(e)));
    return {...g, evs, vals: evs.map(e=>ipMetricVal(e)).sort((a,b)=>a-b)}; }).filter(g=>g.vals.length>=5);
  if(!groups.length) return;
  const all = groups.flatMap(g=>g.vals), n = all.length;
  const mean = all.reduce((s,v)=>s+v,0)/n, sd = Math.sqrt(all.reduce((s,v)=>s+(v-mean)**2,0)/n)||1;
  const bw = 0.9*sd*Math.pow(n,-0.2);
  const lo = Math.min(...all)-2.5*bw, hi = Math.max(...all)+2.5*bw, N = 240;
  const xs = Array.from({length:N}, (_,i)=>lo+(hi-lo)*i/(N-1));
  groups.forEach(g=>{ g.d = xs.map(x=> g.vals.reduce((s,v)=> s+Math.exp(-0.5*((x-v)/bw)**2),0)/(g.vals.length*bw*Math.sqrt(2*Math.PI))); });
  const dmax = Math.max(...groups.map(g=>Math.max(...g.d)));
  const jit = e=>{ let h = (e.match*2654435761 + (e.player_in||'').length*40503) >>> 0; h ^= h>>>13; h = Math.imul(h,0x5bd1e995)>>>0; return (h%1000)/1000; };
  const tip = document.getElementById('ipRidgeTip'), wrap = document.getElementById('ipRidgeWrap');
  const draw = ()=>{
    const css = getComputedStyle(document.documentElement), col = v=> (css.getPropertyValue(v)||'').trim() || '#888';
    const W = wrap.clientWidth || 700, dpr = window.devicePixelRatio||1;
    const rowH = 78, overlap = 1.4, rain = 24, padL = 150, padR = 24, padT = 16, padB = 36, tm = document.getElementById('ipTeam').value;
    const H = padT + rowH*(groups.length-1) + rowH*overlap + rain + padB;
    cv.width = W*dpr; cv.height = H*dpr; cv.style.height = H+'px';
    const ctx = cv.getContext('2d'); ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,W,H);
    const X = x=> padL + (x-lo)/(hi-lo)*(W-padL-padR), base0 = padT + rowH*overlap, FONT = '-apple-system,Segoe UI,Roboto,Arial,sans-serif';
    ctx.font = '12px '+FONT; ctx.strokeStyle = col('--border'); ctx.fillStyle = col('--muted'); ctx.lineWidth = 1; ctx.textAlign = 'center';
    const step = Math.pow(10, Math.floor(Math.log10((hi-lo)/6))), tk = [1,2,5].map(m=>m*step).find(t=>(hi-lo)/t<=9) || step*10;
    for(let t=Math.ceil(lo/tk)*tk; t<=hi; t+=tk){ ctx.beginPath(); ctx.moveTo(X(t),padT); ctx.lineTo(X(t),H-padB+4); ctx.stroke(); ctx.fillText(fmt(t), X(t), H-padB+18); }
    ctx.fillText((document.getElementById('ipMetric').value==='oe' ? 'Impact over expected for the substituted slot (runs, vs team median)' : 'Substitution effect vs mean same-role teammate (runs)'), padL+(W-padL-padR)/2, H-4);
    ctx.setLineDash([5,4]); ctx.strokeStyle = col('--text'); ctx.beginPath(); ctx.moveTo(X(0),padT); ctx.lineTo(X(0),H-padB+4); ctx.stroke(); ctx.setLineDash([]);
    cv._dots = [];
    groups.forEach((g,i)=>{
      const base = base0 + rowH*i, amp = rowH*overlap/dmax;
      ctx.beginPath(); ctx.moveTo(X(xs[0]), base); xs.forEach((x,k)=> ctx.lineTo(X(x), base-g.d[k]*amp)); ctx.lineTo(X(xs[N-1]), base); ctx.closePath();
      ctx.fillStyle = g.color+'99'; ctx.fill(); ctx.strokeStyle = g.color; ctx.lineWidth = 1.6; ctx.stroke();
      ctx.strokeStyle = col('--border'); ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(padL,base); ctx.lineTo(W-padR,base); ctx.stroke();
      const med = g.vals[Math.floor((g.vals.length-1)/2)], dm = g.d[xs.findIndex(x=>x>=med)]||0;
      ctx.strokeStyle = col('--text'); ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(X(med),base); ctx.lineTo(X(med),base-dm*amp); ctx.stroke();
      ctx.textAlign = 'right'; ctx.fillStyle = col('--text'); ctx.font = '600 12px '+FONT; ctx.fillText(`${g.year} · ${g.role}`, padL-10, base-6);
      ctx.fillStyle = col('--muted'); ctx.font = '11px '+FONT; ctx.fillText(`n=${g.vals.length}, median ${fmt(med)}`, padL-10, base+8); ctx.textAlign = 'center';
      g.evs.forEach(e=> cv._dots.push({x:X(ipMetricVal(e)), y:base+7+jit(e)*(rain-12), e, g, on: tm==='all'||e.team===tm}));
    });
    // rain drawn last so dots sit on top of the neighbouring cloud
    cv._dots.forEach(d=>{ ctx.globalAlpha = d.on ? 1 : .18; ctx.beginPath(); ctx.arc(d.x,d.y,d===cv._hover?5:3,0,6.283);
      ctx.fillStyle = d.g.color; ctx.fill(); ctx.lineWidth = d===cv._hover?2:1; ctx.strokeStyle = d===cv._hover?col('--text'):col('--card'); ctx.stroke(); });
    ctx.globalAlpha = 1;
  };
  cv._draw = draw; draw();
  if(!cv._ev){
    cv._ev = true;
    const move = ev=>{
      const r = cv.getBoundingClientRect(), mx = ev.clientX-r.left, my = ev.clientY-r.top; let best = null, bd = 64;
      (cv._dots||[]).forEach(d=>{ const dd = (d.x-mx)**2+(d.y-my)**2; if(dd<bd){ bd = dd; best = d; } });
      if(best!==cv._hover){ cv._hover = best; cv._draw(); }
      if(!best){ tip.style.display = 'none'; cv.style.cursor = 'default'; return; }
      const e = best.e; cv.style.cursor = 'pointer';
      tip.innerHTML = `<b>${e.player_in}</b> in for ${e.player_out}<br>${e.team} vs ${e.opponent} · ${e.date.slice(0,10)}<br>${best.g.year} · ${best.g.role} · ${e.slot_role}<br>Effect <b class="${ipMetricVal(e)>=0?'pos':'neg'}">${fmt(ipMetricVal(e))}</b> <span class="muted">${document.getElementById('ipMetric').value==='oe' ? `(bat ${fmt(e.bat_oe)} over ${e.bat_balls} balls, bowl ${fmt(e.bowl_oe)} over ${e.bowl_balls} balls; vs ${e.team} median)` : `(bat ${fmt(e.batting_vs_mean)}, bowl ${fmt(e.bowling_vs_mean)})`}</span>`;
      tip.style.display = 'block';
      const tw = tip.offsetWidth, W = wrap.clientWidth; tip.style.left = Math.max(0, Math.min(W-tw, best.x+12))+'px'; tip.style.top = (best.y+14)+'px';
    };
    cv.addEventListener('pointermove', move); cv.addEventListener('pointerdown', move);
    cv.addEventListener('pointerleave', ()=>{ cv._hover = null; tip.style.display = 'none'; cv._draw&&cv._draw(); });
    if(typeof ResizeObserver!=='undefined') new ResizeObserver(()=>cv._draw&&cv._draw()).observe(wrap);
    document.querySelectorAll('.tab').forEach(t=> t.addEventListener('click', ()=> setTimeout(()=>cv._draw&&cv._draw(),30)));
  }
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

// ============================================================ MATCHUP MODEL (Compare Players + Matchup Shifts)
// DATA.matchups holds, per scope (2023 / 2024 / all), an additive shrinkage model per outcome:
// league effects + each batter's shrunk effect overall / vs style / length / line / speed band.
// A bowler is {sty, len[6], line[4], spd[4]} = style + share of deliveries at each length/line/speed band.
const MU = DATA.matchups, MS = MU.scopes, LV = MU.levels;
const SCOPES = ['2023','2024','all'], SCOPE_LBL = {'2023':'2023','2024':'2024','all':'Both years pooled'};
const OUTC = {runs:{label:'Strike rate (runs/100 balls)', short:'SR', lowGood:false},
              impact:{label:'Impact / 100 balls', short:'Impact/100', lowGood:false},
              wkt:{label:'Dismissals / 100 balls', short:'Dismissals/100', lowGood:true}};
const dotp = (a,w)=> a.reduce((s,v,i)=>s+v*w[i],0);
const oneHot = (n,i)=> Array.from({length:n},(_,k)=>k===i?1:0);
const interp = (n,pos)=>{ const w=Array(n).fill(0), p=Math.min(n-1,Math.max(0,pos)), lo=Math.floor(p), f=p-lo; w[lo]+=1-f; if(f>0) w[Math.min(n-1,lo+1)]+=f; return w; };
const spdBand = kph=> kph>=130?0 : kph>=110?1 : kph>=85?2 : 3;
// batter-specific part only: how much better/worse than the AVERAGE batter of the same hand facing this same mix
function batEdge(scope, oc, name, p){
  const b = MS[scope].model[oc].bat[name]; if(!b) return null;
  return 100*(b.b + b.s[p.sty] + dotp(b.l,p.len) + dotp(b.n,p.line) + dotp(b.p,p.spd));
}
// full predicted outcome per 100 balls (league baseline for this hand/profile + batter edge); name=null -> average batter
// bowler's own shrunk effect (beyond style + mix) vs batters of this hand, in batter-outcome units per 100 balls
function bowEffN(scope, oc, w, hand){ const b = w && MS[scope].model[oc].bow[w]; return b ? 100*(b.b + b.h[hand==='L'?1:0]) : null; }
// full predicted outcome per 100 balls: league baseline (hand, style, mix) + batter edge (null = average batter) + bowler effect (null = average bowler)
function predict(scope, oc, name, hand, p, w){
  const m = MS[scope].model[oc], lg = m.lg, h = hand==='L'?1:0;
  const base = m.ic + lg.hand[h] + lg.sty[p.sty] + lg.hs[h][p.sty] + dotp(lg.len,p.len) + dotp(lg.hl[h],p.len) + dotp(lg.line,p.line) + dotp(lg.hn[h],p.line) + dotp(lg.spd,p.spd);
  let v = 100*base;
  if(name){ const e = batEdge(scope, oc, name, p); if(e==null) return null; v += e; }
  const be = bowEffN(scope, oc, w, hand); if(be!=null) v += be;
  return v;
}
const f1 = v=> v==null||!Number.isFinite(v) ? '–' : v.toFixed(1);
const sgn = v=> v==null||!Number.isFinite(v) ? '–' : (v>=0?'+':'')+v.toFixed(1);
// bp=true -> bowler's perspective (values already sign-flipped for Impact): fewer runs / more wickets / more bowling Impact are good
const isGood = (v, oc, bp)=> bp ? (oc==='runs' ? v<=0 : v>=0) : (OUTC[oc].lowGood ? v<=0 : v>=0);
const goodCls = (v, oc, bp)=> v==null ? '' : (isGood(v,oc,bp) ? 'pos' : 'neg');
function heatBg(v, oc, scale, bp){
  if(v==null) return '';
  const good = isGood(v, oc, bp), a = Math.min(1, Math.abs(v)/scale);
  return `background:${good?'rgba(63,178,127,':'rgba(226,86,79,'}${0.12+0.55*a})`;
}

// ---------- Compare Players ----------
const batByBalls = Object.keys(MS.all.bat).sort((a,b)=>MS.all.bat[b].balls-MS.all.bat[a].balls);
const bowlAll = Object.keys(MS.all.bowl).sort((a,b)=>MS.all.bowl[b].balls-MS.all.bowl[a].balls);
document.getElementById('cmpBatList').innerHTML = batByBalls.map(n=>`<option value="${n}">`).join('');
document.getElementById('cmpBowlList').innerHTML = bowlAll.map(n=>`<option value="${n}">`).join('');
document.getElementById('cmpStyle').innerHTML = LV.sty.map((s,i)=>`<option value="${i}">${s}</option>`).join('');
document.getElementById('cmpA').value = batByBalls[0]; document.getElementById('cmpB').value = batByBalls[1];
document.getElementById('cmpBowl').value = bowlAll[0];
document.getElementById('cmpStyle').value = '0';
['cmpMode','cmpOutcome','cmpSrc','cmpStyle','cmpLen','cmpLine','cmpSpd','cmpA','cmpB','cmpBowl'].forEach(id=>{
  const el = document.getElementById(id); el.addEventListener('input', renderCompare); el.addEventListener('change', renderCompare);
});
function customProfile(){
  const L = +document.getElementById('cmpLen').value, N = +document.getElementById('cmpLine').value, K = +document.getElementById('cmpSpd').value;
  return { sty:+document.getElementById('cmpStyle').value, len: interp(6, L-1), line: interp(4, N-1), spd: oneHot(4, spdBand(K)), _L:L, _N:N, _K:K };
}
function profileFor(scope){   // real bowler: that scope's own mix (fallback to pooled if too few balls that year)
  if(document.getElementById('cmpSrc').value==='custom') return {p:customProfile(), note:''};
  const nm = document.getElementById('cmpBowl').value, sc = MS[scope].bowl[nm];
  if(sc) return {p:sc, note:''};
  const al = MS.all.bowl[nm]; return al ? {p:al, note:'(<30 balls that year — pooled mix used)'} : {p:null, note:''};
}
function renderCompareBat(){
  const mode = document.getElementById('cmpMode').value, oc = document.getElementById('cmpOutcome').value;
  const custom = document.getElementById('cmpSrc').value==='custom';
  document.getElementById('cmpB').style.display = mode==='bvbat' ? '' : 'none';
  document.getElementById('cmpCustom').style.display = custom ? '' : 'none';
  document.getElementById('cmpBowl').style.display = custom ? 'none' : '';
  const cp = customProfile();
  document.getElementById('cmpLenLbl').textContent = `${cp._L.toFixed(1)} (${LV.len[Math.round(cp._L)-1].replace(/_/g,' ').toLowerCase()})`;
  document.getElementById('cmpLineLbl').textContent = `${cp._N.toFixed(1)} (${LV.line[Math.round(cp._N)-1].replace(/_/g,' ').toLowerCase()})`;
  document.getElementById('cmpSpdLbl').textContent = `${cp._K} kph (${LV.spd[spdBand(cp._K)]} band)`;
  const out = document.getElementById('cmpOut');
  const A = document.getElementById('cmpA').value, B = document.getElementById('cmpB').value;
  const bats = mode==='bvbat' ? [A,B] : [A];
  if(bats.some(n=>!MS.all.bat[n])){ out.innerHTML = '<p class="muted">Pick batter(s) from the list (batters with at least 30 balls faced).</p>'; return; }
  if(!custom && !MS.all.bowl[document.getElementById('cmpBowl').value]){ out.innerHTML = '<p class="muted">Pick a bowler from the list (at least 30 balls bowled).</p>'; return; }
  const hand = n=> MS.all.bat[n].hand;
  const prof0 = profileFor('all').p;
  document.getElementById('cmpProfileInfo').innerHTML = custom ? '' :
    `${LV.sty[prof0.sty]} · ${prof0.balls} balls · avg length ${(dotp(prof0.len,[1,2,3,4,5,6])).toFixed(1)}/6 · avg line ${(dotp(prof0.line,[1,2,3,4])).toFixed(1)}/4 · 130+kph ${(prof0.spd[0]*100).toFixed(0)}%. Each season column below uses that season's own mix for the bowler.`;
  const wn = custom ? null : document.getElementById('cmpBowl').value;
  // main table: one row per scope
  const rows = SCOPES.map(sc=>{
    const {p, note} = profileFor(sc); if(!p) return null;
    const r = {sc, note};
    bats.forEach((n,i)=>{ const inS = !!MS[sc].model[oc].bat[n]; r['v'+i] = inS ? predict(sc,oc,n,hand(n),p,wn) : null;
      r['e'+i] = inS ? batEdge(sc,oc,n,p) : null; r['b'+i] = inS ? MS[sc].bat[n].balls : 0; r['s'+i] = inS ? MS[sc].bat[n].sty[p.sty] : 0;
      r['avg'+i] = predict(sc,oc,null,hand(n),p,wn); });
    r.bw = wn ? bowEffN(sc,oc,wn,hand(bats[0])) : null;
    return r;
  }).filter(Boolean);
  let html = `<div class="section"><h3>${mode==='bvb' ? `${A} vs ${custom?'custom profile':document.getElementById('cmpBowl').value}` : `${A} vs ${B}`} — ${OUTC[oc].label}</h3>`;
  html += `<div class="chart-wrap"><canvas id="cmpChart"></canvas></div>`;
  html += '<table style="margin-top:10px"><tr><th>Season</th>' + bats.map(n=>`<th>${n} predicted</th><th>vs avg ${hand(n)}HB</th><th>Balls (vs this style)</th>`).join('') + (mode==='bvbat'?'<th>A − B</th>':'') + '</tr>';
  rows.forEach(r=>{
    html += `<tr><td><b>${SCOPE_LBL[r.sc]}</b> <span class="muted">${r.note}</span>${r.bw!=null?`<br><span class="muted">bowler effect ${sgn(r.bw)}</span>`:''}</td>`;
    bats.forEach((n,i)=>{ html += `<td>${f1(r['v'+i])}</td><td class="${goodCls(r['e'+i],oc)}">${sgn(r['e'+i])}</td><td class="muted">${r['b'+i]||'<30'} (${r['s'+i]})</td>`; });
    if(mode==='bvbat'){ const d = (r.v0!=null&&r.v1!=null) ? r.v0-r.v1 : null; html += `<td class="${goodCls(d,oc)}"><b>${sgn(d)}</b></td>`; }
    html += '</tr>';
  });
  html += `</table><p class="note">“Predicted” = league baseline for a batter of that hand vs this delivery mix plus the batter's shrunk matchup edge and, for a real bowler, that bowler's own shrunk effect (shown under the season). “vs avg” isolates the batter's edge over an average same-hand batter. Green = favours the batter (for dismissals, fewer is better). Edges are shrunk toward zero for small samples, so treat gaps under ~3 SR points as noise.</p></div>`;
  // breakdown by delivery type, per season
  const dims = [['Style',LV.sty,'s'],['Length',LV.len.map(x=>x.replace(/_/g,' ').toLowerCase()),'l'],['Line',LV.line.map(x=>x.replace(/_/g,' ').toLowerCase()),'n'],['Speed',LV.spd,'p']];
  const sc2 = ['2023','2024'];
  let scale = 0.01; bats.forEach(n=>sc2.forEach(sc=>{ const b = MS[sc].model[oc].bat[n]; if(b) dims.forEach(d=>b[d[2]].forEach(v=>{scale=Math.max(scale,Math.abs(v*100));})); }));
  scale = Math.max(scale*0.7, 0.5);
  html += `<div class="section"><h3>Where each batter's edge comes from, by season</h3><table class="grid-heat"><tr><th>Delivery type</th>` +
    bats.map(n=>sc2.map(sc=>`<th>${n}<br>${sc}</th>`).join('')).join('') + '</tr>';
  dims.forEach(([dn,labels,k])=>{
    labels.forEach((lb,i)=>{
      html += `<tr><th style="text-align:left">${i===0?`<span class="pill">${dn}</span> `:''}${lb}</th>`;
      bats.forEach(n=> sc2.forEach(sc=>{ const b = MS[sc].model[oc].bat[n]; const v = b ? b[k][i]*100 : null;
        html += `<td style="${heatBg(v,oc,scale)}">${sgn(v)}</td>`; }));
      html += '</tr>';
    });
  });
  html += `</table><p class="note">Each cell = the batter's shrunk effect (${OUTC[oc].short}) vs an average same-hand batter against that delivery type, separately for each season — compare the 2023 and 2024 columns to see what changed. “–” = fewer than 30 balls that season.</p></div>`;
  out.innerHTML = html;
  killChart('cmpChart');
  const palette = ['#5b8cff','#f2b84b'];
  const ds = bats.map((n,i)=>({label:n, data: rows.map(r=>r['v'+i]), backgroundColor: palette[i]+'cc'}));
  if(mode==='bvb') ds.push({label:'Average '+hand(A)+'HB (same mix)', data: rows.map(r=>r.avg0), backgroundColor:'rgba(154,162,177,.6)'});
  safeChart('cmpChart','cmpChart',{type:'bar', data:{labels: rows.map(r=>SCOPE_LBL[r.sc]), datasets:ds},
    options:{responsive:true, maintainAspectRatio:false, scales:{y:{title:{display:true,text:OUTC[oc].label}}}}});
}

document.getElementById('cmpBX').value = bowlAll[0]; document.getElementById('cmpBY').value = bowlAll[1];
['cmpBX','cmpBY','cmpCtx'].forEach(id=>{ const el = document.getElementById(id); el.addEventListener('input', renderCompare); el.addEventListener('change', renderCompare); });
function renderCompare(){
  const mode = document.getElementById('cmpMode').value, bm = mode==='bowl' || mode==='bowlbowl';
  document.getElementById('cmpProfileSec').style.display = bm ? 'none' : '';
  document.getElementById('cmpBowlerRow').style.display = bm ? '' : 'none';
  if(!bm){ document.getElementById('cmpA').style.display = ''; return renderCompareBat(); }
  document.getElementById('cmpB').style.display = 'none';
  document.getElementById('cmpA').style.display = document.getElementById('cmpCtx').value==='batter' ? '' : 'none';
  document.getElementById('cmpBY').style.display = mode==='bowlbowl' ? '' : 'none';
  renderCompareBowl(mode);
}
// Bowler's view: everything is shown as the bowler would read it (runs conceded, wickets, bowling Impact = -batter Impact).
function renderCompareBowl(mode){
  const oc = document.getElementById('cmpOutcome').value, out = document.getElementById('cmpOut'), ctx = document.getElementById('cmpCtx').value, A = document.getElementById('cmpA').value;
  const ws = [document.getElementById('cmpBX').value].concat(mode==='bowlbowl' ? [document.getElementById('cmpBY').value] : []);
  if(ws.some(w=>!MS.all.bowl[w])){ out.innerHTML = '<p class="muted">Pick bowler(s) from the list (at least 30 balls bowled).</p>'; return; }
  if(ctx==='batter' && !MS.all.bat[A]){ out.innerHTML = '<p class="muted">Pick Batter A from the list, or choose an average right/left-hander.</p>'; return; }
  const bn = ctx==='batter' ? A : null, hand = ctx==='batter' ? MS.all.bat[A].hand : ctx, hi = hand==='L'?1:0, sg = oc==='impact' ? -1 : 1;
  const lab = {runs:'Runs conceded / 100 balls', impact:'Bowling Impact / 100 balls', wkt:'Wickets / 100 balls'}[oc];
  const ctxLbl = bn || `an average ${hand==='R'?'right':'left'}-hander`;
  const rows = SCOPES.map(sc=>{ const r = {sc};
    ws.forEach((w,i)=>{ const own = MS[sc].bowl[w], p = own || MS.all.bowl[w], inS = !!MS[sc].model[oc].bow[w];
      const full = predict(sc,oc,bn,hand,p,inS?w:null), base = predict(sc,oc,bn,hand,p,null);
      r['v'+i] = full==null ? null : sg*full; r['avg'+i] = base==null ? null : sg*base;
      r['e'+i] = inS ? sg*bowEffN(sc,oc,w,hand) : null; r['b'+i] = own ? own.balls : 0; });
    return r; });
  let html = `<div class="section"><h3>${ws.join(' vs ')} facing ${ctxLbl} — ${lab}</h3><div class="chart-wrap"><canvas id="cmpChart"></canvas></div>`;
  html += '<table style="margin-top:10px"><tr><th>Season</th>' + ws.map(w=>`<th>${w} predicted</th><th>Bowler effect vs avg bowler</th><th>Balls bowled</th>`).join('') + (ws.length>1?'<th>X − Y</th>':'') + '</tr>';
  rows.forEach(r=>{ html += `<tr><td><b>${SCOPE_LBL[r.sc]}</b></td>`;
    ws.forEach((w,i)=>{ html += `<td>${f1(r['v'+i])}</td><td class="${goodCls(r['e'+i],oc,true)}">${sgn(r['e'+i])}</td><td class="muted">${r['b'+i]||'<30'}</td>`; });
    if(ws.length>1){ const d = (r.v0!=null&&r.v1!=null) ? r.v0-r.v1 : null; html += `<td class="${goodCls(d,oc,true)}"><b>${sgn(d)}</b></td>`; }
    html += '</tr>'; });
  html += `</table><p class="note">Predicted = league baseline for ${ctxLbl} against each bowler's own mix of length/line/speed that season, plus ${bn?"the batter's shrunk edge against that mix, plus ":''}the bowler's own shrunk effect vs ${hand==='R'?'right':'left'}-handers. “Bowler effect” isolates that last piece (what he adds beyond his style and where he bowls). Green = good for the bowler (fewer runs, more wickets, more Impact). Treat gaps under ~3 runs/100 as noise.</p></div>`;
  // effect vs batter hand, per season
  const sc2 = ['2023','2024']; let scale = 0.5;
  ws.forEach(w=>sc2.forEach(sc=>{ const b = MS[sc].model[oc].bow[w]; if(b) [b.b+b.h[0], b.b+b.h[1]].forEach(v=>{ scale = Math.max(scale, Math.abs(v*100)*0.7); }); }));
  html += `<div class="section"><h3>Bowler effect by batter hand, per season</h3><table class="grid-heat"><tr><th>Facing</th>` + ws.map(w=>sc2.map(sc=>`<th>${w}<br>${sc}</th>`).join('')).join('') + '</tr>';
  [['Right-handers',0],['Left-handers',1]].forEach(([lb,k])=>{ html += `<tr><th style="text-align:left">${lb}</th>`;
    ws.forEach(w=>sc2.forEach(sc=>{ const b = MS[sc].model[oc].bow[w], v = b ? sg*100*(b.b+b.h[k]) : null; html += `<td style="${heatBg(v,oc,scale,true)}">${sgn(v)}</td>`; })); html += '</tr>'; });
  html += `</table><p class="note">Shrunk effect in ${lab.toLowerCase()} vs an average bowler of the same style and mix. “–” = fewer than 30 balls that season.</p></div>`;
  // where he bowls + how well that works vs this hand (league-wide)
  const lg = MS.all.model[oc].lg;
  const dims = [['Length',LV.len.map(x=>x.replace(/_/g,' ').toLowerCase()),'len',lg.len.map((v,i)=>v+lg.hl[hi][i])],
                ['Line',LV.line.map(x=>x.replace(/_/g,' ').toLowerCase()),'line',lg.line.map((v,i)=>v+lg.hn[hi][i])],
                ['Speed',LV.spd,'spd',lg.spd]];
  const vs = dims.flatMap(d=>d[3]).map(v=>Math.abs(v*100)); const sc3 = Math.max(0.5, Math.max(...vs)*0.8);
  html += `<div class="section"><h3>Where each bowler bowls, per season</h3><table class="grid-heat"><tr><th>Delivery type</th>` + ws.map(w=>sc2.map(sc=>`<th>${w}<br>${sc} share</th>`).join('')).join('') + `<th>League value vs ${hand==='R'?'right':'left'}-handers<br>(${lab.replace(' / 100 balls','')})</th></tr>`;
  dims.forEach(([dn,labels,k,val])=> labels.forEach((lb,i)=>{
    html += `<tr><th style="text-align:left">${i===0?`<span class="pill">${dn}</span> `:''}${lb}</th>`;
    ws.forEach(w=>sc2.forEach(sc=>{ const p = MS[sc].bowl[w]; html += `<td>${p ? (p[k][i]*100).toFixed(0)+'%' : '–'}</td>`; }));
    const v = sg*100*val[i]; html += `<td style="${heatBg(v,oc,sc3,true)}">${sgn(v)}</td></tr>`; }));
  html += `</table><p class="note">Share = % of that bowler's deliveries (that season) at each length / line / speed band. The last column is what the league as a whole gets from that delivery type against this hand, relative to an average delivery (green = good for the bowler) — a quick read on whether he is bowling where it works.</p></div>`;
  out.innerHTML = html;
  killChart('cmpChart');
  const pal = ['#5b8cff','#f2b84b'];
  const ds = ws.map((w,i)=>({label:w, data:rows.map(r=>r['v'+i]), backgroundColor:pal[i]+'cc'}));
  if(ws.length===1) ds.push({label:'Average bowler (same mix)', data:rows.map(r=>r.avg0), backgroundColor:'rgba(154,162,177,.6)'});
  safeChart('cmpChart','cmpChart',{type:'bar', data:{labels:rows.map(r=>SCOPE_LBL[r.sc]), datasets:ds}, options:{responsive:true, maintainAspectRatio:false, scales:{y:{title:{display:true,text:lab}}}}});
}
safeRun(renderCompare,'compare');

// ---------- Matchup Shifts (2023 -> 2024) ----------
['shView','shOutcome','shMin','shPersp'].forEach(id=> document.getElementById(id).onchange = renderShifts);
document.getElementById('shView').addEventListener('change',()=>{ document.getElementById('shPersp').value = ['bhand','bmix'].includes(document.getElementById('shView').value) ? 'bowl' : 'bat'; renderShifts(); });
document.getElementById('shFilter').addEventListener('input', renderShifts);
const typicalStyleProfile = {};
function styleProfile(s){
  if(typicalStyleProfile[s]) return typicalStyleProfile[s];
  const bs = Object.values(MS.all.bowl).filter(b=>b.sty===s), tot = bs.reduce((x,b)=>x+b.balls,0)||1;
  const avg = k=> bs.length ? bs[0][k].map((_,i)=>bs.reduce((x,b)=>x+b[k][i]*b.balls,0)/tot) : null;
  return typicalStyleProfile[s] = {sty:s, len:avg('len')||oneHot(6,2), line:avg('line')||oneHot(4,1), spd:avg('spd')||oneHot(4,1)};
}
function renderShifts(){
  const view = document.getElementById('shView').value, oc = document.getElementById('shOutcome').value, bp = document.getElementById('shPersp').value==='bowl';
  const minB = +document.getElementById('shMin').value, ft = document.getElementById('shFilter').value.trim().toLowerCase();
  const sg = (bp && oc==='impact') ? -1 : 1, S = v=> v==null ? null : sg*v, a = MS['2023'], b = MS['2024'];
  const bats = Object.keys(a.bat).filter(n=> b.bat[n] && a.bat[n].balls>=minB && b.bat[n].balls>=minB);
  const bowls = Object.keys(a.bowl).filter(n=> b.bowl[n] && a.bowl[n].balls>=minB && b.bowl[n].balls>=minB && a.model[oc].bow[n] && b.model[oc].bow[n]);
  const gc = v=> goodCls(v,oc,bp), edgeCols = (k23,k24)=>[
    {label:'Edge 2023',get:r=>r[k23],render:r=>sgn(r[k23]),cls:r=>gc(r[k23])},{label:'Edge 2024',get:r=>r[k24],render:r=>sgn(r[k24]),cls:r=>gc(r[k24])}];
  const dcol = {label:'Δ',get:r=>r.d,render:r=>`<b>${sgn(r.d)}</b>`,cls:r=>gc(r.d)};
  const nm = (n,w)=> !ft || (n && n.toLowerCase().includes(ft)) || (w && w.toLowerCase().includes(ft));
  const lbl = s=>s.replace(/_/g,' ').toLowerCase();
  let cols, rows = [], note;
  if(view==='pair'){
    bats.forEach(n=> bowls.forEach(w=>{
      if(!nm(n,w)) return;
      const p23 = a.bowl[w], p24 = b.bowl[w], hd = a.bat[n].hand;
      const v23 = predict('2023',oc,n,hd,p23,w), v24 = predict('2024',oc,n,hd,p24,w);
      const mix = predict('2023',oc,n,hd,p24,null) - predict('2023',oc,n,hd,p23,null);
      const env = predict('2024',oc,null,hd,p24,null) - predict('2023',oc,null,hd,p24,null);
      const bat = batEdge('2024',oc,n,p24) - batEdge('2023',oc,n,p24), bowl = bowEffN('2024',oc,w,hd) - bowEffN('2023',oc,w,hd);
      if([v23,v24,mix,env,bat,bowl].some(v=>v==null||!Number.isFinite(v))) return;
      rows.push({n,w,hd,v23:S(v23),v24:S(v24),d:S(v24-v23-env),bat:S(bat),bowl:S(bowl),mix:S(mix),env:S(env)});
    }));
    rows.sort((x,y)=>Math.abs(y.d)-Math.abs(x.d)); rows = rows.slice(0,500);
    cols = [{label:'Batter',get:r=>r.n},{label:'Bowler',get:r=>r.w},{label:'Hand',get:r=>r.hd},
      {label:'Predicted 2023',get:r=>r.v23,render:r=>f1(r.v23)},{label:'Predicted 2024',get:r=>r.v24,render:r=>f1(r.v24)},
      {...dcol,label:'Δ excl. league shift'},
      {label:'…batter changed',get:r=>r.bat,render:r=>sgn(r.bat),cls:r=>gc(r.bat)},{label:'…bowler skill changed',get:r=>r.bowl,render:r=>sgn(r.bowl),cls:r=>gc(r.bowl)},
      {label:'…bowler mix changed',get:r=>r.mix,render:r=>sgn(r.mix),cls:r=>gc(r.mix)},{label:'League shift (removed)',get:r=>r.env,render:r=>sgn(r.env)}];
    note = `${bats.length} batters × ${bowls.length} bowlers who qualify in both seasons; top 500 pairs by |Δ excl. league shift|. Predicted = full ${OUTC[oc].short} for that pairing using each season's own bowler mix. The change is split four ways and adds up exactly: the batter's skill changing, the bowler's own skill changing, the bowler moving his length/line/speed mix (incl. how that batter handles the new mix), and the league-wide season shift (removed from the headline Δ).`;
  } else if(view==='style'){
    bats.forEach(n=> LV.sty.forEach((sn,s)=>{
      if(!nm(n)) return; const bs23 = a.bat[n].sty[s], bs24 = b.bat[n].sty[s]; if(bs23<15 || bs24<15) return;
      const p = styleProfile(s), e23 = S(batEdge('2023',oc,n,p)), e24 = S(batEdge('2024',oc,n,p)); rows.push({n,sn,bs23,bs24,e23,e24,d:e24-e23}); }));
    rows.sort((x,y)=>Math.abs(y.d)-Math.abs(x.d));
    cols = [{label:'Batter',get:r=>r.n},{label:'Bowling style',get:r=>r.sn},{label:'Balls 2023',get:r=>r.bs23},{label:'Balls 2024',get:r=>r.bs24},...edgeCols('e23','e24'),dcol];
    note = `Batter vs the league-typical bowler of each style (same delivery mix both seasons, so purely the batter changing). Needs ≥15 balls vs that style in both seasons.`;
  } else if(view==='dim'){
    const dims = [['Length',LV.len.map(lbl),'l'],['Line',LV.line.map(lbl),'n'],['Speed',LV.spd,'p']];
    bats.forEach(n=>{ if(!nm(n)) return; dims.forEach(([dn,labels,k])=> labels.forEach((lb,i)=>{
      const e23 = S(a.model[oc].bat[n][k][i]*100), e24 = S(b.model[oc].bat[n][k][i]*100); rows.push({n,dn,lb,e23,e24,d:e24-e23}); })); });
    rows.sort((x,y)=>Math.abs(y.d)-Math.abs(x.d)); rows = rows.slice(0,500);
    cols = [{label:'Batter',get:r=>r.n},{label:'Dimension',get:r=>r.dn},{label:'Delivery type',get:r=>r.lb},...edgeCols('e23','e24'),dcol];
    note = `Change in each batter's shrunk effect vs a length, line or speed band (top 500 by |Δ|). Shrinkage means a big move here had a lot of balls behind it.`;
  } else if(view==='bhand'){
    bowls.forEach(w=>{ if(!nm(null,w)) return; [['R',0],['L',1]].forEach(([hn,k])=>{
      const m23 = a.model[oc].bow[w], m24 = b.model[oc].bow[w], e23 = S(100*(m23.b+m23.h[k])), e24 = S(100*(m24.b+m24.h[k]));
      rows.push({w,hn:hn==='R'?'Right-handers':'Left-handers',e23,e24,d:e24-e23,b23:a.bowl[w].balls,b24:b.bowl[w].balls}); }); });
    rows.sort((x,y)=>Math.abs(y.d)-Math.abs(x.d));
    cols = [{label:'Bowler',get:r=>r.w},{label:'Facing',get:r=>r.hn},{label:'Balls 2023',get:r=>r.b23},{label:'Balls 2024',get:r=>r.b24},
      {label:'Effect 2023',get:r=>r.e23,render:r=>sgn(r.e23),cls:r=>gc(r.e23)},{label:'Effect 2024',get:r=>r.e24,render:r=>sgn(r.e24),cls:r=>gc(r.e24)},dcol];
    note = `The bowler's own shrunk effect (beyond his style and mix) against right- and left-handers, each season.`;
  } else {
    const lg = MS.all.model[oc].lg, dims = [['Length',LV.len.map(lbl),'len',lg.len.map((v,i)=>(v+(lg.hl[0][i]+lg.hl[1][i])/2))],
      ['Line',LV.line.map(lbl),'line',lg.line.map((v,i)=>(v+(lg.hn[0][i]+lg.hn[1][i])/2))],['Speed',LV.spd,'spd',lg.spd]];
    bowls.forEach(w=>{ if(!nm(null,w)) return; dims.forEach(([dn,labels,k,val])=> labels.forEach((lb,i)=>{
      const s23 = a.bowl[w][k][i]*100, s24 = b.bowl[w][k][i]*100; rows.push({w,dn,lb,s23,s24,d:s24-s23,val:S(100*val[i])}); })); });
    rows.sort((x,y)=>Math.abs(y.d)-Math.abs(x.d)); rows = rows.slice(0,500);
    cols = [{label:'Bowler',get:r=>r.w},{label:'Dimension',get:r=>r.dn},{label:'Delivery type',get:r=>r.lb},
      {label:'Share 2023',get:r=>r.s23,render:r=>r.s23.toFixed(0)+'%'},{label:'Share 2024',get:r=>r.s24,render:r=>r.s24.toFixed(0)+'%'},
      {label:'Δ share (pp)',get:r=>r.d,render:r=>`<b>${sgn(r.d)}</b>`},{label:'League value of this delivery',get:r=>r.val,render:r=>sgn(r.val),cls:r=>gc(r.val)}];
    note = `How each bowler moved where he bowls (top 500 by |Δ share|). “League value” = what the whole league gets from that delivery type vs an average delivery (green = good for the bowler), so you can see whether he moved toward or away from what works.`;
  }
  document.getElementById('shNote').innerHTML = note + (bp ? ' Bowler\'s perspective: green = good for the bowler (fewer runs, more wickets, more bowling Impact).' : ' Positive Δ = '+(OUTC[oc].lowGood?'more dismissals (worse for the batter)':'better for the batter')+'.')+' Click a header to sort.';
  renderSortableTable('shTable', cols, rows);
}
safeRun(renderShifts,'shifts');
