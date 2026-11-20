"use client";

import { useRef, useCallback, useEffect } from "react";
import { SSEParser } from "@/lib/sse-parser";
import { SSEEvent } from "@/lib/types";

interface UseSSEOptions {
  onEvent: (event: SSEEvent) => void;
  onError?: (error: Error) => void;
  onComplete?: () => void;
}

/** 调度句柄：浏览器有 rAF 用它，SSR/测试环境退回 setTimeout */
type FrameHandle =
  | { kind: "raf"; id: number }
  | { kind: "timeout"; id: ReturnType<typeof setTimeout> };

export function useSSE({ onEvent, onError, onComplete }: UseSSEOptions) {
  const parserRef = useRef(new SSEParser());
  const abortRef = useRef<AbortController | null>(null);

  // 事件队列 + 调度句柄。网络 chunk 的到达频率远高于屏幕刷新率，
  // 若每个 chunk 都直接派发，父组件会为同一帧内的几十个 token 反复渲染。
  // 因此这里只入队，攒到下一帧一次性分发。
  const queueRef = useRef<SSEEvent[]>([]);
  const frameRef = useRef<FrameHandle | null>(null);

  // 用 ref 持有最新回调：start 是跨整条流存活的长闭包，
  // 直接捕获 props 回调会一直调用父组件首次渲染时的旧闭包（状态全过期）。
  const onEventRef = useRef(onEvent);
  const onErrorRef = useRef(onError);
  const onCompleteRef = useRef(onComplete);
  onEventRef.current = onEvent;
  onErrorRef.current = onError;
  onCompleteRef.current = onComplete;

  const cancelFrame = useCallback(() => {
    const handle = frameRef.current;
    if (!handle) return;
    frameRef.current = null;
    if (handle.kind === "raf") {
      cancelAnimationFrame(handle.id);
    } else {
      clearTimeout(handle.id);
    }
  }, []);

  const flush = useCallback(() => {
    // 先取消已排队的调度：这里主动收尾时，那一帧不该再跑一次
    cancelFrame();
    if (queueRef.current.length === 0) return;
    // 整批换出再分发：分发过程中若又有事件入队（例如 onEvent 里触发新的请求），
    // 它们会重新 schedule 下一帧，而不会在本轮被重复消费
    const pending = queueRef.current;
    queueRef.current = [];
    for (const event of pending) {
      onEventRef.current(event);
    }
  }, [cancelFrame]);

  const schedule = useCallback(() => {
    // 每批只调度一次，后续 chunk 直接追加到队列
    if (frameRef.current) return;
    if (typeof requestAnimationFrame === "function") {
      frameRef.current = {
        kind: "raf",
        id: requestAnimationFrame(() => flush()),
      };
    } else {
      // SSR / 测试环境没有 rAF：16ms 约等于一帧，保持同样的批量粒度
      frameRef.current = {
        kind: "timeout",
        id: setTimeout(() => flush(), 16),
      };
    }
  }, [flush]);

  const handleChunk = useCallback(
    (chunk: string) => {
      const events = parserRef.current.parse(chunk);
      if (events.length === 0) return;
      // 注意：只入队，不派发。真正的 setState 留到 flush 统一做
      queueRef.current.push(...events);
      schedule();
    },
    [schedule]
  );

  const start = useCallback(
    async (fetchFn: (onChunk: (c: string) => void, signal: AbortSignal) => Promise<void>) => {
      // 取消之前的请求
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      // 每轮开始前重置解析器：上一轮残留的半截 SSE 块不能拼到这一轮
      parserRef.current.reset();
      // 同时丢掉上一轮可能没来得及分发的事件，避免串轮
      queueRef.current = [];
      cancelFrame();

      try {
        await fetchFn(handleChunk, controller.signal);
        // fetch 正常结束不代表队列已空（最后几个 chunk 的事件还在等 rAF）。
        // 必须同步 flush，否则 onComplete 看到的是缺尾巴的半成品状态。
        flush();
        onCompleteRef.current?.();
      } catch (err: unknown) {
        if (err instanceof Error && err.name === "AbortError") {
          // 仅当自己仍是最新请求时才收尾，避免误伤紧接着发起的新请求。
          // 此时队列可能已经属于新请求，所以不能在此处抢着 flush。
          if (abortRef.current === controller) {
            flush();
            onCompleteRef.current?.();
          }
          return;
        }
        // 出错也要先交付已收到的事件，避免界面停在旧状态
        flush();
        onErrorRef.current?.(err instanceof Error ? err : new Error(String(err)));
      }
    },
    [handleChunk, flush, cancelFrame]
  );

  const abort = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  useEffect(() => {
    return () => {
      // 卸载后 rAF/timeout 仍会触发，回调里的 setState 会警告并泄漏
      cancelFrame();
      queueRef.current = [];
    };
  }, [cancelFrame]);

  return { start, abort };
}
