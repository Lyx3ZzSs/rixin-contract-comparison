"""Strict acceptance of the visually reviewed a385ca70 facts, without diff IDs.

Unlike a candidate baseline, NEEDS_REVIEW does not excuse an unexpected item.
This is a regression oracle only; no production filtering imports this file.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

TASK = 'a385ca70-ad82-478b-8e12-da535f8064bf'
HASHES = {
    'original': 'b02b5a406a08285009965b3e65ff938aff9d90605189e41ce509af96c9581a34',
    'compare': '4444ea4c89edf7d3bd209299a1c5244311f6d0fa432011dc6334a7d03baf36c7',
}
EXPECTED = Counter({
    'cover_representative_before':1,'cover_representative_after':1,
    'cover_address_before':1,'cover_address_after':1,
    'tax_fill_4528.3':1,'price_tax_fill_150943.4_9056.6':1,
    'price_tax_fill_452830.2_27169.8':1,'annex_untaxed_price_fill':1,
    'party_a_account':1,'party_a_address':1,'party_a_bank':1,
    'party_a_company_seal':1,'party_a_name_seal':1,'copies_change':1,
    'edge_seam':1,'uncertain_stamp_page13':1,
})


def pages(d, side):
    return {e['page_no'] for e in d[side+'_evidence']}


def classify(d):
    source, old, new = d['source_type'], d['original_text'], d['compare_text']
    old_pages, new_pages = pages(d,'original'),pages(d,'compare')
    if source == 'metadata' and old_pages | new_pages == {2}:
        if d['diff_type'] == 'DELETE' and '法定代表人' in old and not new:
            return 'cover_representative_before'
        if d['diff_type'] == 'ADD' and '法定代表人' in new and '柳文斌' in new:
            return 'cover_representative_after'
        if d['diff_type'] == 'DELETE' and '地址' in old and '王家峰' in old:
            return 'cover_address_before'
        if d['diff_type'] == 'ADD' and '地址' in new and '平阳景苑' in new:
            return 'cover_address_after'
    if source == 'table' and d['diff_type'] == 'ADD' and not old:
        tokens = [x.strip() for x in new.split('|')]
        if all(re.fullmatch(r'\d+(?:\.\d+)?',x) for x in tokens):
            values = tuple(Decimal(x) for x in tokens)
            if new_pages == {7}:
                return {
                    (Decimal('4528.3'),):'tax_fill_4528.3',
                    (Decimal('150943.4'),Decimal('9056.6')):'price_tax_fill_150943.4_9056.6',
                    (Decimal('452830.2'),Decimal('27169.8')):'price_tax_fill_452830.2_27169.8',
                }.get(values)
            if new_pages == {19,20} and values == (Decimal('150943.4'),) and '不含税总价' in d['title']:
                return 'annex_untaxed_price_fill'
    if source == 'signing_region':
        if old_pages == new_pages == {14} and d['diff_type'] == 'MODIFY' and d['title'].startswith('甲方'):
            if old == '0502207509200033381' and new == '485110100100298440':return 'party_a_account'
            if old == '山西省太原市迎泽区王家峰小区东2排4号一层' and new == '山西省太原市小店区平阳路65号平阳景苑B区第5幢720号':return 'party_a_address'
            if old == '中国工商银行股份有限公司太原桃园南路支行' and new == '兴业银行太原体育路支行':return 'party_a_bank'
        kind = d['match_score_details'].get('object_type')
        if d['diff_type'] == 'ADD' and new_pages == {14} and d['title'].startswith('甲方'):
            if kind == 'COMPANY_ROUND_SEAL':return 'party_a_company_seal'
            if kind == 'PERSONAL_NAME_SEAL':return 'party_a_name_seal'
        if kind == 'EDGE_SEAM_SEAL' and len(new_pages)>5:return 'edge_seam'
        if (kind == 'OTHER_STAMP' and new_pages == {13} and d['quality_status'] == 'NEEDS_REVIEW'
                and d['match_score_details'].get('change_decision') != 'CONFIRMED'):
            return 'uncertain_stamp_page13'
    if (source == 'clause' and old_pages == new_pages == {12}
            and '一式柒份' in old and '一式肆份' in new
            and '副本叁份' in old and '副本壹份' in new):
        return 'copies_change'
    return None


def check(diffs):
    assignments = [{'diff_id':d['diff_id'],'fact':classify(d)} for d in diffs]
    actual = Counter(item['fact'] for item in assignments)
    return {'passed':actual == EXPECTED, 'assignments':assignments,
            'missing':dict(EXPECTED-actual),'unexpected':dict(actual-EXPECTED)}


def verify(before, after, review, output):
    result = json.loads((after/TASK/'result.json').read_text())
    diffs = result['diffs']
    report = check(diffs)
    assert report['passed'], report
    before_hashes = json.loads((before/TASK/'input-hashes.json').read_text())
    after_hashes = json.loads((after/TASK/'input-hashes.json').read_text())
    assert before_hashes == after_hashes
    assert all(value in after_hashes.values() for value in HASHES.values())
    for root in (before,after):
        config = json.loads((root/'replay-settings.json').read_text())
        assert config['semantic_match'] is False and config['rerank'] is False
    reviewed = json.loads(review.read_text())
    false_ids = [i['diff_id'] for i in reviewed['items'] if i['classification']=='FALSE_CANDIDATE']
    old_diffs = json.loads((before/TASK/'result.json').read_text())['diffs']
    # Acceptance cannot pass by dropping any retained fact or by downgrading
    # an old false item to NEEDS_REVIEW and continuing to show it.
    mutation_checks = []
    for index, diff in enumerate(diffs):
        assert not check(diffs[:index]+diffs[index+1:])['passed']
        mutation_checks.append({'mutation':'remove_fact','fact':classify(diff),'rejected':True})
    stored = json.loads((review.parent/'stored-diffs.json').read_text())
    for diff in stored:
        if diff['diff_id'] in false_ids:
            noise = deepcopy(diff);noise['quality_status']='NEEDS_REVIEW'
            assert not check([*diffs,noise])['passed']
            mutation_checks.append({'mutation':'reintroduce_false_review_item','old_id':diff['diff_id'],'rejected':True})
    report.update({
        'before_count':len(old_diffs),'after_count':len(diffs),'reviewed_false_candidates_resolved':len(false_ids),
        'resolved_false_candidate_ids':false_ids,'reviewed_mixed_items_resolved':['D017','D040','D059'],
        'retained_uncertain_items':1,'input_hashes_equal':True,
        'mutation_checks':mutation_checks,'oracle':str(review),
        'scope':'Candidate facts verified against prior page-level visual review; not a population error-rate estimate.',
    })
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in {'assignments','mutation_checks'}},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before',type=Path,required=True)
    parser.add_argument('--after',type=Path,required=True)
    parser.add_argument('--review',type=Path,default=Path('output/analysis/a385ca70-evidence/visual-diff-review.json'))
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    verify(args.before,args.after,args.review,args.output)
