"""ERP 数据库层：连接、建表、种子数据。

默认连接 docker compose 里的 PostgreSQL：
    postgresql+psycopg2://postgres:postgres123@localhost:5432/erp_agent
可通过环境变量 ERP_DB_URL 覆盖。
"""
from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# 加载项目根目录 .env，保证 ERP_DB_URL / ERP_HOST / ERP_PORT 可配置
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

DEFAULT_DB_URL = "postgresql+psycopg2://postgres:postgres123@localhost:5432/erp_agent"
DB_URL = os.getenv("ERP_DB_URL", DEFAULT_DB_URL)

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(DB_URL, pool_pre_ping=True, future=True)
    return _engine


def _to_plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return value


def rows(result) -> list[dict]:
    """把 SQLAlchemy Result 转为 list[dict]，并把 Decimal 转成 float。"""
    return [
        {k: _to_plain(v) for k, v in dict(row).items()}
        for row in result.mappings().all()
    ]


def one(result) -> dict | None:
    data = rows(result)
    return data[0] if data else None


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS suppliers (
    id             SERIAL PRIMARY KEY,
    supplier_code  VARCHAR(64) UNIQUE NOT NULL,
    name           VARCHAR(128) NOT NULL,
    contact_person VARCHAR(64),
    phone          VARCHAR(32),
    email          VARCHAR(128),
    address        VARCHAR(255),
    credit_rating  VARCHAR(8) DEFAULT 'B',
    status         INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS parts (
    id                    SERIAL PRIMARY KEY,
    part_code             VARCHAR(64) UNIQUE NOT NULL,
    name                  VARCHAR(128) NOT NULL,
    model                 VARCHAR(64),
    specification         VARCHAR(128),
    unit                  VARCHAR(16) DEFAULT '件',
    purchase_price        NUMERIC(12,2) DEFAULT 0,
    suggested_retail_price NUMERIC(12,2) DEFAULT 0,
    stock_warning_value   INTEGER DEFAULT 0,
    supplier_id           INTEGER REFERENCES suppliers(id),
    category              VARCHAR(64),
    description           TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    id           SERIAL PRIMARY KEY,
    order_number VARCHAR(64) UNIQUE NOT NULL,
    total_amount NUMERIC(14,2) DEFAULT 0,
    status       INTEGER DEFAULT 0,
    remark       VARCHAR(255),
    order_time   TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS order_detail (
    id         SERIAL PRIMARY KEY,
    order_id   INTEGER REFERENCES orders(id) ON DELETE CASCADE,
    part_id    INTEGER REFERENCES parts(id),
    quantity   INTEGER NOT NULL,
    unit_price NUMERIC(12,2) NOT NULL,
    remark     VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS inventory (
    id                 SERIAL PRIMARY KEY,
    part_id            INTEGER UNIQUE REFERENCES parts(id),
    current_quantity   INTEGER DEFAULT 0,
    safety_stock       INTEGER DEFAULT 0,
    warehouse_location VARCHAR(64)
);
"""

# (code, name, contact, phone, email, address, credit, status)
SUPPLIERS = [
    ("SUP-001", "博世（中国）汽车部件", "张伟", "021-88880001", "sales@bosch.cn", "上海市浦东新区金桥路1号", "A", 1),
    ("SUP-002", "电装（中国）投资", "李娜", "021-88880002", "sales@denso.cn", "上海市闵行区申北路2号", "A", 1),
    ("SUP-003", "杭州万向传动", "王强", "0571-88880003", "sales@wanxiang.cn", "浙江省杭州市萧山区3号", "B", 1),
    ("SUP-004", "宁波拓普减震", "赵敏", "0574-88880004", "sales@tuopu.cn", "浙江省宁波市北仑区4号", "B", 1),
    ("SUP-005", "潍坊盛瑞动力", "陈杰", "0536-88880005", "sales@shengrui.cn", "山东省潍坊市高新区5号", "C", 0),
    ("SUP-006", "上海天合制动", "刘洋", "021-88880006", "sales@trw.cn", "上海市嘉定区6号", "A", 1),
]

# (code, name, model, spec, unit, purchase, retail, warning, supplier_code, category, desc)
PARTS = [
    ("P-001", "铱金火花塞", "IX-24", "M10x1.0", "个", 12.50, 25.00, 100, "SUP-002", "发动机系统", "高原点火稳定，耐久 3 万公里"),
    ("P-002", "空气滤芯", "AF-110", "通用型", "个", 8.00, 18.00, 80, "SUP-002", "发动机系统", "高密度滤纸，过滤效率高"),
    ("P-003", "机油滤芯", "OF-200", "旋装式", "个", 6.50, 15.00, 120, "SUP-002", "发动机系统", "适配 125cc-250cc 车型"),
    ("P-004", "前制动片", "BP-F01", "烧结金属", "套", 22.00, 45.00, 60, "SUP-001", "制动系统", "高温抗衰减，湿态制动稳定"),
    ("P-005", "后制动片", "BP-R01", "烧结金属", "套", 20.00, 40.00, 60, "SUP-001", "制动系统", "低噪音配方"),
    ("P-006", "制动油管", "BH-300", "不锈钢编织", "根", 35.00, 68.00, 30, "SUP-006", "制动系统", "耐高压，抗老化"),
    ("P-007", "活塞环", "PR-150", "150cc", "组", 18.00, 36.00, 50, "SUP-005", "发动机系统", "耐磨合金铸铁"),
    ("P-008", "气缸垫", "CG-150", "150cc", "片", 9.00, 20.00, 70, "SUP-005", "发动机系统", "耐高温密封"),
    ("P-009", "驱动链条", "CH-428", "428H-120L", "条", 28.00, 55.00, 40, "SUP-003", "传动系统", "高强度合金钢"),
    ("P-010", "链轮", "SP-428", "14T/38T", "套", 32.00, 60.00, 35, "SUP-003", "传动系统", "齿面淬火处理"),
    ("P-011", "前减震器", "SH-F01", "倒置式", "对", 210.00, 380.00, 15, "SUP-004", "悬挂系统", "可调阻尼"),
    ("P-012", "后减震器", "SH-R01", "双弹簧", "对", 180.00, 320.00, 15, "SUP-004", "悬挂系统", "氮气减震"),
    ("P-013", "LED 前照灯", "HL-500", "12V 35W", "个", 45.00, 90.00, 25, "SUP-002", "电气系统", "高亮聚光"),
    ("P-014", "蓄电池", "BT-7A", "12V 7Ah", "个", 65.00, 120.00, 20, "SUP-002", "电气系统", "免维护胶体"),
    ("P-015", "离合器片", "CP-200", "湿式多片", "套", 40.00, 78.00, 25, "SUP-005", "传动系统", "耐高温摩擦材料"),
]

# (part_code, current, safety, warehouse)
INVENTORY = [
    ("P-001", 150, 100, "A-01-01"),
    ("P-002", 60, 80, "A-01-02"),   # 低于安全库存 → 预警
    ("P-003", 300, 120, "A-01-03"),
    ("P-004", 45, 60, "B-02-01"),   # 预警
    ("P-005", 90, 60, "B-02-02"),
    ("P-006", 12, 30, "B-02-03"),   # 预警
    ("P-007", 80, 50, "A-03-01"),
    ("P-008", 100, 70, "A-03-02"),
    ("P-009", 25, 40, "C-01-01"),   # 预警
    ("P-010", 40, 35, "C-01-02"),
    ("P-011", 10, 15, "C-02-01"),   # 预警
    ("P-012", 18, 15, "C-02-02"),
    ("P-013", 60, 25, "D-01-01"),
    ("P-014", 15, 20, "D-01-02"),   # 预警
    ("P-015", 30, 25, "C-01-03"),
]

# (order_number, status, remark, [(part_code, qty, unit_price)])
ORDERS = [
    ("PO20260101001", 4, "月度常规补货", [("P-001", 200, 12.50), ("P-002", 150, 8.00)]),
    ("PO20260115002", 2, "制动系统备货", [("P-004", 100, 22.00), ("P-006", 50, 35.00)]),
    ("PO20260201003", 0, "待审批：悬挂件采购", [("P-011", 20, 210.00)]),
]


def init_db() -> None:
    """建表并在空库时灌入示例数据。"""
    engine = get_engine()
    with engine.begin() as conn:
        for stmt in [s.strip() for s in SCHEMA_SQL.split(";") if s.strip()]:
            conn.execute(text(stmt))

        existing = conn.execute(text("SELECT COUNT(*) FROM suppliers")).scalar_one()
        if existing:
            return

        # 供应商
        supplier_id_by_code: dict[str, int] = {}
        for code, name, cp, phone, email, addr, credit, status in SUPPLIERS:
            sid = conn.execute(
                text(
                    "INSERT INTO suppliers (supplier_code,name,contact_person,phone,email,address,credit_rating,status)"
                    " VALUES (:c,:n,:cp,:ph,:em,:ad,:cr,:st) RETURNING id"
                ),
                {"c": code, "n": name, "cp": cp, "ph": phone, "em": email,
                 "ad": addr, "cr": credit, "st": status},
            ).scalar_one()
            supplier_id_by_code[code] = sid

        # 零部件
        part_id_by_code: dict[str, int] = {}
        for (code, name, model, spec, unit, purchase, retail, warning,
             sup_code, category, desc) in PARTS:
            pid = conn.execute(
                text(
                    "INSERT INTO parts (part_code,name,model,specification,unit,purchase_price,"
                    "suggested_retail_price,stock_warning_value,supplier_id,category,description)"
                    " VALUES (:c,:n,:m,:s,:u,:pp,:rp,:w,:sid,:cat,:d) RETURNING id"
                ),
                {"c": code, "n": name, "m": model, "s": spec, "u": unit,
                 "pp": purchase, "rp": retail, "w": warning,
                 "sid": supplier_id_by_code[sup_code], "cat": category, "d": desc},
            ).scalar_one()
            part_id_by_code[code] = pid

        # 库存
        for code, current, safety, location in INVENTORY:
            conn.execute(
                text(
                    "INSERT INTO inventory (part_id,current_quantity,safety_stock,warehouse_location)"
                    " VALUES (:pid,:cq,:ss,:wl)"
                ),
                {"pid": part_id_by_code[code], "cq": current, "ss": safety, "wl": location},
            )

        # 订单
        for number, status, remark, details in ORDERS:
            total = sum(qty * price for _, qty, price in details)
            oid = conn.execute(
                text(
                    "INSERT INTO orders (order_number,total_amount,status,remark)"
                    " VALUES (:on,:ta,:st,:rm) RETURNING id"
                ),
                {"on": number, "ta": total, "st": status, "rm": remark},
            ).scalar_one()
            for part_code, qty, price in details:
                conn.execute(
                    text(
                        "INSERT INTO order_detail (order_id,part_id,quantity,unit_price)"
                        " VALUES (:oid,:pid,:q,:up)"
                    ),
                    {"oid": oid, "pid": part_id_by_code[part_code], "q": qty, "up": price},
                )
