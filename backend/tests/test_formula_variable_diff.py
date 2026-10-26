from app.services.diff.range_refiner import changed_snippets, line_ranges, _match_lines_by_content
from app.services.diff.builder import critical_field_guard_snippet
from app.services.diff.critical_field_guard import critical_field_diff_types


def explanations():
    line = "其中:限电时刻PMi为i时刻的可用功率,不限电时刻"
    left = f"四日前预测45%\n{line}\nPPi为4日前预测功率\n日前预测65%\n{line}\nPMi为实际功率\n四小时前预测70%\n{line}"
    return left, left.replace("PMi", "PM", 1).replace("PPi", "Ppi", 1).replace("PMi为实际", "Pi为实际")


def test_repeated_explanations_keep_section_order():
    left, right = explanations()
    pairs = _match_lines_by_content(line_ranges(left, 0, len(left)), line_ranges(right, 0, len(right)))
    assert pairs == [(i, i) for i in range(8)]
    _, _, lr, _ = changed_snippets(left, right)
    assert all(r.end < left.index("四小时前") for r in lr)


def test_variable_missing_letter_does_not_delete_preserved_letters():
    for left, right, removed in [("PMi为实际功率", "Pi为实际功率", "M"), ("PMi为可用功率", "PM为可用功率", "i")]:
        _, _, lr, rr = changed_snippets(left, right)
        assert "".join(left[r.start:r.end] for r in lr) == removed
        assert rr == []
        _, _, lr, rr = changed_snippets(right, left)
        assert lr == []
        assert "".join(left[r.start:r.end] for r in rr) == removed


def test_disjoint_variable_edits_do_not_mark_unchanged_business_fields():
    left, right = explanations()
    left = left.replace("日前预测65%", "日前预测65 %,乙方4 日内完成")
    right = right.replace("日前预测65%", "日前预测65%,乙方4日内完成")
    ls, rs, lr, rr = changed_snippets(left, right)
    assert critical_field_diff_types(left, right, critical_field_guard_snippet(left, lr, ls),
                                     critical_field_guard_snippet(right, rr, rs)) == []


def test_real_variable_and_percentage_changes_remain_visible():
    left, right = "PMi为实际功率,准确率65%", "PQi为实际功率,准确率70%"
    ls, rs, lr, rr = changed_snippets(left, right)
    assert "M" in ls and "Q" in rs
    assert critical_field_diff_types(left, right, critical_field_guard_snippet(left, lr, ls),
                                     critical_field_guard_snippet(right, rr, rs)) == ["PERCENT_RATE"]
