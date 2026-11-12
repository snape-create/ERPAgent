"use client";

/**
 * 标志：一个方框加"采"字，走印章的路子，不用填充色块。
 */
export default function Logo() {
  return (
    <div className="flex items-center gap-2.5 px-4 pt-5 pb-4">
      <div className="w-7 h-7 rounded-[6px] border border-accent flex items-center justify-center shrink-0">
        <span className="text-accent text-[13px] font-medium leading-none">采</span>
      </div>
      <div className="flex flex-col gap-0.5 min-w-0">
        <span className="text-[14px] font-medium text-ink leading-none">智能采购助手</span>
        <span className="font-mono text-[11px] text-ink-3 leading-none">ERP Agent</span>
      </div>
    </div>
  );
}
