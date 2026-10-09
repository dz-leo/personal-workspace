-- 个人账本工作台 — MySQL 初始化（v2：资金账户 + 三通道分类）
--
-- ======================= 危险操作警告（务必先读） =======================
-- 本脚本会执行 DROP TABLE 删除并重建所有表，库内「现有数据将全部丢失」！
-- 执行前必须先备份（任选其一）：
--   1) mysqldump -uroot -p ledger > ~/ledger-backup-$(date +%F).sql   （回车后输密码）
--   2) 双击 backup.command
--   3) 应用内 右上角「数据」→ 导出 JSON
-- 目标库由命令行给出，本文件不再写死USE（否则 mysql -D 也会被它带跑，导错库）：
--   mysql -uroot -p ledger < ledger-init.sql        （回车后输密码）
--   mysql -uroot -p ledger_test < ledger-init.sql   （建测试库，别碰真库）
-- 账号口令也记在 ~/.ledger.conf 里，见 server.py --init-conf
-- ==========================================================================
--
-- 说明: 建表 + 建 7 个空账户（初始余额均为 0）+ 三通道分类，不含任何示例数据。
--
-- v2 变更：
--   1. accounts 由「名称清单」升级为完整资金账户（初始余额 / 额度 / 账单日 / 计入总资产 / 归档）
--   2. records.account / to_account 存账户 id；转账类记录用两端账户表达资金流动
--   3. categories 改为三通道 32 项（expense 20 / income 9 / transfer 3 + 余额调整）
--   4. 账户余额不落库，由 初始余额 + 收入 - 支出 - 转出 + 转入 实时派生，永不漂移

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS records, notes, categories, accounts, tags, scenes, settings, meta;

-- ============ 配置表 ============
CREATE TABLE categories (
  id      VARCHAR(32)  NOT NULL,
  type    ENUM('expense','income','transfer') NOT NULL,
  name    VARCHAR(32)  NOT NULL,
  color   VARCHAR(16),
  icon    VARCHAR(32),
  subs    JSON,
  custom  TINYINT NOT NULL DEFAULT 0,
  sort    INT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  KEY idx_type (type, sort)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 资金账户：余额 = init_balance + 入账 - 支出 - 转出 + 转入（实时算，不存快照）
-- 信用类账户 init_balance 用负数表示欠款
CREATE TABLE accounts (
  id           VARCHAR(32) NOT NULL,
  name         VARCHAR(32) NOT NULL,
  kind         ENUM('cash','debit','credit','wallet','invest','other') NOT NULL DEFAULT 'other',
  icon         VARCHAR(32),
  color        VARCHAR(16),
  init_balance DECIMAL(14,2) NOT NULL DEFAULT 0,
  credit_limit DECIMAL(14,2) NOT NULL DEFAULT 0,
  bill_day     TINYINT NOT NULL DEFAULT 0,
  repay_day    TINYINT NOT NULL DEFAULT 0,
  card_tail    VARCHAR(8)  NOT NULL DEFAULT '',
  in_total     TINYINT NOT NULL DEFAULT 1,
  archived     TINYINT NOT NULL DEFAULT 0,
  sort         INT NOT NULL DEFAULT 0,
  note         VARCHAR(128) NOT NULL DEFAULT '',
  PRIMARY KEY (id),
  UNIQUE KEY uk_name (name),
  KEY idx_sort (archived, sort)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE tags (
  id    INT AUTO_INCREMENT,
  name  VARCHAR(32) NOT NULL,
  sort  INT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE scenes (
  id    INT AUTO_INCREMENT,
  name  VARCHAR(32) NOT NULL,
  sort  INT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  UNIQUE KEY uk_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE settings (
  k   VARCHAR(32) NOT NULL,
  v   JSON,
  PRIMARY KEY (k)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE meta (
  k   VARCHAR(32) NOT NULL,
  v   VARCHAR(64),
  PRIMARY KEY (k)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============ 业务表 ============
-- type=transfer 为「不计入收支」：资金在账户之间流动（还款 / 理财 / 余额调整）
--   account    = 转出账户（为空表示资金从账本外部流入）
--   to_account = 转入账户（为空表示资金流出账本外部）
CREATE TABLE records (
  id           VARCHAR(32)  NOT NULL,
  date         DATE         NOT NULL,
  time         VARCHAR(8)   NOT NULL DEFAULT '',
  type         ENUM('expense','income','transfer') NOT NULL,
  amount       DECIMAL(14,2) NOT NULL,
  category     VARCHAR(32)  NOT NULL,
  sub_category VARCHAR(32)  NOT NULL DEFAULT '',
  account      VARCHAR(32)  NOT NULL DEFAULT '',
  to_account   VARCHAR(32)  NOT NULL DEFAULT '',
  merchant     VARCHAR(64)  NOT NULL DEFAULT '',
  tags         JSON,
  scene        VARCHAR(32)  NOT NULL DEFAULT '',
  is_fixed     TINYINT NOT NULL DEFAULT 0,
  note         VARCHAR(255) NOT NULL DEFAULT '',
  demo         TINYINT NOT NULL DEFAULT 0,
  created_at   BIGINT,
  updated_at   BIGINT,
  deleted      TINYINT NOT NULL DEFAULT 0,
  PRIMARY KEY (id),
  KEY idx_date (date),
  KEY idx_category (category),
  KEY idx_account (account),
  KEY idx_to_account (to_account)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE notes (
  id         INT AUTO_INCREMENT,
  scope      VARCHAR(16) NOT NULL,
  period_key VARCHAR(32) NOT NULL,
  content    TEXT,
  demo       TINYINT NOT NULL DEFAULT 0,
  created_at BIGINT,
  updated_at BIGINT,
  PRIMARY KEY (id),
  UNIQUE KEY uk_scope_key (scope, period_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

SET FOREIGN_KEY_CHECKS = 1;

-- ============ 分类：三通道，与 index.html 的 DEFAULT_CATEGORIES 逐项对齐 ============
INSERT INTO categories (id, type, name, color, icon, subs, sort) VALUES
-- 支出 20 项
('food',        'expense', '餐饮',     '#C25B54', 'food',        '["早餐","午餐","晚餐","外卖","聚餐","零食饮料"]', 1),
('transport',   'expense', '交通',     '#4A7BA7', 'transport',   '["地铁","公交","打车","加油","停车","高铁机票"]', 2),
('apparel',     'expense', '服饰',     '#B0729B', 'apparel',     '["衣服","鞋帽","配饰"]', 3),
('shopping',    'expense', '购物',     '#C98A2E', 'shopping',    '["日用","数码","美妆","家居"]', 4),
('service',     'expense', '服务',     '#6B8FA1', 'service',     '["维修","家政","理发","其他服务"]', 5),
('study',       'expense', '教育',     '#6B8E6B', 'study',       '["书籍","课程","考试","文具"]', 6),
('fun',         'expense', '娱乐',     '#9B6BB5', 'fun',         '["电影","游戏","演出","会员订阅"]', 7),
('sport',       'expense', '运动',     '#4E8C8C', 'sport',       '["健身","球类","装备"]', 8),
('bills',       'expense', '生活缴费', '#7B8FA1', 'bills',       '["房租","水电","物业","宽带话费"]', 9),
('travel',      'expense', '旅行',     '#5B9B9B', 'travel',      '["机票","住宿","门票","当地消费"]', 10),
('pet',         'expense', '宠物',     '#C2705B', 'pet',         '["粮食","医疗","用品"]', 11),
('medical',     'expense', '医疗',     '#5B9B9B', 'medical',     '["门诊","药品","体检"]', 12),
('insurance',   'expense', '保险',     '#7A8CA0', 'insurance',   '["车险","寿险","医疗险","其他"]', 13),
('charity',     'expense', '公益',     '#C25B7E', 'charity',     '["捐款","公益"]', 14),
('redpacket',   'expense', '发红包',   '#C25B54', 'redpacket',   '["节日","喜事"]', 15),
('transfer',    'expense', '转账',     '#8A94A0', 'transfer',    '["还给朋友","代付","其他"]', 16),
('relative',    'expense', '亲属卡',   '#9B7BA5', 'relative',    '["亲属卡"]', 17),
('social',      'expense', '其他人情', '#C2705B', 'social',      '["送礼","请客","份子"]', 18),
('refund',      'expense', '退还',     '#6B8FA1', 'refund',      '["退货退款"]', 19),
('other',       'expense', '其他',     '#8A94A0', 'other',       '[]', 20),
-- 入账 9 项
('business',    'income',  '生意',     '#3F8A6B', 'business',    '["营业收入","货款"]', 1),
('salary',      'income',  '工资',     '#3F8A6B', 'salary',      '["月薪","补贴"]', 2),
('bonus',       'income',  '奖金',     '#5B9B7B', 'bonus',       '["年终奖","绩效","项目奖"]', 3),
('socialIn',    'income',  '其他人情', '#C2705B', 'social',      '["收礼","份子"]', 4),
('redpacketIn', 'income',  '收红包',   '#C2705B', 'redpacket',   '["节日","喜事"]', 5),
('transferIn',  'income',  '收转账',   '#4A7BA7', 'transfer',    '["收转账"]', 6),
('merchant',    'income',  '商家转账', '#6B8FA1', 'merchant',    '["商家转账"]', 7),
('refundIn',    'income',  '退款',     '#4E8C8C', 'refund',      '["退款"]', 8),
('otherIncome', 'income',  '其他',     '#8A94A0', 'otherIncome', '[]', 9),
-- 不计入收支 4 项（含余额调整）
('finance',       'transfer', '理财',     '#7B8FA1', 'invest',   '["基金","股票","余额宝"]', 1),
('loan',          'transfer', '借还款',   '#8A6F5C', 'debt',     '["房贷","车贷","网贷","信用卡","借款"]', 2),
('adjust',        'transfer', '余额调整', '#C98A2E', 'balance',  '["对账差额"]', 3),
('otherTransfer', 'transfer', '其他',     '#8A94A0', 'other',    '[]', 4);

-- ============ 资金账户：6 个默认 + 1 个理财账户 ============
INSERT INTO accounts (id, name, kind, icon, color, init_balance, credit_limit, bill_day, repay_day, card_tail, in_total, archived, sort, note) VALUES
-- 初始余额一律为 0：建库后请到「资产」页用「对账」填入各账户真实余额
('wechat', '微信',   'wallet', 'wallet',  '#3F8A6B',     0.00,     0.00,  0,  0, '',     1, 0, 1, ''),
('alipay', '支付宝', 'wallet', 'wallet',  '#4A7BA7',     0.00,     0.00,  0,  0, '',     1, 0, 2, ''),
('cash',   '现金',   'cash',   'cash',    '#C98A2E',     0.00,     0.00,  0,  0, '',     1, 0, 3, ''),
('debit',  '银行卡', 'debit',  'bank',    '#3B6E97',     0.00,     0.00,  0,  0, '',     1, 0, 4, ''),
('credit', '信用卡', 'credit', 'creditcard','#C25B54',   0.00,     0.00,  0,  0, '',     1, 0, 5, ''),
('yuebao', '余额宝', 'invest', 'invest',  '#4E8C8C',     0.00,     0.00,  0,  0, '',     1, 0, 6, ''),
('other',  '其他',   'other',  'other',   '#8A94A0',     0.00,     0.00,  0,  0, '',     1, 0, 7, '');

INSERT INTO tags (name, sort) VALUES ('必要',1),('非必要',2),('冲动',3),('家庭',4),('工作',5),('人情',6);
INSERT INTO scenes (name, sort) VALUES ('日常',1),('装修',2),('旅行',3),('育儿',4),('副业',5);

INSERT INTO settings (k, v) VALUES
  ('budget',          '{"enabled":false,"amount":0}'),
  ('monthStartDay',   '1'),
  ('ignoredMissDays', '[]');

INSERT INTO meta (k, v) VALUES
  ('version', '2'),
  ('seeded',  '1'),
  ('isDemo',  '0');   -- 0=真实账本；保持 seeded=1，否则前端会重新灌示例数据

-- ============ 账户余额自检（派生计算，不落库） ============
SELECT
  a.name                                   AS 账户,
  a.init_balance                           AS 初始余额,
  ROUND(COALESCE(t.inflow,0),2)             AS 累计流入,
  ROUND(COALESCE(t.outflow,0),2)            AS 累计流出,
  ROUND(a.init_balance + COALESCE(t.inflow,0) - COALESCE(t.outflow,0), 2) AS 当前余额
FROM accounts a
LEFT JOIN (
  SELECT acc AS id, SUM(inflow) AS inflow, SUM(outflow) AS outflow FROM (
    SELECT account AS acc,
           SUM(CASE WHEN type='income' THEN amount ELSE 0 END) AS inflow,
           SUM(CASE WHEN type IN ('expense','transfer') THEN amount ELSE 0 END) AS outflow
      FROM records WHERE deleted=0 AND account<>'' GROUP BY account
    UNION ALL
    SELECT to_account AS acc, SUM(amount) AS inflow, 0 AS outflow
      FROM records WHERE deleted=0 AND type='transfer' AND to_account<>'' GROUP BY to_account
  ) u GROUP BY acc
) t ON t.id = a.id
WHERE a.archived = 0
ORDER BY a.sort;

SELECT 'init done (v2 · 资金账户)' AS result;
