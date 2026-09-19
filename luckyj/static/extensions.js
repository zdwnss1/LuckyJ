/* Additive UI for fixed melds, real call provenance, conditional values and reviewable inference. */
'use strict';
window.LJExtras=(()=>{
 const E=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
 const names={chi:'吃',pon:'碰',ankan:'暗杠',daiminkan:'大明杠',kakan:'加杠'};
 const states={push:'进攻',mawashi:'兜牌／保留和牌',fold:'弃和',uncertain:'暂不判断',forced:'受限动作'};
 const responses={selected:'已选择',not_selected:'未登记鸣牌',censored:'被阻断／无法确认',chose_win:'选择和牌',eligibility_mismatch:'合法性待核查'};
 function color(node,m){if(!m.call_id)return node;node.classList.add('call-related','call-tone-'+((m.link_number-1)%6));node.dataset.callId=m.call_id;node.dataset.link=String(m.link_number);return node;}
 function meld(m,map=null,small=true){const g=E('div','meld-group');color(g,m);const label=names[m.kind]||m.kind;g.append(E('small','meld-caption',`${m.link_number?'C'+m.link_number+' · ':''}${label}${m.source_discard_number?' / '+['自己','下家','对家','上家'][m.source_relative]+'第'+m.source_discard_number+'张':''}`));g.append(tiles(m.tiles,map,small));g.title=(m.called?'鸣入 '+mapped(m.called,map)+'；':'')+(m.source_event_seq!=null?'来源事件 '+m.source_event_seq:'无他家来源弃牌');return g;}
 function ghost(node,entry,seat,allMelds){const ms=allMelds.flat();const m=ms.find(x=>x.source_relative===seat&&x.source_event_seq===entry.event_seq);if(!m)return;node.classList.add('call-ghost');color(node,m);node.append(E('span','call-number','C'+m.link_number));node.title+=` · C${m.link_number} 被鸣走，原河位保留 / ${names[m.kind]}`;node.setAttribute('aria-label',node.title);node.tabIndex=0;}
 function highlight(target,on){const linked=target?.closest?.('.call-related');if(!linked)return;document.querySelectorAll('.call-related').forEach(n=>{if(n.dataset.callId===linked.dataset.callId)n.classList.toggle('call-linked-hover',on);});}
 for(const [type,on] of [['pointerover',true],['pointerout',false],['focusin',true],['focusout',false]])document.addEventListener(type,e=>highlight(e.target,on));
 function winPanel(p,map){const box=E('details','win-projection');box.append(E('summary','', '假设和牌的役与番（切后听牌）'));
 if(!p||p.status!=='computed'){box.append(E('p','hint',p?.status==='not_tenpai'?'当前切后未听牌，未来组成不唯一，不伪造确定番数。':'尚无役番计算；安装 requirements.txt 后重新 enrich。'));return box;}
 box.append(E('p','hint','仅假设这张待牌荣和／自摸；使用当前已知宝牌与已有立直，不加入未来里宝、杠宝、一发。不是和牌率或合法荣和承诺。'));
 if(p.permanent_furiten)box.append(E('p','ambiguity','当前待牌涉及自身弃牌：永久振听标记。临时振听未计算。'));
 const scroll=E('div','table-scroll'),table=E('table','candidate-table');const tr=E('tr');['待牌','方式／存','番／符','具体役'].forEach(x=>tr.append(E('th','',x)));const head=E('thead');head.append(tr);table.append(head);const body=E('tbody');
 for(const r of p.outcomes){const row=E('tr',r.remaining?'':'exhausted');const t=E('td');t.append(tile(mapped(r.tile,map),true));row.append(t,E('td','',`${r.method==='ron'?'荣和':'自摸'} / ${r.remaining}`));row.append(E('td','',r.error?'不能按此假设和牌':r.yakuman_multiplier?`${r.yakuman_multiplier}倍役满`:`${r.han}番 ${r.fu}符（役${r.yaku_han}+宝${r.bonus_han}）`));row.append(E('td','',r.error||r.yaku.map(y=>`${y.name} ${y.han}`).join('、')));body.append(row);}table.append(body);scroll.append(table);box.append(scroll);return box;}
 function inferencePanel(inf,d){const box=E('details','intent-details');box.append(E('summary','', '逐手押引假说 · 证据与未校准后验'));
 if(!inf){box.append(E('p','hint','尚无推断索引，请重新 enrich。'));return box;}
 box.append(E('p','ambiguity','实验性专家模型，尚无人工标签校准。下面是给定先验和似然的模型后验，不是已经验证的正确率。'));
 const q=inf.online,ev=inf.evidence;
 function line(r,title){const n=E('div','posterior-line');n.append(E('b','',title+' · '+states[r.label]));n.append(E('span','',Object.entries(r.model_posterior).map(([k,v])=>`${states[k]} ${(v*100).toFixed(1)}%`).join(' / ')));return n;}
 box.append(line(q,'当手：只看当前与过去'),line(inf.review,'复盘：后续有限窗口'));
 box.append(E('p','hint',`复盘后续事件：${inf.future_evidence_events.join(', ')||'无'}；两种结果不得混用。受限动作不按主动押引统计。`));
 const selected=ev.selected;box.append(E('p','',`威胁：${ev.threats.map(t=>['自己','下家','对家','上家'][t.seat]+(t.kind==='riichi'?'立直':'三副露代理')).join('、')||'无明确威胁'}。联合证据类别：${ev.category}。`));
 box.append(E('p','',`实际切后${selected.after_shanten}向听；最小${ev.best_after_shanten}；与最快候选的进张相对损失${ev.relative_ukeire_loss==null?'不可比较':(ev.relative_ukeire_loss*100).toFixed(1)+'%'}；安全性代理增益${ev.safety_gain_over_fastest==null?'无':ev.safety_gain_over_fastest.toFixed(2)}。`));
 box.append(E('p','hint','安全性代理依据现物、立直后通过牌、字牌可见张数与弱筋信息，不是放铳概率。跟切与安全信息相关，不作为第二条独立似然重复相乘。'));
 const model=E('details');model.append(E('summary','', '先验、敏感度与全部证据'));const pre=E('pre','evidence-json',JSON.stringify({model_version:inf.model_version,model_sha256:inf.model_sha256,prior:inf.prior,likelihood:inf.likelihood,sensitivity_envelope:inf.sensitivity_envelope,sensitivity_is_credible_interval:false,evidence:ev},null,2));model.append(pre);box.append(model);
 const review=E('div','human-review');const sel=E('select');sel.setAttribute('aria-label','人工押引判断');sel.append(new Option('人工判断：尚未标注',''));[['push','进攻'],['mawashi','兜牌／保留和牌'],['fold','弃和'],['uncertain','无法判断']].forEach(([v,t])=>sel.append(new Option(t,v)));
 const note=E('textarea');note.placeholder='人工理由／反例（不会自动回填训练或改写原谱）';note.rows=2;
 const save=E('button','secondary','导出这手人工复核记录');save.type='button';save.onclick=()=>downloadJSON({schema:'luckyj-human-label-v1',log_id:d.log_id,event_seq:d.event_seq,human_label:sel.value||null,human_comment:note.value,label_view:'review',labeler:'',model_sha256:inf.model_sha256,shown_model_prediction:true,prediction:inf.online,features:ev},`review-${d.log_id}-${d.event_seq}.json`);
 review.append(E('p','hint','此面板已展示后续复盘信息，导出标注记为 review；在线校准请使用盲标导出。'),sel,note,save);box.append(review);return box;}
 function card(article,d,map){if(d.own_melds?.length){const groups=E('div','fixed-melds');groups.append(E('span','micro-label','固定面子'));d.own_melds.forEach(m=>groups.append(meld(m,map)));article.append(groups);}
 const inf=d.intent;if(inf){const r=inf.online;article.append(E('p','intent-badge intent-'+r.label,`${states[r.label]} · ${r.confidence==null?'不评主动选择':`弃和后验 ${(r.model_posterior.fold*100).toFixed(0)}% / 兜牌 ${(r.model_posterior.mawashi*100).toFixed(0)}%`} · 未校准`));}
 }
 function detail(body,d,map){body.append(winPanel(d.win_projection,map),inferencePanel(d.intent,d));}
 function setup(){const form=document.querySelector('#research-form');const hand=form.elements.hand;hand.placeholder='233m [p]　或 x{11} [c:456s@6s]';
 const hint=E('p','hint','副露写在方括号：[p]碰、[c]吃、[a]暗杠、[kakan]加杠；[999]为一门的九刻，[999s]指定九索。一个固定面子占3个结构位，一副露暗手最多11张。');hand.parentNode.after(hint);
 const ml=E('label','', '固定面子匹配');const ms=E('select');ms.name='meld_mode';ms.append(new Option('至少包含这些面子','contains'),new Option('固定面子全部对应','exact'));ml.append(ms);hint.after(ml);
 const fieldset=E('fieldset');fieldset.append(E('legend','', '04 / 条件和牌与逐手判断'));
 function select(name,label,opts){const l=E('label','',label),s=E('select');s.name=name;opts.forEach(([v,t])=>s.append(new Option(t,v)));l.append(s);fieldset.append(l);}
 const y=E('label','','假设和牌包含役（中文或代码，逗号为同时满足）'),input=E('input');input.name='win_yaku';input.placeholder='例如 平和,门清自摸';y.append(input);fieldset.append(y);
 select('win_method','和牌方式',[['','不筛选'],['ron','荣和'],['tsumo','自摸']]);select('win_han_basis','番数口径',[['total','役+已知宝牌'],['yaku','不含宝牌']]);
 const rs=E('div','fields');for(const [key,label] of [['win_han_min','番数 ≥'],['win_han_max','番数 ≤'],['win_yakuman_min','役满倍数 ≥']]){const l=E('label','',label),n=E('input');n.type='number';n.min='0';n.max='100';n.name=key;l.append(n);rs.append(l);}fieldset.append(rs,E('p','hint','役番仅筛切后听牌的条件和牌，默认要求该待牌尚有存量；非听牌未来路线不作确定估值，役满与普通番数分开。'));
 select('intent_state','押引模型状态',[['','不筛选'],['fold','弃和'],['mawashi','兜牌／保留和牌'],['push','进攻']]);select('intent_mode','判断使用的信息',[['online','当手：仅当前及过去'],['review','复盘：还看后续最多3次切牌']]);
 const il=E('label','','所选状态的模型后验 ≥ %'),ni=E('input');ni.type='number';ni.name='intent_min';ni.min='0';ni.max='100';ni.placeholder='默认62（未校准）';il.append(ni);fieldset.append(il,E('p','ambiguity','这是可修改权重的实验模型，不是人类意图真值。受限动作排除；可在详情导出人工复核记录。'));
 document.querySelector('#submit').before(fieldset);
 const panel=E('section','panel call-opportunities');const det=E('details');det.append(E('summary','','鸣牌／杠机会：选了什么，何时无法判断没鸣'));
 det.append(E('p','hint','每次他家弃牌或自身摸牌为一个机会，多种候选不扩增分母。其他人优先鸣牌／荣和等单列“无法确认”。输入条件作用于行动前状态；[p]代表之前已有碰，不是本次候选。'));
 const editor=E('textarea');editor.id='call-query';editor.rows=4;editor.setAttribute('aria-label','鸣牌机会查询');editor.value=JSON.stringify({trigger:'external',call_kind:'chi',limit:10},null,2);const run=E('button','secondary','查询鸣牌机会'),out=E('div','opportunity-results');run.type='button';
 run.onclick=async()=>{run.disabled=true;out.replaceChildren(E('p','hint','计算完整机会集合…'));try{const q=JSON.parse(editor.value),data=await api('/api/research/opportunities?'+new URLSearchParams(q));out.replaceChildren(E('h3','',`${fmt(data.total)} 个机会 · ${fmt(data.rounds)} 局 / ${fmt(data.games)} 场`));out.append(E('p','',Object.entries(data.status_counts).map(([k,n])=>`${responses[k]||k} ${fmt(n)}`).join(' / ')),E('p','hint',`明确响应分母 ${data.resolved_response_opportunities}；所选类型登记选择率 ${data.selected_rate_among_resolved==null?'无':(data.selected_rate_among_resolved*100).toFixed(1)+'%'}。不把删失当不鸣，也不将这一比例解释为主动意图。`));
 for(const p of data.items){const c=E('article','opportunity-card');c.append(E('h3','',`${p.snapshot.wind===0?'东':p.snapshot.wind===1?'南':'后续场'}${p.snapshot.hand_no}局 · ${responses[p.resolution.status]} · ${p.trigger==='self_draw'?'自身摸牌后杠机会':'他家弃牌 '+p.offered}`),tiles(p.snapshot.hand));
 const groups=E('div','fixed-melds');p.snapshot.melds[0].forEach(m=>groups.append(meld(m)));c.append(groups);
 for(const cand of p.candidates){const row=E('div','candidate-call'+(cand.selected?' selected':''));row.append(E('strong','',`${cand.selected?'✓ ':''}${names[cand.kind]} `),tiles(cand.tiles136.map(x=>{const k=Math.floor(x/4);return p.availability.aka&&[16,52,88].includes(x)?'0'+'mps'[Math.floor(k/9)]:String(k%9+1)+'mpsz'[Math.floor(k/9)];}),null,true),E('small','',cand.best_after_discard_shanten==null?`岭上摸前 ${cand.shanten_before_replacement} 向听`:`鸣后最低切后 ${cand.best_after_discard_shanten} 向听`));c.append(row);}
 c.append(E('p','source-block',`${p.id} · 来源座位 ${p.source_relative??'自己'} · 结果事件 ${p.resolution.event_seq}；未来结果仅作动作标签，不用于行动前评分。`));out.append(c);}
 const exportBtn=E('button','quiet','导出本次全集计数与示例');exportBtn.onclick=()=>downloadJSON(data,'luckyj-call-query.json');out.append(exportBtn);
 }catch(e){out.replaceChildren(E('p','ambiguity',e.message));}finally{run.disabled=false;}};
 det.append(editor,run,out);panel.append(det);document.querySelector('.result-head').before(panel);
 }
 return {meld,ghost,card,detail,setup};
})();
