"""Conditional win value AFTER the recorded discard, never final-result leakage.

Only tenpai is assigned exact per-tile outcomes. A non-tenpai hand has many
future completions and returns not_tenpai, not a fabricated zero-han estimate.
Pinned optional calculator: MahjongRepository/mahjong 1.4.0 (MIT).
"""
from __future__ import annotations
from collections import Counter
from importlib.metadata import version as package_version, PackageNotFoundError
from .analysis import counts, effective, INDEX, TOKENS
from .tiles import kind
from .shanten import shanten

VERSION='conditional-win-1'
ALIASES={
 '断幺九':'tanyao','断么九':'tanyao','平和':'pinfu','立直':'riichi','两立直':'daburu_riichi',
 '门清自摸':'tsumo','门前清自摸和':'tsumo','自摸':'tsumo','一杯口':'iipeiko',
 '白':'haku','发':'hatsu','發':'hatsu','中':'chun','场风':'yakuhai_round','自风':'yakuhai_place',
 '役牌':'yakuhai','三色同顺':'sanshoku','一气通贯':'ittsu','混全带幺九':'chantai',
 '混老头':'honroto','对对和':'toitoi','对对胡':'toitoi','三暗刻':'sanankou','三杠子':'sankantsu',
 '三色同刻':'sanshoku_douko','七对子':'chiitoitsu','小三元':'shosangen','混一色':'honitsu',
 '纯全带幺九':'junchan','二杯口':'ryanpeiko','清一色':'chinitsu','国士无双':'kokushi',
 '九莲宝灯':'chuuren_poutou','四暗刻':'suuankou','大三元':'daisangen','小四喜':'shosuushi',
 '绿一色':'ryuisou','四杠子':'suukantsu','字一色':'tsuisou','清老头':'chinroto',
 '大四喜':'daisuushi','宝牌':'dora','赤宝牌':'aka_dora'
}
CATALOG={0:'tsumo',1:'riichi',3:'ippatsu',4:'chankan',5:'rinshan',6:'haitei',7:'houtei',
         8:'daburu_riichi',12:'pinfu',13:'tanyao',14:'iipeiko',15:'haku',16:'hatsu',17:'chun',
         18:'east',19:'south',20:'west',21:'north',22:'yakuhai_place',23:'yakuhai_round',
         24:'sanshoku',25:'ittsu',26:'chantai',27:'honroto',28:'toitoi',29:'sanankou',30:'sankantsu',
         31:'sanshoku_douko',32:'chiitoitsu',33:'shosangen',34:'honitsu',35:'junchan',36:'ryanpeiko',
         37:'chinitsu',38:'kokushi',39:'chuuren_poutou',40:'suuankou',41:'daisangen',42:'shosuushi',
         43:'ryuisou',44:'suukantsu',45:'tsuisou',46:'chinroto',49:'daisuushi',50:'daburu_kokushi',
         51:'suuankou_tanki',52:'daburu_chuuren_poutou',53:'tenhou',54:'chiihou',58:'dora',59:'aka_dora'}
BONUS={'dora','aka_dora'}
FAMILIES={'riichi':{'riichi','daburu_riichi'},'kokushi':{'kokushi','daburu_kokushi'},'chuuren_poutou':{'chuuren_poutou','daburu_chuuren_poutou'},'suuankou':{'suuankou','suuankou_tanki'}}
YAKUHAI={'haku','hatsu','chun','yakuhai_place','yakuhai_round','east','south','west','north'}
FILTERS={'win_yaku','win_method','win_han_min','win_han_max','win_han_basis','win_live_only','win_yakuman_min'}


def canonical_yaku(text):
    parts=[x.strip() for x in text.replace('，',',').split(',') if x.strip()]
    out=[ALIASES.get(x,x.lower()) for x in parts]
    if any(x not in set(CATALOG.values())|{'yakuhai'} for x in out):
        raise ValueError('未知役名；使用明示的中文名或 /api/research/schema 中的 win_yaku 代码')
    return out


def validate_filters(q):
    if q.get('win_method','either') not in ('ron','tsumo','either'):raise ValueError('win_method为ron/tsumo/either')
    if q.get('win_han_basis','total') not in ('total','yaku'):raise ValueError('番数口径为total或yaku')
    if q.get('win_live_only','1') not in ('0','1'):raise ValueError('win_live_only为0/1')
    canonical_yaku(q.get('win_yaku',''))
    for k in ('win_han_min','win_han_max','win_yakuman_min'):
        if k in q:
            try:n=int(q[k])
            except (ValueError,TypeError):raise ValueError(k+'应为整数') from None
            if str(n)!=str(q[k]) or not 0<=n<=100:raise ValueError(k+'应为0..100')
    if int(q.get('win_han_min',0))>int(q.get('win_han_max',100)):raise ValueError('番数下限大于上限')


def calculator_status():
    try:v=package_version('mahjong')
    except PackageNotFoundError:return 'missing'
    if v!='1.4.0':raise ValueError('和牌计算器版本不匹配，安装 requirements.txt 中的 mahjong==1.4.0')
    return v


def project(snapshot, decision, f, trace=None):
    hand=list(snapshot['hand']);hand.remove(snapshot['discard'])
    current=shanten(counts(hand))
    base={'version':VERSION,'scope':'actual_discard_after_tenpai','after_shanten':current,
          'future_information':False,'outcomes':[],
          'assumptions':['Current declared/established riichi is retained; no new hypothetical declaration.',
                         'No future ura/kan dora, ippatsu, last-tile, rinshan or chankan bonus assumed.',
                         'This is a value conditional on winning, not a win probability or ron permission.'],
          'temporary_furiten':'not_evaluated'}
    if current!=0:return {**base,'status':'not_tenpai'}
    dep=calculator_status()
    if dep=='missing':return {**base,'status':'dependency_missing','install':'python -m pip install -r requirements.txt'}
    from mahjong.hand_calculating.hand import HandCalculator
    from mahjong.hand_calculating.hand_config import HandConfig,OptionalRules
    from mahjong.meld import Meld
    waits=effective(counts(hand));own_river={kind(x['tile']) for x in snapshot['rivers'][0]}|{kind(snapshot['discard'])}
    permanent=bool(set(waits)&own_river)
    base['permanent_furiten']=permanent;base['calculator_version']=dep
    aka=f['aka'];open_hand=any(m['kind']!='ankan' for m in snapshot['melds'][0])
    declared=bool(snapshot['riichi'][0] or decision['riichi_declared'])
    double=bool((trace or {}).get('double_riichi',[False]*4)[0] and declared)
    indicators=[kind(t)*4 for t in snapshot['dora_indicators']]
    cfg_rules=OptionalRules(has_open_tanyao=not bool(f.get('game_type',1)&4),has_aka_dora=aka,
                            has_double_yakuman=True)
    def allocate(ts,used):
        result=[]
        for t in ts:
            k=kind(t);b=k*4
            available=([b] if t.startswith('0') else list(range(b+1,b+4)) if aka and k in (4,13,22) else list(range(b,b+4)))
            x=next((x for x in available if x not in used),None)
            if x is None:raise ValueError('Projected hand exceeds physical supply')
            used.add(x);result.append(x)
        return result
    for wait in waits:
        variants=[TOKENS[wait]]+([f'0{"mps"[wait//9]}'] if aka and wait in (4,13,22) else [])
        for winning in variants:
            used=set()
            try:
                closed_ids=allocate(hand,used);melds=[];all_ids=list(closed_ids)
                for m in snapshot['melds'][0]:
                    ids=allocate(m['tiles'],used);all_ids.extend(ids)
                    typ=Meld.CHI if m['kind']=='chi' else Meld.PON if m['kind']=='pon' else Meld.SHOUMINKAN if m['kind']=='kakan' else Meld.KAN
                    melds.append(Meld(typ,ids,opened=m['kind']!='ankan'))
                win=allocate([winning],used)[0];all_ids.append(win)
            except ValueError:
                continue  # Formal fifth-copy tenpai is not a physically completable win.
            for method in ('ron','tsumo'):
                cfg=HandConfig(is_tsumo=method=='tsumo',is_riichi=declared and not double,
                               is_daburu_riichi=double,player_wind=27+decision['seat_wind'],
                               round_wind=27+decision['wind'],options=cfg_rules)
                score=HandCalculator().estimate_hand_value(all_ids,win,melds=melds,dora_indicators=indicators,config=cfg)
                row={'tile':winning,'method':method,'remaining':f['remaining37'][INDEX[winning]],
                     'error':score.error,'ron_blocked_by_permanent_furiten':method=='ron' and permanent}
                if score.error:
                    row.update(yaku=[],han=None,yaku_han=None,bonus_han=None,yakuman_multiplier=0,fu=None)
                else:
                    ys=[]
                    for y in score.yaku:
                        code=CATALOG.get(y.yaku_id,'upstream_'+str(y.yaku_id))
                        value=y.han_open if open_hand else y.han_closed
                        ys.append({'code':code,'name':str(y),'han':value,'yakuman':bool(y.is_yakuman)})
                    yakuman=sum(y['han']//13 for y in ys if y['yakuman'])
                    bonus=sum(y['han'] for y in ys if y['code'] in BONUS)
                    row.update(yaku=ys,han=None if yakuman else score.han,
                               yaku_han=None if yakuman else score.han-bonus,bonus_han=0 if yakuman else bonus,
                               yakuman_multiplier=yakuman,fu=score.fu)
                base['outcomes'].append(row)
    return {**base,'status':'computed'}


def matches(profile,q):
    if profile['status']=='dependency_missing':raise ValueError('和牌役番尚未计算：安装 requirements.txt 后重新 enrich')
    if profile['status']!='computed':return False
    required=canonical_yaku(q.get('win_yaku',''))
    for r in profile['outcomes']:
        if r['error'] or (q.get('win_live_only','1')=='1' and r['remaining']<=0):continue
        if q.get('win_method','either')!='either' and r['method']!=q['win_method']:continue
        codes={y['code'] for y in r['yaku']}
        if any(not (codes & (YAKUHAI if y=='yakuhai' else FAMILIES.get(y,{y}))) for y in required):continue
        if int(q.get('win_yakuman_min',0))>r['yakuman_multiplier']:continue
        value=r['han' if q.get('win_han_basis','total')=='total' else 'yaku_han']
        if 'win_han_min' in q or 'win_han_max' in q:
            if value is None or not int(q.get('win_han_min',0))<=value<=int(q.get('win_han_max',100)):continue
        return True
    return False


def active(q):
    return q.get('win_enabled')=='1' or bool(q.get('win_yaku')) or q.get('win_method') in ('ron','tsumo') or any(k in q for k in ('win_han_min','win_han_max','win_yakuman_min'))
