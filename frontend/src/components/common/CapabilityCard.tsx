"use client";

import { ChartColumn, ShoppingCart, PackageSearch, Search } from "lucide-react";
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

/** 能力入口卡片：图标、标题、一句话说明，点击即发送对应提问 */
export default function CapabilityCard({ card, onClick }: Props) {
  const Icon = ICONS[card.icon];

  return (
    <button
      onClick={() => onClick(card.prompt)}
      className="group flex flex-col items-start gap-1.5 w-full p-4 text-left bg-surface border border-hairline rounded-card shadow-lift hover:border-accent/35 transition-[border-color] duration-150 ease-quiet"
    >
      <Icon
        size={16}
        strokeWidth={1.5}
        className="text-ink-3 group-hover:text-accent transition-colors duration-150 ease-quiet"
      />
      <span className="mt-1 text-[14px] font-medium text-ink">{card.title}</span>
      <span className="text-[13px] leading-[1.6] text-ink-3">
        {card.description}
      </span>
    </button>
  );
}
