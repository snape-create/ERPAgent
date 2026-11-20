"use client";

import { useEffect, useState } from "react";
import { AlertCircle, Check, ChevronRight, Pause } from "lucide-react";
import BubblingDots from "./BubblingDots";

interface Props {
  status: "streaming" | "done" | "interrupted" | "error";
  lastEventAt: number;
  startedAt?: number;
  endedAt?: number;
  stepCount: number;
  hasDrafts: boolean;
  hasAnswerText: boolean;
  onOpen: () => void;
}

/** 超过这个静默时长就提示"可能较慢"：服务端还在跑，只是暂时没有事件 */
const STALL_MS = 15_000;
/** 耗时至少 3 秒后才出现，避免刚发出去就跳秒 */
const ELAPSED_REVEAL_MS = 3_000;

/** 流式阶段只取整秒，完成后保留一位小数，读起来更像"耗时"而不是秒表 */
function formatElapsed(ms: number, precise: boolean): string {
  const total = Math.max(0, ms) / 1000;
  if (precise && total < 60) return `${total.toFixed(1)}s`;
  const whole = Math.floor(total);
  if (whole < 60) return `${whole}s`;
  return `${Math.floor(whole / 60)}m ${String(whole % 60).padStart(2, "0")}s`;
}

/**
 * 每条 assistant 消息正文上方的"正在思考"入口。
 * 只做一行安静的摘要，点开由 ProcessDrawer 展示完整执行过程。
 */
export default function ProcessHeader({
  status,
  lastEventAt,
  startedAt,
  endedAt,
  stepCount,
  hasDrafts,
  hasAnswerText,
  onOpen,
}: Props) {
  const [now, setNow] = useState(() => Date.now());

  // 只有进行中的轮次需要走秒；完成后停表，不产生多余渲染
  useEffect(() => {
    if (status !== "streaming") return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [status]);

  const start = typeof startedAt === "number" && startedAt > 0 ? startedAt : null;
  const endTs = endedAt ?? now;
  const elapsed = start !== null ? Math.max(0, endTs - start) : null;

  const stalled =
    status === "streaming" && lastEventAt > 0 && now - lastEventAt >= STALL_MS;

  // 3 秒之前不显示耗时：刚发出去就跳数字比不显示更吵
  const showElapsed =
    elapsed !== null &&
    (status !== "streaming" || elapsed >= ELAPSED_REVEAL_MS);

  const label =
    status === "interrupted"
      ? "等待你确认"
      : status === "error"
        ? "执行中断"
        : status === "streaming"
          ? stalled
            ? "服务端仍在处理，可能较慢…"
            : "正在思考"
          : "思考完成";

  // 同一时间只允许一组冒泡点：已经有正文时，冒泡点交给正文尾部
  const showBubbling = status === "streaming" && !hasAnswerText;

  return (
    <button
      type="button"
      onClick={onOpen}
      title="查看执行详情"
      aria-label="查看执行详情"
      className="flex items-center gap-2 h-7 -ml-1 px-1 rounded-[4px] text-[12px] text-ink-3 hover:bg-raised/50 hover:text-ink-2 transition-colors ease-quiet self-start"
    >
      {showBubbling ? (
        <BubblingDots className="shrink-0 text-accent" />
      ) : status === "streaming" ? (
        // 正文已经在出字，入口里只留一个静态圆点，避免两组动画抢注意力
        <span
          aria-hidden="true"
          className="shrink-0 w-1 h-1 rounded-full bg-accent"
        />
      ) : status === "interrupted" ? (
        <Pause size={12} strokeWidth={1.5} className="shrink-0 text-warning" />
      ) : status === "error" ? (
        <AlertCircle
          size={12}
          strokeWidth={1.5}
          className="shrink-0 text-negative"
        />
      ) : (
        <Check size={12} strokeWidth={1.5} className="shrink-0 text-ink-3" />
      )}

      <span className="min-w-0 truncate">{label}</span>

      {showElapsed && elapsed !== null && (
        <span className="shrink-0 min-w-[4.5ch] font-mono text-[11px] text-ink-3 tnum">
          · {formatElapsed(elapsed, status !== "streaming")}
        </span>
      )}

      {stepCount > 0 && (
        <span className="shrink-0 font-mono text-[11px] text-ink-3 tnum">
          · {stepCount} 步
        </span>
      )}

      {hasDrafts && <span className="shrink-0">· 含草稿</span>}

      <ChevronRight size={12} strokeWidth={1.5} aria-hidden="true" className="shrink-0" />
    </button>
  );
}
