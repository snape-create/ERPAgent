"use client";

import { useEffect, useRef, useState } from "react";

/**
 * 打字机效果：把 target 逐步"吐"出来。
 *
 * 为什么需要它：SSE 的多个 token 在传输层经常被合并成一个大 chunk，
 * 直接渲染 message.content 会变成"一大块一大块地蹦出来"，看起来不像流式。
 *
 * 两个关键约束：
 * 1. 流式期间限流到约 20fps，避免长文本每帧全量解析 Markdown 导致掉帧
 * 2. streaming 变为 false 时立即追平全文 —— 与流式期使用同一个渲染组件，
 *    所以不会出现"结束瞬间文字突然长出来"的跳变
 */
export function useTypewriter(target: string, streaming: boolean, speed = 3) {
  const [count, setCount] = useState(streaming ? 0 : target.length);
  const countRef = useRef(streaming ? 0 : target.length);
  const targetRef = useRef(target);
  const rafRef = useRef(0);
  const runningRef = useRef(false);
  const lastPaintRef = useRef(0);

  // 始终同步最新目标长度（不触发渲染）
  targetRef.current = target;

  useEffect(() => {
    // 历史消息 / 流式结束：立即全显
    if (!streaming) {
      runningRef.current = false;
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = 0;
      }
      countRef.current = target.length;
      setCount(target.length);
      return;
    }

    // 内容被重置（新一轮消息）：从头开始
    if (countRef.current > target.length) {
      countRef.current = 0;
      setCount(0);
      return;
    }

    if (countRef.current >= target.length || runningRef.current) return;

    runningRef.current = true;
    const tick = (now: number) => {
      const total = targetRef.current.length;
      const current = countRef.current;

      if (current >= total) {
        runningRef.current = false;
        rafRef.current = 0;
        return;
      }

      // 智能追赶：落后越多步进越大
      const gap = total - current;
      const inc =
        gap > 120 ? speed * 6 : gap > 60 ? speed * 4 : gap > 25 ? speed * 2 : speed;
      countRef.current = Math.min(current + inc, total);

      const caughtUp = countRef.current >= total;
      if (caughtUp || now - lastPaintRef.current >= 50) {
        lastPaintRef.current = now;
        setCount(countRef.current);
      }

      if (caughtUp) {
        runningRef.current = false;
        rafRef.current = 0;
        return;
      }

      rafRef.current = requestAnimationFrame(tick);
    };

    rafRef.current = requestAnimationFrame(tick);
  }, [target, streaming, speed]);

  // 卸载时停止循环
  useEffect(() => {
    return () => {
      runningRef.current = false;
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  return target.slice(0, count);
}
