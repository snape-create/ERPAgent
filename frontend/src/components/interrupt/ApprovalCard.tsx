"use client";

import { useMemo, useState } from "react";
import { ArrowRight, Check, Pencil, X } from "lucide-react";
import { InterruptData } from "@/lib/types";
import Stamp from "@/components/common/Stamp";

interface Props {
  data: InterruptData;
  onApprove: () => void;
  onReject: () => void;
  /** 带修改参数提交：edit 决策会把 args 覆盖回工具调用 */
  onEdit: (args: Record<string, unknown>) => void;
}

const TITLES: Record<string, string> = {
  order_create: "创建采购订单",
  order_update: "修改采购订单",
  order_update_status: "订单状态变更",
};

/** 后端会带标签，这里留一份兜底，避免多一次往返才看得懂 */
const STATUS_NAMES: Record<number, string> = {
  0: "待审核",
  1: "已审核",
  2: "已发货",
  3: "已收货",
  4: "已完成",
};

function statusLabel(value: unknown): string | null {
  if (typeof value === "number" && STATUS_NAMES[value]) return STATUS_NAMES[value];
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    if (Number.isFinite(parsed) && STATUS_NAMES[parsed]) return STATUS_NAMES[parsed];
  }
  return null;
}

type Dict = Record<string, unknown>;
type DetailLine = Record<string, unknown>;

function isOrderArgs(args: Dict): boolean {
  return Array.isArray(args.orderDetail);
}

function num(value: unknown): number {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function money(value: number): string {
  return value.toFixed(2);
}

function cellText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function cloneArgs(args: Dict): Dict {
  try {
    return JSON.parse(JSON.stringify(args)) as Dict;
  } catch {
    return { ...args };
  }
}

export default function ApprovalCard({ data, onApprove, onReject, onEdit }: Props) {
  const args = useMemo<Dict>(() => {
    const raw = data.tool_args ?? data.order_data ?? {};
    return raw && typeof raw === "object" && !Array.isArray(raw) ? (raw as Dict) : {};
  }, [data.tool_args, data.order_data]);

  const isOrder = isOrderArgs(args);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Dict>(() => cloneArgs(args));

  // 编辑态用草稿显示，其余时候直接显示后端传来的参数
  const view = editing ? draft : args;
  const lines: DetailLine[] =
    isOrder && Array.isArray(view.orderDetail)
      ? (view.orderDetail as DetailLine[])
      : [];

  const total = lines.reduce(
    (sum, line) => sum + num(line.quantity) * num(line.unitPrice),
    0
  );

  const title = TITLES[String(data.tool_name ?? "")] ?? "写操作审批";
  const actionCount = data.action_count ?? 0;
  const isStatusChange = data.tool_name === "order_update_status";
  const currentLabel =
    data.current_status_label ?? statusLabel(data.current_status);
  const targetLabel =
    data.target_status_label ?? statusLabel(args.status);

  const startEditing = () => {
    setDraft(cloneArgs(args));
    setEditing(true);
  };

  const updateLine = (index: number, key: "quantity" | "unitPrice", value: string) => {
    setDraft((prev) => {
      const detail = Array.isArray(prev.orderDetail)
        ? [...(prev.orderDetail as DetailLine[])]
        : [];
      detail[index] = { ...detail[index], [key]: value === "" ? 0 : Number(value) };
      return { ...prev, orderDetail: detail };
    });
  };

  // 提交前按修改后的明细重算总额：后端 _write_order 会直接采用 totalAmount，
  // 只改数量不同步总额会写出一张金额对不上的订单
  const submitEdit = () => {
    const next: Dict = { ...draft };
    if (Array.isArray(next.orderDetail)) {
      const detail = (next.orderDetail as DetailLine[]).map((line) => ({
        ...line,
        quantity: num(line.quantity),
        unitPrice: num(line.unitPrice),
      }));
      next.orderDetail = detail;
      next.totalAmount = detail.reduce(
        (sum, line) => sum + num(line.quantity) * num(line.unitPrice),
        0
      );
    }
    onEdit(next);
  };

  return (
    <div className="shrink-0 px-4 md:px-6 pb-3 animate-rise">
      <div className="mx-auto w-full rounded-card border border-hairline bg-surface shadow-lift">
        <div className="flex items-center gap-2 h-11 px-3.5 border-b border-hairline">
          <Stamp>待审批</Stamp>
          <span className="text-[13px] font-medium text-ink">{title}</span>
          {data.tool_name && (
            <span className="ml-auto font-mono text-[11px] text-ink-3 truncate">
              {data.tool_name}
            </span>
          )}
        </div>

        <div className="px-3.5 py-3">
          <p className="text-[13px] leading-[1.6] text-ink-2">
            {editing
              ? "修改下面的参数后提交，写入仍在你确认之后执行。"
              : "以下写入会等你确认后执行，拒绝则不产生任何单据。"}
          </p>

          {actionCount > 1 && (
            <p className="mt-1.5 text-[12px] text-ink-3">
              本次包含 <span className="tnum">{actionCount}</span> 个写操作，这里只列出第 1 个
            </p>
          )}

          {isStatusChange ? (
            <div className="mt-3">
              {data.order_number && (
                <div className="flex items-baseline gap-2 pb-2">
                  <span className="text-[11px] text-ink-3">订单编号</span>
                  <span className="font-mono text-[13px] text-ink">
                    {data.order_number}
                  </span>
                </div>
              )}

              <div className="flex items-center gap-2.5">
                {currentLabel ? (
                  <>
                    <span className="text-[13px] text-ink-2">{currentLabel}</span>
                    <ArrowRight size={14} strokeWidth={1.5} className="text-ink-3" />
                  </>
                ) : (
                  <span className="text-[13px] text-ink-2">目标状态</span>
                )}
                <span className="text-[13px] font-medium text-ink">
                  {targetLabel ?? "—"}
                </span>
              </div>

              {!currentLabel && (
                <p className="mt-2 text-[12px] text-ink-3">
                  未能读取当前状态，请在批准前自行核对订单
                </p>
              )}
            </div>
          ) : isOrder ? (
            <div className="mt-3">
              <div className="flex items-baseline gap-2 pb-2">
                <span className="text-[11px] text-ink-3">订单编号</span>
                {editing ? (
                  <input
                    value={String(draft.orderNumber ?? "")}
                    onChange={(event) =>
                      setDraft((prev) => ({ ...prev, orderNumber: event.target.value }))
                    }
                    className="h-7 min-w-0 flex-1 max-w-[220px] px-2 bg-paper border border-hairline rounded-[6px] font-mono text-[12px] text-ink focus:border-accent focus:outline-none"
                  />
                ) : (
                  <span className="font-mono text-[13px] text-ink">
                    {cellText(view.orderNumber)}
                  </span>
                )}
              </div>

              <table className="w-full text-[13px] tnum">
                <thead>
                  <tr className="text-[11px] text-ink-3">
                    <th className="pb-1.5 pr-3 text-left font-normal">零件 ID</th>
                    <th className="pb-1.5 pr-3 text-right font-normal">数量</th>
                    <th className="pb-1.5 pr-3 text-right font-normal">单价</th>
                    <th className="pb-1.5 text-right font-normal">小计</th>
                  </tr>
                </thead>
                <tbody>
                  {lines.map((line, index) => (
                    <tr
                      key={index}
                      className="border-t border-hairline align-middle"
                    >
                      <td className="py-1.5 pr-3 text-ink">{cellText(line.partId)}</td>
                      <td className="py-1.5 pr-3 text-right text-ink">
                        {editing ? (
                          <input
                            type="number"
                            min={1}
                            value={num(line.quantity)}
                            onChange={(event) =>
                              updateLine(index, "quantity", event.target.value)
                            }
                            className="w-20 h-7 px-1.5 text-right bg-paper border border-hairline rounded-[6px] text-[13px] tnum text-ink focus:border-accent focus:outline-none"
                          />
                        ) : (
                          cellText(line.quantity)
                        )}
                      </td>
                      <td className="py-1.5 pr-3 text-right text-ink">
                        {editing ? (
                          <input
                            type="number"
                            min={0}
                            step="0.01"
                            value={num(line.unitPrice)}
                            onChange={(event) =>
                              updateLine(index, "unitPrice", event.target.value)
                            }
                            className="w-24 h-7 px-1.5 text-right bg-paper border border-hairline rounded-[6px] text-[13px] tnum text-ink focus:border-accent focus:outline-none"
                          />
                        ) : (
                          money(num(line.unitPrice))
                        )}
                      </td>
                      <td className="py-1.5 text-right text-ink">
                        {money(num(line.quantity) * num(line.unitPrice))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <div className="flex items-baseline justify-end gap-3 pt-2 mt-1 border-t border-hairline">
                <span className="text-[11px] text-ink-3">总金额</span>
                <span className="text-[14px] font-medium text-ink tnum">
                  {money(total)} <span className="text-[11px] text-ink-3">CNY</span>
                </span>
              </div>
            </div>
          ) : (
            Object.keys(args).length > 0 && (
              <table className="w-full mt-3 text-[13px] tnum">
                <tbody>
                  {Object.entries(args).map(([key, value]) => (
                    <tr key={key} className="border-b border-hairline last:border-0">
                      <td className="py-1.5 pr-4 align-top font-mono text-[11px] text-ink-3 whitespace-nowrap">
                        {key}
                      </td>
                      <td className="py-1.5 text-ink break-words">{cellText(value)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          )}
        </div>

        <div className="flex items-center gap-2 px-3.5 pb-3.5">
          {editing ? (
            <>
              <button
                onClick={submitEdit}
                className="flex items-center gap-1.5 h-8 px-3.5 bg-accent hover:bg-accent-hover active:scale-[0.98] text-white text-[13px] font-medium rounded-ctl transition-[background-color,transform] duration-150 ease-quiet"
              >
                <Check size={14} strokeWidth={1.5} />
                提交修改
              </button>
              <button
                onClick={() => setEditing(false)}
                className="flex items-center gap-1.5 h-8 px-3.5 bg-surface border border-hairline hover:bg-raised/60 text-ink-2 text-[13px] rounded-ctl transition-colors duration-150 ease-quiet"
              >
                取消
              </button>
            </>
          ) : (
            <>
              <button
                onClick={onApprove}
                className="flex items-center gap-1.5 h-8 px-3.5 bg-accent hover:bg-accent-hover active:scale-[0.98] text-white text-[13px] font-medium rounded-ctl transition-[background-color,transform] duration-150 ease-quiet"
              >
                <Check size={14} strokeWidth={1.5} />
                {data.tool_name === "order_create"
                  ? "批准并创建"
                  : isStatusChange
                    ? "批准变更"
                    : "批准执行"}
              </button>
              {isOrder && (
                <button
                  onClick={startEditing}
                  className="flex items-center gap-1.5 h-8 px-3.5 bg-surface border border-hairline hover:bg-raised/60 text-ink-2 text-[13px] rounded-ctl transition-colors duration-150 ease-quiet"
                >
                  <Pencil size={14} strokeWidth={1.5} />
                  修改
                </button>
              )}
              <button
                onClick={onReject}
                className="flex items-center gap-1.5 h-8 px-3.5 bg-surface border border-hairline hover:bg-raised/60 text-ink-2 text-[13px] rounded-ctl transition-colors duration-150 ease-quiet"
              >
                <X size={14} strokeWidth={1.5} />
                拒绝
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
