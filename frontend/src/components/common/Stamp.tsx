"use client";

interface Props {
  children: React.ReactNode;
  tone?: "accent" | "warning";
}

/**
 * 方章式状态标记。全站只有审批与补充这两类状态使用，
 * 作为这套界面的识别细节。
 */
export default function Stamp({ children, tone = "accent" }: Props) {
  const toneClass =
    tone === "accent"
      ? "border-accent/40 text-accent"
      : "border-warning/40 text-warning";

  return (
    <span
      className={`inline-flex items-center h-5 px-1.5 rounded-[4px] border font-mono text-[11px] tracking-[0.02em] whitespace-nowrap ${toneClass}`}
    >
      {children}
    </span>
  );
}
