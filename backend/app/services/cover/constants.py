FIELD_LABELS: dict[str, str] = {
    "contract_no": "合同编号",
    "project_title": "项目名称",
    "buyer": "甲方",
    "seller": "乙方",
    "tax_no": "税号",
    "sign_place": "签订地点",
    "sign_date": "签订日期",
}

LABEL_TO_KEY: dict[str, str] = {
    "合同编号": "contract_no",
    "项目名称": "project_title",
    "项目": "project_title",
    "甲": "buyer",
    "甲方": "buyer",
    "买方": "buyer",
    "乙": "seller",
    "乙方": "seller",
    "卖方": "seller",
    "税号": "tax_no",
    "纳税人识别号": "tax_no",
    "签订地点": "sign_place",
    "签订日期": "sign_date",
    "签订时间": "sign_date",
}

FIELD_ORDER: list[str] = [
    "contract_no",
    "project_title",
    "buyer",
    "seller",
    "tax_no",
    "sign_place",
    "sign_date",
]

EXTRA_FIELD_LABELS: tuple[str, ...] = (
    "账号",
    "税号",
    "电话",
    "传真",
    "开户行",
    "法定代表人",
    "委托代理人",
    "通讯地址",
)
