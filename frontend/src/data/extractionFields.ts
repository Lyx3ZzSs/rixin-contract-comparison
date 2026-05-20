export interface ExtractionFieldDefinition {
  id: string;
  name: string;
  type: string;
  description: string;
  semanticExtraction: boolean;
}

export const extractionFields: ExtractionFieldDefinition[] = [
  { id: "party-a-name", name: "甲方名称", type: "文本", description: "甲方（需方）名称", semanticExtraction: true },
  { id: "party-a-credit-code", name: "甲方统一社会信用代码", type: "文本", description: "甲方统一社会信用代码/注册号", semanticExtraction: true },
  { id: "party-a-legal-rep", name: "甲方法定代表人", type: "文本", description: "甲方法定代表人/经营者姓名", semanticExtraction: true },
  { id: "party-a-address", name: "甲方经营场所", type: "文本", description: "甲方住所或经营场所", semanticExtraction: true },
  { id: "party-a-phone", name: "甲方联系电话", type: "文本", description: "甲方联系人电话", semanticExtraction: true },
  { id: "party-b-name", name: "乙方名称", type: "文本", description: "乙方（供方）名称", semanticExtraction: true },
  { id: "party-b-credit-code", name: "乙方统一社会信用代码", type: "文本", description: "乙方统一社会信用代码/注册号", semanticExtraction: true },
  { id: "party-b-legal-rep", name: "乙方法定代表人", type: "文本", description: "乙方法定代表人/经营者姓名", semanticExtraction: true },
  { id: "party-b-address", name: "乙方经营场所", type: "文本", description: "乙方住所或经营场所", semanticExtraction: true },
  { id: "party-b-phone", name: "乙方联系电话", type: "文本", description: "乙方联系人电话", semanticExtraction: true },
  { id: "purchase-name", name: "采购货物名称", type: "文本", description: "合同采购标的名称", semanticExtraction: true },
  { id: "purchase-quantity", name: "采购数量", type: "文本", description: "采购货物数量", semanticExtraction: true },
  { id: "unit-price", name: "单价", type: "文本", description: "合同单价", semanticExtraction: true },
  { id: "total-price", name: "总价", type: "文本", description: "合同总价", semanticExtraction: true },
  { id: "payment-method", name: "支付方式", type: "文本", description: "付款方式和结算要求", semanticExtraction: true },
];
