"""ERP 后端服务：FastAPI + PostgreSQL

提供 Agent/MCP 工具所需的 23 个 HTTP 接口（供应商/零部件/订单/库存）。
默认监听 0.0.0.0:8081（与 .env 的 ERP_BASE_URL 对应）。

启动：
    .venv/bin/python -m src.erp_service.main
"""
from __future__ import annotations

import math
import os
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Body, FastAPI, HTTPException, Query
from sqlalchemy import text

from .db import get_engine, init_db, one, rows

ERP_HOST = os.getenv("ERP_HOST", "0.0.0.0")
ERP_PORT = int(os.getenv("ERP_PORT", "8081"))


def _page_result(records: list[dict], total: int, current: int, size: int) -> dict:
    return {
        "records": records,
        "total": total,
        "current": current,
        "size": size,
        "pages": math.ceil(total / size) if size else 0,
    }


def _fetch_page(count_sql: str, data_sql: str, params: dict, current: int, size: int) -> dict:
    engine = get_engine()
    with engine.connect() as conn:
        total = conn.execute(text(count_sql), params).scalar_one()
        data = rows(
            conn.execute(
                text(data_sql + " LIMIT :limit OFFSET :offset"),
                {**params, "limit": size, "offset": (current - 1) * size},
            )
        )
    return _page_result(data, int(total), current, size)


# ---------- 供应商 ----------
SUPPLIER_SELECT = """
SELECT id, supplier_code AS "supplierCode", name, contact_person AS "contactPerson",
       phone, email, address, credit_rating AS "creditRating", status
FROM suppliers
"""


def _get_supplier(supplier_id: int) -> dict:
    with get_engine().connect() as conn:
        data = one(conn.execute(text(SUPPLIER_SELECT + " WHERE id = :id"), {"id": supplier_id}))
    if not data:
        raise HTTPException(status_code=404, detail=f"供应商不存在: {supplier_id}")
    return data


# ---------- 零部件 ----------
PART_SELECT = """
SELECT id, part_code AS "partCode", name, model, specification, unit,
       purchase_price AS "purchasePrice", suggested_retail_price AS "suggestedRetailPrice",
       stock_warning_value AS "stockWarningValue", supplier_id AS "supplierId",
       category, description
FROM parts
"""


def _get_part(part_id: int) -> dict:
    with get_engine().connect() as conn:
        data = one(conn.execute(text(PART_SELECT + " WHERE id = :id"), {"id": part_id}))
    if not data:
        raise HTTPException(status_code=404, detail=f"零部件不存在: {part_id}")
    return data


# ---------- 订单 ----------
ORDER_SELECT = """
SELECT id, order_number AS "orderNumber", total_amount AS "totalAmount", status,
       remark, order_time AS "orderTime"
FROM orders
"""

DETAIL_SELECT = """
SELECT d.id, d.part_id AS "partId", p.part_code AS "partCode", p.name AS "partName",
       d.quantity, d.unit_price AS "unitPrice", d.remark
FROM order_detail d
LEFT JOIN parts p ON p.id = d.part_id
WHERE d.order_id = :oid
ORDER BY d.id
"""


def _get_order(order_id: int) -> dict:
    engine = get_engine()
    with engine.connect() as conn:
        order = one(conn.execute(text(ORDER_SELECT + " WHERE id = :id"), {"id": order_id}))
        if not order:
            raise HTTPException(status_code=404, detail=f"订单不存在: {order_id}")
        order["orderDetail"] = rows(conn.execute(text(DETAIL_SELECT), {"oid": order_id}))
    if isinstance(order.get("orderTime"), datetime):
        order["orderTime"] = order["orderTime"].isoformat(sep=" ")
    return order


def _write_order(order_id: int | None, body: dict) -> dict:
    details = body.get("orderDetail") or []
    total = body.get("totalAmount")
    if total is None:
        total = sum(float(d.get("quantity", 0)) * float(d.get("unitPrice", 0)) for d in details)
    status = body.get("status", 0)
    remark = body.get("remark")

    engine = get_engine()
    with engine.begin() as conn:
        if order_id is None:
            order_number = body.get("orderNumber")
            if not order_number:
                raise HTTPException(status_code=400, detail="orderNumber 必填")
            order_id = conn.execute(
                text(
                    "INSERT INTO orders (order_number,total_amount,status,remark) "
                    "VALUES (:on,:ta,:st,:rm) RETURNING id"
                ),
                {"on": order_number, "ta": total, "st": status, "rm": remark},
            ).scalar_one()
        else:
            conn.execute(
                text(
                    "UPDATE orders SET total_amount=:ta, status=:st, remark=:rm,"
                    " order_number=COALESCE(:on, order_number) WHERE id=:id"
                ),
                {"id": order_id, "ta": total, "st": status, "rm": remark,
                 "on": body.get("orderNumber")},
            )
            conn.execute(text("DELETE FROM order_detail WHERE order_id=:id"), {"id": order_id})

        for d in details:
            conn.execute(
                text(
                    "INSERT INTO order_detail (order_id,part_id,quantity,unit_price,remark) "
                    "VALUES (:oid,:pid,:q,:up,:rm)"
                ),
                {
                    "oid": order_id,
                    "pid": d.get("partId"),
                    "q": d.get("quantity", 0),
                    "up": d.get("unitPrice", 0),
                    "rm": d.get("remark"),
                },
            )
    return _get_order(order_id)


# ---------- 库存 ----------
INVENTORY_BASE = """
SELECT i.id, i.part_id AS "partId", i.current_quantity AS "currentQuantity",
       i.safety_stock AS "safetyStock", i.warehouse_location AS "warehouseLocation",
       p.part_code AS "partCode", p.name AS "partName", p.category,
       p.purchase_price AS "purchasePrice", p.unit
FROM inventory i
LEFT JOIN parts p ON p.id = i.part_id
"""


def _inventory_record(row: dict) -> dict:
    return {
        "id": row["id"],
        "partId": row["partId"],
        "currentQuantity": row["currentQuantity"],
        "safetyStock": row["safetyStock"],
        "warehouseLocation": row["warehouseLocation"],
        "partDetail": {
            "partCode": row["partCode"],
            "name": row["partName"],
            "category": row["category"],
            "purchasePrice": row["purchasePrice"],
            "unit": row["unit"],
        },
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="ERP Backend (Mock)", version="1.0.0", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "service": "erp-backend"}


# ================= 供应商 =================
@app.get("/api/suppliers/search")
def supplier_search(name: str | None = None):
    with get_engine().connect() as conn:
        data = rows(
            conn.execute(
                text(SUPPLIER_SELECT + " WHERE (:name IS NULL OR name ILIKE :kw) ORDER BY id"),
                {"name": name, "kw": f"%{name}%"},
            )
        )
    return data


@app.get("/api/suppliers/page")
def supplier_page(current: int = 1, size: int = 10, name: str | None = None,
                        status: int | None = None, creditRating: str | None = None):
    where = " WHERE 1=1"
    params: dict = {}
    if name:
        where += " AND name ILIKE :name"
        params["name"] = f"%{name}%"
    if status is not None:
        where += " AND status = :status"
        params["status"] = status
    if creditRating:
        where += " AND credit_rating = :cr"
        params["cr"] = creditRating
    return _fetch_page(
        "SELECT COUNT(*) FROM suppliers" + where,
        SUPPLIER_SELECT + where + " ORDER BY id",
        params, current, size,
    )


@app.get("/api/suppliers/get/{supplier_id}")
def supplier_get(supplier_id: int):
    return _get_supplier(supplier_id)


@app.post("/api/suppliers/create")
def supplier_create(body: dict = Body(...)):
    engine = get_engine()
    with engine.begin() as conn:
        sid = conn.execute(
            text(
                "INSERT INTO suppliers (supplier_code,name,contact_person,phone,email,address,"
                "credit_rating,status) VALUES (:c,:n,:cp,:ph,:em,:ad,:cr,:st) RETURNING id"
            ),
            {
                "c": body.get("supplierCode"), "n": body.get("name"),
                "cp": body.get("contactPerson"), "ph": body.get("phone"),
                "em": body.get("email"), "ad": body.get("address"),
                "cr": body.get("creditRating", "B"), "st": body.get("status", 1),
            },
        ).scalar_one()
    return _get_supplier(sid)


@app.patch("/api/suppliers/update-status/{supplier_id}")
def supplier_update_status(supplier_id: int, status: int):
    engine = get_engine()
    with engine.begin() as conn:
        res = conn.execute(
            text("UPDATE suppliers SET status=:st WHERE id=:id"),
            {"id": supplier_id, "st": status},
        )
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"供应商不存在: {supplier_id}")
    return _get_supplier(supplier_id)


# ================= 零部件 =================
@app.get("/api/parts/get/{part_id}")
def part_get(part_id: int):
    return _get_part(part_id)


@app.get("/api/parts/search")
def part_search(name: str | None = None):
    with get_engine().connect() as conn:
        data = rows(
            conn.execute(
                text(PART_SELECT + " WHERE (:name IS NULL OR name ILIKE :kw) ORDER BY id"),
                {"name": name, "kw": f"%{name}%"},
            )
        )
    return data


@app.get("/api/parts/supplier/{supplier_id}")
def part_by_supplier(supplier_id: int):
    with get_engine().connect() as conn:
        data = rows(
            conn.execute(
                text(PART_SELECT + " WHERE supplier_id = :sid ORDER BY id"),
                {"sid": supplier_id},
            )
        )
    return data


@app.get("/api/parts/page")
def part_page(current: int = 1, size: int = 10, name: str | None = None,
                    category: str | None = None, supplierId: int | None = None):
    where = " WHERE 1=1"
    params: dict = {}
    if name:
        where += " AND name ILIKE :name"
        params["name"] = f"%{name}%"
    if category:
        where += " AND category = :cat"
        params["cat"] = category
    if supplierId is not None:
        where += " AND supplier_id = :sid"
        params["sid"] = supplierId
    return _fetch_page(
        "SELECT COUNT(*) FROM parts" + where,
        PART_SELECT + where + " ORDER BY id",
        params, current, size,
    )


@app.post("/api/parts/create")
def part_create(body: dict = Body(...)):
    engine = get_engine()
    with engine.begin() as conn:
        pid = conn.execute(
            text(
                "INSERT INTO parts (part_code,name,model,specification,unit,purchase_price,"
                "suggested_retail_price,stock_warning_value,supplier_id,category,description)"
                " VALUES (:c,:n,:m,:s,:u,:pp,:rp,:w,:sid,:cat,:d) RETURNING id"
            ),
            {
                "c": body.get("partCode"), "n": body.get("name"),
                "m": body.get("model"), "s": body.get("specification"),
                "u": body.get("unit", "件"), "pp": body.get("purchasePrice", 0),
                "rp": body.get("suggestedRetailPrice", 0),
                "w": body.get("stockWarningValue", 0), "sid": body.get("supplierId"),
                "cat": body.get("category"), "d": body.get("description"),
            },
        ).scalar_one()
        # 同步建一条库存记录
        conn.execute(
            text(
                "INSERT INTO inventory (part_id,current_quantity,safety_stock)"
                " VALUES (:pid,0,:ss) ON CONFLICT (part_id) DO NOTHING"
            ),
            {"pid": pid, "ss": body.get("stockWarningValue", 0)},
        )
    return _get_part(pid)


# ================= 订单 =================
@app.post("/api/orders/create")
def order_create(body: dict = Body(...)):
    return _write_order(None, body)


@app.put("/api/orders/update/{order_id}")
def order_update(order_id: int, body: dict = Body(...)):
    return _write_order(order_id, body)


@app.get("/api/orders/page")
def order_page(current: int = 1, size: int = 10, orderNumber: str | None = None,
                     status: int | None = None, startDate: str | None = None,
                     endDate: str | None = None):
    where = " WHERE 1=1"
    params: dict = {}
    if orderNumber:
        where += " AND order_number ILIKE :on"
        params["on"] = f"%{orderNumber}%"
    if status is not None:
        where += " AND status = :st"
        params["st"] = status
    if startDate:
        where += " AND order_time::date >= :sd"
        params["sd"] = startDate
    if endDate:
        where += " AND order_time::date <= :ed"
        params["ed"] = endDate

    result = _fetch_page(
        "SELECT COUNT(*) FROM orders" + where,
        ORDER_SELECT + where + " ORDER BY id DESC",
        params, current, size,
    )
    for rec in result["records"]:
        if isinstance(rec.get("orderTime"), datetime):
            rec["orderTime"] = rec["orderTime"].isoformat(sep=" ")
    return result


@app.get("/api/orders/get/{order_id}")
def order_get(order_id: int):
    return _get_order(order_id)


@app.get("/api/orders/search-details")
def order_search_details(partName: str | None = None,
                               startDate: str | None = None,
                               endDate: str | None = None):
    where = " WHERE 1=1"
    params: dict = {}
    if partName:
        where += " AND p.name ILIKE :pn"
        params["pn"] = f"%{partName}%"
    if startDate:
        where += " AND o.order_time::date >= :sd"
        params["sd"] = startDate
    if endDate:
        where += " AND o.order_time::date <= :ed"
        params["ed"] = endDate
    sql = """
    SELECT d.id, o.id AS "orderId", o.order_number AS "orderNumber", o.order_time AS "orderTime",
           d.part_id AS "partId", p.part_code AS "partCode", p.name AS "partName",
           d.quantity, d.unit_price AS "unitPrice",
           (d.quantity * d.unit_price) AS "amount",
           s.name AS "supplierName"
    FROM order_detail d
    JOIN orders o ON o.id = d.order_id
    LEFT JOIN parts p ON p.id = d.part_id
    LEFT JOIN suppliers s ON s.id = p.supplier_id
    """ + where + " ORDER BY o.id DESC, d.id"
    with get_engine().connect() as conn:
        data = rows(conn.execute(text(sql), params))
    for rec in data:
        if isinstance(rec.get("orderTime"), datetime):
            rec["orderTime"] = rec["orderTime"].isoformat(sep=" ")
    return data


@app.get("/api/orders/statistics")
def order_statistics(startDate: str | None = None, endDate: str | None = None):
    where = " WHERE 1=1"
    params: dict = {}
    if startDate:
        where += " AND order_time::date >= :sd"
        params["sd"] = startDate
    if endDate:
        where += " AND order_time::date <= :ed"
        params["ed"] = endDate
    engine = get_engine()
    with engine.connect() as conn:
        summary = one(
            conn.execute(
                text(
                    "SELECT COUNT(*) AS \"totalOrders\", COALESCE(SUM(total_amount),0) AS \"totalAmount\""
                    " FROM orders" + where
                ),
                params,
            )
        )
        status_rows = rows(
            conn.execute(
                text(
                    "SELECT status, COUNT(*) AS count FROM orders" + where + " GROUP BY status ORDER BY status"
                ),
                params,
            )
        )
    status_names = {0: "待审核", 1: "已审核", 2: "已发货", 3: "已收货", 4: "已完成"}
    summary["statusCounts"] = [
        {"status": r["status"], "label": status_names.get(r["status"], str(r["status"])),
         "count": r["count"]}
        for r in status_rows
    ]
    return summary


@app.patch("/api/orders/update-status/{order_id}")
def order_update_status(order_id: int, status: int):
    engine = get_engine()
    with engine.begin() as conn:
        res = conn.execute(
            text("UPDATE orders SET status=:st WHERE id=:id"),
            {"id": order_id, "st": status},
        )
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"订单不存在: {order_id}")
    return _get_order(order_id)


# ================= 库存 =================
@app.get("/api/inventory/warning")
def inventory_warning():
    with get_engine().connect() as conn:
        data = rows(
            conn.execute(
                text(INVENTORY_BASE + " WHERE i.current_quantity < i.safety_stock ORDER BY i.id")
            )
        )
    return [_inventory_record(r) for r in data]


@app.get("/api/inventory/page")
def inventory_page(current: int = 1, size: int = 10,
                         partName: str | None = None,
                         warehouseLocation: str | None = None):
    where = " WHERE 1=1"
    params: dict = {}
    if partName:
        where += " AND p.name ILIKE :pn"
        params["pn"] = f"%{partName}%"
    if warehouseLocation:
        where += " AND i.warehouse_location = :wl"
        params["wl"] = warehouseLocation
    result = _fetch_page(
        "SELECT COUNT(*) FROM inventory i LEFT JOIN parts p ON p.id=i.part_id" + where,
        INVENTORY_BASE + where + " ORDER BY i.id",
        params, current, size,
    )
    result["records"] = [_inventory_record(r) for r in result["records"]]
    return result


@app.get("/api/inventory/check")
def inventory_check():
    engine = get_engine()
    with engine.connect() as conn:
        summary = one(
            conn.execute(
                text(
                    """
                    SELECT COUNT(*) AS "totalSku",
                           COALESCE(SUM(i.current_quantity),0) AS "totalQuantity",
                           COALESCE(SUM(i.current_quantity * p.purchase_price),0) AS "totalStockValue",
                           COALESCE(SUM(CASE WHEN i.current_quantity < i.safety_stock THEN 1 ELSE 0 END),0)
                               AS "warningCount"
                    FROM inventory i LEFT JOIN parts p ON p.id = i.part_id
                    """
                )
            )
        )
    return summary


@app.post("/api/inventory/inbound")
def inventory_inbound(partId: int, quantity: int, warehouseLocation: str | None = None):
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity 必须大于 0")
    engine = get_engine()
    with engine.begin() as conn:
        exists = conn.execute(
            text("SELECT id FROM inventory WHERE part_id=:pid"), {"pid": partId}
        ).scalar_one_or_none()
        if exists is None:
            conn.execute(
                text("INSERT INTO inventory (part_id,current_quantity,safety_stock) VALUES (:pid,:q,0)"),
                {"pid": partId, "q": quantity},
            )
        else:
            conn.execute(
                text("UPDATE inventory SET current_quantity=current_quantity+:q WHERE part_id=:pid"),
                {"pid": partId, "q": quantity},
            )
        if warehouseLocation:
            conn.execute(
                text("UPDATE inventory SET warehouse_location=:wl WHERE part_id=:pid"),
                {"pid": partId, "wl": warehouseLocation},
            )
    return _inventory_by_part(partId)


@app.post("/api/inventory/outbound")
def inventory_outbound(partId: int, quantity: int):
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity 必须大于 0")
    engine = get_engine()
    with engine.begin() as conn:
        current = conn.execute(
            text("SELECT current_quantity FROM inventory WHERE part_id=:pid"), {"pid": partId}
        ).scalar_one_or_none()
        if current is None:
            raise HTTPException(status_code=404, detail=f"库存记录不存在: partId={partId}")
        if current < quantity:
            raise HTTPException(status_code=400, detail=f"库存不足：当前 {current}，出库 {quantity}")
        conn.execute(
            text("UPDATE inventory SET current_quantity=current_quantity-:q WHERE part_id=:pid"),
            {"pid": partId, "q": quantity},
        )
    return _inventory_by_part(partId)


@app.get("/api/inventory/get/{inventory_id}")
def inventory_get(inventory_id: int):
    with get_engine().connect() as conn:
        data = one(conn.execute(text(INVENTORY_BASE + " WHERE i.id=:id"), {"id": inventory_id}))
    if not data:
        raise HTTPException(status_code=404, detail=f"库存记录不存在: {inventory_id}")
    return _inventory_record(data)


def _inventory_by_part(part_id: int) -> dict:
    with get_engine().connect() as conn:
        data = one(conn.execute(text(INVENTORY_BASE + " WHERE i.part_id=:pid"), {"pid": part_id}))
    if not data:
        raise HTTPException(status_code=404, detail=f"库存记录不存在: partId={part_id}")
    return _inventory_record(data)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=ERP_HOST, port=ERP_PORT)
