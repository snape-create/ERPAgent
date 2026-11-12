"use client";

import { useState } from "react";
import { ArrowUp } from "lucide-react";
import { InterruptData } from "@/lib/types";
import Stamp from "@/components/common/Stamp";

interface Props {
  data: InterruptData;
  onSubmit: (supplement: string) => void;
}

export default function SupplementForm({ data, onSubmit }: Props) {
  const [text, setText] = useState("");

  const handleSubmit = () => {
    if (!text.trim()) return;
    onSubmit(text.trim());
    setText("");
  };

  return (
    <div className="shrink-0 px-4 md:px-6 pb-3 animate-rise">
      <div className="mx-auto w-full rounded-card border border-hairline bg-surface shadow-lift">
        <div className="flex items-center gap-2 h-11 px-3.5 border-b border-hairline">
          <Stamp tone="warning">待补充</Stamp>
          <span className="text-[13px] font-medium text-ink">订单信息不完整</span>
        </div>

        <div className="px-3.5 py-3">
          {data.message && (
            <p className="text-[13px] leading-[1.6] text-ink-2">{data.message}</p>
          )}

          {data.missing_fields && data.missing_fields.length > 0 && (
            <div className="mt-3 flex flex-wrap items-center gap-1.5">
              <span className="font-mono text-[11px] text-ink-3">缺少</span>
              {data.missing_fields.map((f) => (
                <span
                  key={f}
                  className="px-1.5 py-0.5 rounded-[4px] border border-hairline font-mono text-[11px] text-ink-2"
                >
                  {f}
                </span>
              ))}
            </div>
          )}

          {data.extracted_data && Object.keys(data.extracted_data).length > 0 && (
            <div className="mt-3">
              <span className="font-mono text-[11px] text-ink-3">已提取</span>
              <pre className="mt-1 p-2 rounded-[6px] bg-paper border border-hairline font-mono text-[11px] leading-[1.6] text-ink-2 whitespace-pre-wrap break-words">
                {JSON.stringify(data.extracted_data, null, 2)}
              </pre>
            </div>
          )}
        </div>

        <div className="flex items-center gap-2 px-3.5 pb-3.5">
          <input
            type="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) =>
              e.key === "Enter" && !e.nativeEvent.isComposing && handleSubmit()
            }
            placeholder="补充信息，如：零件 P-1042，数量 100，单价 25.5"
            className="flex-1 h-8 px-2.5 text-[13px] bg-paper border border-hairline rounded-ctl text-ink placeholder:text-ink-3 focus:outline-none focus:border-accent/50 focus:ring-2 focus:ring-accent/15 transition-[border-color,box-shadow] duration-150 ease-quiet"
          />
          <button
            onClick={handleSubmit}
            disabled={!text.trim()}
            aria-label="提交补充信息"
            className="shrink-0 w-8 h-8 flex items-center justify-center bg-accent hover:bg-accent-hover active:scale-[0.97] disabled:bg-raised disabled:text-ink-3 text-white rounded-ctl transition-[background-color,transform] duration-150 ease-quiet"
          >
            <ArrowUp size={16} strokeWidth={1.5} />
          </button>
        </div>
      </div>
    </div>
  );
}
