"use client";

import { CapabilityCard } from "@/lib/types";
import CapabilityCardComponent from "@/components/common/CapabilityCard";

const CARDS: CapabilityCard[] = [
  {
    icon: "chart",
    title: "供应商分析",
    description: "对比信用评级与供货能力",
    prompt: "帮我对所有供应商进行综合分析，生成对比图表",
  },
  {
    icon: "cart",
    title: "采购下单",
    description: "缺字段会追问，写入前需审批",
    prompt: "帮我新增一个采购订单",
  },
  {
    icon: "box",
    title: "库存预警",
    description: "找出低于安全库存的零部件",
    prompt: "查看当前库存预警信息",
  },
  {
    icon: "search",
    title: "零部件查询",
    description: "查库存、价格与关联供应商",
    prompt: "查询所有零部件的库存和价格信息",
  },
];

interface Props {
  onPromptClick: (prompt: string) => void;
}

/**
 * 空状态：左对齐的一段说明 + 四个入口。
 * 不再整体垂直居中——居中的一小块放在大画布中间，会显得页面是空的。
 */
export default function WelcomeScreen({ onPromptClick }: Props) {
  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      {/* 垂直居中但略偏上：既不是贴顶留一大片空白，也不是正中的"悬浮小方块" */}
      <div className="mx-auto flex min-h-full w-full max-w-[760px] flex-col justify-center px-6 pt-16 pb-20 -mt-6">
        <p className="font-mono text-[11px] leading-none text-ink-3">采购工作台</p>

        <h1 className="mt-3 text-[26px] font-semibold leading-[1.35] text-ink">
          查库存、比供应商、下单走审批
        </h1>

        <p className="mt-2 text-[13px] leading-[1.7] text-ink-3">
          写操作停下来等你确认，拒绝则不产生任何单据
        </p>

        <div className="mt-10 grid grid-cols-1 min-[520px]:grid-cols-2 gap-3">
          {CARDS.map((card) => (
            <CapabilityCardComponent
              key={card.title}
              card={card}
              onClick={onPromptClick}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
