"use client";

import { Conversation } from "@/lib/types";
import HistoryItem from "./HistoryItem";

interface Props {
  conversations: Conversation[];
  activeThreadId: string;
  onSelect: (id: string) => void;
  onPrefetch: (id: string) => void;
  onDelete: (id: string) => void;
}

export default function HistoryList({
  conversations,
  activeThreadId,
  onSelect,
  onPrefetch,
  onDelete,
}: Props) {
  if (conversations.length === 0) {
    return (
      <div className="flex-1 flex items-start justify-center px-4 pt-6">
        <p className="text-[12px] text-ink-3">暂无会话记录</p>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto px-3 pb-2">
      <div className="flex flex-col gap-1">
        {conversations.map((conv) => (
          <HistoryItem
            key={conv.thread_id}
            conversation={conv}
            active={conv.thread_id === activeThreadId}
            onSelect={onSelect}
            onPrefetch={onPrefetch}
            onDelete={onDelete}
          />
        ))}
      </div>
    </div>
  );
}
