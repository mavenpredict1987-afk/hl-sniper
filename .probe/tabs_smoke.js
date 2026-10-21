// headless smoke test of the tabbed registry: no browser, just the render code
const fs=require('fs'), vm=require('vm');
const els={};
function el(id){
  if(!els[id]) els[id]={id:id,innerHTML:'',textContent:'',style:{},dataset:{},
    classList:{toggle(){},add(){},remove(){},contains(){return false}},
    addEventListener(){},querySelectorAll(){return []},closest(){return null},
    appendChild(){},getContext(){return null},children:[]};
  return els[id];
}
const ctx={console, Date, Number, String, Math, JSON, isNaN, parseInt, parseFloat, Infinity, NaN,
  document:{getElementById:el,querySelectorAll:()=>[],addEventListener(){},body:el('body'),
            createElement:()=>el('new'),createTextNode:()=>({})},
  setInterval:()=>0, setTimeout:()=>0, clearInterval:()=>{}, clearTimeout:()=>{},
  fetch:()=>new Promise(()=>{}), location:{href:'/'}, navigator:{userAgent:'node'}, requestAnimationFrame:()=>0};
ctx.window=ctx; ctx.globalThis=ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('.probe/dash.js','utf8'), ctx, {filename:'dash.js'});

const items=[
 {id:'pairs',cat:'strategy',name:'Парный арбитраж',status:'paper',live:'open 3, realised $8.0',
  live_detail:'сделок 3',evidence:'+17.9%, PF 1.22 - единственная стратегия, прошедшая OOS'},
 {id:'grid',cat:'strategy',name:'Сетка в боковике',status:'paper',live:'лотов 6',
  live_detail:'закрыто 1',evidence:'PF 1.70, E +0.15..+0.40%/лот'},
 {id:'cascade',cat:'strategy',name:'Каскад-бай',status:'rejected',live:'',live_detail:'',
  evidence:'брутто +0.14%, нетто -0.05%'},
 {id:'cost_gate',cat:'filter',name:'Гейт издержек',status:'on',live:'',
  live_detail:'',evidence:'цель должна покрывать комиссии x1.5'},
 {id:'daily_stop',cat:'risk',name:'Дневной стоп',status:'off',live:'',live_detail:'',
  evidence:'выключен до разморозки'},
 {id:'state',cat:'infra',name:'Сохранение состояния',status:'on',live:'',live_detail:'',
  evidence:'позиции и лоты переживают рестарт'}
];
ctx.stLast={items:items,total:6,working:2,paper:2,off:1,rejected:1,missing:0};
ctx.renderStrategies();

const tabs=els['strat-tabs'].innerHTML, list=els['strat'].innerHTML;
if(process.env.SHOWTABS)console.log('TABS:',tabs);
const checks=[
 ['tab bar rendered', tabs.includes('Стратегии') && tabs.includes('Фильтры')],
 ['tab counts shown', tabs.includes('<span class="tn">3</span>') && (tabs.match(/<span class="tn">1<\/span>/g)||[]).length===3],
 ['default tab is strategies', tabs.includes('class="tab on" onclick="setStTab(\'strategy\')"')],
 ['strategy rows rendered', list.includes('Парный арбитраж') && list.includes('Сетка в боковике')],
 ['other categories hidden', !list.includes('Гейт издержек')],
 ['status labels', list.includes('бумажная') && list.includes('отклонён')],
 ['evidence hidden until tap', list.includes('class="sev"') && !list.includes(' strat open"')],
 ['live numbers visible', list.includes('open 3, realised $8.0')]
];
ctx.setStTab('filter');
checks.push(['filter tab switches', els['strat'].innerHTML.includes('Гейт издержек')]);
ctx.toggleStrat('cost_gate');
checks.push(['tap opens the measurement', els['strat'].innerHTML.includes('strat open')]);
ctx.setStTab('strategy');
checks.push(['open state remembered per id', els['strat'].innerHTML.includes('strat open')===false]);
ctx.toggleStrat('pairs');
checks.push(['tap on strategy opens it', els['strat'].innerHTML.includes('strat open') &&
  els['strat'].innerHTML.indexOf('strat open')<els['strat'].innerHTML.indexOf('Парный арбитраж')+400]);
let fail=0;
checks.forEach(([n,ok])=>{ console.log((ok?'PASS  ':'FAIL  ')+n); if(!ok)fail++; });
console.log(fail?('FAILED: '+fail):'ALL CHECKS PASSED');
process.exit(fail?1:0);
