from __future__ import annotations

import re


SIGNATURE_TERMS = re.compile(
    r"(?:签字页|签署页|此页无正文|以下无正文|盖章|单位名称|法定代表人|法人代表|"
    r"授权代表|授权委托人|委托代理人|签字|供方|需方|甲方|乙方|买方|卖方|"
    r"单位地址|开户银行|开户行|税号|邮政编码)"
)
ROLE_PATTERN = re.compile(r"(供\s*方|需\s*方|甲\s*方|乙\s*方|买\s*方|卖\s*方|丙\s*方|丁\s*方)")
LABEL_PATTERN = re.compile(
    r"(单位名称(?:（章）|\(章\))?|单位地址|地址|联系人|邮箱|Email|E-mail|合同编号|"
    r"签署地点|签约地点|法人代表或授权委托人|法定代表人|法人代表|"
    r"授权委托人|授权代表|委托代理人|签署人|签字人|电\s*话|传\s*真|开户\s*银行|开户行|"
    r"帐\s*号|账\s*号|统一社会信用代码|纳税人识别号|税号|税\s*号|邮政编码|政编码|日期)\s*[:：]"
)
TEMPLATE_CLEANUP_PATTERN = re.compile(
    r"(?:签字页|签署页|此页无正文|以下无正文|盖章|签字|单位名称|法定代表人|法人代表|"
    r"授权代表|授权委托人|委托代理人|签署人|签字人|电话|传真|开户银行|开户行|"
    r"帐号|账号|税号|邮政编码|政编码|日期|章)"
)
DATE_PATTERN = re.compile(r"(?:19|20)\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日")
PARTY_STAMP_PATTERN = re.compile(
    r"(甲\s*方|乙\s*方|丙\s*方|丁\s*方|供\s*方|需\s*方|买\s*方|卖\s*方)"
    r"\s*[:：]\s*[【\[]?\s*([^【】\[\]()（）:：]{2,80}?公司)\s*[】\]]?\s*[（(]?\s*盖章\s*[）)]?"
)

FIELD_KEY_LABELS = {
    "company_name": "单位名称",
    "address": "单位地址",
    "legal_representative": "法人代表",
    "authorized_representative": "授权代表",
    "agent": "委托代理人",
    "phone": "电话",
    "fax": "传真",
    "bank": "开户银行",
    "account": "账号",
    "tax_no": "税号",
    "postcode": "邮政编码",
    "date": "签署日期",
    "seal_text": "盖章主体",
    "contact": "联系人",
    "email": "邮箱",
    "contract_no": "合同编号",
    "sign_place": "签署地点",
    "credit_code": "统一社会信用代码",
}

PARTY_LABELS = {
    "supplier": "供方",
    "buyer": "需方",
    "party_a": "甲方",
    "party_b": "乙方",
    "seller": "卖方",
    "purchaser": "买方",
    "party_c": "丙方",
    "party_d": "丁方",
    "unknown_left": "左栏",
    "unknown_right": "右栏",
    "unknown": "签字页",
}

CONTINUATION_FIELDS = {"address", "bank", "postcode"}
