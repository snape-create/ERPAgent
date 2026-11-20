"use client";

import { memo, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { Bot, Check, Copy, User } from "lucide-react";
import { ChatMessage, MessagePart, ToolCallInfo } from "@/lib/types";
import { extractChatArtifacts } from "@/lib/artifacts";
import StreamingMarkdown from "./StreamingMarkdown";
import ArtifactPanel from "./ArtifactPanel";
import BubblingDots from "./BubblingDots";
import ProcessHeader from "./ProcessHeader";
import ProcessDrawer from "./ProcessDrawer";

interface Props {
  message: ChatMessage;
  isStreaming?: boolean;
  /** 当前轮停在审批中断：还不算完成，不显示"用时"页脚 */
  isInterrupted?: boolean;
  showToolCalls?: boolean;
  /** 仅最后一条 assistant 消息携带：本轮的实时状态 */
  live?: {
    activityLabel: string;
    lastEventAt: number;
    phase: string;
    phaseLabel: string;
  };
}

// 评审器的结构化调用不是业务工具，历史消息里也可能有，一并过滤
const INTERNAL_TOOL_RE = /grade|rubric/i;

// 这些工具正在运行时，产物往往还没落成链接；给用户一个"在生成"的反馈
const FILE_GENERATING_TOOLS = new Set([
  "generate_document",
  "generate_table_report",
  "download_sandbox_file",
  "generate_chart",
]);

/** 整轮用时：10 秒内保留一位小数，1 分钟内取整，超过则按分秒 */
function formatDuration(ms: number): string {
  const seconds = ms / 1000;
  if (seconds < 10) return `${seconds.toFixed(1)}s`;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const total = Math.round(seconds);
  return `${Math.floor(total / 60)}m ${String(total % 60).padStart(2, "0")}s`;
}

type ProcessStatus = "streaming" | "done" | "interrupted" | "error";

const STATUS_LABEL: Record<ProcessStatus, string> = {
  streaming: "正在思考",
  done: "思考完成",
  interrupted: "等待你确认",
  error: "执行中断",
};

function MessageBubble({
  message,
  isStreaming,
  isInterrupted,
  showToolCalls = true,
  live,
}: Props) {
  const isUser = message.role === "user";
  // 流式期间每帧都对全文跑正则提取产物代价很高；延后到浏览器空闲时再算。
  // 只影响产物面板的更新时机，正文仍然使用实时 content。
  const deferredContent = useDeferredValue(message.content);
  const [copied, setCopied] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    };
  }, []);

  // 有 parts 就按真实顺序还原；旧消息回退成"工具在上、正文在下"
  const parts = useMemo<MessagePart[]>(() => {
    if (message.parts && message.parts.length > 0) {
      return message.parts.filter(
        (part) => part.type === "text" || !INTERNAL_TOOL_RE.test(part.call.name)
      );
    }

    const toolParts: MessagePart[] = (message.toolCalls ?? [])
      .filter((tc) => !INTERNAL_TOOL_RE.test(tc.name))
      .map((tc): MessagePart => ({ type: "tool", call: tc }));

    if (!message.content) return toolParts;
    const textPart: MessagePart = { type: "text", text: message.content };
    return [...toolParts, textPart];
  }, [message.parts, message.toolCalls, message.content]);

  // 终稿分界：旧消息没有该字段时按 0 处理，全部正文都算答案
  const finalStart = message.finalStartIndex ?? 0;
  // 第一次修订发生时的草稿结束位置：修订期间用它锁定"第一版草稿"
  const firstRevision = message.firstRevisionIndex ?? 0;

  const toolCalls = useMemo(
    () =>
      parts
        .filter((p): p is { type: "tool"; call: ToolCallInfo } => p.type === "tool")
        .map((p) => p.call)
        .filter((tc) => !INTERNAL_TOOL_RE.test(tc.name)),
    [parts]
  );
  const draftTexts = useMemo(
    () =>
      parts
        .slice(0, finalStart)
        .filter((p) => p.type === "text")
        .map((p) => p.text)
        .filter((t) => t.trim()),
    [parts, finalStart]
  );
  const answerTexts = useMemo(
    () =>
      parts
        .slice(finalStart)
        .filter((p) => p.type === "text")
        .map((p) => p.text)
        .filter((t) => t.trim()),
    [parts, finalStart]
  );
  // 第一版草稿：第一次修订前的全部正文，修订期间答案区继续展示它
  const stableDraftTexts = useMemo(
    () =>
      firstRevision > 0
        ? parts
            .slice(0, firstRevision)
            .filter((p) => p.type === "text")
            .map((p) => p.text)
            .filter((t) => t.trim())
        : [],
    [parts, firstRevision]
  );

  const hasAnswerText = answerTexts.length > 0;
  const hasDrafts = draftTexts.length > 0;

  // 修订进行中（流式且已经跨过终稿分界）：答案区先钉住第一版草稿，
  // 不跟随 finalStart 清空，等终稿完成后再一次性替换。
  const inRevision = !!isStreaming && finalStart > 0;
  const showStableDraft = inRevision && stableDraftTexts.length > 0;
  const visibleAnswerTexts = showStableDraft ? stableDraftTexts : answerTexts;
  // 已展示稳定草稿时冒泡点交给草稿尾部的"审查中"提示，避免两组动画
  const showAnswerTailDots = !!isStreaming && !showStableDraft;
  // 抽屉保留所有草稿；修订进行中把当前修订稿也并进去
  const drawerDraftTexts = showStableDraft
    ? [...draftTexts, ...answerTexts]
    : draftTexts;

  const hasProcess =
    !!isStreaming || !!isInterrupted || toolCalls.length > 0 || hasDrafts;

  const status: ProcessStatus = isInterrupted
    ? "interrupted"
    : live?.activityLabel === "执行中断"
      ? "error"
      : isStreaming
        ? "streaming"
        : "done";

  // 终稿为空但留了草稿：答案区用最后一版草稿兜底，并明确说明它不是终稿
  const showDraftFallback = !isStreaming && !hasAnswerText && hasDrafts;
  const fallbackDraft = showDraftFallback ? draftTexts[draftTexts.length - 1] : "";

  // 复制只复制答案区实际展示的内容，不把草稿/过程文本一起带走
  const visibleCopyText = visibleAnswerTexts.join("\n\n");
  const copyText = visibleCopyText || (!isStreaming ? fallbackDraft : "");

  const handleCopy = useCallback(async () => {
    if (!copyText) return;
    try {
      // navigator.clipboard 在非安全上下文可能整体不存在，属性访问本身就会抛错
      if (!navigator.clipboard) return;
      await navigator.clipboard.writeText(copyText);
      setCopied(true);
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
      copyTimerRef.current = setTimeout(() => setCopied(false), 1500);
    } catch {
      // 剪贴板不可用（权限/非安全上下文等）时静默失败，不打断阅读
    }
  }, [copyText]);

  // 正文和工具结果里的下载链接都收进产物面板；纯函数可在 SSR 下运行
  const artifacts = useMemo(
    () =>
      extractChatArtifacts({
        content: deferredContent,
        toolCalls: message.toolCalls,
      }),
    [deferredContent, message.toolCalls]
  );

  const generating = (message.toolCalls ?? []).some(
    (tc) => tc.status === "running" && FILE_GENERATING_TOOLS.has(tc.name)
  );
  const showArtifactArea = !isUser && (artifacts.length > 0 || generating);

  // 整轮用时只在起止时间都落定后展示；旧历史消息没有时间戳就跳过
  const duration =
    message.startedAt &&
    message.endedAt &&
    message.endedAt >= message.startedAt
      ? message.endedAt - message.startedAt
      : null;
  // 底部信息条：正文流完之后才有意义，流式/等待确认期间保持安静
  const showFooter =
    !isUser &&
    !isStreaming &&
    !isInterrupted &&
    (duration !== null || toolCalls.length > 0 || !!copyText);

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
      <div className="min-w-0 flex-1 pt-1 flex flex-col gap-2">
        {/* 执行过程入口：正文上方一行，点开右侧抽屉 */}
        {hasProcess && (
          <ProcessHeader
            status={status}
            lastEventAt={live?.lastEventAt ?? 0}
            startedAt={message.startedAt}
            endedAt={message.endedAt}
            stepCount={toolCalls.length}
            hasDrafts={hasDrafts}
            hasAnswerText={showStableDraft || hasAnswerText}
            onOpen={() => setDrawerOpen(true)}
          />
        )}

        {/* 答案区：只放最终稿；修订期间改放第一版草稿，终稿完成后一次性替换 */}
        {visibleAnswerTexts.map((text, idx) => {
          const isLast = idx === visibleAnswerTexts.length - 1;
          return (
            <div key={`answer-${idx}`} className="markdown-body">
              <StreamingMarkdown text={text} />
              {isLast && showAnswerTailDots && (
                <BubblingDots className="text-accent ml-1 align-middle" />
              )}
            </div>
          );
        })}

        {/* 修订进行中：草稿末尾提示静候终稿，避免用户以为答案已经定稿 */}
        {showStableDraft && (
          <div className="flex items-center gap-1.5 text-[12px] text-ink-3">
            <BubblingDots className="text-accent" />
            <span>审查中，完成后自动更新</span>
          </div>
        )}

        {/* 审查后没有终稿：用最后一版草稿兜底，并说清它不是终稿 */}
        {showDraftFallback && (
          <>
            <div className="text-[12px] text-ink-3">
              审查后未生成终稿，以下为最后一版草稿
            </div>
            <div className="markdown-body">
              <StreamingMarkdown text={fallbackDraft} />
            </div>
          </>
        )}

        {/* 生成的产物：正文为空时也单独显示，避免只剩一个空气泡 */}
        {showArtifactArea && (
          <div className={hasAnswerText || showDraftFallback ? "mt-1" : ""}>
            <ArtifactPanel artifacts={artifacts} generating={generating} />
          </div>
        )}

        {/* 来源标识 */}
        {sourceLabel && message.source !== "main" && (
          <span className="font-mono text-[11px] text-ink-3">来源 {sourceLabel}</span>
        )}

        {/* 收尾信息条：用时、步骤数，以及执行详情 / 复制入口 */}
        {showFooter && (
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-3">
            {duration !== null && (
              <span className="font-mono tnum">用时 {formatDuration(duration)}</span>
            )}
            {toolCalls.length > 0 && (
              <span className="font-mono tnum">{toolCalls.length} 个步骤</span>
            )}
            <div className="ml-auto flex items-center gap-0.5">
              {hasProcess && (
                <button
                  onClick={() => setDrawerOpen(true)}
                  className="h-6 px-1.5 rounded-[4px] hover:bg-raised/60 transition-colors"
                >
                  查看执行详情
                </button>
              )}
              {copyText && (
                <button
                  onClick={handleCopy}
                  className="h-6 px-1.5 rounded-[4px] hover:bg-raised/60 transition-colors inline-flex items-center gap-1"
                >
                  {copied ? <Check size={11} /> : <Copy size={11} />}
                  {copied ? "已复制" : "复制"}
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      {/* 执行详情抽屉：portal 到 body，避免被消息列的布局与滚动裁切 */}
      <ProcessDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        statusLabel={STATUS_LABEL[status]}
        startedAt={message.startedAt}
        endedAt={message.endedAt}
        stepCount={toolCalls.length}
        phase={live?.phase ?? "idle"}
        phaseLabel={live?.phaseLabel ?? ""}
        toolCalls={toolCalls}
        draftTexts={drawerDraftTexts}
        showToolCalls={showToolCalls}
      />
    </div>
  );
}

// 流式 token 只改变最后一条消息；memo 后历史气泡不再跟着重渲染
export default memo(MessageBubble);
