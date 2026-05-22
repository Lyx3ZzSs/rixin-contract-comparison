from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).lower() in {"1", "true", "yes", "on"}


class Settings:
    base_dir: Path = Path(__file__).resolve().parents[1]
    storage_dir: Path = Path(os.getenv("STORAGE_DIR", base_dir / "storage"))
    uploads_dir: Path = storage_dir / "uploads"
    tasks_dir: Path = storage_dir / "tasks"
    highlighted_dir: Path = storage_dir / "highlighted"
    screenshots_dir: Path = storage_dir / "screenshots"
    reports_dir: Path = storage_dir / "reports"
    ocr_dir: Path = storage_dir / "ocr"

    document_extractor: str = os.getenv("DOCUMENT_EXTRACTOR", "auto").lower()
    pymupdf_min_text_chars: int = int(os.getenv("PYMUPDF_MIN_TEXT_CHARS", "1"))
    align_structured_extraction: bool = _env_bool("ALIGN_STRUCTURED_EXTRACTION", "true")
    ppocrv5_url: str = os.getenv("PPOCRV5_URL", "")
    ppocrv5_access_token: str = os.getenv("PPOCRV5_ACCESS_TOKEN", "")
    ppocrv5_timeout_seconds: int = int(os.getenv("PPOCRV5_TIMEOUT_SECONDS", "600"))
    ppocrv5_return_word_box: bool = _env_bool("PPOCRV5_RETURN_WORD_BOX", "true")
    ppocrv5_text_rec_score_thresh: float = float(os.getenv("PPOCRV5_TEXT_REC_SCORE_THRESH", "0.0"))
    ppocrv5_edge_noise_score_thresh: float = float(os.getenv("PPOCRV5_EDGE_NOISE_SCORE_THRESH", "0.30"))
    ppocrv5_edge_noise_margin_ratio: float = float(os.getenv("PPOCRV5_EDGE_NOISE_MARGIN_RATIO", "0.02"))
    ppocrv5_edge_noise_max_chars: int = int(os.getenv("PPOCRV5_EDGE_NOISE_MAX_CHARS", "2"))
    ppocrv5_use_doc_orientation_classify: bool = _env_bool("PPOCRV5_USE_DOC_ORIENTATION_CLASSIFY", "false")
    ppocrv5_use_doc_unwarping: bool = _env_bool("PPOCRV5_USE_DOC_UNWARPING", "false")
    ppocrv5_use_textline_orientation: bool = _env_bool("PPOCRV5_USE_TEXTLINE_ORIENTATION", "false")
    ppstructure_url: str = os.getenv("PPSTRUCTURE_URL", "")
    ppstructure_access_token: str = os.getenv("PPSTRUCTURE_ACCESS_TOKEN", "")
    ppstructure_timeout_seconds: int = int(os.getenv("PPSTRUCTURE_TIMEOUT_SECONDS", "600"))
    ppstructure_use_doc_orientation_classify: bool = _env_bool("PPSTRUCTURE_USE_DOC_ORIENTATION_CLASSIFY", "false")
    ppstructure_use_doc_unwarping: bool = _env_bool("PPSTRUCTURE_USE_DOC_UNWARPING", "false")
    ppstructure_use_textline_orientation: bool = _env_bool("PPSTRUCTURE_USE_TEXTLINE_ORIENTATION", "false")
    ppstructure_use_table_recognition: bool = _env_bool("PPSTRUCTURE_USE_TABLE_RECOGNITION", "true")
    ppstructure_use_seal_recognition: bool = _env_bool("PPSTRUCTURE_USE_SEAL_RECOGNITION", "false")
    ppstructure_use_region_detection: bool = _env_bool("PPSTRUCTURE_USE_REGION_DETECTION", "true")
    ppstructure_format_block_content: bool = _env_bool("PPSTRUCTURE_FORMAT_BLOCK_CONTENT", "true")
    hybrid_layout_overlap_threshold: float = float(os.getenv("HYBRID_LAYOUT_OVERLAP_THRESHOLD", "0.5"))
    hybrid_layout_center_fallback: bool = _env_bool("HYBRID_LAYOUT_CENTER_FALLBACK", "true")
    hybrid_save_merged_raw: bool = _env_bool("HYBRID_SAVE_MERGED_RAW", "true")
    save_ocr_raw_result: bool = _env_bool("SAVE_OCR_RAW_RESULT", "true")

    match_threshold: int = int(os.getenv("MATCH_THRESHOLD", "85"))
    report_font_path: str = os.getenv("REPORT_FONT_PATH", "")
    libreoffice_path: str = os.getenv("LIBREOFFICE_PATH", "")

    ai_llm_base_url: str = os.getenv("AI_LLM_BASE_URL", "")
    ai_llm_api_key: str = os.getenv("AI_LLM_API_KEY", "")
    ai_llm_model: str = os.getenv("AI_LLM_MODEL", "")
    ai_extraction_timeout_seconds: int = int(os.getenv("AI_EXTRACTION_TIMEOUT_SECONDS", "120"))
    report_max_screenshot_pages: int = int(os.getenv("REPORT_MAX_SCREENSHOT_PAGES", "10"))

    max_upload_size_mb: int = int(os.getenv("MAX_UPLOAD_SIZE_MB", "30"))
    extraction_max_document_size_mb: int = int(os.getenv("EXTRACTION_MAX_DOCUMENT_SIZE_MB", "60"))
    extraction_max_image_size_mb: int = int(os.getenv("EXTRACTION_MAX_IMAGE_SIZE_MB", "5"))
    save_extraction_raw_result: bool = _env_bool("SAVE_EXTRACTION_RAW_RESULT", "true")
    extraction_task_description: str = os.getenv("EXTRACTION_TASK_DESCRIPTION", (
        "你是一个专业的企业合同信息抽取助手。"
        "你的任务是：从 OCR 解析后的合同文本中，准确提取指定字段的信息。"
        "请严格遵守以下要求：\n"
        "【一、信息来源要求】\n"
        "- 所有答案必须完全基于 OCR 文本内容；禁止使用外部知识；禁止猜测；"
        "禁止补充 OCR 中不存在的信息；如果无法确定，请返回\"未知\"。\n"
        "【二、合同场景特殊要求】\n"
        "- 合同属于法律文本，必须保持原文语义；不允许改写、简化或总结；"
        "金额、日期、编号、单位、比例必须与原文一致；必须保留人民币符号、百分号、小数点、逗号等格式。\n"
        "【三、字段提取原则】\n"
        "- 优先提取合同正文中的明确信息；"
        "若同一字段出现多个值，优先选择：1.合同首页 2.合同签署页 3.最终版本内容。\n"
        "【四、语义理解规则】\n"
        "- 合同中甲乙双方信息通常以\"甲方：\"\"乙方：\"标签分段列出，段内字段不带甲/乙方前缀。"
        "keyList 中的字段名可能包含\"甲方\"或\"乙方\"前缀（如\"甲方法定代表人\"），"
        "对应文档中甲方/乙方段落下不带前缀的字段（如\"法定代表人\"）。"
        "请根据字段名中的前缀定位到正确的段落再提取。\n"
        "- 同义词映射：合同金额=合同总价=含税金额=总金额；"
        "甲方=买方=采购方；乙方=卖方=供应商；签订日期=合同日期。\n"
        "【五、幻觉控制】\n"
        "- 不允许生成 OCR 中不存在的公司名称、金额、日期；不允许编造合同条款。\n"
        "OCR 内容使用 ``` 包围。问题列表使用 [] 包围。"
    ))
    extraction_output_format: str = os.getenv("EXTRACTION_OUTPUT_FORMAT", (
        "请严格使用 JSON 格式输出结果。\n"
        '输出格式：{"字段名": {"value": "提取结果", "confidence": 0.95, "evidence": "OCR原文片段"}}\n'
        "- value：字段值；confidence：0~1 置信度；evidence：对应的 OCR 原文。\n"
        '如果无法确定：{"字段名": {"value": "未知", "confidence": 0, "evidence": ""}}\n'
        "禁止输出 markdown、注释、多余解释、非 JSON 内容。"
    ))
    extraction_rules_str: str = os.getenv("EXTRACTION_RULES_STR", (
        "【合同信息抽取规则】\n"
        "1. 金额规则：必须保留货币单位（元/万元/人民币/USD）；不允许自动换算；不允许丢失小数位。\n"
        "2. 日期规则：保持原始日期格式，不允许改写为其他格式。\n"
        "3. 编号规则：合同编号必须完整，不允许遗漏字母、横杠、下划线、前缀。\n"
        "4. 百分比规则：百分比必须带 %，不允许转换为小数。\n"
        "5. OCR 纠错规则：允许轻微 OCR 纠错，但必须保证语义不变、数字不变、金额不变。\n"
        "6. 上下文规则：如果字段缺失但上下文明确指向该字段，可进行合理关联；"
        "不允许超出 OCR 内容进行推理。\n"
        "7. 多页规则：允许跨页查找字段；如果字段在附件中出现，以正文优先。\n"
        "8. 表格规则：如果字段位于表格中，必须结合表头理解，不允许只抽取单元格值。\n"
        "9. 甲乙方段落定位规则：字段名中的\"甲方\"\"乙方\"前缀用于定位对应段落，"
        "段内字段标签不含前缀。例如\"甲方法定代表人\"应在甲方段落中查找\"法定代表人\"对应的值。\n"
        "10. 未知规则：如果无法确认，必须返回\"未知\"。"
    ))
    extraction_few_shot_demo: str = os.getenv("EXTRACTION_FEW_SHOT_DEMO", json.dumps({
        "demo_text_content": (
            "甲方：国家电网有限公司\n"
            "法定代表人：张三\n"
            "委托代理人：\n"
            "通讯地址：北京市海淀区XX路XX号\n"
            "开户行：中国银行北京海淀支行\n"
            "账号：1234567890123\n"
            "税号：91110108XXXXXXXXXX\n"
            "电话：010-12345678\n"
            "乙方：南瑞继保电气有限公司\n"
            "法定代表人：李四\n"
            "委托代理人：王五\n"
            "通讯地址：南京市江宁区XX路XX号\n"
            "开户行：工商银行南京江宁支行\n"
            "账号：9876543210456\n"
            "税号：91320100XXXXXXXXXX\n"
            "电话：025-87654321"
        ),
        "demo_key_value_list": {
            "甲方名称": {"value": "国家电网有限公司", "confidence": 0.98, "evidence": "甲方：国家电网有限公司"},
            "甲方法定代表人": {"value": "张三", "confidence": 0.95, "evidence": "法定代表人：张三"},
            "甲方委托代理人": {"value": "未知", "confidence": 0, "evidence": ""},
            "甲方通讯地址": {"value": "北京市海淀区XX路XX号", "confidence": 0.95, "evidence": "通讯地址：北京市海淀区XX路XX号"},
            "甲方开户行": {"value": "中国银行北京海淀支行", "confidence": 0.95, "evidence": "开户行：中国银行北京海淀支行"},
            "甲方账号": {"value": "1234567890123", "confidence": 0.95, "evidence": "账号：1234567890123"},
            "甲方税号": {"value": "91110108XXXXXXXXXX", "confidence": 0.95, "evidence": "税号：91110108XXXXXXXXXX"},
            "甲方电话": {"value": "010-12345678", "confidence": 0.95, "evidence": "电话：010-12345678"},
            "乙方名称": {"value": "南瑞继保电气有限公司", "confidence": 0.98, "evidence": "乙方：南瑞继保电气有限公司"},
            "乙方法定代表人": {"value": "李四", "confidence": 0.95, "evidence": "法定代表人：李四"},
            "乙方委托代理人": {"value": "王五", "confidence": 0.9, "evidence": "委托代理人：王五"},
            "乙方通讯地址": {"value": "南京市江宁区XX路XX号", "confidence": 0.95, "evidence": "通讯地址：南京市江宁区XX路XX号"},
            "乙方开户行": {"value": "工商银行南京江宁支行", "confidence": 0.95, "evidence": "开户行：工商银行南京江宁支行"},
            "乙方账号": {"value": "9876543210456", "confidence": 0.95, "evidence": "账号：9876543210456"},
            "乙方税号": {"value": "91320100XXXXXXXXXX", "confidence": 0.95, "evidence": "税号：91320100XXXXXXXXXX"},
            "乙方电话": {"value": "025-87654321", "confidence": 0.95, "evidence": "电话：025-87654321"},
        },
    }, ensure_ascii=False))

    @property
    def storage_subdirs(self) -> list[Path]:
        return [
            self.uploads_dir,
            self.tasks_dir,
            self.highlighted_dir,
            self.screenshots_dir,
            self.reports_dir,
            self.ocr_dir,
        ]

    def ensure_storage(self) -> None:
        for directory in self.storage_subdirs:
            directory.mkdir(parents=True, exist_ok=True)


settings = Settings()
