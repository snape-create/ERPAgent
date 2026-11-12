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

export default function WelcomeScreen({ onPromptClick }: Props) {
  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      <div className="mx-auto flex min-h-full w-full max-w-[640px] flex-col justify-center px-4 py-10">
        <h1 className="text-[22px] font-medium text-ink text-center">
          智能采购助手
        </h1>
        <p className="mt-2 text-[13px] leading-[1.7] text-ink-3 text-center">
          供应商分析、采购下单、库存预警、零件查询
          <br className="hidden min-[420px]:block" />
          写操作会停下来等你审批
        </p>

        <div className="mt-8 grid grid-cols-1 min-[420px]:grid-cols-2 gap-3">
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
