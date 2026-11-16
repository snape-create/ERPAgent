"use client";

import { Trash2 } from "lucide-react";
import { Conversation } from "@/lib/types";

interface Props {
  conversation: Conversation;
  active: boolean;
  onSelect: (id: string) => void;
  onPrefetch?: (id: string) => void;
  onDelete: (id: string) => void;
}

/** 相对时间：今天显示时刻，昨天/几天前，更早显示日期。历史列表有了时间才像台账。 */
function relativeTime(iso?: string): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";

  const diff = Date.now() - t;
  if (diff < 60_000) return "刚刚";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;

  const days = Math.floor(diff / 86_400_000);
  if (days === 0) {
    return new Date(t).toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
    });
  }
  if (days === 1) return "昨天";
  if (days < 7) return `${days} 天前`;
  return new Date(t).toLocaleDateString("zh-CN", {
    month: "numeric",
    day: "numeric",
  });
}

export default function HistoryItem({
  conversation,
  active,
  onSelect,
  onPrefetch,
  onDelete,
}: Props) {
  const time = relativeTime(conversation.updated_at || conversation.created_at);

  return (
    <div
      // 悬停 / 键盘聚焦时预取，点击瞬间大概率已命中缓存
      onMouseEnter={() => onPrefetch?.(conversation.thread_id)}
      onFocus={() => onPrefetch?.(conversation.thread_id)}
      className={`group relative h-9 rounded-ctl transition-colors duration-150 ease-quiet ${
        active ? "bg-surface shadow-lift" : "hover:bg-raised/70"
      }`}
    >
      {active && (
        <span className="absolute left-0 top-2 bottom-2 w-0.5 rounded-full bg-accent" />
      )}

      {/* 整行就是一个按钮；删除键是并列的第二个按钮，不做嵌套 */}
      <button
        onClick={() => onSelect(conversation.thread_id)}
        aria-current={active ? "true" : undefined}
        className="absolute inset-0 flex items-center pl-2 pr-14 rounded-ctl text-left"
      >
        <span
          className={`min-w-0 text-[13px] truncate ${
            active ? "text-ink" : "text-ink-2"
          }`}
        >
          {conversation.title}
        </span>
      </button>

      {/* 时间常态可见，悬停时让位给删除键 */}
      {time && (
        <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 font-mono text-[10px] text-ink-3 tnum transition-opacity duration-150 ease-quiet group-hover:opacity-0">
          {time}
        </span>
      )}

      <button
        onClick={(e) => {
          e.stopPropagation();
          // 防误触：删除前简单确认
          if (window.confirm(`确定删除会话「${conversation.title}」吗？`)) {
            onDelete(conversation.thread_id);
          }
        }}
        aria-label={`删除会话 ${conversation.title}`}
        className="absolute right-1 top-1/2 -translate-y-1/2 w-5 h-5 flex items-center justify-center rounded-[4px] text-ink-3 opacity-0 group-hover:opacity-100 focus-visible:opacity-100 hover:text-negative hover:bg-raised transition-[color,opacity,background-color] duration-150 ease-quiet"
      >
        <Trash2 size={13} strokeWidth={1.5} />
      </button>
    </div>
  );
}
