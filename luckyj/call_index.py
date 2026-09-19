"""Replay public call provenance and LuckyJ's response/self-kan opportunities.

Events already belong to one validated four-player round. Opponents' concealed
hands are not consulted. Registered response != intent: skips may be automatic;
a competing call or terminal event censors the absence of a LuckyJ response.
"""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
from itertools import combinations, product
import re
from .tiles import meld as decode_meld, token, kind, ranks
from .analysis import counts, effective, legal_discards, facts
from .shanten import shanten

VERSION = 'call-opportunities-1'
MOVE = re.compile(r'([TUVWDEFGtuvwdefg])(\d+)$')


def candidate_key(m, aka):
    return m['kind']+':'+','.join(sorted(token(t,aka) for t in m['tiles136']))


def response(events, start, actor, trigger):
    """Read the registered resolution, separately from the pre-event features."""
    for e in events[start+1:]:
        tag=e['tag'];a=e['attributes'];seq=e['event_seq']
        if tag=='N':
            if int(a['who'])==actor:
                return {'status':'selected','event_seq':seq,'meld':decode_meld(int(a['m']),actor)}
            return {'status':'censored','event_seq':seq,'reason':'other_player_call_priority_or_choice_unknown'}
        if tag=='AGARI':
            return {'status':'chose_win' if int(a['who'])==actor else 'censored',
                    'event_seq':seq,'reason':'winning_response'}
        if tag in ('RYUUKYOKU','INIT'):
            return {'status':'censored','event_seq':seq,'reason':'round_ended'}
        match=MOVE.fullmatch(tag)
        if match:
            letter=match[1].upper()
            if trigger=='external' and letter in 'TUVW':
                return {'status':'not_selected','event_seq':seq,'reason':'play_continued_without_registered_call'}
            if trigger=='self_draw' and letter in 'DEFG' and 'DEFG'.index(letter)==actor:
                return {'status':'not_selected','event_seq':seq,'reason':'discard_instead_of_kan'}
            return {'status':'censored','event_seq':seq,'reason':'unexpected_intervening_action'}
    return {'status':'censored','event_seq':None,'reason':'end_of_available_event_stream'}


def public_meld(m, actor, aka):
    out={'kind':m['kind'],'tiles':[token(t,aka) for t in m['tiles136']],
         'tiles136':list(m['tiles136']), 'called':token(m['called136'],aka) if m['called136'] is not None else None,
         'called136':m['called136'],'source_relative':(m['source']-actor)%4,
         'call_id':m.get('call_id'),'call_seq':m.get('call_seq'),
         'origin_call_seq':m.get('origin_call_seq'),
         'source_event_seq':m.get('source_event_seq'),
         'source_discard_number':m.get('source_discard_number'),
         'link_number':m.get('link_number')}
    return out


def enumerate_calls(snapshot, offered, source_relative, actor, wall, total_kans, aka):
    if snapshot['riichi'][0] or wall<=0: return []
    h=snapshot['hand136'];t=offered//4;bytype={i:[x for x in h if x//4==i] for i in range(34)}
    candidates=[]
    def add(kind_, consume):
        m={'kind':kind_,'who':actor,'source':(actor+source_relative)%4,
           'tiles136':sorted([*consume,offered]),'called136':offered,'consume136':list(consume)}
        after=deepcopy(snapshot)
        for x in consume:
            j=after['hand136'].index(x);after['hand136'].pop(j);after['hand'].pop(j)
        after['melds'][0].append(public_meld(m,actor,aka));after['draw']=None;after['draw136']=None
        if kind_ in ('chi','pon'):
            choices=legal_discards(after)
            if not choices:return  # kuikae can make a nominal call impossible
            minimum=min(shanten(tuple(n-int(i==kind(c)) for i,n in enumerate(counts(after['hand'])))) for c in choices)
            m['legal_post_discards']=choices;m['best_after_discard_shanten']=minimum
        else:
            m['legal_post_discards']=[];m['best_after_discard_shanten']=None
        m['shanten_before_replacement']=shanten(counts(after['hand'])) if kind_=='daiminkan' else None
        m['post_concealed'] = list(after['hand']);m['eligibility']='verified_structural_rules'
        m['key']=candidate_key(m,aka)
        candidates.append(m)
    if len(bytype[t])>=2:
        for c in combinations(bytype[t],2):add('pon',c)
    if len(bytype[t])==3 and total_kans<4:
        add('daiminkan',bytype[t])
    if source_relative==3 and t<27:
        for low in range(max((t//9)*9,t-2),min((t//9)*9+6,t)+1):
            needs=[i for i in range(low,low+3) if i!=t]
            if len(needs)==2:
                for combo in product(*(bytype[i] for i in needs)):add('chi',combo)
    unique={}
    for c in candidates:
        if c['key'] not in unique:
            c['physical_variants']=[c['consume136']];unique[c['key']]=c
        elif c['consume136'] not in unique[c['key']]['physical_variants']:
            unique[c['key']]['physical_variants'].append(c['consume136'])
    return list(unique.values())


def enumerate_kans(snapshot, actor, wall, total_kans, aka):
    if snapshot.get('draw136') is None or wall<=0 or total_kans>=4:return []
    h=snapshot['hand136'];bytype={i:[x for x in h if x//4==i] for i in range(34)};out=[]
    for t,ids in bytype.items():
        if len(ids)==4:
            after=[x for x in h if x//4!=t]
            if snapshot['riichi'][0]:
                # Tenhou: drawn fourth tile, no okurikan, unchanged waits;
                # changes of decomposition/yaku alone are NOT disqualifying.
                drawn=snapshot['draw136']
                before=list(h);before.remove(drawn)
                bh=counts(token(x,aka) for x in before);ah=counts(token(x,aka) for x in after)
                if drawn//4!=t or shanten(bh)!=0 or set(effective(bh))!=set(effective(ah)):
                    continue
            m={'kind':'ankan','who':actor,'source':actor,'called136':None,'tiles136':ids,
               'consume136':ids,'post_concealed':[token(x,aka) for x in after]}
            out.append(m)
        if snapshot['riichi'][0]:continue
        for existing in snapshot['melds'][0]:
            if existing['kind']=='pon' and kind(existing['tiles'][0])==t and ids:
                # At most one concealed fourth copy exists in a valid corpus.
                all_ids=list(existing['tiles136'])+[ids[0]]
                out.append({'kind':'kakan','who':actor,'source':(actor+existing['source_relative'])%4,
                            'called136':existing['called136'],'tiles136':all_ids,'consume136':[ids[0]],
                            'post_concealed':[token(x,aka) for x in h if x!=ids[0]],
                            'upgrades_call_id':existing.get('call_id')})
    for m in out:
        m['key']=candidate_key(m,aka);m['physical_variants']=[list(m['consume136'])]
        m['shanten_before_replacement']=shanten(counts(m['post_concealed']))
        m['best_after_discard_shanten']=None;m['legal_post_discards']=[]
        m['eligibility']='verified_structural_rules'
    return out


def index_round(events, actor, log_id, round_seq, game_type):
    aka=not bool(game_type&2);state=None;traces={};opportunities=[];call_events=[]
    link_count=0;total_kans=0;wall=70;last_discard=None
    order=[(actor+i)%4 for i in range(4)]
    def snap():
        return {'hand136':list(state['hand']), 'hand':[token(t,aka) for t in state['hand']],
                'draw136':state['draw'], 'draw':token(state['draw'],aka) if state['draw'] is not None else None,
                'rivers':[[dict(d) for d in state['rivers'][i]] for i in order],
                'melds':[[public_meld(m,actor,aka) for m in state['melds'][i]] for i in order],
                'riichi':[state['riichi'][i] for i in order],
                'riichi_pending':[state['pending'][i] for i in order],
                'scores':[state['scores'][i] for i in order],
                'ranks':[ranks(state['scores'],(state['dealer']-state['kyoku'])%4)[i] for i in order],
                'dealer_relative':(state['dealer']-actor)%4,
                'dora_indicators':[token(t,aka) for t in state['dora']],
                'dora136':list(state['dora']), 'wall_remaining':wall,
                'riichi_events':[state['riichi_events'][i] for i in order],
                'double_riichi':[state['double_riichi'][i] for i in order],
                'wind':state['kyoku']//4,'hand_no':state['kyoku']%4+1,
                'honba':state['honba'],'seat_wind':(actor-state['dealer'])%4,
                'turn':len(state['rivers'][actor])+1}
    def opportunity(index, trigger, offered=None, source=None):
        s=snap()
        candidates=(enumerate_calls(s,offered,source,actor,wall,total_kans,aka) if trigger=='external'
                    else enumerate_kans(s,actor,wall,total_kans,aka))
        resolution=response(events,index,actor,trigger)
        actual=resolution.get('meld')
        # A call that does not consume the offered tile is not this opportunity.
        if trigger=='external' and actual and actual['called136']!=offered:actual=None
        if not candidates and actual is None:return
        expected=candidate_key(actual,aka) if actual else None
        mismatch=expected is not None and expected not in {c['key'] for c in candidates}
        for c in candidates:c['selected']=c['key']==expected
        seq=events[index]['event_seq'];identifier=f'{log_id}:{round_seq}:{seq}:{trigger}'
        public_resolution={k:v for k,v in resolution.items() if k!='meld'}
        if mismatch:public_resolution={'status':'eligibility_mismatch','event_seq':resolution['event_seq'],'reason':expected}
        f=facts(s,aka)
        opportunities.append({'id':identifier,'log_id':log_id,'round_seq':round_seq,'event_seq':seq,
                              'trigger':trigger,'offered':token(offered,aka) if offered is not None else None,
                              'source_relative':source,'wall_remaining':wall,'total_kans':total_kans,
                              'shanten_before':shanten(counts(s['hand'])),
                              'snapshot':s,'availability':f,'candidates':candidates,
                              'resolution':public_resolution,'selected_key':expected,
                              'selected_kind':actual['kind'] if actual else None,
                              'registered_no_call_is_not_intent':True,
                              'red_variant_client_control':'not_inferred_from_xml'})
    for index,e in enumerate(events):
        seq=e['event_seq'];tag=e['tag'];a=e['attributes']
        if tag=='INIT':
            seed=[int(x) for x in a['seed'].split(',')]
            state={'hand':[int(x) for x in a['hai'+str(actor)].split(',')],
                   'draw':None,'draws':[None]*4,'rivers':[[],[],[],[]],'melds':[[],[],[],[]],
                   'riichi':[False]*4,'pending':[False]*4,'riichi_events':[None]*4,
                   'double_riichi':[False]*4,
                   'scores':[int(x)*100 for x in a['ten'].split(',')],
                   'dealer':int(a['oya']),'dora':[seed[5]],'kyoku':seed[0],'honba':seed[1]}
            wall=70;total_kans=0;last_discard=None;continue
        if state is None:continue
        match=MOVE.fullmatch(tag)
        if match:
            letter=match[1].upper();tile=int(match[2])
            if letter in 'TUVW':
                wall-=1;last_discard=None
                state['draws']['TUVW'.index(letter)]=tile
                if 'TUVW'.index(letter)==actor:
                    state['hand'].append(tile);state['draw']=tile
                    opportunity(index,'self_draw')
            else:
                who='DEFG'.index(letter)
                if who==actor:
                    traces[seq]={'melds':[[public_meld(m,actor,aka) for m in state['melds'][i]] for i in order],
                                 'wall_remaining':wall,'riichi_events':[state['riichi_events'][i] for i in order],
                                 'double_riichi':[state['double_riichi'][i] for i in order]}
                    state['hand'].remove(tile);state['draw']=None
                river=state['rivers'][who]
                river.append({'tile':token(tile,aka),'tile136':tile,'event_seq':seq,
                              'called_by':None,'riichi':state['pending'][who],'tsumogiri':state['draws'][who]==tile})
                last_discard=(who,tile,seq,len(river));state['draws'][who]=None
                if who!=actor:opportunity(index,'external',tile,(who-actor)%4)
        elif tag=='N':
            who=int(a['who']);m=decode_meld(int(a['m']),who)
            before=snap() if who==actor else None
            if m['kind']=='kakan':
                old=next(x for x in state['melds'][who] if x['kind']=='pon' and x['tiles136'][0]//4==m['tiles136'][0]//4)
                for k in ('source','called136','call_id','origin_call_seq','source_event_seq','source_discard_number','link_number'):
                    m[k]=old[k]
                state['melds'][who].remove(old);consume=[m['tiles136'][-1]]
            else:
                link_count+=1;m['link_number']=link_count;m['call_id']=f'{log_id}:{round_seq}:call:{seq}'
                m['origin_call_seq']=seq
                if m['kind']=='ankan':
                    m['source_event_seq']=None;m['source_discard_number']=None;consume=list(m['tiles136'])
                else:
                    if not last_discard or last_discard[:2]!=(m['source'],m['called136']):
                        raise ValueError(f'Call does not follow its source discard: {log_id}:{seq}')
                    m['source_event_seq']=last_discard[2];m['source_discard_number']=last_discard[3]
                    state['rivers'][m['source']][-1]['called_by']=(who-actor)%4
                    consume=[t for t in m['tiles136'] if t!=m['called136']]
            m['call_seq']=seq
            if who==actor:
                for t in consume:state['hand'].remove(t)
                state['draw']=None
            if m['kind'] in ('ankan','daiminkan','kakan'):total_kans+=1
            state['melds'][who].append(m);last_discard=None;state['draws'][who]=None
            call_events.append({'log_id':log_id,'round_seq':round_seq,'event_seq':seq,
                                'actor_relative':(who-actor)%4,**public_meld(m,actor,aka),
                                'before_snapshot':before,'after_concealed':[token(t,aka) for t in state['hand']] if who==actor else None})
        elif tag=='REACH':
            who=int(a['who'])
            if a['step']=='1':
                state['pending'][who]=True;state['riichi_events'][who]=seq
                state['double_riichi'][who]=not state['rivers'][who] and not call_events
            elif a['step']=='2':
                state['riichi'][who]=True;state['pending'][who]=False;state['scores'][who]-=1000
        elif tag=='DORA':state['dora'].append(int(a['hai']))
    return {'version':VERSION,'traces':traces,'opportunities':opportunities,'call_events':call_events}
