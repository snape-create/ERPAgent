"use client";

import { ArrowRight, ChartColumn, ShoppingCart, PackageSearch, Search } from "lucide-react";
import { CapabilityCard as CardType } from "@/lib/types";

const ICONS = {
  chart: ChartColumn,
  cart: ShoppingCart,
  box: PackageSearch,
  search: Search,
} as const;

interface Props {
  card: CardType;
  onClick: (prompt: string) => void;
}

/**
 * 能力入口：横向一行，图标装在一个 32×32 的方框里，右侧标题 + 说明，
 * 悬停时箭头出现。比四个大方块更像工具面板，扫描成本也更低。
 */
export default function CapabilityCard({ card, onClick }: Props) {
  const Icon = ICONS[card.icon];

  return (
    <button
      onClick={() => onClick(card.prompt)}
      className="group flex items-center gap-3 w-full px-4 py-3.5 text-left bg-surface border border-hairline rounded-card shadow-lift hover:border-accent/35 transition-[border-color] duration-150 ease-quiet"
    >
      <span className="flex items-center justify-center w-8 h-8 shrink-0 rounded-[8px] border border-hairline bg-paper text-ink-3 group-hover:text-accent transition-colors duration-150 ease-quiet">
        <Icon size={16} strokeWidth={1.5} />
      </span>

      <span className="min-w-0 flex-1">
        <span className="block text-[14px] font-medium leading-[1.4] text-ink">
          {card.title}
        </span>
        <span className="mt-0.5 block truncate text-[12.5px] leading-[1.5] text-ink-3">
          {card.description}
        </span>
      </span>

      <ArrowRight
        size={14}
        strokeWidth={1.5}
        className="shrink-0 text-ink-3 opacity-0 -translate-x-1 group-hover:opacity-100 group-hover:translate-x-0 transition-[opacity,transform] duration-150 ease-quiet"
      />
    </button>
  );
}
