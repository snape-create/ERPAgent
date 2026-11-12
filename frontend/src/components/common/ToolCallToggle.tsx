"use client";

interface Props {
  show: boolean;
  onToggle: (v: boolean) => void;
}

export default function ToolCallToggle({ show, onToggle }: Props) {
  return (
    <button
      role="switch"
      aria-checked={show}
      onClick={() => onToggle(!show)}
      className="group flex items-center gap-2 h-6 text-[11px] text-ink-3 hover:text-ink-2 transition-colors duration-150 ease-quiet"
    >
      <span
        className={`relative w-7 h-4 rounded-full transition-colors duration-150 ease-quiet ${
          show ? "bg-accent" : "bg-raised"
        }`}
      >
        <span
          className={`absolute top-0.5 left-0.5 w-3 h-3 rounded-full bg-surface shadow-knob transition-transform duration-150 ease-quiet ${
            show ? "translate-x-3" : "translate-x-0"
          }`}
        />
      </span>
      工具调用明细
    </button>
  );
}
