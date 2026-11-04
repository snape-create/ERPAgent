"use client";

import { useEffect, useRef, useCallback } from "react";
import { ChatMessage } from "@/lib/types";
import MessageBubble from "./MessageBubble";

interface Props {
  messages: ChatMessage[];
  streaming: boolean;
  showToolCalls: boolean;
}

/** 距底部多少像素内算"贴着底部" */
const BOTTOM_THRESHOLD = 80;

export default function MessageList({ messages, streaming, showToolCalls }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const userScrolledUp = useRef(false);

  // 检测用户是否手动上翻了
  const handleScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const distanceToBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    userScrolledUp.current = distanceToBottom > BOTTOM_THRESHOLD;
  }, []);

  // 直接改 scrollTop，不用 scrollIntoView：
  // scrollIntoView 会连带滚动所有可滚动祖先，长内容时会出现整页乱滚
  const scrollToBottom = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, []);

  // 内容或容器尺寸变化时跟随底部。
  // 流式 token 增长、表格/公式渲染完成、输入框变高都会触发，
  // 不依赖 messages 引用是否变化，因此流式过程中也能实时跟随。
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    let raf = 0;
    const observer = new ResizeObserver(() => {
      if (userScrolledUp.current) return;
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(scrollToBottom);
    });

    observer.observe(el);
    if (contentRef.current) observer.observe(contentRef.current);

    return () => {
      observer.disconnect();
      cancelAnimationFrame(raf);
    };
  }, [scrollToBottom]);

  // 新消息或流式状态切换时贴底
  useEffect(() => {
    if (!userScrolledUp.current) scrollToBottom();
  }, [messages, streaming, scrollToBottom]);

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden px-6 pt-4 pb-6"
    >
      <div ref={contentRef} className="space-y-4">
        {messages.map((msg, idx) => (
          <MessageBubble
            key={msg.id}
            message={msg}
            isStreaming={streaming && idx === messages.length - 1 && msg.role === "assistant"}
            showToolCalls={showToolCalls}
          />
        ))}
      </div>
    </div>
  );
}
