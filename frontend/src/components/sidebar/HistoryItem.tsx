"use client";

import { Trash2 } from "lucide-react";
import { Conversation } from "@/lib/types";

interface Props {
  conversation: Conversation;
  active: boolean;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}

export default function HistoryItem({
  conversation,
  active,
  onSelect,
  onDelete,
}: Props) {
  return (
    <div
      className={`group relative h-9 rounded-ctl transition-colors duration-150 ease-quiet ${
        active
          ? "bg-surface shadow-lift"
          : "hover:bg-raised/70"
      }`}
    >
      {active && (
        <span className="absolute left-0 top-2 bottom-2 w-0.5 rounded-full bg-accent" />
      )}

      {/* 整行就是一个按钮；删除键是并列的第二个按钮，不做嵌套 */}
      <button
        onClick={() => onSelect(conversation.thread_id)}
        aria-current={active ? "true" : undefined}
        className="absolute inset-0 flex items-center pl-2 pr-8 rounded-ctl text-left"
      >
        <span
          className={`min-w-0 text-[13px] truncate ${
            active ? "text-ink" : "text-ink-2"
          }`}
        >
          {conversation.title}
        </span>
      </button>

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
