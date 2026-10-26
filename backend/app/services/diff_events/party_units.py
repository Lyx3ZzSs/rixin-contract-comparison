"""Project fully accounted party-field edits without changing raw evidence."""
from app.services.party_field_scope import text_fields


def plan(raw):
    if raw.source_type != 'clause' or raw.diff_type != 'MODIFY':
        return None
    fields={s:text_fields(getattr(raw,f'{s}_text')) for s in ('original','compare')}
    common=fields['original'].keys() & fields['compare'].keys()
    selections={}
    for side in ('original','compare'):
        text=getattr(raw,f'{side}_text');boxes=getattr(raw,f'{side}_evidence')
        used=set()
        for index,r in enumerate(getattr(raw,f'{side}_change_ranges')):
            owners=[key for key in common if fields[side][key][0]<=r.start<r.end<=fields[side][key][1]]
            if len(owners)!=1:
                return None
            evidence=[i for i,b in enumerate(boxes) if b.text==text[r.start:r.end]]
            # Repeated glyphs and multi-line evidence require richer geometry;
            # retain the original event rather than assigning an uncertain box.
            if len(evidence)!=1 or evidence[0] in used:
                return None
            used.update(evidence)
            unit=selections.setdefault(owners[0],{s:{'span':fields[s][owners[0]],'ranges':[],'evidence':[]} for s in fields})
            unit[side]['ranges'].append(index);unit[side]['evidence'].extend(evidence)
        if used!=set(range(len(boxes))):
            return None
    return selections or None


def partitions(event,raw,identity,version,task_id):
    if len(event['raw_diff_ids'])!=1:
        return [event]
    units=plan(raw)
    if not units:
        return [event]
    result=[]
    for key,selection in units.items():
        values={s:getattr(raw,f'{s}_text')[v['span'][0]:v['span'][1]] for s,v in selection.items()}
        direction='MODIFY' if values['original'] and values['compare'] else 'ADD' if values['compare'] else 'DELETE'
        result.append({**event,'event_id':'E-'+identity([version,task_id,event['event_id'],'party_field',key,selection])[:24],
            'field_selection':selection,'field_title':f'{key[0]} · {key[2]}','party_identity':list(key[:2]),
            'direction':direction,'element_type':'party_field','related_event_group':event['event_id'],
            'grouping_reason':'party_qualified_field'})
    return result


def project(raw,event):
    result=raw.model_copy(deep=True)
    result.title=event['field_title'];result.diff_type=event['direction']
    for side,selection in event['field_selection'].items():
        start,end=selection['span'];text=getattr(raw,f'{side}_text')[start:end]
        setattr(result,f'{side}_text',text)
        setattr(result,f'{side}_snippet',text)
        ranges=getattr(raw,f'{side}_change_ranges')
        setattr(result,f'{side}_change_ranges',[ranges[i].model_copy(update={'start':ranges[i].start-start,'end':ranges[i].end-start}) for i in selection['ranges']])
        setattr(result,f'{side}_evidence',[getattr(raw,f'{side}_evidence')[i].model_copy(deep=True) for i in selection['evidence']])
    result.readable_change=f'{result.title}：{result.original_text} → {result.compare_text}'
    return result
