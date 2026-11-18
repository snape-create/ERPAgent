-- Active: 1794832807115@@127.0.0.1@5432@erp_agent
-- ============================================================
-- ERP 示例数据恢复脚本
--
-- 数据库：PostgreSQL（默认 localhost:5432/erp_agent）
-- 内容：约 104 家供应商、230 个零部件、230 条库存、209 张订单、400+ 条订单明细
--       （前 14/30/30/9 为手工精编数据，其余由 generate_series 批量生成）
--
-- 特点：可重复执行（幂等）
--   - 供应商/零部件按业务编码 upsert
--   - 库存按 part_id upsert
--   - 订单按 order_number upsert
--   - 订单明细先删后插，避免重复
--
-- 用法：
--   docker compose exec -T postgres psql -U postgres -d erp_agent \
--     < src/erp_service/seed_data.sql
-- ============================================================

BEGIN;

-- ============================================================
-- 0. 可选：彻底清空后重灌（会删除现有 ERP 数据）
--    需要完全恢复到示例数据时，取消下面这行注释
-- ============================================================
-- TRUNCATE order_detail, inventory, orders, parts, suppliers RESTART IDENTITY CASCADE;

-- ============================================================
-- 1. 建表（与 src/erp_service/db.py 的 SCHEMA_SQL 保持一致）
-- ============================================================

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

-- ============================================================
-- 2. 供应商（6 家）
-- ============================================================

INSERT INTO suppliers
    (supplier_code, name, contact_person, phone, email, address, credit_rating, status)
VALUES
    ('SUP-001', '博世（中国）汽车部件', '张伟', '021-88880001', 'sales@bosch.cn',
     '上海市浦东新区金桥路1号', 'A', 1),
    ('SUP-002', '电装（中国）投资', '李娜', '021-88880002', 'sales@denso.cn',
     '上海市闵行区申北路2号', 'A', 1),
    ('SUP-003', '杭州万向传动', '王强', '0571-88880003', 'sales@wanxiang.cn',
     '浙江省杭州市萧山区3号', 'B', 1),
    ('SUP-004', '宁波拓普减震', '赵敏', '0574-88880004', 'sales@tuopu.cn',
     '浙江省宁波市北仑区4号', 'B', 1),
    ('SUP-005', '潍坊盛瑞动力', '陈杰', '0536-88880005', 'sales@shengrui.cn',
     '山东省潍坊市高新区5号', 'C', 0),
    ('SUP-006', '上海天合制动', '刘洋', '021-88880006', 'sales@trw.cn',
     '上海市嘉定区6号', 'A', 1)
ON CONFLICT (supplier_code) DO UPDATE SET
    name           = EXCLUDED.name,
    contact_person = EXCLUDED.contact_person,
    phone          = EXCLUDED.phone,
    email          = EXCLUDED.email,
    address        = EXCLUDED.address,
    credit_rating  = EXCLUDED.credit_rating,
    status         = EXCLUDED.status;

-- ============================================================
-- 3. 零部件（15 个）
-- ============================================================

INSERT INTO parts
    (part_code, name, model, specification, unit, purchase_price,
     suggested_retail_price, stock_warning_value, supplier_id, category, description)
SELECT
    v.part_code, v.name, v.model, v.specification, v.unit, v.purchase_price,
    v.suggested_retail_price, v.stock_warning_value, s.id, v.category, v.description
FROM (VALUES
    ('P-001', '铱金火花塞', 'IX-24', 'M10x1.0', '个', 12.50, 25.00, 100, 'SUP-002', '发动机系统', '高原点火稳定，耐久 3 万公里'),
    ('P-002', '空气滤芯', 'AF-110', '通用型', '个', 8.00, 18.00, 80, 'SUP-002', '发动机系统', '高密度滤纸，过滤效率高'),
    ('P-003', '机油滤芯', 'OF-200', '旋装式', '个', 6.50, 15.00, 120, 'SUP-002', '发动机系统', '适配 125cc-250cc 车型'),
    ('P-004', '前制动片', 'BP-F01', '烧结金属', '套', 22.00, 45.00, 60, 'SUP-001', '制动系统', '高温抗衰减，湿态制动稳定'),
    ('P-005', '后制动片', 'BP-R01', '烧结金属', '套', 20.00, 40.00, 60, 'SUP-001', '制动系统', '低噪音配方'),
    ('P-006', '制动油管', 'BH-300', '不锈钢编织', '根', 35.00, 68.00, 30, 'SUP-006', '制动系统', '耐高压，抗老化'),
    ('P-007', '活塞环', 'PR-150', '150cc', '组', 18.00, 36.00, 50, 'SUP-005', '发动机系统', '耐磨合金铸铁'),
    ('P-008', '气缸垫', 'CG-150', '150cc', '片', 9.00, 20.00, 70, 'SUP-005', '发动机系统', '耐高温密封'),
    ('P-009', '驱动链条', 'CH-428', '428H-120L', '条', 28.00, 55.00, 40, 'SUP-003', '传动系统', '高强度合金钢'),
    ('P-010', '链轮', 'SP-428', '14T/38T', '套', 32.00, 60.00, 35, 'SUP-003', '传动系统', '齿面淬火处理'),
    ('P-011', '前减震器', 'SH-F01', '倒置式', '对', 210.00, 380.00, 15, 'SUP-004', '悬挂系统', '可调阻尼'),
    ('P-012', '后减震器', 'SH-R01', '双弹簧', '对', 180.00, 320.00, 15, 'SUP-004', '悬挂系统', '氮气减震'),
    ('P-013', 'LED 前照灯', 'HL-500', '12V 35W', '个', 45.00, 90.00, 25, 'SUP-002', '电气系统', '高亮聚光'),
    ('P-014', '蓄电池', 'BT-7A', '12V 7Ah', '个', 65.00, 120.00, 20, 'SUP-002', '电气系统', '免维护胶体'),
    ('P-015', '离合器片', 'CP-200', '湿式多片', '套', 40.00, 78.00, 25, 'SUP-005', '传动系统', '耐高温摩擦材料')
) AS v(part_code, name, model, specification, unit, purchase_price,
       suggested_retail_price, stock_warning_value, supplier_code, category, description)
JOIN suppliers s ON s.supplier_code = v.supplier_code
ON CONFLICT (part_code) DO UPDATE SET
    name                   = EXCLUDED.name,
    model                  = EXCLUDED.model,
    specification          = EXCLUDED.specification,
    unit                   = EXCLUDED.unit,
    purchase_price         = EXCLUDED.purchase_price,
    suggested_retail_price = EXCLUDED.suggested_retail_price,
    stock_warning_value    = EXCLUDED.stock_warning_value,
    supplier_id            = EXCLUDED.supplier_id,
    category               = EXCLUDED.category,
    description            = EXCLUDED.description;

-- ============================================================
-- 4. 库存（15 条）
-- ============================================================

INSERT INTO inventory
    (part_id, current_quantity, safety_stock, warehouse_location)
SELECT p.id, v.current_quantity, v.safety_stock, v.warehouse_location
FROM (VALUES
    ('P-001', 150, 100, 'A-01-01'),
    ('P-002', 60, 80, 'A-01-02'),   -- 预警
    ('P-003', 300, 120, 'A-01-03'),
    ('P-004', 45, 60, 'B-02-01'),   -- 预警
    ('P-005', 90, 60, 'B-02-02'),
    ('P-006', 12, 30, 'B-02-03'),   -- 预警
    ('P-007', 80, 50, 'A-03-01'),
    ('P-008', 100, 70, 'A-03-02'),
    ('P-009', 25, 40, 'C-01-01'),   -- 预警
    ('P-010', 40, 35, 'C-01-02'),
    ('P-011', 10, 15, 'C-02-01'),   -- 预警
    ('P-012', 18, 15, 'C-02-02'),
    ('P-013', 60, 25, 'D-01-01'),
    ('P-014', 15, 20, 'D-01-02'),   -- 预警
    ('P-015', 30, 25, 'C-01-03')
) AS v(part_code, current_quantity, safety_stock, warehouse_location)
JOIN parts p ON p.part_code = v.part_code
ON CONFLICT (part_id) DO UPDATE SET
    current_quantity   = EXCLUDED.current_quantity,
    safety_stock       = EXCLUDED.safety_stock,
    warehouse_location = EXCLUDED.warehouse_location;

-- ============================================================
-- 5. 订单（3 张）
--    total_amount:
--      PO20260101001 = 200*12.50 + 150*8.00 = 3700.00
--      PO20260115002 = 100*22.00 + 50*35.00 = 3950.00
--      PO20260201003 = 20*210.00            = 4200.00
-- ============================================================

INSERT INTO orders
    (order_number, total_amount, status, remark, order_time)
VALUES
    ('PO20260101001', 3700.00, 4, '月度常规补货', '2026-11-08 10:00:00'),
    ('PO20260115002', 3950.00, 2, '制动系统备货', '2026-11-08 10:00:00'),
    ('PO20260201003', 4200.00, 0, '待审批：悬挂件采购', '2026-11-08 10:00:00')
ON CONFLICT (order_number) DO UPDATE SET
    total_amount = EXCLUDED.total_amount,
    status       = EXCLUDED.status,
    remark       = EXCLUDED.remark,
    order_time   = EXCLUDED.order_time;

-- ============================================================
-- 6. 订单明细（5 条）
--    先删掉这 3 张示例订单的旧明细，避免重复执行产生重复行
-- ============================================================

DELETE FROM order_detail
WHERE order_id IN (
    SELECT id FROM orders
    WHERE order_number IN ('PO20260101001', 'PO20260115002', 'PO20260201003')
);

INSERT INTO order_detail
    (order_id, part_id, quantity, unit_price)
SELECT o.id, p.id, v.quantity, v.unit_price
FROM (VALUES
    ('PO20260101001', 'P-001', 200, 12.50),
    ('PO20260101001', 'P-002', 150, 8.00),
    ('PO20260115002', 'P-004', 100, 22.00),
    ('PO20260115002', 'P-006', 50, 35.00),
    ('PO20260201003', 'P-011', 20, 210.00)
) AS v(order_number, part_code, quantity, unit_price)
JOIN orders o ON o.order_number = v.order_number
JOIN parts p ON p.part_code = v.part_code;

-- ============================================================
-- 7. 额外补充：更多供应商（SUP-007 ~ SUP-014）
-- ============================================================

INSERT INTO suppliers
    (supplier_code, name, contact_person, phone, email, address, credit_rating, status)
VALUES
    ('SUP-007', '华胜机械制造', '周明', '0571-88880007', 'sales@huasheng.cn',
     '杭州市余杭区7号', 'B', 1),
    ('SUP-008', '恒达配件', '孙丽', '0755-88880008', 'sales@hengda.cn',
     '深圳市宝安区8号', 'A', 1),
    ('SUP-009', '金轮零部件', '吴军', '0531-88880009', 'sales@jinlun.cn',
     '济南市历城区9号', 'A', 1),
    ('SUP-010', '远航橡塑', '郑涛', '0371-88880010', 'sales@yuanhang.cn',
     '郑州市中原区10号', 'B', 1),
    ('SUP-011', '中信摩配', '冯艳', '010-88880011', 'sales@zhongxin.cn',
     '北京市大兴区11号', 'A', 1),
    ('SUP-012', '川渝减震', '何伟', '028-88880012', 'sales@chuanyu.cn',
     '成都市双流区12号', 'B', 1),
    ('SUP-013', '雷石制动', '唐敏', '023-88880013', 'sales@leishi.cn',
     '重庆市渝北区13号', 'A', 1),
    ('SUP-014', '安特传动', '蒋波', '020-88880014', 'sales@ante.cn',
     '广州市黄埔区14号', 'A', 1)
ON CONFLICT (supplier_code) DO UPDATE SET
    name           = EXCLUDED.name,
    contact_person = EXCLUDED.contact_person,
    phone          = EXCLUDED.phone,
    email          = EXCLUDED.email,
    address        = EXCLUDED.address,
    credit_rating  = EXCLUDED.credit_rating,
    status         = EXCLUDED.status;

-- ============================================================
-- 8. 额外补充：更多零部件（P-016 ~ P-030）
-- ============================================================

INSERT INTO parts
    (part_code, name, model, specification, unit, purchase_price,
     suggested_retail_price, stock_warning_value, supplier_id, category, description)
SELECT
    v.part_code, v.name, v.model, v.specification, v.unit, v.purchase_price,
    v.suggested_retail_price, v.stock_warning_value, s.id, v.category, v.description
FROM (VALUES
    ('P-016', '点火线圈', 'DG-200', '高能型', '个', 55.00, 110.00, 30, 'SUP-011', '发动机系统', '高能点火，油耗更低'),
    ('P-017', '节气门', 'TB-125', '电子式', '个', 95.00, 190.00, 15, 'SUP-011', '发动机系统', '电子节气门，响应灵敏'),
    ('P-018', '氧传感器', 'OS-01', '前氧', '个', 88.00, 176.00, 20, 'SUP-008', '发动机系统', '闭环控制，空燃比精确'),
    ('P-019', '起动机', 'ST-7', '12V', '台', 160.00, 320.00, 10, 'SUP-008', '发动机系统', '大扭矩启动马达'),
    ('P-020', '曲轴', 'CR-150', '150cc', '根', 240.00, 480.00, 8, 'SUP-009', '发动机系统', '锻钢材质，动平衡精密'),
    ('P-021', '鼓式制动鼓', 'DR-01', '后轮', '个', 120.00, 240.00, 12, 'SUP-013', '制动系统', '高强度铸铁'),
    ('P-022', '制动主缸', 'MC-250', '通用', '个', 75.00, 150.00, 15, 'SUP-013', '制动系统', '铝合金缸体，耐磨损'),
    ('P-023', '刹车盘', 'RD-200', '前轮通风盘', '片', 68.00, 136.00, 20, 'SUP-013', '制动系统', '通风散热，抗热衰减'),
    ('P-024', '传动轴', 'AX-01', '后轮', '根', 140.00, 280.00, 8, 'SUP-014', '传动系统', '高强度合金轴'),
    ('P-025', '万向节', 'UV-01', '十字轴', '个', 45.00, 90.00, 25, 'SUP-014', '传动系统', '耐磨十字轴承'),
    ('P-026', '后桥齿轮', 'GR-01', '40:8', '套', 130.00, 260.00, 10, 'SUP-009', '传动系统', '渗碳淬火齿轮组'),
    ('P-027', '前叉油封', 'OS-33', '33mm', '个', 22.00, 44.00, 40, 'SUP-012', '悬挂系统', '耐油耐压密封圈'),
    ('P-028', '减震弹簧', 'SP-44', '渐进式', '根', 35.00, 70.00, 30, 'SUP-012', '悬挂系统', '渐进式刚度，舒适耐用'),
    ('P-029', '转向灯', 'TL-12', '12V', '个', 15.00, 30.00, 50, 'SUP-007', '电气系统', '高亮 LED 灯珠'),
    ('P-030', '启动继电器', 'RL-01', '12V 30A', '个', 18.00, 36.00, 60, 'SUP-007', '电气系统', '大电流继电器')
) AS v(part_code, name, model, specification, unit, purchase_price,
       suggested_retail_price, stock_warning_value, supplier_code, category, description)
JOIN suppliers s ON s.supplier_code = v.supplier_code
ON CONFLICT (part_code) DO UPDATE SET
    name                   = EXCLUDED.name,
    model                  = EXCLUDED.model,
    specification          = EXCLUDED.specification,
    unit                   = EXCLUDED.unit,
    purchase_price         = EXCLUDED.purchase_price,
    suggested_retail_price = EXCLUDED.suggested_retail_price,
    stock_warning_value    = EXCLUDED.stock_warning_value,
    supplier_id            = EXCLUDED.supplier_id,
    category               = EXCLUDED.category,
    description            = EXCLUDED.description;

-- ============================================================
-- 9. 额外补充：更多库存（P-016 ~ P-030）
-- ============================================================

INSERT INTO inventory
    (part_id, current_quantity, safety_stock, warehouse_location)
SELECT p.id, v.current_quantity, v.safety_stock, v.warehouse_location
FROM (VALUES
    ('P-016', 45, 30, 'A-04-01'),
    ('P-017', 8, 15, 'B-05-01'),    -- 预警
    ('P-018', 25, 20, 'B-05-02'),
    ('P-019', 6, 10, 'B-05-03'),    -- 预警
    ('P-020', 12, 8, 'A-04-02'),
    ('P-021', 10, 12, 'B-06-01'),   -- 预警
    ('P-022', 20, 15, 'B-06-02'),
    ('P-023', 30, 20, 'B-06-03'),
    ('P-024', 6, 8, 'C-04-01'),     -- 预警
    ('P-025', 40, 25, 'C-04-02'),
    ('P-026', 9, 10, 'C-04-03'),    -- 预警
    ('P-027', 60, 40, 'C-05-01'),
    ('P-028', 25, 30, 'C-05-02'),   -- 预警
    ('P-029', 80, 50, 'D-02-01'),
    ('P-030', 70, 60, 'D-02-02')
) AS v(part_code, current_quantity, safety_stock, warehouse_location)
JOIN parts p ON p.part_code = v.part_code
ON CONFLICT (part_id) DO UPDATE SET
    current_quantity   = EXCLUDED.current_quantity,
    safety_stock       = EXCLUDED.safety_stock,
    warehouse_location = EXCLUDED.warehouse_location;

-- ============================================================
-- 10. 额外补充：更多订单（PO20260301004 ~ PO20260601009）
-- ============================================================

INSERT INTO orders
    (order_number, total_amount, status, remark, order_time)
VALUES
    ('PO20260301004', 2790.00, 3, '发动机系统采购', '2026-03-01 09:30:00'),
    ('PO20260315005', 4595.00, 1, '制动系统采购', '2026-03-15 14:00:00'),
    ('PO20260402006', 3090.00, 4, '传动系统备货', '2026-04-02 11:00:00'),
    ('PO20260420007', 2940.00, 2, '电气系统采购', '2026-04-20 16:00:00'),
    ('PO20260505008', 2720.00, 0, '待审核：悬挂件补货', '2026-05-05 10:00:00'),
    ('PO20260601009', 3920.00, 1, '发动机系统采购（点火/传感器）', '2026-06-01 09:00:00')
ON CONFLICT (order_number) DO UPDATE SET
    total_amount = EXCLUDED.total_amount,
    status       = EXCLUDED.status,
    remark       = EXCLUDED.remark,
    order_time   = EXCLUDED.order_time;

-- ============================================================
-- 11. 额外补充：更多订单明细
-- ============================================================

DELETE FROM order_detail
WHERE order_id IN (
    SELECT id FROM orders
    WHERE order_number IN (
        'PO20260301004', 'PO20260315005', 'PO20260402006',
        'PO20260420007', 'PO20260505008', 'PO20260601009'
    )
);

INSERT INTO order_detail
    (order_id, part_id, quantity, unit_price)
SELECT o.id, p.id, v.quantity, v.unit_price
FROM (VALUES
    ('PO20260301004', 'P-016', 30, 55.00),
    ('PO20260301004', 'P-017', 12, 95.00),
    ('PO20260315005', 'P-023', 40, 68.00),
    ('PO20260315005', 'P-022', 25, 75.00),
    ('PO20260402006', 'P-025', 50, 45.00),
    ('PO20260402006', 'P-009', 30, 28.00),
    ('PO20260420007', 'P-029', 100, 15.00),
    ('PO20260420007', 'P-030', 80, 18.00),
    ('PO20260505008', 'P-028', 40, 35.00),
    ('PO20260505008', 'P-027', 60, 22.00),
    ('PO20260601009', 'P-018', 30, 88.00),
    ('PO20260601009', 'P-019', 8, 160.00)
) AS v(order_number, part_code, quantity, unit_price)
JOIN orders o ON o.order_number = v.order_number
JOIN parts p ON p.part_code = v.part_code;

-- ============================================================
-- 12. 批量生成：更多供应商（SUP-015 ~ SUP-104）
--      总共约 104 家，保证“上百”
-- ============================================================

INSERT INTO suppliers
    (supplier_code, name, contact_person, phone, email, address, credit_rating, status)
SELECT
    'SUP-' || lpad(n::text, 3, '0'),
    '示例供应商' || n,
    '联系人' || n,
    '0' || n || '-8888' || lpad(((n * 37) % 10000)::text, 4, '0'),
    'sales' || n || '@example.cn',
    '示例城市' || ((n % 20) + 1) || '路' || n || '号',
    CASE (n % 3) WHEN 0 THEN 'A' WHEN 1 THEN 'B' ELSE 'C' END,
    CASE WHEN n % 10 = 0 THEN 0 ELSE 1 END
FROM generate_series(15, 104) n
ON CONFLICT (supplier_code) DO UPDATE SET
    name           = EXCLUDED.name,
    contact_person = EXCLUDED.contact_person,
    credit_rating  = EXCLUDED.credit_rating,
    status         = EXCLUDED.status;

-- ============================================================
-- 13. 批量生成：更多零部件（P-031 ~ P-230，共 200 个）
--      加上前面的 30 个，总共 230 个
-- ============================================================

INSERT INTO parts
    (part_code, name, model, specification, unit, purchase_price,
     suggested_retail_price, stock_warning_value, supplier_id, category, description)
SELECT
    'P-' || lpad(n::text, 3, '0'),
    (ARRAY['发动机系统', '制动系统', '传动系统', '悬挂系统', '电气系统'])[1 + (n % 5)] || '件' || n,
    'MOD-' || n,
    'SPEC-' || n,
    '件',
    round((10 + (n % 90))::numeric + ((n * 13) % 100)::numeric / 100, 2),
    round(((10 + (n % 90))::numeric + ((n * 13) % 100)::numeric / 100) * 1.9, 2),
    10 + (n % 50),
    (SELECT id FROM suppliers
      ORDER BY id
      LIMIT 1 OFFSET ((n * 7) % (SELECT count(*)::int FROM suppliers))),
    (ARRAY['发动机系统', '制动系统', '传动系统', '悬挂系统', '电气系统'])[1 + (n % 5)],
    '批量示例件 ' || n
FROM generate_series(31, 230) n
ON CONFLICT (part_code) DO UPDATE SET
    name                   = EXCLUDED.name,
    model                  = EXCLUDED.model,
    specification          = EXCLUDED.specification,
    unit                   = EXCLUDED.unit,
    purchase_price         = EXCLUDED.purchase_price,
    suggested_retail_price = EXCLUDED.suggested_retail_price,
    stock_warning_value    = EXCLUDED.stock_warning_value,
    supplier_id            = EXCLUDED.supplier_id,
    category               = EXCLUDED.category,
    description            = EXCLUDED.description;

-- ============================================================
-- 14. 批量生成：更多库存（P-031 ~ P-230，共 200 条）
--      加上前面的 30 条，总共 230 条
-- ============================================================

INSERT INTO inventory
    (part_id, current_quantity, safety_stock, warehouse_location)
SELECT p.id, (20 + ((n * 37) % 200)), (15 + ((n * 13) % 80)),
       'WH-' || lpad(((n * 7) % 60 + 1)::text, 2, '0')
FROM generate_series(31, 230) n
JOIN parts p ON p.part_code = 'P-' || lpad(n::text, 3, '0')
ON CONFLICT (part_id) DO UPDATE SET
    current_quantity   = EXCLUDED.current_quantity,
    safety_stock       = EXCLUDED.safety_stock,
    warehouse_location = EXCLUDED.warehouse_location;

-- ============================================================
-- 15. 批量生成：更多订单（200 张，PO2026 日期+序号）
--      订单时间分散在 2026 年，便于做趋势分析；金额先用 0，稍后按明细回填
-- ============================================================

INSERT INTO orders
    (order_number, total_amount, status, remark, order_time)
SELECT
    'PO' || to_char(date '2026-01-01' + g, 'YYYYMMDD') || lpad(g::text, 4, '0'),
    0,
    (g % 5),
    '批量采购订单' || g,
    date '2026-01-01' + g
FROM generate_series(10, 209) g
ON CONFLICT (order_number) DO UPDATE SET
    status     = EXCLUDED.status,
    remark     = EXCLUDED.remark,
    order_time = EXCLUDED.order_time;

-- ============================================================
-- 16. 批量生成：更多订单明细（每张订单 2 行，共 400 行）
--      先删掉这批批量订单的旧明细，避免重复执行产生重复行
-- ============================================================

DELETE FROM order_detail
WHERE order_id IN (SELECT id FROM orders WHERE remark LIKE '批量采购订单%');

INSERT INTO order_detail
    (order_id, part_id, quantity, unit_price)
SELECT o.id, p.id, det.qty, round(p.purchase_price * 1.3, 2)
FROM generate_series(10, 209) g
CROSS JOIN LATERAL (
    VALUES
        ('P-' || lpad(((g * 5) % 230 + 1)::text, 3, '0'), 1 + (g % 4)),
        ('P-' || lpad(((g * 7) % 230 + 1)::text, 3, '0'), 1 + (g % 3))
) AS det(part_code, qty)
JOIN orders o ON o.order_number =
    'PO' || to_char(date '2026-01-01' + g, 'YYYYMMDD') || lpad(g::text, 4, '0')
JOIN parts p ON p.part_code = det.part_code;

-- ============================================================
-- 17. 统一回填订单金额（按订单明细重算，幂等）
-- ============================================================

UPDATE orders o
SET total_amount = COALESCE(
    (SELECT sum(d.quantity * d.unit_price)
     FROM order_detail d
     WHERE d.order_id = o.id), 0);

COMMIT;
