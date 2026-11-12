"use client";

interface Props {
  phase: string;
  phaseLabel: string;
  visible: boolean;
}

const PHASES = [
  { key: "thinking", label: "思考" },
  { key: "planning", label: "规划" },
  { key: "executing", label: "执行" },
  { key: "reviewing", label: "审查" },
  { key: "done", label: "完成" },
];

const PHASE_ORDER = ["thinking", "planning", "executing", "reviewing", "done"];

/**
 * Harness 工作流阶段：一条状态带，点位表示进度，标签用等宽字。
 * 只在真正运行或刚结束时出现，不做循环动画。
 */
export default function HarnessPhaseBar({ phase, phaseLabel, visible }: Props) {
  if (!visible || phase === "idle") return null;

  const currentIdx = PHASE_ORDER.indexOf(phase);

  return (
    <div className="shrink-0 h-9 flex items-center px-4 md:px-6 border-b border-hairline animate-rise">
      <div className="mx-auto w-full flex items-center gap-2 min-w-0">
        {PHASES.map((p, idx) => {
          const isPast = idx < currentIdx;
          const isCurrent = idx === currentIdx;

          return (
            <div key={p.key} className="flex items-center gap-1.5 shrink-0">
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  isCurrent
                    ? "bg-accent"
                    : isPast
                      ? "bg-accent/40"
                      : "bg-hairline-strong"
                }`}
              />
              <span
                className={`font-mono text-[11px] leading-none ${
                  isCurrent ? "text-ink" : isPast ? "text-ink-2" : "text-ink-3"
                }`}
              >
                {p.label}
              </span>
              {idx < PHASES.length - 1 && (
                <span
                  className={`h-px w-4 md:w-6 ml-0.5 ${
                    isPast ? "bg-accent/25" : "bg-hairline"
                  }`}
                />
              )}
            </div>
          );
        })}

        {phaseLabel && (
          <span className="ml-auto pl-3 min-w-0 font-mono text-[11px] text-ink-3 truncate hidden sm:block">
            {phaseLabel}
          </span>
        )}
      </div>
    </div>
  );
}
