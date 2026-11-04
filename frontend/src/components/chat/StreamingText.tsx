"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import Markdown from "./Markdown";

interface Props {
  text: string;
  speed?: number; // 每帧显示的字符数（默认2）
  markdown?: boolean; // 是否以 Markdown 渲染（流式回答时应为 true）
}

/**
 * 打字机效果组件
 *
 * 用 requestAnimationFrame 逐字推进，并区分两种刷新节奏：
 * - 纯文本：每帧刷新
 * - Markdown：每帧重建语法树代价高，限制到约 20fps
 *
 * 循环采用"追上即停、有新内容再唤醒"的模型：
 * 早期版本每帧都跑（浪费 CPU），后来改成每次 text 变化就 cancel 重启，
 * 结果 token 密集时动画永远跑不到第一帧，文字会整块突然出现。
 * 现在由独立的唤醒 effect 负责启动，已在运行时不打断。
 */
export default function StreamingText({ text, speed = 2, markdown = false }: Props) {
  const [displayedCount, setDisplayedCount] = useState(0);
  const countRef = useRef(0);
  const targetRef = useRef(0);
  const rafRef = useRef<number>(0);
  const runningRef = useRef(false);
  const lastPaintRef = useRef(0);

  // 始终同步目标长度（不触发 effect）
  targetRef.current = text.length;

  const start = useCallback(() => {
    if (runningRef.current) return;
    runningRef.current = true;

    const animate = (now: number) => {
      const target = targetRef.current;
      const current = countRef.current;

      // 追上了就停下，等下一次 text 增长时由唤醒 effect 重启
      if (current >= target) {
        runningRef.current = false;
        rafRef.current = 0;
        return;
      }

      // 智能追赶：差距大时加速，接近时逐字
      const gap = target - current;
      const increment = gap > 80 ? speed * 4 : gap > 40 ? speed * 3 : gap > 15 ? speed * 2 : speed;
      countRef.current = Math.min(current + increment, target);

      const caughtUp = countRef.current >= target;
      // 纯文本每帧刷新即可；Markdown 限流，避免长回答流式输出时掉帧
      if (!markdown || caughtUp || now - lastPaintRef.current >= 50) {
        lastPaintRef.current = now;
        setDisplayedCount(countRef.current);
      }

      if (caughtUp) {
        runningRef.current = false;
        rafRef.current = 0;
        return;
      }

      rafRef.current = requestAnimationFrame(animate);
    };

    rafRef.current = requestAnimationFrame(animate);
  }, [speed, markdown]);

  // 有新内容时唤醒循环；已经在跑就不打断
  useEffect(() => {
    if (targetRef.current > countRef.current) start();
  }, [text.length, start]);

  // 仅在卸载时停止循环
  useEffect(() => {
    return () => {
      runningRef.current = false;
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = 0;
      }
    };
  }, []);

  // 新消息重置
  useEffect(() => {
    if (text.length === 0) {
      countRef.current = 0;
      setDisplayedCount(0);
    }
  }, [text.length]);

  // 等待状态（还没有任何文字）
  if (!text) {
    return (
      <div className="flex items-center gap-1.5 py-1">
        <span className="typing-dot w-2 h-2 bg-blue-500 rounded-full inline-block animate-bounce [animation-delay:0ms]" />
        <span className="typing-dot w-2 h-2 bg-blue-500 rounded-full inline-block animate-bounce [animation-delay:150ms]" />
        <span className="typing-dot w-2 h-2 bg-blue-500 rounded-full inline-block animate-bounce [animation-delay:300ms]" />
      </div>
    );
  }

  const displayedText = text.slice(0, displayedCount);
  const isComplete = displayedCount >= text.length;

  // 打字光标
  const cursor = !isComplete && (
    <span className="inline-block w-[2px] h-[1em] bg-blue-500 ml-0.5 align-middle animate-pulse" />
  );

  if (markdown) {
    return (
      <div>
        <Markdown>{displayedText}</Markdown>
        {cursor}
      </div>
    );
  }

  return (
    <span className="whitespace-pre-wrap">
      {displayedText}
      {cursor}
    </span>
  );
}
