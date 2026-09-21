"""Experimental version-2 wire format. Lossless, no fields removed from v1."""
import copy
import hashlib
import json


def pack(payload):
    evidence_by_id={}
    def item(diff):
        output=dict(diff)
        for side in ('original','compare'):
            key=side+'_evidence'
            if key not in output:
                continue
            references=[]
            for evidence in output.pop(key):
                canonical=json.dumps(evidence,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
                identity='ev_'+hashlib.sha256(canonical.encode()).hexdigest()
                if identity in evidence_by_id and evidence_by_id[identity]!=evidence:
                    raise ValueError('Evidence digest collision')
                evidence_by_id[identity]=evidence
                references.append(identity)
            output[side+'_evidence_refs']=references
        return output
    events=[dict(event,diff=item(event['diff']),children=[item(child) for child in event['children']]) for event in payload['events']]
    return dict(payload,events=events,evidence_by_id=evidence_by_id,wire_version=2)


def unpack(payload):
    if payload.get('wire_version')!=2:
        raise ValueError('Unsupported wire version')
    evidence=payload['evidence_by_id']
    def item(diff):
        result=dict(diff)
        for side in ('original','compare'):
            key=side+'_evidence_refs'
            if key in result:
                # Independent objects preserve v1 mutation semantics exactly.
                result[side+'_evidence']=[copy.deepcopy(evidence[ref]) for ref in result.pop(key)]
        return result
    result={key:value for key,value in payload.items() if key not in {'wire_version','evidence_by_id'}}
    result['events']=[dict(event,diff=item(event['diff']),children=[item(child) for child in event['children']]) for event in payload['events']]
    return result
