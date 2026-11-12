"use client";

import { Plus } from "lucide-react";

interface Props {
  onClick: () => void;
}

export default function NewChatButton({ onClick }: Props) {
  return (
    <button
      onClick={onClick}
      className="w-full h-9 flex items-center justify-center gap-1.5 bg-accent hover:bg-accent-hover active:scale-[0.98] text-white text-[13px] font-medium rounded-ctl transition-[background-color,transform] duration-150 ease-quiet"
    >
      <Plus size={16} strokeWidth={1.5} />
      新建对话
    </button>
  );
}
