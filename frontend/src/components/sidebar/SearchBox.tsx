"use client";

import { Search } from "lucide-react";

interface Props {
  value: string;
  onChange: (v: string) => void;
}

export default function SearchBox({ value, onChange }: Props) {
  return (
    <div className="relative">
      <Search
        size={14}
        strokeWidth={1.5}
        className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-3 pointer-events-none"
      />
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="搜索会话"
        className="w-full h-8 pl-8 pr-2.5 text-[13px] bg-surface border border-hairline rounded-ctl text-ink placeholder:text-ink-3 focus:outline-none focus:border-accent/50 focus:ring-2 focus:ring-accent/15 transition-[border-color,box-shadow] duration-150 ease-quiet"
      />
    </div>
  );
}
