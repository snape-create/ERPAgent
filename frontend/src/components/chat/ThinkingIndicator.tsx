"use client";

import { useEffect, useState } from "react";

interface Props {
  visible: boolean;
}

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${String(seconds % 60).padStart(2, "0")}s`;
}

export default function ThinkingIndicator({ visible }: Props) {
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    if (!visible) return;
    setSeconds(0);
    const timer = setInterval(() => setSeconds((value) => value + 1), 1000);
    return () => clearInterval(timer);
  }, [visible]);

  if (!visible) return null;

  return (
    <div className="shrink-0 px-4 md:px-6 pb-1 animate-rise">
      <div className="mx-auto w-full flex items-center gap-2">
        <span className="w-3 h-3 rounded-full border border-ink-3/30 border-t-accent animate-spin" />
        <span className="text-[12px] text-ink-2">正在思考</span>
        <span className="font-mono text-[11px] text-ink-3">分析意图 · 规划步骤</span>
        {seconds >= 3 && (
          <span className="ml-auto font-mono text-[11px] text-ink-3 tnum">
            已用时 {formatElapsed(seconds)}
          </span>
        )}
      </div>
    </div>
  );
}
