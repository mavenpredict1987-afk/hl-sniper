
function toggle(id){
  var el=document.getElementById(id);
  el.classList.toggle('open');
}
document.getElementById('eq-card').addEventListener('click',function(e){
  if(e.target.closest('.acc-inner'))return;
  document.getElementById('eq-acc').classList.toggle('open');
});
function fmt(x,d){ if(x===undefined||x===null)return '-'; d=(d===undefined)?2:d; return Number(x).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d}); }
function usd(x){ return (x>=0?'+':'')+'$'+fmt(Math.abs(x)); }
function tm(ts){ var d=new Date(ts*1000); function p(n){return (n<10?'0':'')+n} return p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds()); }
function hm(sec){ sec=Math.floor(sec); var h=Math.floor(sec/3600),m=Math.floor((sec%3600)/60); if(h>0)return h+'h '+m+'m'; return m+'m '+Math.floor(sec%60)+'s'; }
function inCell(p){ return !!p && p.trend==='trend' && p.vol==='turb'; }
// The server runs in UTC, the phone does not. Everything time-based is shown
// in the phone's own clock, computed from the epoch, so 02:45 never shows up
// next to a local 06:05 and looks like a three-hour outage.
function clock(ts){ try{ return new Date(Number(ts)*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}); }catch(e){ return ''; } }
function when(e){ if(e&&typeof e==='object'&&e.ts!==undefined)return clock(e.ts); return (e&&e.time)?e.time:''; }
function eventLine(e){ if(e&&typeof e==='object')return (e.ts!==undefined?clock(e.ts)+' | ':'')+(e.msg||''); return String(e); }
function cls(x){ return x>0?'pos':(x<0?'neg':'neu'); }

// Registry of every strategy and module: tabs by category, one compact row per
// decision, tap to open the measurement that put it there. The state lives
// outside the render loop so the open row and the chosen tab survive the 5s
// refresh instead of snapping back to the top.
var STABS=[['strategy','Стратегии'],['filter','Фильтры'],['risk','Риск'],['infra','Инфра']];
var SLAB={on:['работает','on'],paper:['бумажная','warn'],off:['выключен','off'],
          rejected:['отклонён','neg'],missing:['нет','off']};
var stTab='strategy', stOpen={}, stLast=null;
function renderStrategies(){
  var sm=stLast; if(!sm)return;
  var items=sm.items||[];
  document.getElementById('strat-tabs').innerHTML=STABS.map(function(g){
    var n=items.filter(function(i){return i.cat===g[0];}).length;
    return '<div class="tab'+(stTab===g[0]?' on':'')+'" onclick="setStTab(\''+g[0]+'\')">'+
      g[1]+'<span class="tn">'+n+'</span></div>';
  }).join('');
  var rows=items.filter(function(i){return i.cat===stTab;});
  var sh='<div class="strat-list">';
  rows.forEach(function(i){
    var L=SLAB[i.status]||[i.status,''];
    sh+='<div class="strat'+(stOpen[i.id]?' open':'')+'">'+
      '<div class="strat-row" onclick="toggleStrat(\''+i.id+'\')">'+
      '<span class="sdot '+L[1]+'"></span><span class="sname">'+i.name+'</span>'+
      '<span class="sstat '+L[1]+'">'+L[0]+'</span><span class="schev">&#9654;</span></div>'+
      (i.live?'<div class="slive">'+i.live+
        (i.live_detail?' <span class="coin-dim">· '+i.live_detail+'</span>':'')+'</div>':'')+
      '<div class="strat-body"><div class="sev">'+i.evidence+'</div></div></div>';
  });
  sh+='</div>';
  document.getElementById('strat').innerHTML=sh;
}
function setStTab(c){ stTab=c; renderStrategies(); }
function toggleStrat(id){ stOpen[id]=!stOpen[id]; renderStrategies(); }

// What the bot knew when it opened the position, as opposed to what the chart
// says now. Stored with the position so it survives a restart.
function posCtx(c){
  if(!c)return '';
  var bits=[];
  if(c.score!==undefined&&c.score!==null) bits.push('score '+fmt(c.score,3));
  if(c.strength) bits.push(c.strength);
  if(c.regime) bits.push('regime '+c.regime);
  if(c.edge_pct!==undefined&&c.edge_pct!==null) bits.push('edge '+fmt(c.edge_pct)+'%');
  if(c.cost_pct!==undefined&&c.cost_pct!==null) bits.push('cost '+fmt(c.cost_pct)+'%');
  if(c.coverage_x!==undefined&&c.coverage_x!==null) bits.push('cover '+fmt(c.coverage_x,1)+'x');
  if(c.cluster) bits.push('cluster '+c.cluster);
  if(!bits.length)return '';
  return '<div style="opacity:.6;font-size:11px;margin-top:6px">'+bits.join(' &middot; ')+'</div>';
}

function spark(data){
  var w=600,h=90;
  var min=Infinity,max=-Infinity,i;
  for(i=0;i<data.length;i++){ var v=data[i][1]; if(v<min)min=v; if(v>max)max=v; }
  if(max-min<1e-9){max=min+1;}
  var pad=6, step=(w-2*pad)/(data.length-1), pts='', pts2='';
  for(i=0;i<data.length;i++){
    var x=pad+i*step;
    var y=h-pad-((data[i][1]-min)/(max-min))*(h-2*pad);
    pts+=x.toFixed(1)+','+y.toFixed(1)+' ';
    pts2+=x.toFixed(1)+','+(h-pad)+' ';
  }
  var up=data[data.length-1][1]>=data[0][1];
  var col=up?'#5cf2c9':'#f87171';
  return '<svg viewBox="0 0 '+w+' '+h+'" preserveAspectRatio="none" style="width:100%;height:90px;display:block">'+
    '<polygon points="'+(pts+pts2).trim()+'" fill="'+(up?'rgba(92,242,201,.08)':'rgba(248,113,113,.08)')+'"/>'+
    '<polyline points="'+pts.trim()+'" fill="none" stroke="'+col+'" stroke-width="2"/></svg>';
}

function render(s){
  document.getElementById('dot').className='dot '+(s.status==='running'?'on':'off');
  var mb=document.getElementById('mode-badge');
  mb.textContent=s.mode.toUpperCase();
  mb.className='badge '+(s.mode==='live'?'live':'paper');
  document.getElementById('halt-badge').style.display=s.halted?'inline-block':'none';
  document.getElementById('dayhalt-badge').style.display=s.day_halt?'inline-block':'none';
  document.getElementById('upd').textContent='обновлено '+tm(Date.now()/1000);

  // status strip
  var blocked=s.halted||s.day_halt;
  document.getElementById('v-bot').textContent=s.status;
  document.getElementById('lamp-bot').className='lamp '+(s.status==='running'?'g':'r');
  document.getElementById('v-ent').textContent=blocked?'запрещены':'разрешены';
  document.getElementById('lamp-ent').className='lamp '+(blocked?'a':'g');
  document.getElementById('v-dd').textContent=fmt(s.drawdown_pct)+'%';
  document.getElementById('lamp-dd').className='lamp '+(s.drawdown_pct>=10?'r':(s.drawdown_pct>=5?'a':'g'));
  document.getElementById('v-risk').textContent=fmt(s.open_risk_pct)+'%';
  document.getElementById('lamp-risk').className='lamp b';

  // prices
  var ph='';
  (s.symbols||[]).forEach(function(sym){
    var px=(s.prices&&s.prices[sym])||0;
    ph+='<div class="pchip"><span class="sym">'+sym+'</span>'+fmt(px, px<10?4:2)+'</div>';
  });
  document.getElementById('prices').innerHTML=ph||'<div class="empty">жду цены...</div>';

  // active configuration: the guards are invisible otherwise, and a coin that
  // silently stops trading needs a reason on screen
  var st=s.settings||{}, chips=[];
  function chip(t,k){ chips.push('<span class="chip '+(k||'')+'">'+t+'</span>'); }
  chip(st.shorts_enabled?'лонги + шорты':'только лонги', st.shorts_enabled?'':'off');
  chip(st.atr_trail_mult>0?('трейл '+st.atr_trail_mult+'\u00d7ATR'):'без трейла',
       st.atr_trail_mult>0?'on':'');
  chip('частичный выход '+Math.round((st.partial_ratio||0)*100)+'% на '+st.partial_at_r+'R');
  chip('стоп '+st.atr_stop_mult+'\u00d7ATR');
  if(st.max_direction_risk_pct!=null) chip('на направление '+fmt(st.max_direction_risk_pct*100,1)+'%');
  chip('кластеров '+(st.clusters||0));
  if(st.daily_stop_pct!=null) chip('дневной стоп '+fmt(st.daily_stop_pct*100,1)+'%');
  if(st.price_poll_sec) chip('опрос '+st.price_poll_sec+'с');
  if(s.consec_losses>0) chip(s.consec_losses+' убытков подряд', s.paused?'warn':''); 
  var bench=s.benched||[];
  if(bench.length) chip('в бане: '+bench.join(', '),'warn');
  document.getElementById('cfg-row').innerHTML=chips.join('');
  var notes=[];
  if(s.paused) notes.push('новые входы на паузе');
  if(s.halted) notes.push('остановлен: достигнут лимит просадки');
  if(s.day_halt) notes.push('достигнут дневной лимит убытка');
  if(bench.length) notes.push('монеты из бана вернутся сами после паузы');
  document.getElementById('cfg-note').textContent=notes.join(' \u00b7 ');

  // pairs module: the one strategy that survived an out-of-sample split
  var pr=s.pairs||{};
  function pchip(t,k){ return '<span class="chip '+(k||'')+'">'+t+'</span>'; }
  var prow=[];
  if(pr.enabled){ prow.push(pchip('включён','on')); } else { prow.push(pchip('выключен','off')); }
  prow.push(pchip((pr.trades||0)+' сделок'));
  if(pr.trades) prow.push(pchip('винрейт '+pr.winrate+'%'));
  prow.push(pchip('реализовано '+(pr.pnl>=0?'+':'')+'$'+fmt(pr.pnl||0), pr.pnl>0?'on':(pr.pnl<0?'warn':'')));
  prow.push(pchip('нереализовано '+(pr.unrealized>=0?'+':'')+'$'+fmt(pr.unrealized||0)));
  prow.push(pchip('окно '+(pr.lookback_h||0)+'ч / держим '+(pr.hold_h||0)+'ч'));
  prow.push(pchip((pr.n_pairs||0)+' пары, '+Math.round((pr.exposure||0)*100)+'%'));
  if(pr.enabled) prow.push(pchip('ребаланс через '+(pr.next_rebalance_h||0)+'ч'));
  document.getElementById('pairs-row').innerHTML=prow.join('');
  // Cost coverage: does the average cycle earn more than the round trip costs?
  // A strategy that is right on direction but wrong on costs still loses money,
  // so the ratio matters more than the P&L number while the sample is small.
  var pc=pr.costs||{};
  var pcc=[];
  pcc.push(pchip('издержки '+fmt(pc.cost_pct)+'% за цикл'));
  if(pc.samples){
    pcc.push(pchip('валовая '+fmt(pc.gross_avg_pct)+'%', pc.gross_avg_pct>0?'on':'warn'));
    pcc.push(pchip('чистая '+fmt(pc.net_avg_pct)+'%', pc.net_avg_pct>0?'on':'warn'));
    if(pc.coverage_x===null||pc.coverage_x===undefined){
      pcc.push(pchip('покрытие n/a'));
    } else {
      pcc.push(pchip('покрытие '+fmt(pc.coverage_x,1)+'x ('+(pc.grade||'?')+')',
        pc.grade==='ok'?'on':(pc.grade==='below_cost'?'warn':'')));
    }
    pcc.push(pchip(pc.samples+' циклов измерено'));
  } else {
    pcc.push(pchip('закрытых циклов пока нет'));
  }
  document.getElementById('pairs-costs').innerHTML=pcc.join('');
  var poh='';
  (pr.open_pairs||[]).forEach(function(q){
    var c=(q.pnl>=0)?'pos':'neg';
    poh+='<div class="ev"><span>лонг '+q.long+' @ '+q.long_entry+' &middot; шорт '+q.short+' @ '+q.short_entry+'</span>'
       +'<span class="'+c+'">'+(q.pnl>=0?'+':'')+'$'+fmt(q.pnl)+' &middot; '+q.hours+'h</span></div>';
    // why this pair, captured at entry rather than reconstructed later
    var qc=q.context||{}, bits=[];
    if(qc.long_rank) bits.push('rank '+qc.long_rank+'/'+qc.short_rank+' of '+qc.universe);
    if(qc.long&&qc.long.ret_pct!==undefined)
      bits.push('7d ret '+fmt(qc.long.ret_pct)+'% / '+fmt(qc.short&&qc.short.ret_pct)+'%');
    if(qc.long&&qc.long.atr_pct!==undefined) bits.push('atr '+fmt(qc.long.atr_pct)+'%');
    if(qc.long&&qc.long.funding_bp_h!==undefined)
      bits.push('funding '+fmt(qc.long.funding_bp_h,1)+' bp/h long');
    if(bits.length) poh+='<div class="ev" style="opacity:.6;font-size:11px">'+bits.join(' &middot; ')+'</div>';
  });
  (pr.recent||[]).slice(0,4).forEach(function(q){
    poh+='<div class="ev"><span>закрыта '+q.long+'/'+q.short+'</span>'
       +'<span class="'+(q.pnl>=0?'pos':'neg')+'">'+(q.pnl>=0?'+':'')+'$'+fmt(q.pnl)+'</span></div>';
  });
  document.getElementById('pairs-open').innerHTML=poh;
  document.getElementById('pairs-note').textContent=pr.last_error?('последняя ошибка: '+pr.last_error):'';

  // equity
  document.getElementById('eq').textContent='$'+fmt(s.equity);
  var dl=document.getElementById('eq-delta');
  dl.textContent=usd(s.day_pnl)+' ('+(s.day_pnl_pct>=0?'+':'')+fmt(s.day_pnl_pct)+'%) за сутки';
  dl.className='eq-delta '+cls(s.day_pnl);
  var sw=document.getElementById('spark-wrap');
  if(s.equity_history&&s.equity_history.length>1){
    sw.innerHTML=spark(s.equity_history);
  }

  // stats
  document.getElementById('s-dd').textContent=fmt(s.drawdown_pct)+'%';
  var sd=document.getElementById('s-day');
  sd.textContent=usd(s.day_pnl); sd.className='v '+cls(s.day_pnl);
  document.getElementById('s-risk').textContent='$'+fmt(s.open_risk);
  document.getElementById('s-risk-h').textContent='лимит 6% — '+fmt(s.open_risk_pct)+'%';
  document.getElementById('s-winrate').textContent=fmt(s.winrate,1)+'%';
  document.getElementById('s-wl').textContent=(s.wins||0)+'П / '+(s.losses||0)+'У';
  document.getElementById('s-avgr').textContent=fmt(s.avg_r);
  document.getElementById('s-kelly').textContent=fmt(s.kelly_risk_pct)+'%';
  var ss=s.sniper_stats||{};
  document.getElementById('s-snipes').textContent=ss.total_snipes!==undefined?ss.total_snipes:'-';
  document.getElementById('s-snipes-h').textContent=(ss.strong_snipes||0)+' сильных — '+fmt(ss.avg_slippage_bps||0,1)+' bps средний слиппедж';
  document.getElementById('s-uptime').textContent=hm(s.uptime_sec||0);
  document.getElementById('s-started').textContent=s.started?tm(s.started):'-';

  // positions
  var pos=s.positions||[];
  document.getElementById('pos-count').textContent=pos.length+' открыто';
  var ph2='';
  pos.forEach(function(p){
    var px=(s.prices&&s.prices[p.symbol])||p.entry;
    var unreal=(px-p.entry)*p.size;
    if(p.direction==='short')unreal=(p.entry-px)*p.size;
    var span=Math.abs(p.target-p.stop);
    var frac=span>0?Math.min(1,Math.max(0,(px-p.stop)/(p.target-p.stop))):0;
    var entryF=span>0?Math.min(1,Math.max(0,(p.entry-p.stop)/(p.target-p.stop))):0;
    var side=p.direction==='long';
    ph2+='<div class="pos-card '+(side?'long':'short')+'">'+
      '<div class="pos-head"><span class="pos-sym">'+p.symbol+'</span>'+
      '<span class="dir '+(side?'long':'short')+'">'+p.direction.toUpperCase()+'</span>'+
      '<span class="pos-pnl '+cls(unreal)+'">'+usd(unreal)+'</span></div>'+
      '<div class="bar"><div class="fill" style="width:'+(frac*100).toFixed(1)+'%"></div>'+
      '<div class="tick" style="left:'+(entryF*100).toFixed(1)+'%"></div></div>'+
      '<div class="pos-rows">'+
      '<div><span class="k">Вход</span><span class="v">'+fmt(p.entry,p.entry<10?4:2)+'</span></div>'+
      '<div><span class="k">Цена</span><span class="v">'+fmt(px,px<10?4:2)+'</span></div>'+
      '<div><span class="k">Размер</span><span class="v">'+fmt(p.size,4)+'</span></div>'+
      '<div><span class="k">Стоп</span><span class="v neg">'+fmt(p.stop,p.stop<10?4:2)+'</span></div>'+
      '<div><span class="k">Цель</span><span class="v pos">'+fmt(p.target,p.target<10?4:2)+'</span></div>'+
      '<div><span class="k">Нереал %</span><span class="v '+cls(unreal)+'">'+fmt(p.entry?unreal/(p.entry*p.size)*100:0)+'%</span></div>'+
      '</div>'+posCtx(p.context)+'</div>';
  });
  document.getElementById('positions').innerHTML=ph2||'<div class="empty">открытых позиций нет</div>';

  // sniper stats
  document.getElementById('sn-sub').textContent=(ss.total_snipes||0)+' снайпов';
  var cells=[['Исполнено',ss.total_snipes],['Отклонено',ss.rejected_snipes],['Сильных',ss.strong_snipes],['Слиппедж, bps',ss.avg_slippage_bps]];
  var sh='';
  cells.forEach(function(c){ sh+='<div class="sn"><div class="t">'+c[0]+'</div><div class="v">'+(c[1]!==undefined?fmt(c[1],1):'-')+'</div></div>'; });
  document.getElementById('sn-grid').innerHTML=sh;

  // trades
  var tr=s.trades||[];
  document.getElementById('tr-count').textContent=(s.trades_total||0)+' всего';
  document.getElementById('trades-empty').style.display=tr.length?'none':'block';
  var th='';
  tr.forEach(function(t){
    th+='<tr><td>'+tm(t.time)+'</td><td style="font-weight:700">'+t.symbol+'</td>'+
      '<td class="'+(t.direction==='long'?'pos':'neg')+'">'+t.direction.toUpperCase()+'</td>'+
      '<td>'+fmt(t.entry,t.entry<10?4:2)+'</td><td>'+fmt(t.exit,t.exit<10?4:2)+'</td>'+
      '<td class="'+cls(t.pnl)+'">'+usd(t.pnl)+'</td><td>'+fmt(t.r)+'</td>'+
      '<td style="color:var(--dim)">'+t.reason+'</td></tr>';
  });
  document.getElementById('trades').innerHTML=th;

  // events
  var ev=s.events||[];
  document.getElementById('ev-count').textContent=ev.length+' событий';
  var eh='';
  ev.forEach(function(e){ eh+='<div class="ev">'+eventLine(e)+'</div>'; });
  document.getElementById('events').innerHTML=eh||'<div class="empty">событий пока нет</div>';

  // decision journal, newest first: the numbers behind each decision
  var jr=s.journal||[], jc=s.journal_counts||{};
  var jkeys=Object.keys(jc).sort();
  document.getElementById('jr-sub').textContent=jr.length+' recent'+
    (s.journal_dropped?' / '+s.journal_dropped+' deduped':'');
  var jh='';
  if(jkeys.length){
    var jch=[];
    jkeys.forEach(function(k){ jch.push(pchip(k+' '+jc[k])); });
    jh+='<div class="cfg" style="margin-bottom:8px">'+jch.join('')+'</div>';
  }
  jr.forEach(function(e){
    var d=e.data||{}, bits=[];
    Object.keys(d).slice(0,5).forEach(function(k){
      bits.push(k+' '+d[k]);
    });
    jh+='<div class="ev"><span><b>'+when(e)+'</b> '+(e.symbol||'-')+
      ' <span style="color:var(--dim)">'+e.module+'.'+e.action+'</span> '+e.reason+
      (bits.length?' <span style="color:var(--dim)">['+bits.join(', ')+']</span>':'')+
      '</span></div>';
  });
  document.getElementById('journal').innerHTML=jh||'<div class="empty">журнал пуст</div>';

  // paper regime lab: three arms, one hour, no money involved
  var lab=s.lab||null;
  if(lab){
    var ph=lab.phase||{}, arms=lab.arms||{}, ah='';
    document.getElementById('lab-sub').textContent=
      (lab.enabled?'вкл':'выкл')+' / '+(ph.trend||'-')+'/'+(ph.vol||'-')+
      ' / '+fmt(lab.hours_seen,0)+'h';
    ah+='<div class="cfg" style="margin-bottom:8px">'+
      pchip('фаза '+(ph.trend||'-')+'/'+(ph.vol||'-'), inCell(ph)?'pos':'') +
      pchip('ER '+(ph.er===undefined?'-':ph.er)) +
      pchip('ATR% '+(ph.atr_pct===undefined?'-':ph.atr_pct)) +
      pchip('pct '+(ph.pct===undefined?'-':ph.pct)) +
      pchip('часов '+fmt(lab.hours_seen,0)) +
      pchip('возраст '+(lab.age_sec===null?'-':hm(lab.age_sec)))+
      // a lab that cannot read its file forgets every open trade on restart,
      // so the file itself is on the card, not only the numbers in memory
      pchip(lab.state&&lab.state.exists?'файл состояния есть':'ФАЙЛ СОСТОЯНИЯ ПРОПАЛ',
            lab.state&&lab.state.exists?'':'neg')+
      pchip(lab.restored?'восстановлено':'холодный старт')+'</div>';
    ['cell','control','cell_all'].forEach(function(a){
      var st=arms[a]||{}, open=(lab.open&&lab.open[a])||[];
      ah+='<div class="ev"><span><b>'+a+'</b> n '+fmt(st.n,0)+
        ' mean '+fmt(st.mean_pct,3)+'% PF '+(st.pf===null||st.pf===undefined?'-':fmt(st.pf,2))+
        ' t '+fmt(st.t,2)+' wins '+fmt(st.wins_pct,1)+'%'+
        ' <span style="color:var(--dim)">open '+(open.length?open.map(function(p){return p.coin}).join(', '):'none')+'</span></span></div>';
    });
    if(lab.last_error) ah+='<div class="ev"><span style="color:var(--neg)">'+lab.last_error+'</span></div>';
    document.getElementById('lab').innerHTML=ah;
  }

  // paper grid arm: the reference bot's idea, sized at half a percent per lot.
  // Mark-to-market drawdown is on the card because a grid that is judged only
  // by closed trades looks profitable while it is under water.
  var gl=s.grid||null;
  if(gl){
    document.getElementById('grid-sub').textContent=
      (gl.enabled?'вкл':'выкл')+' / '+fmt(gl.open_count,0)+' открыто / '+
      fmt(gl.lots_closed,0)+' закрыто';
    var ddp=(gl.mtm_max_dd_pct||0), gh='';
    gh+='<div class="cfg" style="margin-bottom:8px">'+
      pchip('капитал $'+fmt(gl.equity), (gl.equity||0)>=1000?'on':'off')+
      pchip('просадка МТМ '+fmt(ddp,2)+'%', ddp>8?'warn':'')+
      pchip('под водой '+fmt(gl.pct_time_below_start,1)+'%')+
      pchip('риск открыт $'+fmt(gl.open_risk_usd)+' / '+fmt(gl.open_risk_pct,2)+'%')+
      pchip('часов '+fmt(gl.hours,0))+
      pchip(gl.restored?'восстановлена':'с холодного старта', gl.restored?'':'off')+'</div>';
    gh+='<div class="ev"><span><b>лоты</b> '+fmt(gl.lots_closed,0)+
      ' WR '+fmt(gl.winrate_pct,1)+'%'+
      ' PF '+(gl.profit_factor===null||gl.profit_factor===undefined?'-':fmt(gl.profit_factor,2))+
      ' E '+fmt(gl.expectancy_pct_per_lot,3)+'%/lot'+
      ' реализовано $'+fmt(gl.realized_usd)+' издержки $'+fmt(gl.costs_usd)+
      ' <span style="color:var(--dim)">лот $'+fmt(gl.lot_usd)+'</span></span></div>';
    (gl.open||[]).forEach(function(p){
      var up=(p.unreal_pct>=0), col=up?'var(--green)':'var(--red)';
      gh+='<div class="ev"><span><b>'+p.coin+'</b> '+
        '<span style="color:var(--dim)">вход</span> '+fmt(p.entry,6)+
        ' <span style="color:'+col+'">'+(up?'+':'')+fmt(p.unreal_pct,2)+'%</span>'+
        ' <span style="color:var(--dim)">'+fmt(p.age_h,1)+'ч, цель +'+
        fmt(p.to_target_pct,2)+'%, стоп '+fmt(p.to_stop_pct,2)+'%</span></span></div>';
    });
    if(!(gl.open||[]).length) gh+='<div class="ev"><span style="color:var(--dim)">открытых лотов нет</span></div>';
    if(gl.last_error) gh+='<div class="ev"><span style="color:var(--red)">'+gl.last_error+'</span></div>';
    document.getElementById('grid').innerHTML=gh;
  }

  // strategy and module registry: summary counts stay visible, the list itself
  // is tabbed and collapsed (see renderStrategies above)
  var sm=s.strategies||null;
  if(sm){
    document.getElementById('strat-sum').innerHTML=[
      pchip('всего '+sm.total), pchip('работает '+sm.working,'on'),
      pchip('бумажных '+sm.paper), pchip('выключено '+sm.off,'off'),
      pchip('отклонено '+sm.rejected,'neg'), pchip('нет '+sm.missing)
    ].join('');
    stLast=sm;
    renderStrategies();
  }

  // per-coin breakdown: the scanned picture, not only the booked trades
  var cn=s.coins||[];
  var w=s.watch||{}, newest=0;
  cn.forEach(function(c){if(c.measured_ts&&c.measured_ts>newest)newest=c.measured_ts;});
  var hdr=cn.length+' в разборе';
  if(newest)hdr+=' · замер '+Math.max(0,Math.round((Date.now()/1000-newest)/60))+' мин назад';
  if(w.error)hdr+=' · ошибка: '+w.error;
  document.getElementById('coin-sub').textContent=hdr;
  var ch='<div class="coin-row coin-hd"><div>Монета</div><div>Цена / 24ч</div>'+
         '<div>Score</div><div>ADX</div><div>Экспозиция</div></div>';
  cn.forEach(function(c){
    var chg=(c.chg_24h===null||c.chg_24h===undefined)?''
      :'<span class="'+(c.chg_24h>=0?'pos':'neg')+'">'+(c.chg_24h>=0?'+':'')+fmt(c.chg_24h,2)+'%</span>';
    var expo='<span class="coin-dim">—</span>';
    if(c.pos) expo='<span class="dir '+(c.pos==='long'?'long':'short')+'">'+c.pos.toUpperCase()+'</span>';
    else if(c.grid_lots) expo='<span class="dir long">СЕТКА '+c.grid_lots+'</span>';
    else if(c.pair) expo='<span class="dir '+(c.pair==='L'?'long':'short')+'">ПАРА '+c.pair+'</span>';
    var tags=[];
    if(c.grid_lots&&c.grid_unreal_pct!==null&&c.grid_unreal_pct!==undefined)
      tags.push('лот '+(c.grid_unreal_pct>=0?'+':'')+fmt(c.grid_unreal_pct,2)+'%');
    if(c.atr_pct!==null&&c.atr_pct!==undefined) tags.push('ATR '+fmt(c.atr_pct,2)+'%');
    if(c.chg_7d!==null&&c.chg_7d!==undefined)
      tags.push('7д '+(c.chg_7d>=0?'+':'')+fmt(c.chg_7d,2)+'%');
    if(c.vol_usd) tags.push('объём $'+fmt(c.vol_usd/1e6,1)+'M');
    if(c.noise) tags.push(c.noise);
    if(c.benched) tags.push('в бане');
    if(c.tradable===false) tags.push('не торгуется');
    var sub=(c.note?c.note:'')+(c.note&&tags.length?' · ':'')+tags.join(' · ');
    ch+='<div class="coin-row"><div class="coin-sym">'+c.sym+'</div>'+
      '<div>'+fmt(c.price,(c.price&&c.price<10)?4:2)+' '+chg+'</div>'+
      '<div>'+((c.score===null||c.score===undefined)?'<span class="coin-dim">—</span>':fmt(c.score,2))+'</div>'+
      '<div>'+((c.adx===null||c.adx===undefined)?'<span class="coin-dim">—</span>':fmt(c.adx,0))+'</div>'+
      '<div>'+expo+'</div></div>'+
      (sub?'<div class="sev" style="padding:0 2px 7px">'+sub+'</div>':'');
  });
  document.getElementById('coins').innerHTML=ch;

}

function load(){
  fetch('/status',{cache:'no-store'}).then(function(r){return r.json()}).then(render).catch(function(){
    document.getElementById('upd').textContent='связь потеряна — повторяю...';
    document.getElementById('dot').className='dot off';
  });
}
load();
setInterval(load,5000);
