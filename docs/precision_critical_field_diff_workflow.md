# Precision Phase 2C：关键字段差异保护工作流

## 目标

Phase 2C 用于保护合同关键字段变化。它不做 LLM 复核，也不重构字段抽取系统，而是在 clause `MODIFY` diff 生成后识别高价值字段变化，给 diff 写入可回归的 flags 和 debug hints。

## 新增输出

命中字段 guard 的 diff 会包含：

```json
{
  "match_score_details": {
    "critical_field_diff_types": ["AMOUNT"],
    "critical_field_guard_applied": 1.0
  },
  "review_flags": [
    "CRITICAL_FIELD_CHANGE",
    "CRITICAL_FIELD_AMOUNT_CHANGE",
    "CRITICAL_VALUE_CHANGE"
  ]
}
```

## 支持字段

| 字段类型 | 示例 | review flag |
| --- | --- | --- |
| `AMOUNT` | `1000元` -> `5000元` | `CRITICAL_FIELD_AMOUNT_CHANGE` |
| `DATE` | `2026年6月30日` -> `2027年7月31日` | `CRITICAL_FIELD_DATE_CHANGE` |
| `PERCENT_RATE` | `6%` -> `13%` | `CRITICAL_FIELD_PERCENT_RATE_CHANGE` |
| `DURATION` | `30日` -> `45日` | `CRITICAL_FIELD_DURATION_CHANGE` |
| `QUANTITY` | `3台` -> `5台` | `CRITICAL_FIELD_QUANTITY_CHANGE` |
| `PARTY_ROLE` | `甲方` -> `乙方` | `CRITICAL_FIELD_PARTY_ROLE_CHANGE` |

## 使用方式

查看实际任务的 diff payload：

```text
diffs[].review_flags
diffs[].match_score_details.critical_field_diff_types
diffs[].match_score_details.critical_field_guard_applied
```

## 人工判断建议

- `CRITICAL_FIELD_CHANGE` 表示该 diff 包含业务字段变化，不表示系统已经完成法律风险判断。
- 如果同时出现 OCR 风险 flag，diff 应保留并进入人工复核，不应被低价值噪声规则删除。
- 如果字段变化被标记但人工判断为 OCR 噪声，应补充 focused test 收紧对应字段正则。
- 如果关键字段变化没有被标记，应优先补 `backend/tests/test_critical_field_guard.py` 的最小复现。

## 边界

第一版只处理 clause `MODIFY` diff，不处理整条 `ADD` / `DELETE`。table、header/footer、seal 等非 clause diff 即使带有类似 flag，也仍按各自质量规则处理，不会走 clause critical-field 保护路径。
