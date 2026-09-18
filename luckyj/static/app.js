'use strict';
const $ = s => document.querySelector(s);
const form = $('#filters');
const seatNames = ['LuckyJ', '下家', '对家', '上家'];
const winds = ['东','南','西','北'];
const honorNames = ['东','南','西','北','白','发','中'];
const suitNames = {m:'万',p:'筒',s:'索',z:'字'};
let query = new URLSearchParams(), cursor = null, loaded = 0, requestController = null, detailController = null;
let currentDetail = null, mode = 'context';
// A named control can shadow form.reset; call the native method explicitly.
function resetForm() { HTMLFormElement.prototype.reset.call(form); }
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
function tile(t, small=false, extra='') {
  const e = el('span',`tile ${t[1]} ${t[0]==='0'?'red':''} ${small?'small':''} ${extra}`);
  e.append(el('span','n', t[1]==='z'?honorNames[Number(t[0])-1]:(t[0]==='0'?'5':t[0])),el('span','s',t[0]==='0'?'赤'+suitNames[t[1]]:suitNames[t[1]]));
  e.title = t;
  e.setAttribute('aria-label', t[0]==='0'?'赤五'+suitNames[t[1]]:t[1]==='z'?honorNames[Number(t[0])-1]:t[0]+suitNames[t[1]]);
  return e;
}
function tiles(values, small=false) { const e=el('div','tiles'); values.forEach(t=>e.append(tile(t,small))); return e; }
function formatted(n) { return Number(n).toLocaleString('zh-CN'); }
function roundTitle(d) { return `${winds[d.wind]||'?'}${d.hand_no}局 ${d.honba}本场`; }
function notice(text,error=false) { const e=$('#notice'); e.hidden=!text; e.textContent=text; e.classList.toggle('error',error); }
async function api(path, signal) {
  const r=await fetch(path,{signal}); let body;
  try {body=await r.json();} catch {throw new Error('服务器未返回有效 JSON');}
  if (!r.ok) throw new Error(body.error||`HTTP ${r.status}`);
  return body;
}
function setMode(value) {
  mode=value;
  $('#contextMode').classList.toggle('active',value==='context');
  $('#actionMode').classList.toggle('active',value==='action');
  $('#contextMode').setAttribute('aria-pressed',String(value==='context'));
  $('#actionMode').setAttribute('aria-pressed',String(value==='action'));
  const root=$('#filterSections');
  root.replaceChildren(...(value==='context'?[$('#contextFields'),$('#actionFields')]:[$('#actionFields'),$('#contextFields')]));
  $('#resultTitle').textContent=value==='context'?'这类场况下的切牌':'这类切牌发生的场况';
}
function updateURL(d=null) {
  const q=new URLSearchParams(query); q.delete('after'); q.delete('limit');
  if (mode==='action') q.set('mode','action');
  if (d) { q.set('game',d.log_id);q.set('event',String(d.event_seq)); }
  history.replaceState(null,'',location.pathname+(q.size?'?'+q.toString():''));
}
function readForm() {
  const q=new URLSearchParams();
  for(const [key,value] of new FormData(form)) if(String(value).trim()) q.set(key,String(value).trim());
  q.set('red',$('#red').checked?'1':'0'); return q;
}
function fillForm(q) {
  resetForm();
  for(const [key,value] of q) if(form.elements.namedItem(key)) form.elements.namedItem(key).value=value;
  $('#red').checked=q.get('red')!=='0';
  if([...q.keys()].some(k=>/^(score|rank)[123]/.test(k))) $('details').open=true;
}
function card(d) {
  const e=el('article','decision');
  const top=el('div','decision-top');
  const title=el('div'); title.append(el('span','round-title',roundTitle(d)),el('span','subtle',`　${winds[d.seat_wind]}家 · 第 ${d.turn} 次切牌`));
  top.append(title,el('span','badge',`${d.ranks[0]} 位 · ${formatted(d.scores[0])}`));
  const hand=el('div','handline'); hand.append(tiles(d.hand));
  const action=el('div','action-result'); action.append(el('span','action-label',d.tsumogiri?'摸切':'手切'),tile(d.discard));
  if(d.riichi_declared) action.append(el('span','badge','宣言立直'));
  hand.append(action);
  const scores=el('div','scoreline');
  d.scores.forEach((s,i)=>{const n=el('span',i===0?'self':'');n.append(document.createTextNode(seatNames[i]+' '),el('strong','',formatted(s)),document.createTextNode(` / ${d.ranks[i]}位`));scores.append(n);});
  const bottom=el('div','card-bottom');
  const left=el('span','subtle',`手牌含摸牌 · ${d.draw?'摸入 '+d.draw:'副露后切牌'}${d.melds.length?' · 副露 '+d.melds.length+' 组':''}`);
  const open=el('button','text-button','查看此刻 →');open.type='button';open.addEventListener('click',()=>loadDetail(d.id));bottom.append(left,open);
  e.append(top,hand,scores,bottom);return e;
}
async function runSearch(more=false) {
  if(requestController) requestController.abort();
  requestController=new AbortController(); const controller=requestController;
  if(!more) {query=readForm();cursor=null;loaded=0;$('#results').replaceChildren();updateURL();}
  $('#more').hidden=true;notice(more?'正在载入…':'正在检索…');
  const send=new URLSearchParams(query);send.set('limit','30');if(more && cursor)send.set('after',String(cursor));
  const active=[...query.keys()].filter(k=>!['red','basis','hand_mode'].includes(k));
  $('#querySummary').textContent=`${active.length?'已组合 '+active.length+' 项条件':'全部切牌'} · ${query.get('basis')==='round_start'?'分数与次位：本局开始':'分数与次位：切牌前'} · ${query.get('red')==='1'?'赤五独立匹配':'赤五合并为普通五'}`;
  try {
    const data=await api('api/search?'+send,controller.signal);
    if(controller!==requestController)return;
    const fragment=document.createDocumentFragment();data.items.forEach(d=>fragment.append(card(d)));$('#results').append(fragment);
    loaded+=data.items.length;cursor=data.next_after;
    $('#resultCount').textContent=`${formatted(data.total)} 条匹配 · 已显示 ${formatted(loaded)}`;
    notice(data.total?'':'没有符合条件的切牌。可以放宽分数区间、减少手牌张数，或取消赤牌区分。');
    $('#more').hidden=cursor===null;
  } catch(e) {if(e.name!=='AbortError'){notice(e.message,true);$('#resultCount').textContent='检索失败';}}
}
function renderMelds(ms) {
  const e=el('div','meldline');
  const names={chi:'吃',pon:'碰',ankan:'暗杠',daiminkan:'明杠',kakan:'加杠'};
  ms.forEach(m=>{const g=el('div','meldgroup');g.append(el('small','',names[m.kind]||m.kind));m.tiles.forEach(t=>g.append(tile(t,true)));e.append(g);});return e;
}
function drawDetail(d) {
  currentDetail=d;const s=d.snapshot;
  $('#detailTitle').textContent=`${roundTitle(d)} · 第 ${d.turn} 次切牌`;
  const body=$('#detailBody');body.replaceChildren();
  const meta=el('div','snapshot-meta');
  const stamp=d.started_at?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Tokyo',dateStyle:'medium',timeStyle:'short'}).format(new Date(d.started_at*1000))+' JST':'';
  [stamp,`LuckyJ ${winds[d.seat_wind]}家`,`供托 ${d.kyotaku} 根`,`当前 ${s.ranks[0]} 位 / ${formatted(s.scores[0])} 点`].filter(Boolean).forEach(t=>meta.append(el('span','',t)));body.append(meta);
  const handBox=el('section','self-hand');handBox.append(el('h3','',`LuckyJ 的暗手牌　→　${d.tsumogiri?'摸切':'手切'} ${d.discard}${d.riichi_declared?' · 宣言立直':''}`));
  const hand=el('div','tiles');let used=false;
  s.hand136.forEach((v,i)=>{if(v===s.draw136&&!used){used=true;return;}hand.append(tile(s.hand[i]));});
  if(s.draw)hand.append(tile(s.draw,false,'draw-gap'));handBox.append(hand);
  if(s.melds[0].length)handBox.append(renderMelds(s.melds[0]));
  handBox.append(el('div','section-label','已公开的宝牌指示牌'),tiles(s.dora_indicators,true));
  const note=d.riichi_declared?'此刻是宣言牌打出前，尚未扣除此人的本次立直棒。':'分数为此步切牌前持点；不会使用局末结算。';
  handBox.append(el('p','snapshot-note',note));body.append(handBox);
  body.append(el('div','section-label','各家牌河与副露 · 不含当前尚未切出的这一张'));
  const table=el('div','table-grid');
  s.names.forEach((name,i)=>{
    const p=el('section','table-player');const h=el('h3','',`${seatNames[i]} · ${name}`);
    h.append(el('span','',`${formatted(s.scores[i])} / ${s.ranks[i]}位${s.dealer_relative===i?' · 庄家':''}${s.riichi[i]?' · 已立直':''}`));p.append(h);
    p.append(el('div','subtle',`局初 ${formatted(s.scores_start[i])} / ${s.ranks_start[i]}位`));
    const river=el('div','river');
    s.rivers[i].forEach(r=>{const t=tile(r.tile,true,`${r.called_by!==null?'called':''} ${r.riichi?'reach':''}`);t.title=`${r.tile} · ${r.tsumogiri?'摸切':'手切'}${r.called_by!==null?' · 已被'+seatNames[r.called_by]+'副露':''}${r.riichi?' · 立直宣言牌':''}`;river.append(t);});
    p.append(el('div','section-label','牌河'),river);
    if(s.melds[i].length)p.append(renderMelds(s.melds[i]));table.append(p);
  });body.append(table);
  body.append(el('div','section-label','LuckyJ 本局截至当前的切牌序列（最后一张是本次切牌）'),tiles(s.sequence,true));
  body.append(el('div','source-ref',`${d.log_id} / round_seq=${d.round_seq} / event_seq=${d.event_seq}`));
  const share=el('button','text-button','复制此步链接');share.type='button';share.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(location.href);share.textContent='链接已复制';}catch{share.textContent='地址栏即此步链接';}});body.append(share);
  $('#previous').disabled=d.previous===null;$('#next').disabled=d.next===null;
  $('#official').href=d.tenhou_url;
  updateURL(d);
}
async function loadDetail(id,resolveQuery=null) {
  if(detailController)detailController.abort();detailController=new AbortController();const controller=detailController;
  $('#detailBody').replaceChildren(el('p','subtle','正在读取切牌前快照…'));
  $('#previous').disabled=true;$('#next').disabled=true;$('#official').removeAttribute('href');
  if(!$('#detail').open)$('#detail').showModal();
  try {const d=await api(resolveQuery?'api/resolve?'+resolveQuery:'api/decisions/'+id,controller.signal);if(controller===detailController)drawDetail(d);}catch(e){if(e.name!=='AbortError')$('#detailBody').replaceChildren(el('p','snapshot-note',e.message));}
}
async function status() {
  try {const d=await api('api/status');
    $('#games').textContent=formatted(d.indexed_logs);$('#rounds').textContent=formatted(d.rounds);$('#decisions').textContent=formatted(d.decisions);$('#missing').textContent=formatted(d.records_without_url);
    const coverage=d.all_linked_logs_indexed?`带链接的 ${formatted(d.unique_linked_logs)} 场已全部入库。`:`已入库 ${d.indexed_logs}/${d.unique_linked_logs} 场；缺原谱 ${d.missing_raw_count} 场，解析失败 ${d.index_error_count} 场。`;
    $('#coverageNote').textContent=`${coverage} 源站另有 ${d.records_without_url} 条无链接记录，不纳入切牌检索。范围仅限此次源站清单，不代表全部历史对局。`;
    $('#coverageNote').classList.toggle('warn',!d.all_linked_logs_indexed);
  }catch(e){$('#coverageNote').textContent=e.message;$('#coverageNote').classList.add('warn');}
}
for(let i=1;i<4;i++){
  const group=el('div','opponent-fields');const label=el('label','',seatNames[i]+'次位');const select=el('select');select.name='rank'+i;select.append(new Option('不限',''));for(let r=1;r<=4;r++)select.append(new Option(r+' 位',String(r)));label.append(select);
  const range=el('div','range');for(const side of ['min','max']){const input=el('input');input.type='number';input.step='100';input.name='score'+i+'_'+side;input.placeholder=side==='min'?'最低分':'最高分';input.setAttribute('aria-label',seatNames[i]+input.placeholder);if(side==='max')range.append(el('span','','—'));range.append(input);}group.append(label,range);$('#opponents').append(group);
}
$('#contextMode').addEventListener('click',()=>{setMode('context');updateURL();});$('#actionMode').addEventListener('click',()=>{setMode('action');updateURL();});
form.addEventListener('submit',e=>{e.preventDefault();runSearch();});
$('#more').addEventListener('click',()=>runSearch(true));
$('#reset').addEventListener('click',()=>{resetForm();$('details').open=false;runSearch();});
$('.examples').addEventListener('click',e=>{const v=e.target.dataset.example;if(!v)return;resetForm();if(v==='south4'){form.elements.wind.value='1';form.elements.hand_no.value='4';form.elements.rank0.value='4';setMode('context');}if(v==='233'){form.elements.hand.value='233m';form.elements.discard.value='2m';setMode('action');}if(v==='red'){form.elements.discard.value='0p';setMode('action');}runSearch();});
$('#closeDetail').addEventListener('click',()=>$('#detail').close());
$('#detail').addEventListener('close',()=>{if(detailController)detailController.abort();updateURL();});
$('#previous').addEventListener('click',()=>{if(currentDetail?.previous)loadDetail(currentDetail.previous);});
$('#next').addEventListener('click',()=>{if(currentDetail?.next)loadDetail(currentDetail.next);});
const initial=new URLSearchParams(location.search), game=initial.get('game'), event=initial.get('event');
setMode(initial.get('mode')==='action'?'action':'context');
initial.delete('mode');initial.delete('game');initial.delete('event');fillForm(initial);status();
runSearch().then(()=>{if(game&&event!==null)loadDetail(null,new URLSearchParams({log_id:game,event_seq:event}));});
