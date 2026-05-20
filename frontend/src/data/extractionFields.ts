export interface ExtractionFieldDefinition {
  id: string;
  name: string;
  type: string;
  description: string;
  semanticExtraction: boolean;
}

export const extractionFields: ExtractionFieldDefinition[] = [
  { id: "party-a-name", name: "甲方名称", type: "文本", description: "甲方名称", semanticExtraction: true },
  { id: "party-a-legal-rep", name: "甲方法定代表人", type: "文本", description: "甲方法定代表人姓名", semanticExtraction: true },
  { id: "party-a-agent", name: "甲方委托代理人", type: "文本", description: "甲方委托代理人姓名", semanticExtraction: true },
  { id: "party-a-address", name: "甲方通讯地址", type: "文本", description: "甲方通讯地址", semanticExtraction: true },
  { id: "party-a-bank", name: "甲方开户行", type: "文本", description: "甲方开户银行名称", semanticExtraction: true },
  { id: "party-a-account", name: "甲方账号", type: "文本", description: "甲方银行账号", semanticExtraction: true },
  { id: "party-a-tax", name: "甲方税号", type: "文本", description: "甲方纳税人识别号", semanticExtraction: true },
  { id: "party-a-phone", name: "甲方电话", type: "文本", description: "甲方联系电话", semanticExtraction: true },
  { id: "party-b-name", name: "乙方名称", type: "文本", description: "乙方名称", semanticExtraction: true },
  { id: "party-b-legal-rep", name: "乙方法定代表人", type: "文本", description: "乙方法定代表人姓名", semanticExtraction: true },
  { id: "party-b-agent", name: "乙方委托代理人", type: "文本", description: "乙方委托代理人姓名", semanticExtraction: true },
  { id: "party-b-address", name: "乙方通讯地址", type: "文本", description: "乙方通讯地址", semanticExtraction: true },
  { id: "party-b-bank", name: "乙方开户行", type: "文本", description: "乙方开户银行名称", semanticExtraction: true },
  { id: "party-b-account", name: "乙方账号", type: "文本", description: "乙方银行账号", semanticExtraction: true },
  { id: "party-b-tax", name: "乙方税号", type: "文本", description: "乙方纳税人识别号", semanticExtraction: true },
  { id: "party-b-phone", name: "乙方电话", type: "文本", description: "乙方联系电话", semanticExtraction: true },
];
