"use client";

import { Check, X } from "lucide-react";
import { InterruptData } from "@/lib/types";
import Stamp from "@/components/common/Stamp";
import SupplementForm from "./SupplementForm";
import ApprovalCard from "./ApprovalCard";

/**
 * 兜底中断卡片。
 *
 * 后端可能返回未知的 interrupt_type（或字段名不一致导致类型为空）。
 * 早期实现直接 return null，结果是：后端在等用户操作，前端却一片空白，
 * 用户只会看到"发消息没反应"。任何中断都必须给出可见反馈。
 */
function UnknownInterrupt({
  data,
  onApprove,
  onReject,
}: {
  data: InterruptData;
  onApprove: () => void;
  onReject: () => void;
}) {
  const type = data.interrupt_type || "未知类型";
  const detail = Object.keys(data).length > 0 ? JSON.stringify(data, null, 2) : "";

  return (
    <div className="shrink-0 px-4 md:px-6 pb-3 animate-rise">
      <div className="mx-auto w-full rounded-card border border-hairline bg-surface shadow-lift">
        <div className="flex items-center gap-2 h-11 px-3.5 border-b border-hairline">
          <Stamp tone="warning">待确认</Stamp>
          <span className="text-[13px] font-medium text-ink">任务已暂停</span>
        </div>

        <div className="px-3.5 py-3">
          <p className="text-[13px] leading-[1.6] text-ink-2">
            后端返回了未识别的中断类型
            <span className="font-mono text-ink ml-1.5">{type}</span>
          </p>
          {detail && (
            <pre className="mt-3 max-h-40 overflow-auto rounded-[6px] bg-paper border border-hairline p-2 font-mono text-[11px] leading-[1.6] text-ink-2 whitespace-pre-wrap break-words">
              {detail}
            </pre>
          )}
        </div>

        <div className="flex items-center gap-2 px-3.5 pb-3.5">
          <button
            onClick={onApprove}
            className="flex items-center gap-1.5 h-8 px-3.5 bg-accent hover:bg-accent-hover active:scale-[0.98] text-white text-[13px] font-medium rounded-ctl transition-[background-color,transform] duration-150 ease-quiet"
          >
            <Check size={14} strokeWidth={1.5} />
            继续执行
          </button>
          <button
            onClick={onReject}
            className="flex items-center gap-1.5 h-8 px-3.5 bg-surface border border-hairline hover:bg-raised/60 text-ink-2 text-[13px] rounded-ctl transition-colors duration-150 ease-quiet"
          >
            <X size={14} strokeWidth={1.5} />
            取消
          </button>
        </div>
      </div>
    </div>
  );
}

interface Props {
  data: InterruptData;
  onSupplement: (text: string) => void;
  onApprove: () => void;
  onReject: () => void;
  onEdit: (args: Record<string, unknown>, toolName: string) => void;
}

export default function InterruptBanner({
  data,
  onSupplement,
  onApprove,
  onReject,
  onEdit,
}: Props) {
  if (data.interrupt_type === "order_info_supplement") {
    return <SupplementForm data={data} onSubmit={onSupplement} />;
  }

  if (data.interrupt_type === "hitl_approval") {
    return (
      <ApprovalCard
        data={data}
        onApprove={onApprove}
        onReject={onReject}
        onEdit={(args) => onEdit(args, data.tool_name ?? "")}
      />
    );
  }

  // 未知/缺失类型也要渲染，避免"静默空白"
  return (
    <UnknownInterrupt data={data} onApprove={onApprove} onReject={onReject} />
  );
}
