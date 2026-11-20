"use client";

import { useEffect, useRef, useCallback, useState } from "react";
import { ArrowDown } from "lucide-react";
import { ChatMessage } from "@/lib/types";
import MessageBubble from "./MessageBubble";

interface Props {
  messages: ChatMessage[];
  streaming: boolean;
  interrupted: boolean;
  showToolCalls: boolean;
  activityLabel: string;
  lastEventAt: number;
  phase: string;
  phaseLabel: string;
}

/** 距底部多少像素内算"贴着底部" */
const BOTTOM_THRESHOLD = 80;

export default function MessageList({
  messages,
  streaming,
  interrupted,
  showToolCalls,
  activityLabel,
  lastEventAt,
  phase,
  phaseLabel,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const userScrolledUp = useRef(false);
  const [showJump, setShowJump] = useState(false);

  // 检测用户是否手动上翻了
  const handleScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const distanceToBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    const away = distanceToBottom > BOTTOM_THRESHOLD;
    userScrolledUp.current = away;
    setShowJump(away);
  }, []);

  // 直接改 scrollTop，不用 scrollIntoView：
  // scrollIntoView 会连带滚动所有可滚动祖先，长内容时会出现整页乱滚
  const scrollToBottom = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, []);

  // 用户主动回到最新：清掉上翻标记，后续增量继续自动贴底
  const jumpToLatest = useCallback(() => {
    userScrolledUp.current = false;
    setShowJump(false);
    scrollToBottom();
  }, [scrollToBottom]);

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

  // 新消息或流式状态切换时贴底。
  // 区分增量来源：最后一条是用户消息，说明是用户自己刚发送（或切换会话），
  // 此时无论是否上翻过都强制贴底；AI 增量则尊重用户的上翻状态。
  const prevLengthRef = useRef(0);
  useEffect(() => {
    const lastMsg = messages[messages.length - 1];
    // 必须在覆盖 prevLength 之前比较，否则"切换会话长度变短"永远判不出来
    const prevLength = prevLengthRef.current;
    const lengthGrew = messages.length > prevLength;
    prevLengthRef.current = messages.length;

    // 切换会话（消息被整体替换，长度可能变短）时重置上翻标记
    if (messages.length < prevLength || (lengthGrew && lastMsg?.role === "user")) {
      userScrolledUp.current = false;
    }

    if (!userScrolledUp.current) {
      scrollToBottom();
      setShowJump(false);
    }
  }, [messages, streaming, scrollToBottom]);

  return (
    <div className="relative flex flex-1 min-h-0">
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="h-full flex-1 min-w-0 overflow-y-auto overflow-x-hidden px-4 md:px-6"
      >
        {/* 顶部留出与输入区呼应的呼吸空间 */}
        <div ref={contentRef} className="mx-auto w-full max-w-[760px] pt-6 pb-6 flex flex-col gap-6">
          {messages.map((msg, idx) => (
            <MessageBubble
              key={msg.id}
              message={msg}
              isStreaming={streaming && idx === messages.length - 1 && msg.role === "assistant"}
              isInterrupted={
                interrupted && idx === messages.length - 1 && msg.role === "assistant"
              }
              showToolCalls={showToolCalls}
              live={
                idx === messages.length - 1 && msg.role === "assistant"
                  ? { activityLabel, lastEventAt, phase, phaseLabel }
                  : undefined
              }
            />
          ))}
        </div>
      </div>

      {/* 上翻阅读时给一个回到底部的入口，不打断当前阅读位置 */}
      {showJump && (
        <button
          onClick={jumpToLatest}
          className="absolute bottom-4 inset-x-0 mx-auto w-fit z-10 flex items-center gap-1.5 h-8 px-3 rounded-full bg-surface border border-hairline shadow-lift text-[12px] text-ink-2 hover:bg-raised transition-colors duration-150 ease-quiet animate-rise"
        >
          <ArrowDown size={13} strokeWidth={1.5} />
          回到最新
        </button>
      )}
    </div>
  );
}
