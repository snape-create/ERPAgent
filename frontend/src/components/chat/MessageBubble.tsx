"use client";

import { memo, useMemo, useState } from "react";
import {
  Bot,
  User,
  ChevronDown,
  ChevronRight,
  Wrench,
} from "lucide-react";
import { ChatMessage } from "@/lib/types";
import { extractChatArtifacts } from "@/lib/artifacts";
import { useTypewriter } from "@/hooks/useTypewriter";
import ToolCallDisplay from "./ToolCallDisplay";
import Markdown from "./Markdown";
import ArtifactPanel from "./ArtifactPanel";

interface Props {
  message: ChatMessage;
  isStreaming?: boolean;
  showToolCalls?: boolean;
}

const COLLAPSE_THRESHOLD = 3;

// 这些工具正在运行时，产物往往还没落成链接；给用户一个"在生成"的反馈
const FILE_GENERATING_TOOLS = new Set([
  "generate_document",
  "generate_table_report",
  "download_sandbox_file",
  "generate_chart",
]);

function MessageBubble({
  message,
  isStreaming,
  showToolCalls = true,
}: Props) {
  const isUser = message.role === "user";
  // 评审器的结构化调用不是业务工具，历史消息里也可能有，一并过滤
  const toolCalls = (message.toolCalls ?? []).filter(
    (tc) => !/grade|rubric/i.test(tc.name)
  );
  const hasMany = toolCalls.length > COLLAPSE_THRESHOLD;
  const [groupExpanded, setGroupExpanded] = useState(!hasMany);
  // 用户消息与历史消息直接全显；仅正在流式回答的气泡逐字推进
  const typed = useTypewriter(message.content, !isUser && !!isStreaming);

  // 正文和工具结果里的下载链接都收进产物面板；纯函数可在 SSR 下运行
  const artifacts = useMemo(
    () =>
      extractChatArtifacts({
        content: message.content,
        toolCalls: message.toolCalls,
      }),
    [message.content, message.toolCalls]
  );
  const generating = toolCalls.some(
    (tc) => tc.status === "running" && FILE_GENERATING_TOOLS.has(tc.name)
  );
  const showArtifactArea = !isUser && (artifacts.length > 0 || generating);

  const sourceLabel =
    message.source === "analyst"
      ? "采购分析专家"
      : message.source === "order"
        ? "采购订单专家"
        : null;

  if (isUser) {
    return (
      <div className="flex flex-row-reverse gap-3">
        <div className="w-7 h-7 rounded-full bg-surface border border-hairline flex items-center justify-center shrink-0 mt-0.5">
          <User size={14} strokeWidth={1.5} className="text-ink-3" />
        </div>
        <div className="max-w-[80%] sm:max-w-[70%] rounded-[14px] rounded-br-[4px] bg-accent px-3.5 py-2.5 shadow-lift">
          <p className="text-[14px] leading-[1.65] text-white whitespace-pre-wrap break-words">
            {message.content}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex gap-3">
      <div className="w-7 h-7 rounded-full bg-surface border border-hairline flex items-center justify-center shrink-0 mt-0.5">
        <Bot size={14} strokeWidth={1.5} className="text-accent" />
      </div>

      {/* 正文不套卡片：回答再长也不会被包成一个长条边框 */}
      <div className="min-w-0 flex-1 pt-1">
        {/* 工具调用 - 折叠分组 */}
        {showToolCalls && toolCalls.length > 0 && (
          <div className="mb-2.5">
            <button
              onClick={() => setGroupExpanded((v) => !v)}
              aria-expanded={groupExpanded}
              className="flex items-center gap-1.5 h-6 px-1 -ml-1 rounded-[4px] text-[12px] text-ink-2 hover:bg-raised/60 transition-[color,background-color] duration-150 ease-quiet"
            >
              {groupExpanded ? (
                <ChevronDown size={13} strokeWidth={1.5} />
              ) : (
                <ChevronRight size={13} strokeWidth={1.5} />
              )}
              <Wrench size={13} strokeWidth={1.5} className="text-ink-3" />
              <span>
                工具调用 <span className="tnum">({toolCalls.length})</span>
              </span>
            </button>
            {groupExpanded && (
              <div className="mt-1.5 flex flex-col gap-1">
                {toolCalls.map((tc) => (
                  <ToolCallDisplay key={tc.id} toolCall={tc} />
                ))}
              </div>
            )}
          </div>
        )}

        {/* 消息内容 */}
        {isStreaming && !message.content ? (
          <div className="flex items-center gap-1.5 py-1" role="status" aria-label="正在思考">
            <span className="typing-dot w-1.5 h-1.5 bg-ink-3 rounded-full inline-block" />
            <span className="typing-dot w-1.5 h-1.5 bg-ink-3 rounded-full inline-block" />
            <span className="typing-dot w-1.5 h-1.5 bg-ink-3 rounded-full inline-block" />
          </div>
        ) : message.content ? (
          // 流式与完成态使用同一个渲染路径：流式期显示打字机进度，
          // 结束后 typed 立即追平全文，因此不会出现组件切换导致的跳变
          <div className="markdown-body">
            <Markdown>{typed}</Markdown>
            {isStreaming && (
              <span className="inline-block w-[2px] h-[1em] bg-accent ml-0.5 align-middle" />
            )}
          </div>
        ) : null}

        {/* 生成的产物：正文为空时也单独显示，避免只剩一个空气泡 */}
        {showArtifactArea && (
          <div className={message.content ? "mt-3" : "mt-1"}>
            <ArtifactPanel artifacts={artifacts} generating={generating} />
          </div>
        )}

        {/* 来源标识 */}
        {sourceLabel && message.source !== "main" && (
          <div className="mt-3">
            <span className="font-mono text-[11px] text-ink-3">来源 {sourceLabel}</span>
          </div>
        )}
      </div>
    </div>
  );
}

// 流式 token 只改变最后一条消息；memo 后历史气泡不再跟着重渲染
export default memo(MessageBubble);
