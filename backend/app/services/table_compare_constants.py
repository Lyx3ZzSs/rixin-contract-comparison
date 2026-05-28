from __future__ import annotations

import re

NOISE_PATTERN = re.compile(r"^(共\d+页第\d+页|第?\d+页)$")
SUMMARY_LABEL_PATTERN = re.compile(
    r"(?:(?<!\d)\d{1,3}(?:套|项|台|个|批|份|件|年|月)?(?:总合计|总计|合计)|小计|总合计|总计|合计)"
)
AMOUNT_TOKEN_PATTERN = re.compile(r"(?:人民币|[¥￥])?[+-]?\d[\d,]*(?:\.\d+)?(?:万)?元?")
TABLE_HEADERS = {"序号", "产品名称", "详细配置", "品牌", "单位", "数量", "单价", "金额", "备注"}
TABLE_BLOCK_TYPES = {"table", "table_title", "table_cell"}
TABLE_TYPE_LABELS = {
    "cover": "封面信息",
    "product": "标的物",
    "payment": "付款节点",
    "acceptance": "验收标准",
    "contact": "联系人",
    "generic": "通用表格",
}
