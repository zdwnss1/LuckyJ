'use strict';
const $ = (s, root = document) => root.querySelector(s);
const form = $('#query');
const nf = new Intl.NumberFormat('zh-CN');
const relative = ['自己', '下家', '对家', '上家'];
const winds = ['东', '南', '西', '北'];
const honors = ['东', '南', '西', '北', '白', '发', '中'];
let page = 1, total = 0, limit = 24, controller = null, detailRequest = 0;
let activeParams = new URLSearchParams();
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}
function roundLabel(no, honba) { return `${winds[Math.floor(no / 4)] || `第${Math.floor(no / 4) + 1}圈`}${no % 4 + 1}局 · ${honba}本场`; }
function tile(t, options = {}) {
  const node = el('span', `tile ${t[1]}${t[0] === '0' ? ' red' : ''}${options.small ? ' small' : ''}${options.drawn ? ' drawn' : ''}${options.cut ? ' cut' : ''}${options.called ? ' called' : ''}${options.riichi ? ' riichi-mark' : ''}`);
  const face = t[1] === 'z' ? honors[Number(t[0]) - 1] : (t[0] === '0' ? '5' : t[0]);
  const suit = t[1] === 'z' ? `${t[0]}z` : ({m:'万',p:'筒',s:'索'}[t[1]]);
  node.append(el('span', 'face', face), el('span', 'suit', t[0] === '0' ? `赤${suit}` : suit));
  node.title = `${t}${options.drawn ? ' · 本次摸牌' : ''}${options.cut ? ' · 本次切牌' : ''}${options.called ? ' · 已被鸣走' : ''}${options.riichi ? ' · 立直宣言牌' : ''}${options.tsumogiri ? ' · 摸切' : ''}`;
  node.setAttribute('aria-label', node.title);
  if (options.tsumogiri) node.append(el('span', 'tiny-mark', '·'));
  return node;
}
function tiles(items, small = false) {
  const node = el('div', 'tiles');
  items.forEach(t => node.append(tile(t, {small})));
  return node;
}
function hand(c, detail = false) {
  const node = el('div', 'tiles');
  const entries = c.hand_ids.map((id, i) => [id, c.hand[i]]);
  entries.sort((a, b) => (a[0] === c.draw_id ? 1 : b[0] === c.draw_id ? -1 : a[0] - b[0]));
  for (const [id, t] of entries) node.append(tile(t, {drawn: id === c.draw_id, cut: detail && id === c.discard_id}));
  return node;
}
function scoreLine(c, basis) {
  const node = el('div', 'scoreline');
  const scores = basis === 'start' ? c.start_scores : c.scores;
  relative.forEach((name, r) => {
    const seat = (c.lucky_seat + r) % 4;
    const item = el('span', '', `${name}${seat === c.dealer ? '（亲）' : ''}`);
    item.append(el('strong', '', nf.format(scores[seat])));
    node.append(item);
  });
  return node;
}
for (let i = 0; i < 16; i++) {
  const option = el('option', '', `${winds[Math.floor(i / 4)]}${i % 4 + 1}局`);
  option.value = String(i); $('#round-no').append(option);
}
relative.forEach((name, i) => {
  const row = el('div', 'score-row'); row.append(el('span', '', name));
  for (const kind of ['min', 'max']) {
    if (kind === 'max') row.append(el('span', 'dash', '—'));
    const input = el('input'); input.type = 'number'; input.step = '100'; input.name = `score${i}_${kind}`;
    input.min = '-200000'; input.max = '500000'; input.placeholder = kind === 'min' ? '分数下限' : '分数上限';
    input.setAttribute('aria-label', `${name}分数${kind === 'min' ? '下限' : '上限'}`); row.append(input);
  }
  $('#score-fields').append(row);
});
function setMode(discardFirst) {
  $('#mode-situation').setAttribute('aria-pressed', String(!discardFirst));
  $('#mode-discard').setAttribute('aria-pressed', String(discardFirst));
  $('#filter-sections').prepend($(discardFirst ? '#discard-section' : '#situation-section'));
}
$('#mode-situation').onclick = () => setMode(false);
$('#mode-discard').onclick = () => setMode(true);
function paramsFromForm() {
  const params = new URLSearchParams();
  for (const [key, value] of new FormData(form)) if (String(value).trim()) params.set(key, String(value).trim());
  params.set('limit', String(limit)); return params;
}
async function request(url, options) {
  const response = await fetch(url, options); const body = await response.json();
  if (!response.ok) throw new Error(body.error || `请求失败 (${response.status})`);
  return body;
}
function card(c, basis) {
  const article = el('article', 'card');
  const head = el('div', 'card-head');
  const title = el('div', 'round-title', roundLabel(c.round_no, c.honba));
  title.append(el('span', 'badge', `${basis === 'start' ? c.start_rank : c.rank} 位`));
  head.append(title, el('span', 'step', `第 ${c.turn} 次切牌${c.riichi_declaration ? ' · 立直宣言' : ''}`));
  article.append(head, scoreLine(c, basis));
  const row = el('div', 'decision-row'), hw = el('div', 'hand-wrap');
  hw.append(el('span', 'micro-label', c.draw ? `切牌前暗手牌 · 绿框为摸入 ${c.draw}` : '切牌前暗手牌 · 鸣牌后切牌'), hand(c));
  const choice = el('div', 'choice'); choice.append(el('span', 'micro-label', '选择切出'));
  const selected = el('div', 'tiles'); selected.append(tile(c.discard), el('small', '', c.tsumogiri ? '摸切' : '手出')); choice.append(selected);
  row.append(hw, choice); article.append(row);
  const foot = el('div', 'card-foot'); foot.append(el('span', 'game-meta', `${c.log_id} / 局序 ${c.round_ordinal + 1}`));
  const button = el('button', 'detail-button', '查看当时场面 ↗'); button.type = 'button'; button.onclick = () => showDetail(c.id);
  foot.append(button); article.append(foot); return article;
}
async function runSearch(newPage = 1, fromForm = true) {
  if (controller) controller.abort(); controller = new AbortController();
  const requestController = controller;
  if (fromForm) activeParams = paramsFromForm();
  page = newPage; activeParams.set('page', String(page));
  $('#search').disabled = true; $('#prev').disabled = true; $('#next').disabled = true;
  $('#message').textContent = '正在检索…'; $('#results').replaceChildren(); $('#page-label').textContent = '';
  history.replaceState(null, '', `${location.pathname}?${activeParams.toString()}${location.hash}`);
  try {
    const data = await request(`/api/search?${activeParams.toString()}`, {signal: requestController.signal});
    total = data.total; limit = data.limit;
    $('#result-count').textContent = `${nf.format(total)} 条切牌记录`;
    $('#result-context').textContent = `牌面包含即将切出的牌；本次分数与次位口径：${data.score_basis === 'start' ? '本局开始时' : '切牌前'}。${activeParams.get('game_id') ? '当前限定于一场对局。' : ''}`;
    const fragment = document.createDocumentFragment();
    for (const c of data.items) fragment.append(card(c, data.score_basis));
    if (!data.items.length) { const empty = el('div', 'empty'); empty.append(el('b', '', '没有符合条件的记录'), el('span', '', '可以先减少手牌或分数限制，再逐项缩小范围。')); fragment.append(empty); }
    $('#results').replaceChildren(fragment); $('#message').textContent = '';
    $('#page-label').textContent = `第 ${page} / ${Math.max(1, Math.ceil(total / limit))} 页`;
    $('#prev').disabled = page <= 1; $('#next').disabled = page * limit >= total;
  } catch (error) {
    if (error.name === 'AbortError') return;
    $('#message').textContent = error.message; $('#result-count').textContent = '检索未完成';
  } finally { if (requestController === controller) $('#search').disabled = false; }
}
form.onsubmit = event => { event.preventDefault(); runSearch(); };
$('#reset').onclick = () => { form.reset(); $('#game-id').value = ''; setMode(false); history.replaceState(null, '', location.pathname); runSearch(); };
$('#prev').onclick = () => runSearch(page - 1, false);
$('#next').onclick = () => runSearch(page + 1, false);
$('#share').onclick = async () => {
  try { await navigator.clipboard.writeText(location.href); $('#share').textContent = '已复制'; setTimeout(() => { $('#share').textContent = '复制检索链接'; }, 1500); }
  catch { $('#message').textContent = '请复制浏览器地址栏，当前条件已保存在链接中。'; }
};
function closeDetail() { detailRequest++; $('#detail').close(); history.replaceState(null, '', location.pathname + location.search); }
$('#close-detail').onclick = closeDetail;
$('#detail').addEventListener('cancel', event => { event.preventDefault(); closeDetail(); });
async function showDetail(id) {
  const serial = ++detailRequest;
  $('#detail-body').textContent = '正在读取场面…'; $('#detail-title').textContent = '切牌前的场面';
  if (!$('#detail').open) $('#detail').showModal();
  history.replaceState(null, '', location.pathname + location.search + '#case=' + encodeURIComponent(id));
  try {
    const c = await request('/api/decision/' + encodeURIComponent(id)); if (serial !== detailRequest) return;
    $('#detail-title').textContent = `${roundLabel(c.round_no, c.honba)} · 第 ${c.turn} 次切牌`;
    const body = $('#detail-body'); body.replaceChildren();
    body.append(el('div', 'detail-summary', `切牌前 ${c.rank} 位 · 场上立直棒 ${c.kyotaku} 根 · ${c.riichi_declaration ? '本次为立直宣言牌，尚未扣除宣言棒' : c.tsumogiri ? '本次摸切' : '本次手出'}`));
    body.append(scoreLine(c, 'current'));
    const hw = el('div', 'detail-hand'); hw.append(el('span', 'micro-label', 'LuckyJ 暗手牌 · 绿框为本次摸牌，棕框为本次切牌'), hand(c, true)); body.append(hw);
    body.append(el('span', 'micro-label', '宝牌指示牌（不是宝牌本身）'), tiles(c.dora_indicators, true));
    const board = el('div', 'board');
    for (let r = 0; r < 4; r++) {
      const seat = (c.lucky_seat + r) % 4, panel = el('div', 'seat-panel' + (r === 0 ? ' me' : ''));
      const header = el('div', 'seat-head'); header.append(el('b', '', `${relative[r]} · ${c.names[seat]}${seat === c.dealer ? '（亲）' : ''}`), el('span', '', `${c.ranks[seat]} 位${c.riichi[seat] ? ' · 立直' : ''}`)); panel.append(header);
      const river = el('div', 'river');
      c.rivers[seat].forEach(t => river.append(tile(t.tile, {small:true, called:t.called, riichi:t.riichi, tsumogiri:t.tsumogiri})));
      if (!c.rivers[seat].length) river.append(el('span', 'hint', '尚未切牌'));
      panel.append(river);
      if (c.melds[seat].length) {
        const melds = el('div', 'meld-line');
        c.melds[seat].forEach(m => { const group = el('div'); const kinds = {chi:'吃',pon:'碰',ankan:'暗杠',daiminkan:'明杠',kakan:'加杠'}; group.append(el('span', 'micro-label', kinds[m.kind]), tiles(m.tiles, true)); melds.append(group); }); panel.append(melds);
      }
      board.append(panel);
    }
    body.append(board, el('p', 'detail-note', '场面停在本次切牌之前，因此本次切出的牌尚未出现在牌河中。浅色为此前已被鸣走的牌；描边为立直宣言牌，小圆点为摸切。对手暗手牌不会展示。'));
    body.append(el('span', 'micro-label', 'LuckyJ 本局切牌顺序（含本次选择）'), tiles(c.sequence, true));
    const links = el('div', 'links');
    const original = el('a', '', '在天凤打开原谱 ↗'); original.href = `https://tenhou.net/0/?log=${encodeURIComponent(c.log_id)}&tw=${c.lucky_seat}`; original.target = '_blank'; original.rel = 'noreferrer';
    const raw = el('a', '', '下载原始 XML.gz'); raw.href = '/api/raw/' + encodeURIComponent(c.log_id);
    const sameGame = el('button', 'text-button', '查看本场全部 LuckyJ 切牌'); sameGame.onclick = () => { closeDetail(); form.reset(); $('#game-id').value = c.log_id; runSearch(); };
    links.append(original, raw, sameGame); body.append(links);
    body.append(el('p', 'detail-note', `稳定记录 ID：${c.id}。原谱包含整场信息；事件序号从 XML 的第一个子元素起，以 0 为起点。`));
  } catch (error) { if (serial === detailRequest) $('#detail-body').textContent = error.message; }
}
async function init() {
  const url = new URLSearchParams(location.search);
  for (const [name, value] of url) {
    const field = form.elements.namedItem(name);
    if (field) { if (field.type === 'checkbox') field.checked = value === '1'; else field.value = value; }
  }
  try {
    const status = await request('/api/status');
    const metrics = [
      ['来源对局记录', status.source_records, '含没有牌谱链接的记录'],
      ['已索引原谱', status.indexed_games, `可定位原谱 ${nf.format(status.linked_games)} 份`],
      ['LuckyJ 切牌', status.decisions, `覆盖 ${nf.format(status.rounds)} 个实际局次`],
      ['来源缺失链接', status.records_without_url, '保留缺失清单，不计入切牌样本']
    ];
    $('#metrics').replaceChildren(...metrics.map(([label, value, caption]) => { const n = el('div', 'metric'); n.append(el('span', 'metric-label', label), el('strong', '', nf.format(value)), el('small', '', caption)); return n; }));
    $('#coverage').textContent = `覆盖说明：${status.all_linked_games_indexed ? '全部带链接的原谱已通过解析并入库。' : '当前索引尚未覆盖全部可定位原谱。'}另有 ${status.records_without_url} 条来源记录没有牌谱链接；下载缺失 ${status.not_downloaded.length} 份，解析失败 ${status.parse_failures.length} 份。`;
    const missing = el('a', '', '查看缺失明细 ↗'); missing.href = '/api/missing'; missing.target = '_blank'; missing.rel = 'noreferrer'; $('#coverage').append(missing);
  } catch (error) { $('#metrics').textContent = '索引状态暂不可用'; $('#coverage').textContent = error.message; }
  const initialPage = Number(url.get('page'));
  await runSearch(Number.isInteger(initialPage) && initialPage > 0 ? initialPage : 1);
  const match = location.hash.match(/^#case=(.*)$/);
  if (match) showDetail(decodeURIComponent(match[1]));
}
init();
