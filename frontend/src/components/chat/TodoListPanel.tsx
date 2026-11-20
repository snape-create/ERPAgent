"use client";

import { useState } from "react";
import { TodoItem } from "@/lib/types";
import {
  Check,
  ChevronDown,
  ChevronUp,
  Circle,
  Loader2,
  ListTodo,
  XCircle,
} from "lucide-react";

interface Props {
  items: TodoItem[];
  visible: boolean;
}

const STATUS = {
  pending: { icon: Circle, className: "text-ink-3/50" },
  in_progress: { icon: Loader2, className: "text-accent", spin: true },
  complete: { icon: Check, className: "text-ink-3" },
  cancelled: { icon: XCircle, className: "text-ink-3/60" },
} as const;

/**
 * 任务规划：输入框上方的一条状态栏，展开看步骤。
 * 在流式输出时用户视线就在这一带，所以不浮动、不遮挡正文。
 */
export default function TodoListPanel({ items, visible }: Props) {
  // 默认折叠：任务步骤是参考信息，不该长期占用输入框上方的空间
  const [open, setOpen] = useState(false);

  if (!visible || items.length === 0) return null;

  const completedCount = items.filter((i) => i.status === "complete").length;
  const current = items.find((i) => i.status === "in_progress");

  return (
    <div className="shrink-0 px-4 md:px-6 pb-2 animate-rise">
      <div className="mx-auto w-full rounded-ctl border border-hairline bg-surface shadow-lift overflow-hidden">
        <button
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="w-full flex items-center gap-2 h-8 px-2.5 hover:bg-raised/40 transition-colors duration-150 ease-quiet"
        >
          <ListTodo size={13} strokeWidth={1.5} className="shrink-0 text-ink-3" />
          <span className="shrink-0 text-[12px] text-ink-2">任务</span>
          <span className="shrink-0 font-mono text-[11px] text-ink-3 tnum">
            {completedCount}/{items.length}
          </span>
          {!open && current && (
            <span className="min-w-0 truncate text-[12px] text-ink-3">
              {current.content}
            </span>
          )}
          <span className="ml-auto shrink-0 text-ink-3">
            {open ? (
              <ChevronDown size={13} strokeWidth={1.5} />
            ) : (
              <ChevronUp size={13} strokeWidth={1.5} />
            )}
          </span>
        </button>

        {/* 进度：一条实色细线 */}
        <div className="h-px bg-raised">
          <div
            className="h-px bg-accent transition-[width] duration-300 ease-quiet"
            style={{ width: `${(completedCount / items.length) * 100}%` }}
          />
        </div>

        {open && (
          <div className="px-2 py-1.5 flex flex-col gap-0.5 max-h-[160px] overflow-y-auto">
            {items.map((item) => {
              const config = STATUS[item.status] ?? STATUS.pending;
              const Icon = config.icon;
              const isDone = item.status === "complete" || item.status === "cancelled";
              return (
                <div key={item.id} className="flex items-start gap-2 px-1.5 py-1">
                  <Icon
                    size={13}
                    strokeWidth={1.5}
                    className={`shrink-0 mt-0.5 ${config.className} ${
                      "spin" in config && config.spin ? "animate-spin" : ""
                    }`}
                  />
                  <span
                    className={`text-[12px] leading-[1.6] ${
                      isDone
                        ? "text-ink-3 line-through"
                        : item.status === "in_progress"
                          ? "text-ink"
                          : "text-ink-2"
                    }`}
                  >
                    {item.content}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
