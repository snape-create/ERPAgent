"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, Loader2, AlertCircle, Check } from "lucide-react";
import { ToolCallInfo } from "@/lib/types";

interface Props {
  toolCall: ToolCallInfo;
}

const STATUS_ICON = {
  running: <Loader2 size={12} strokeWidth={1.5} className="text-accent animate-spin" />,
  error: <AlertCircle size={12} strokeWidth={1.5} className="text-negative" />,
  done: <Check size={12} strokeWidth={1.5} className="text-ink-3" />,
};

/** 折叠时展示的结果首行，让"调用过什么、拿到了什么"不用展开也能看到 */
function firstLine(text: string): string {
  const line =
    text
      .split("\n")
      .map((l) => l.trim())
      .find(Boolean) ?? "";
  return line.length > 90 ? `${line.slice(0, 90)}…` : line;
}

export default function ToolCallDisplay({ toolCall }: Props) {
  const [expanded, setExpanded] = useState(false);
  const preview = !expanded && toolCall.result ? firstLine(toolCall.result) : "";

  return (
    <div className="min-w-0">
      <button
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        aria-label={`工具调用 ${toolCall.name}`}
        className="w-full flex items-center gap-2 h-7 pl-1 pr-2 rounded-[6px] hover:bg-raised/50 transition-colors duration-150 ease-quiet"
      >
        {expanded ? (
          <ChevronDown size={13} strokeWidth={1.5} className="shrink-0 text-ink-3" />
        ) : (
          <ChevronRight size={13} strokeWidth={1.5} className="shrink-0 text-ink-3" />
        )}
        <span className="shrink-0 font-mono text-[11.5px] text-ink-2 truncate max-w-[42%]">
          {toolCall.name}
        </span>
        {preview && (
          <span className="min-w-0 flex-1 truncate text-[11px] text-ink-3 text-left">
            {preview}
          </span>
        )}
        <span className="ml-auto shrink-0">{STATUS_ICON[toolCall.status]}</span>
      </button>

      {expanded && (
        <div className="mt-1 px-2 py-2 flex flex-col gap-2 border-t border-hairline">
          {toolCall.args && (
            <div>
              <span className="font-mono text-[11px] text-ink-3">参数</span>
              <pre className="mt-1 p-2 rounded-[6px] bg-paper border border-hairline font-mono text-[11px] leading-[1.6] text-ink-2 overflow-x-auto whitespace-pre-wrap break-words">
                {formatJSON(toolCall.args)}
              </pre>
            </div>
          )}
          {toolCall.result && (
            <div>
              <span className="font-mono text-[11px] text-ink-3">结果</span>
              <pre className="mt-1 p-2 rounded-[6px] bg-paper border border-hairline font-mono text-[11px] leading-[1.6] text-ink-2 overflow-x-auto overflow-y-auto max-h-40 whitespace-pre-wrap break-words">
                {formatJSON(toolCall.result)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function formatJSON(str: string): string {
  try {
    return JSON.stringify(JSON.parse(str), null, 2);
  } catch {
    return str;
  }
}
