"use client";

import { Check, X } from "lucide-react";
import { InterruptData } from "@/lib/types";
import Stamp from "@/components/common/Stamp";

interface Props {
  data: InterruptData;
  onApprove: () => void;
  onReject: () => void;
}

export default function ApprovalCard({ data, onApprove, onReject }: Props) {
  const orderData = data.order_data || data.tool_args || {};

  return (
    <div className="shrink-0 px-4 md:px-6 pb-3 animate-rise">
      <div className="mx-auto w-full rounded-card border border-hairline bg-surface shadow-lift">
        <div className="flex items-center gap-2 h-11 px-3.5 border-b border-hairline">
          <Stamp>待审批</Stamp>
          <span className="text-[13px] font-medium text-ink">订单审批</span>
          {data.tool_name && (
            <span className="ml-auto font-mono text-[11px] text-ink-3 truncate">
              {data.tool_name}
            </span>
          )}
        </div>

        <div className="px-3.5 py-3">
          <p className="text-[13px] leading-[1.6] text-ink-2">
            以下写入会等你确认后执行，拒绝则不产生任何单据。
          </p>

          {Object.keys(orderData).length > 0 && (
            <table className="w-full mt-3 text-[13px] tnum">
              <tbody>
                {Object.entries(orderData).map(([key, value]) => (
                  <tr key={key} className="border-b border-hairline last:border-0">
                    <td className="py-1.5 pr-4 align-top font-mono text-[11px] text-ink-3 whitespace-nowrap">
                      {key}
                    </td>
                    <td className="py-1.5 text-ink break-words">
                      {value === null || value === undefined
                        ? "—"
                        : typeof value === "object"
                          ? JSON.stringify(value)
                          : String(value)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="flex items-center gap-2 px-3.5 pb-3.5">
          <button
            onClick={onApprove}
            className="flex items-center gap-1.5 h-8 px-3.5 bg-accent hover:bg-accent-hover active:scale-[0.98] text-white text-[13px] font-medium rounded-ctl transition-[background-color,transform] duration-150 ease-quiet"
          >
            <Check size={14} strokeWidth={1.5} />
            批准执行
          </button>
          <button
            onClick={onReject}
            className="flex items-center gap-1.5 h-8 px-3.5 bg-surface border border-hairline hover:bg-raised/60 text-ink-2 text-[13px] rounded-ctl transition-colors duration-150 ease-quiet"
          >
            <X size={14} strokeWidth={1.5} />
            拒绝
          </button>
        </div>
      </div>
    </div>
  );
}
