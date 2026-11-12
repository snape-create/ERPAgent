"use client";

import { useState, useRef, useCallback } from "react";
import { ArrowUp } from "lucide-react";
import ToolCallToggle from "@/components/common/ToolCallToggle";

interface Props {
  onSend: (message: string) => void;
  disabled?: boolean;
  queued?: number;
  showToolCalls?: boolean;
  onToggleToolCalls?: (v: boolean) => void;
}

export default function InputBar({
  onSend,
  disabled,
  queued = 0,
  showToolCalls = true,
  onToggleToolCalls,
}: Props) {
  const [input, setInput] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSend = useCallback(() => {
    // 注意：streaming 时不能拦截——此时发送的消息要进入排队队列
    if (!input.trim()) return;
    onSend(input);
    setInput("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [input, onSend]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // isComposing：中文输入法组词时不提交，避免 Enter 选词误发送
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    // 自动调整高度
    const el = e.target;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 160) + "px";
  };

  return (
    <div className="shrink-0 px-4 md:px-6 pt-2 pb-3">
      <div className="mx-auto w-full">
        <div className="flex items-end gap-2 bg-surface border border-hairline rounded-panel px-3 py-2 shadow-lift focus-within:border-accent/60 focus-within:ring-4 focus-within:ring-accent/10 transition-[border-color,box-shadow] duration-150 ease-quiet">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            placeholder={disabled ? "回答中，发送后自动排队" : "输入消息"}
            rows={1}
            className="flex-1 resize-none bg-transparent outline-none text-[14px] leading-[1.6] text-ink placeholder:text-ink-3 max-h-[160px] py-1.5"
          />
          {queued > 0 && (
            <span className="shrink-0 font-mono text-[11px] text-ink-3 tnum pb-2 whitespace-nowrap">
              {queued} 条排队
            </span>
          )}
          <button
            onClick={handleSend}
            disabled={!input.trim()}
            aria-label="发送"
            className="shrink-0 w-9 h-9 flex items-center justify-center bg-accent hover:bg-accent-hover active:scale-[0.97] disabled:bg-raised disabled:text-ink-3 text-white rounded-full transition-[background-color,transform] duration-150 ease-quiet"
          >
            <ArrowUp size={18} strokeWidth={1.5} />
          </button>
        </div>

        <div className="mt-2 flex items-center justify-between gap-3 px-1">
          {onToggleToolCalls ? (
            <ToolCallToggle show={showToolCalls} onToggle={onToggleToolCalls} />
          ) : (
            <span />
          )}
          <p className="flex items-center gap-1 text-[11px] text-ink-3">
            <kbd className="keycap">Enter</kbd>
            <span>发送</span>
            <kbd className="keycap ml-1.5">Shift</kbd>
            <span>+</span>
            <kbd className="keycap">Enter</kbd>
            <span>换行</span>
          </p>
        </div>
      </div>
    </div>
  );
}
