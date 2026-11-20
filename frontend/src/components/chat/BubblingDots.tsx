"use client";

interface Props {
  className?: string;
}

/** 三个冒泡点：只出现在状态胶囊里，正文不再有闪烁光标 */
export default function BubblingDots({ className = "" }: Props) {
  return (
    <span className={`inline-flex items-center gap-[3px] ${className}`} aria-hidden="true">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="bubbling-dot w-1 h-1 rounded-full bg-current"
          style={{ animationDelay: `${i * 0.15}s` }}
        />
      ))}
    </span>
  );
}
