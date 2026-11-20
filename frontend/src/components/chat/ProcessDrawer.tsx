"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, ChevronRight, X } from "lucide-react";
import { ToolCallInfo } from "@/lib/types";
import ToolCallDisplay from "./ToolCallDisplay";
import Markdown from "./Markdown";

interface Props {
  open: boolean;
  onClose: () => void;
  statusLabel: string;
  startedAt?: number;
  endedAt?: number;
  stepCount: number;
  phase: string;
  phaseLabel: string;
  toolCalls: ToolCallInfo[];
  draftTexts: string[];
  showToolCalls: boolean;
}

// 与后端 HarnessPhase 对齐；阶段只在这个抽屉里出现，不常驻
const PHASES = [
  { key: "thinking", label: "思考" },
  { key: "planning", label: "规划" },
  { key: "executing", label: "执行" },
  { key: "reviewing", label: "审查" },
  { key: "done", label: "完成" },
] as const;

const PHASE_ORDER: readonly string[] = PHASES.map((p) => p.key);

/** 耗时：10 秒内保留一位小数，1 分钟内取整，超过按分秒 */
function formatDuration(ms: number): string {
  const seconds = ms / 1000;
  if (seconds < 10) return `${seconds.toFixed(1)}s`;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const total = Math.round(seconds);
  return `${Math.floor(total / 60)}m ${String(total % 60).padStart(2, "0")}s`;
}

/** 折叠行只放首行摘要，避免大段 Markdown 在收起状态下也参与布局 */
function firstLine(text: string, max = 80): string {
  const line =
    text
      .split("\n")
      .map((l) => l.trim())
      .find(Boolean) ?? "";
  return line.length > max ? `${line.slice(0, max)}…` : line;
}

function SectionTitle({ children }: { children: string }) {
  return (
    <div className="font-mono text-[11px] text-ink-3 tracking-wide">{children}</div>
  );
}

/**
 * 右侧"执行详情"抽屉：状态摘要 / 阶段 / 工具时间线 / 草稿正文。
 * 全部内容都是本轮的旁路信息，最终回答仍然留在消息正文里。
 */
export default function ProcessDrawer({
  open,
  onClose,
  statusLabel,
  startedAt,
  endedAt,
  stepCount,
  phase,
  phaseLabel,
  toolCalls,
  draftTexts,
  showToolCalls,
}: Props) {
  const [expandedDraft, setExpandedDraft] = useState<number | null>(null);

  // Escape 关闭：与预览弹窗保持一致的手感
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // 关闭时收起展开项，下次打开从折叠态开始
  useEffect(() => {
    if (!open) setExpandedDraft(null);
  }, [open]);

  // 服务端渲染没有 document；未打开时也直接不渲染，避免多挂一层遮罩
  if (!open || typeof document === "undefined") return null;

  const duration =
    typeof startedAt === "number" &&
    typeof endedAt === "number" &&
    endedAt >= startedAt
      ? endedAt - startedAt
      : null;

  // 历史消息没有实时 phase（idle）：已完成的按"完成"收尾，其余停在第一步
  const phaseIdx = PHASE_ORDER.indexOf(phase);
  const currentIdx =
    phaseIdx >= 0
      ? phaseIdx
      : statusLabel === "思考完成"
        ? PHASES.length - 1
        : 0;

  return createPortal(
    <>
      <div
        className="fixed inset-0 z-40 bg-ink/25"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="执行详情"
        className="fixed inset-y-0 right-0 z-50 flex w-[min(560px,92vw)] flex-col border-l border-hairline bg-surface shadow-modal animate-rise"
      >
        <div className="flex h-12 shrink-0 items-center justify-between border-b border-hairline px-4">
          <span className="text-[13px] font-medium text-ink">执行详情</span>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭执行详情"
            className="w-6 h-6 flex items-center justify-center rounded-[4px] text-ink-3 hover:bg-raised hover:text-ink-2 transition-colors ease-quiet"
          >
            <X size={14} strokeWidth={1.5} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-3 flex flex-col gap-4">
          {/* 第一层：状态摘要 */}
          <section className="flex flex-col gap-1.5">
            <SectionTitle>状态</SectionTitle>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-ink-2">
              <span>{statusLabel}</span>
              {duration !== null && (
                <span className="font-mono tnum">用时 {formatDuration(duration)}</span>
              )}
              <span className="font-mono tnum">{stepCount} 个步骤</span>
            </div>
          </section>

          {/* 第二层：五个阶段的小时间线 */}
          <section className="flex flex-col gap-2">
            <SectionTitle>阶段</SectionTitle>
            <div className="flex items-center gap-1.5">
              {PHASES.map((p, idx) => {
                const isCurrent = idx === currentIdx;
                const isPast = idx < currentIdx;
                return (
                  <div key={p.key} className="flex items-center gap-1.5">
                    {idx > 0 && (
                      <span
                        aria-hidden="true"
                        className={`w-3 h-px ${isPast || isCurrent ? "bg-accent/40" : "bg-hairline-strong"}`}
                      />
                    )}
                    <span
                      aria-hidden="true"
                      className={`w-1.5 h-1.5 rounded-full ${
                        isCurrent
                          ? "bg-accent"
                          : isPast
                            ? "bg-accent/40"
                            : "bg-hairline-strong"
                      }`}
                    />
                    <span
                      className={`text-[12px] ${
                        isCurrent ? "text-ink" : isPast ? "text-ink-3" : "text-ink-3/60"
                      }`}
                    >
                      {p.label}
                    </span>
                  </div>
                );
              })}
              {phaseLabel && (
                <span className="ml-auto min-w-0 truncate text-[11px] text-ink-3">
                  {phaseLabel}
                </span>
              )}
            </div>
          </section>

          {/* 第三层：工具时间线 */}
          <section className="flex flex-col gap-1.5">
            <SectionTitle>工具调用</SectionTitle>
            {!showToolCalls ? (
              <div className="text-[12px] text-ink-3">工具明细已隐藏</div>
            ) : toolCalls.length === 0 ? (
              <div className="text-[12px] text-ink-3">本轮没有工具调用</div>
            ) : (
              <div className="flex flex-col gap-0.5">
                {toolCalls.map((tc, idx) => (
                  // 旧历史数据可能没有 tool id，补 index 避免 key 重复
                  <ToolCallDisplay key={`${tc.id || "tool"}-${idx}`} toolCall={tc} />
                ))}
              </div>
            )}
          </section>

          {/* 草稿 / 过程正文：折叠时不渲染 Markdown，避免大段内容白白解析 */}
          <section className="flex flex-col gap-1.5">
            <SectionTitle>过程正文 / 草稿</SectionTitle>
            {draftTexts.length === 0 ? (
              <div className="text-[12px] text-ink-3">本轮没有保留草稿</div>
            ) : (
              <div className="flex flex-col gap-1">
                {draftTexts.map((text, idx) => {
                  const expanded = expandedDraft === idx;
                  const preview = firstLine(text);
                  return (
                    <div key={idx} className="min-w-0 border-b border-hairline pb-1 last:border-b-0">
                      <button
                        type="button"
                        onClick={() => setExpandedDraft(expanded ? null : idx)}
                        aria-expanded={expanded}
                        className="w-full flex items-center gap-2 h-7 pl-1 pr-2 rounded-[6px] text-left hover:bg-raised/50 transition-colors ease-quiet"
                      >
                        {expanded ? (
                          <ChevronDown size={13} strokeWidth={1.5} className="shrink-0 text-ink-3" />
                        ) : (
                          <ChevronRight size={13} strokeWidth={1.5} className="shrink-0 text-ink-3" />
                        )}
                        <span className="min-w-0 flex-1 truncate text-[12px] text-ink-2">
                          草稿 {idx + 1}
                          {preview && ` · ${preview}`}
                        </span>
                        <span className="shrink-0 font-mono text-[10.5px] text-ink-3 tnum">
                          {text.length} 字
                        </span>
                      </button>
                      {expanded && (
                        <div className="markdown-body mt-1 max-h-[60vh] overflow-y-auto rounded-[6px] border border-hairline bg-paper px-3 py-2">
                          <Markdown>{text}</Markdown>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </section>
        </div>
      </div>
    </>,
    document.body
  );
}
