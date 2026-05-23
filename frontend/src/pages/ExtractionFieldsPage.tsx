import { useMemo, useState } from "react";
import { Plus, RotateCcw, Save, Search, Trash2, X } from "lucide-react";

import type { ExtractionFieldDefinition } from "../data/extractionFields";
import {
  createExtractionFieldId,
  readExtractionFieldLibrary,
  resetExtractionFieldLibrary,
  writeExtractionFieldLibrary,
} from "../lib/extractionFieldLibrary";

type FieldDraft = Pick<ExtractionFieldDefinition, "name" | "description">;

const EMPTY_DRAFT: FieldDraft = {
  name: "",
  description: "",
};

export function ExtractionFieldsPage() {
  const [fields, setFields] = useState(() => readExtractionFieldLibrary());
  const [query, setQuery] = useState("");
  const [editingFieldId, setEditingFieldId] = useState("");
  const [isAdding, setIsAdding] = useState(false);
  const [draft, setDraft] = useState<FieldDraft>(EMPTY_DRAFT);

  const filteredFields = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    return fields.filter((field) => {
      return (
        !keyword ||
        field.name.toLowerCase().includes(keyword) ||
        field.description.toLowerCase().includes(keyword)
      );
    });
  }, [fields, query]);

  function persist(nextFields: ExtractionFieldDefinition[]) {
    setFields(nextFields);
    writeExtractionFieldLibrary(nextFields);
  }

  function updateDraft(key: keyof FieldDraft, value: string) {
    setDraft((currentDraft) => ({ ...currentDraft, [key]: value }));
  }

  function startAdd() {
    setIsAdding(true);
    setEditingFieldId("");
    setDraft(EMPTY_DRAFT);
  }

  function startEdit(field: ExtractionFieldDefinition) {
    setIsAdding(false);
    setEditingFieldId(field.id);
    setDraft({ name: field.name, description: field.description });
  }

  function cancelDraft() {
    setIsAdding(false);
    setEditingFieldId("");
    setDraft(EMPTY_DRAFT);
  }

  function saveDraft() {
    const name = draft.name.trim();
    const description = draft.description.trim();
    if (!name) {
      return;
    }
    if (isAdding) {
      persist([
        ...fields,
        {
          id: createExtractionFieldId(name),
          name,
          type: "文本",
          description,
          semanticExtraction: true,
        },
      ]);
    } else if (editingFieldId) {
      persist(
        fields.map((field) =>
          field.id === editingFieldId
            ? {
                ...field,
                name,
                type: field.type || "文本",
                description,
              }
            : field,
        ),
      );
    }
    cancelDraft();
  }

  function removeField(fieldId: string) {
    persist(fields.filter((field) => field.id !== fieldId));
    if (editingFieldId === fieldId) {
      cancelDraft();
    }
  }

  function toggleDefaultField(fieldId: string) {
    persist(
      fields.map((field) =>
        field.id === fieldId ? { ...field, semanticExtraction: !field.semanticExtraction } : field,
      ),
    );
  }

  function restoreDefaults() {
    setFields(resetExtractionFieldLibrary());
    setQuery("");
    cancelDraft();
  }

  const defaultCount = fields.filter((field) => field.semanticExtraction).length;

  return (
    <section className="field-library-workspace" aria-labelledby="field-library-title">
      <header className="field-library-header">
        <div>
          <span>合同智能提取</span>
          <h1 id="field-library-title">提取字段管理</h1>
        </div>
        <div className="field-library-summary" aria-label="字段库统计">
          <span>
            <b>{fields.length}</b>
            字段
          </span>
          <span>
            <b>{defaultCount}</b>
            默认字段
          </span>
        </div>
      </header>

      <div className="field-library-panel">
        <div className="field-library-toolbar">
          <label className="field-library-search">
            <Search aria-hidden="true" />
            <input
              aria-label="搜索提取字段"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索字段名称或描述"
            />
          </label>
          <button className="field-library-secondary" type="button" onClick={restoreDefaults}>
            <RotateCcw aria-hidden="true" />
            恢复默认字段
          </button>
          <button className="field-library-primary" type="button" onClick={startAdd}>
            <Plus aria-hidden="true" />
            新增字段
          </button>
        </div>

        {isAdding && (
          <FieldEditor
            draft={draft}
            title="新增提取字段"
            onCancel={cancelDraft}
            onChange={updateDraft}
            onSave={saveDraft}
          />
        )}

        <div className="field-library-table" aria-label="提取字段库">
          <div className="field-library-row header">
            <strong>字段名称</strong>
            <strong>字段描述</strong>
            <strong>是否默认</strong>
            <strong>操作</strong>
          </div>
          {filteredFields.length === 0 ? (
            <div className="field-library-empty">
              <strong>没有匹配字段</strong>
              <span>调整搜索或筛选条件后再试。</span>
            </div>
          ) : (
            filteredFields.map((field) =>
              editingFieldId === field.id ? (
                <FieldEditor
                  key={field.id}
                  draft={draft}
                  title={`编辑 ${field.name}`}
                  onCancel={cancelDraft}
                  onChange={updateDraft}
                  onSave={saveDraft}
                />
              ) : (
                <div className="field-library-row" key={field.id}>
                  <strong title={field.name}>{field.name}</strong>
                  <span title={field.description}>{field.description || "-"}</span>
                  <button
                    className={field.semanticExtraction ? "field-switch on" : "field-switch"}
                    type="button"
                    aria-label={`${field.name}是否默认`}
                    aria-pressed={field.semanticExtraction}
                    onClick={() => toggleDefaultField(field.id)}
                  />
                  <div className="field-library-actions">
                    <button type="button" onClick={() => startEdit(field)}>
                      编辑
                    </button>
                    <button type="button" onClick={() => removeField(field.id)}>
                      <Trash2 aria-hidden="true" />
                      删除
                    </button>
                  </div>
                </div>
              ),
            )
          )}
        </div>
      </div>
    </section>
  );
}

function FieldEditor({
  draft,
  title,
  onCancel,
  onChange,
  onSave,
}: {
  draft: FieldDraft;
  title: string;
  onCancel: () => void;
  onChange: (key: keyof FieldDraft, value: string) => void;
  onSave: () => void;
}) {
  return (
    <div className="field-library-editor" aria-label={title}>
      <strong>{title}</strong>
      <label>
        <span>字段名称</span>
        <input
          aria-label="字段名称"
          value={draft.name}
          onChange={(event) => onChange("name", event.target.value)}
          placeholder="例如 合同金额"
        />
      </label>
      <label>
        <span>字段描述</span>
        <input
          aria-label="字段描述"
          value={draft.description}
          onChange={(event) => onChange("description", event.target.value)}
          placeholder="用于提示模型抽取的字段含义"
        />
      </label>
      <div className="field-library-editor-actions">
        <button type="button" onClick={onSave} disabled={!draft.name.trim()}>
          <Save aria-hidden="true" />
          保存
        </button>
        <button type="button" onClick={onCancel}>
          <X aria-hidden="true" />
          取消
        </button>
      </div>
    </div>
  );
}
