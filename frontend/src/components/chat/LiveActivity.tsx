"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { ChatMessage, ToolCallInfo } from "@/lib/types";

interface Props {
  messages: ChatMessage[];
  streaming: boolean;
}

// 与 MessageBubble 保持一致：评审器的结构化调用不是业务工具
const INTERNAL_TOOL_RE = /grade|rubric/i;

const FILE_TOOLS = new Set([
  "generate_document",
  "generate_table_report",
  "generate_chart",
  "download_sandbox_file",
]);

function isBusinessTool(tc: ToolCallInfo) {
  return !INTERNAL_TOOL_RE.test(tc.name);
}

/** 当前是否有正在执行业务工具；供外部判断是否让出"思考中"状态 */
export function hasRunningToolCall(messages: ChatMessage[]) {
  return messages.some((msg) =>
    (msg.toolCalls ?? []).some(
      (tc) => tc.status === "running" && isBusinessTool(tc)
    )
  );
}

function activityLabel(name: string) {
  if (FILE_TOOLS.has(name)) return "正在生成文件";
  if (name === "execute") return "正在执行沙盒脚本";
  return `正在运行 ${name || "工具"}`;
}

function formatElapsed(ms: number) {
  const total = Math.max(0, Math.floor(ms / 1000));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
}

/**
 * 安静的实时运行状态：只在流式运行且有工具正在执行时出现。
 * 每秒刷新的是本组件自己的计时状态，不触发消息列表重渲染。
 */
export default function LiveActivity({ messages, streaming }: Props) {
  const running = useMemo(() => {
    if (!streaming) return [];
    const items: ToolCallInfo[] = [];
    for (const msg of messages) {
      for (const tc of msg.toolCalls ?? []) {
        if (tc.status === "running" && isBusinessTool(tc)) items.push(tc);
      }
    }
    return items;
  }, [messages, streaming]);

  const active = running.length > 0;

  const [now, setNow] = useState(() => Date.now());
  // 兼容没有 startedAt 的历史/旧事件：以组件首次看到它的时间为起点
  const firstSeenRef = useRef<Map<string, number>>(new Map());

  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);

  useEffect(() => {
    const seen = firstSeenRef.current;
    const ids = new Set<string>();
    for (const tc of running) {
      ids.add(tc.id);
      if (!seen.has(tc.id)) seen.set(tc.id, Date.now());
    }
    // 已结束的工具不再占用缓存
    for (const id of seen.keys()) {
      if (!ids.has(id)) seen.delete(id);
    }
  }, [running]);

  if (!active) return null;

  const primary = running[running.length - 1];
  const startedAt =
    typeof primary.startedAt === "number" && Number.isFinite(primary.startedAt)
      ? primary.startedAt
      : (firstSeenRef.current.get(primary.id) ?? now);
  const extra = running.length - 1;

  return (
    <div
      role="status"
      aria-live="off"
      className="shrink-0 h-8 flex items-center px-4 md:px-6 border-b border-hairline"
    >
      <div className="mx-auto w-full max-w-[760px] flex items-center gap-2 min-w-0">
        <Loader2
          size={12}
          strokeWidth={1.5}
          aria-hidden="true"
          className="shrink-0 text-ink-3 animate-spin"
        />
        <span className="text-[12px] text-ink-2 truncate">
          {activityLabel(primary.name)}
        </span>
        {extra > 0 && (
          <span className="shrink-0 font-mono text-[11px] text-ink-3">
            · 另有 {extra} 个工具运行
          </span>
        )}
        <span className="ml-auto pl-3 shrink-0 font-mono text-[11px] text-ink-3 tnum">
          已用时 {formatElapsed(now - startedAt)}
        </span>
      </div>
    </div>
  );
}
