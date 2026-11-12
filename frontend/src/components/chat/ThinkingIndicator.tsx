"use client";

interface Props {
  visible: boolean;
}

export default function ThinkingIndicator({ visible }: Props) {
  if (!visible) return null;

  return (
    <div className="shrink-0 px-4 md:px-6 pb-1 animate-rise">
      <div className="mx-auto w-full flex items-center gap-2">
        <span className="w-3 h-3 rounded-full border border-ink-3/30 border-t-accent animate-spin" />
        <span className="text-[12px] text-ink-2">正在思考</span>
        <span className="font-mono text-[11px] text-ink-3">分析意图 · 规划步骤</span>
      </div>
    </div>
  );
}
