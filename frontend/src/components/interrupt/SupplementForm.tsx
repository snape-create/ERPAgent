"use client";

import { useMemo, useState } from "react";
import { Plus, Send, X } from "lucide-react";
import { InterruptData } from "@/lib/types";
import Stamp from "@/components/common/Stamp";

interface Props {
  data: InterruptData;
  onSubmit: (supplement: string) => void;
}

interface DetailLine {
  partId: string;
  quantity: string;
  unitPrice: string;
}

const EMPTY_LINE: DetailLine = { partId: "", quantity: "", unitPrice: "" };

/** 只保留有内容的字段，避免把空串写进订单 */
function num(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : null;
}

/**
 * 补充信息表单：直接给结构化输入，提交时拼成 JSON 交给后端。
 *
 * 后端 parse_supplement_text 支持 JSON 直通，所以这里不再依赖"正则猜字段"，
 * 用户填什么就是什么——不会被"零件 P-001"这类写法卡在重复追问上。
 */
export default function SupplementForm({ data, onSubmit }: Props) {
  const extracted = (data.extracted_data ?? {}) as Record<string, unknown>;
  const prefilled = Array.isArray(extracted.orderDetail)
    ? (extracted.orderDetail as Record<string, unknown>[])
    : [];

  const [orderNumber, setOrderNumber] = useState(
    typeof extracted.orderNumber === "string" ? extracted.orderNumber : ""
  );
  const [supplierId, setSupplierId] = useState(
    extracted.supplierId != null ? String(extracted.supplierId) : ""
  );
  const [remark, setRemark] = useState(
    typeof extracted.remark === "string" ? extracted.remark : ""
  );
  const [lines, setLines] = useState<DetailLine[]>(
    prefilled.length
      ? prefilled.map((item) => ({
          partId: item.partId != null ? String(item.partId) : "",
          quantity: item.quantity != null ? String(item.quantity) : "",
          unitPrice: item.unitPrice != null ? String(item.unitPrice) : "",
        }))
      : [{ ...EMPTY_LINE }]
  );

  const updateLine = (index: number, key: keyof DetailLine, value: string) => {
    setLines((prev) =>
      prev.map((line, i) => (i === index ? { ...line, [key]: value } : line))
    );
  };

  const addLine = () => setLines((prev) => [...prev, { ...EMPTY_LINE }]);
  const removeLine = (index: number) =>
    setLines((prev) =>
      prev.length === 1 ? prev : prev.filter((_, i) => i !== index)
    );

  interface ParsedLine {
    partId?: number;
    partCode?: string;
    quantity: number | null;
    unitPrice: number | null;
  }

  // 零件允许填数字 ID（1）或编码（P-001）：编码交给后端换成 partId，
  // 否则用户填了编码只会看到按钮变灰、莫名其妙
  const detail = useMemo<ParsedLine[]>(
    () =>
      lines
        .map((line) => {
          const item: ParsedLine = {
            quantity: num(line.quantity),
            unitPrice: num(line.unitPrice),
          };
          const partId = num(line.partId);
          if (partId != null) item.partId = partId;
          else if (line.partId.trim()) item.partCode = line.partId.trim().toUpperCase();
          return item;
        })
        .filter((line) => line.partId != null || line.partCode),
    [lines]
  );

  const complete =
    detail.length > 0 &&
    detail.every(
      (line) =>
        (line.partId != null || line.partCode) &&
        line.quantity != null &&
        line.quantity > 0 &&
        line.unitPrice != null
    );

  const canSubmit = orderNumber.trim().length > 0 && complete;

  const handleSubmit = () => {
    if (!canSubmit) return;
    const payload: Record<string, unknown> = {
      orderNumber: orderNumber.trim(),
      orderDetail: detail,
    };
    const supplier = num(supplierId);
    if (supplier != null) payload.supplierId = supplier;
    if (remark.trim()) payload.remark = remark.trim();
    onSubmit(JSON.stringify(payload));
  };

  return (
    <div className="shrink-0 px-4 md:px-6 pb-3 animate-rise">
      <div className="mx-auto w-full max-w-[760px] rounded-card border border-hairline bg-surface shadow-lift">
        <div className="flex items-center gap-2 h-11 px-3.5 border-b border-hairline">
          <Stamp tone="warning">待补充</Stamp>
          <span className="text-[13px] font-medium text-ink">补齐订单信息</span>
          <span className="ml-auto font-mono text-[11px] text-ink-3">
            零件 ID 可在「查询零件」结果里看到
          </span>
        </div>

        <div className="px-3.5 py-3 flex flex-col gap-3">
          <div className="grid grid-cols-1 min-[520px]:grid-cols-3 gap-2">
            <label className="flex flex-col gap-1">
              <span className="font-mono text-[11px] text-ink-3">订单编号</span>
              <input
                value={orderNumber}
                onChange={(e) => setOrderNumber(e.target.value)}
                placeholder="如 PO20261112001"
                className="h-8 px-2.5 text-[13px] bg-paper border border-hairline rounded-ctl text-ink placeholder:text-ink-3 focus:outline-none focus:border-accent/50 focus:ring-2 focus:ring-accent/15 transition-[border-color,box-shadow] duration-150 ease-quiet"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="font-mono text-[11px] text-ink-3">供应商 ID（可选）</span>
              <input
                value={supplierId}
                onChange={(e) => setSupplierId(e.target.value)}
                placeholder="如 2"
                inputMode="numeric"
                className="h-8 px-2.5 text-[13px] bg-paper border border-hairline rounded-ctl text-ink placeholder:text-ink-3 focus:outline-none focus:border-accent/50 focus:ring-2 focus:ring-accent/15 transition-[border-color,box-shadow] duration-150 ease-quiet"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="font-mono text-[11px] text-ink-3">备注（可选）</span>
              <input
                value={remark}
                onChange={(e) => setRemark(e.target.value)}
                placeholder="如 加急"
                className="h-8 px-2.5 text-[13px] bg-paper border border-hairline rounded-ctl text-ink placeholder:text-ink-3 focus:outline-none focus:border-accent/50 focus:ring-2 focus:ring-accent/15 transition-[border-color,box-shadow] duration-150 ease-quiet"
              />
            </label>
          </div>

          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <span className="font-mono text-[11px] text-ink-3">
                订单明细
              </span>
              <button
                onClick={addLine}
                className="flex items-center gap-1 h-6 px-1.5 -mr-1 rounded-[4px] text-[12px] text-ink-2 hover:bg-raised/70 transition-colors duration-150 ease-quiet"
              >
                <Plus size={13} strokeWidth={1.5} />
                添加一行
              </button>
            </div>

            {lines.map((line, index) => (
              <div
                key={index}
                className="grid grid-cols-[1fr_1fr_1fr_auto] gap-2 items-center"
              >
                <input
                  value={line.partId}
                  onChange={(e) => updateLine(index, "partId", e.target.value)}
                  placeholder="零件 ID 或 P-001"
                  aria-label={`第 ${index + 1} 行零件 ID 或编码`}
                  className="h-8 px-2.5 text-[13px] bg-paper border border-hairline rounded-ctl text-ink placeholder:text-ink-3 focus:outline-none focus:border-accent/50 focus:ring-2 focus:ring-accent/15 transition-[border-color,box-shadow] duration-150 ease-quiet tnum"
                />
                <input
                  value={line.quantity}
                  onChange={(e) => updateLine(index, "quantity", e.target.value)}
                  placeholder="数量"
                  inputMode="numeric"
                  aria-label={`第 ${index + 1} 行数量`}
                  className="h-8 px-2.5 text-[13px] bg-paper border border-hairline rounded-ctl text-ink placeholder:text-ink-3 focus:outline-none focus:border-accent/50 focus:ring-2 focus:ring-accent/15 transition-[border-color,box-shadow] duration-150 ease-quiet tnum"
                />
                <input
                  value={line.unitPrice}
                  onChange={(e) => updateLine(index, "unitPrice", e.target.value)}
                  placeholder="单价"
                  inputMode="decimal"
                  aria-label={`第 ${index + 1} 行单价`}
                  className="h-8 px-2.5 text-[13px] bg-paper border border-hairline rounded-ctl text-ink placeholder:text-ink-3 focus:outline-none focus:border-accent/50 focus:ring-2 focus:ring-accent/15 transition-[border-color,box-shadow] duration-150 ease-quiet tnum"
                />
                <button
                  onClick={() => removeLine(index)}
                  disabled={lines.length === 1}
                  aria-label={`删除第 ${index + 1} 行`}
                  className="w-7 h-7 flex items-center justify-center rounded-[4px] text-ink-3 hover:text-negative hover:bg-raised disabled:opacity-40 disabled:hover:bg-transparent transition-[color,background-color] duration-150 ease-quiet"
                >
                  <X size={14} strokeWidth={1.5} />
                </button>
              </div>
            ))}

            {!complete && (
              <p className="text-[11px] leading-[1.6] text-ink-3">
                每行填零件（数字 ID 或编码，如 P-001）、数量、单价；订单编号必填
              </p>
            )}
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 px-3.5 pb-3.5">
          <button
            onClick={handleSubmit}
            disabled={!canSubmit}
            className="flex items-center gap-1.5 h-8 px-3 bg-accent hover:bg-accent-hover active:scale-[0.98] disabled:bg-raised disabled:text-ink-3 text-white text-[13px] rounded-ctl transition-[background-color,transform,color] duration-150 ease-quiet"
          >
            <Send size={14} strokeWidth={1.5} />
            提交补充
          </button>
        </div>
      </div>
    </div>
  );
}
