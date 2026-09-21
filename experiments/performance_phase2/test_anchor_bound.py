import random

import pytest
from app.services.page_alignment import PageSequenceAligner
from tests.test_page_alignment import _body, _document
from anchor_bound import AnchorBoundAligner


def assert_equivalent(a,b):
    left,right=_document(a),_document(b)
    expected=PageSequenceAligner().align(left,right)
    actual=AnchorBoundAligner().align(left,right)
    assert actual.entries == expected.entries
    assert actual.to_debug_payload() == expected.to_debug_payload()
    return expected,actual


@pytest.mark.parametrize('operation',['same','insert','delete','split','merge','reorder','header_footer','near_duplicate'])
def test_page_scenarios_preserve_every_mapping_field(operation):
    a=[_body(str(i)) for i in range(7)]
    b=a.copy()
    if operation=='insert': b.insert(3,'独立的新增安全保密条款，数据权限和加密办法。'*4)
    if operation=='delete': del b[3]
    if operation=='split': b[3:4]=[b[3][:25],b[3][25:]]
    if operation=='merge': b[3:5]=[''.join(b[3:5])]
    if operation=='reorder': b[2],b[4]=b[4],b[2]
    if operation=='header_footer':
        a=['共同页眉'+x+'共同页脚' for x in a];b=['共同页眉'+x+'共同页脚' for x in b]
    if operation=='near_duplicate': b[3]=b[2]+'附注'
    assert_equivalent(a,b)


def test_anchor_is_only_an_incumbent_not_a_forced_mapping():
    # An exact unique middle anchor is NOT proof that the optimal DP uses it.
    # Two nearly identical surrounding pages make the diagonal cheaper than gaps.
    common='双方确认交付范围、服务标准、付款方式及违约责任。'*15
    a=[common+'甲',common+'乙',_body('独立尾页')]
    b=[common+'丙',common+'甲',_body('独立尾页')]
    before,after=assert_equivalent(a,b)
    assert before.entries[0].compare_pages == (1,)
    assert after.entries[0].compare_pages == (1,)


def test_random_insert_delete_split_merge_reorder_and_duplicates():
    randomizer=random.Random(20260921)
    for _ in range(80):
        a=[_body(str(randomizer.randrange(15))) for _ in range(randomizer.randrange(1,10))]
        b=a.copy()
        for __ in range(randomizer.randrange(1,4)):
            index=randomizer.randrange(len(b))
            operation=randomizer.randrange(5)
            if operation==0:b.insert(index,_body('新增'+str(index)))
            elif operation==1 and len(b)>1:del b[index]
            elif operation==2:b[index:index+1]=[b[index][:30],b[index][30:]]
            elif operation==3 and index+1<len(b):b[index:index+2]=[''.join(b[index:index+2])]
            else:b.reverse()
        assert_equivalent(a,b)


def test_actual_similarity_evaluations_are_reduced():
    a=[_body(str(i)) for i in range(40)]
    before,after=assert_equivalent(a,a)
    assert after.performance['counters']['dp_states_pruned']>0
    assert after.performance['counters']['similarity_cache_misses'] < before.performance['counters']['similarity_cache_misses']


def test_conflicting_or_duplicate_anchors_explicitly_use_full_dp():
    for left,right in [([_body('1'),_body('2')],[_body('2'),_body('1')]),
                       ([_body('1'),_body('1')],[_body('1'),_body('1')])]:
        _,result=assert_equivalent(left,right)
        assert result.performance['counters']['full_dp_fallback_count'] == 1


def test_original_cost_and_precision_contract_supports_the_bound():
    aligner = PageSequenceAligner()
    # _similarity rounds before the DP uses the score, not just on backtracking.
    # If that contract changes, upper bounds must use unrounded transitions.
    doc = _document([_body('甲'), _body('乙') + '增加不同内容', ''])
    items = [aligner._fingerprint((page,)) for page in doc.pages]
    for left in items:
        assert aligner._gap_cost(left) >= .025
        for right in items:
            score = aligner._similarity(left, right)[0]
            assert 0 <= score <= 1 and score == round(score, 4)
            assert aligner._match_cost(score) >= .025 * 2
            assert aligner._group_cost(score) >= .025 * 3
