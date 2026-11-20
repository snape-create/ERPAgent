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

/** 后端已把结果截断到 2000 字；达到上限就提示用户内容可能不完整 */
const RESULT_TRUNCATE_HINT_AT = 2000;

/** 折叠时展示的结果首行，让"调用过什么、拿到了什么"不用展开也能看到 */
function firstLine(text: string): string {
  const line =
    text
      .split("\n")
      .map((l) => l.trim())
      .find(Boolean) ?? "";
  return line.length > 90 ? `${line.slice(0, 90)}…` : line;
}

/** 单个工具耗时：保留 1 位小数，0.4s 这种量级比整秒更有信息量 */
function formatDuration(ms: number): string {
  const seconds = ms / 1000;
  if (seconds < 0.1) return "<0.1s";
  return `${seconds.toFixed(1)}s`;
}

export default function ToolCallDisplay({ toolCall }: Props) {
  const [expanded, setExpanded] = useState(false);
  // 摘要优先：summary 是后端给的单行结论，比结果首行更贴近"发生了什么"
  const preview = !expanded
    ? toolCall.summary || (toolCall.result ? firstLine(toolCall.result) : "")
    : "";

  const duration =
    typeof toolCall.startedAt === "number" &&
    typeof toolCall.endedAt === "number" &&
    toolCall.endedAt >= toolCall.startedAt
      ? formatDuration(toolCall.endedAt - toolCall.startedAt)
      : null;

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
        {duration && (
          <span className="shrink-0 font-mono text-[10.5px] text-ink-3 tnum">
            {duration}
          </span>
        )}
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
              <pre className="mt-1 p-2 rounded-[6px] bg-paper border border-hairline font-mono text-[11px] leading-[1.6] text-ink-2 overflow-x-auto overflow-y-auto max-h-56 whitespace-pre-wrap break-words">
                {formatJSON(toolCall.args)}
              </pre>
            </div>
          )}
          {toolCall.result && (
            <div>
              <span className="font-mono text-[11px] text-ink-3">结果</span>
              <pre className="mt-1 p-2 rounded-[6px] bg-paper border border-hairline font-mono text-[11px] leading-[1.6] text-ink-2 overflow-x-auto overflow-y-auto max-h-56 whitespace-pre-wrap break-words">
                {formatJSON(toolCall.result)}
              </pre>
              {toolCall.result.length >= RESULT_TRUNCATE_HINT_AT && (
                <div className="mt-1 font-mono text-[11px] text-ink-3">
                  结果较长，仅显示前 {RESULT_TRUNCATE_HINT_AT} 字
                </div>
              )}
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
