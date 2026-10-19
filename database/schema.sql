-- 智印助手 · 建表脚本（MySQL 8 / InnoDB / utf8mb4）
-- 与 app/models/*.py 的 SQLAlchemy 模型保持一致；结构与 backend/schema.sql 相同，
-- 另附初始数据（时段与欢迎公告），重复执行不会重复插入。
-- 两种建表方式等价，任选其一：
--   1) 启动服务时自动建表（.env 中 AUTO_CREATE_TABLES=true）
--   2) 手工执行本脚本：mysql -uroot -p < schema.sql
-- 金额一律为「分」的整数列，禁止浮点数。

CREATE DATABASE IF NOT EXISTS `zhiyin`
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE `zhiyin`;

-- 用户：V1 只用设备标识 / 取件码作为身份标识，不收集学号、年级等敏感信息
CREATE TABLE IF NOT EXISTS `users` (
  `id`              BIGINT       NOT NULL AUTO_INCREMENT,
  `openid`          VARCHAR(64)  DEFAULT NULL COMMENT '微信 openid；APP 端为设备标识',
  `nickname`        VARCHAR(64)  DEFAULT NULL COMMENT '昵称，非必填真实姓名',
  `phone_tail`      VARCHAR(4)   DEFAULT NULL COMMENT '手机号后四位，可选',
  `noshow_count`    INT          NOT NULL DEFAULT 0 COMMENT '未履约次数',
  `auth_provider`   VARCHAR(16)  NOT NULL DEFAULT 'device' COMMENT '预留：device/wechat/cas',
  `student_no_hash` VARCHAR(64)  DEFAULT NULL COMMENT '预留：学号哈希，V1 不采集',
  `created_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_users_openid` (`openid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户表';

-- 订单：核心表，状态机见交接文档 3.1
CREATE TABLE IF NOT EXISTS `orders` (
  `id`             BIGINT       NOT NULL AUTO_INCREMENT,
  `order_no`       VARCHAR(16)  NOT NULL COMMENT '如 261016-007，服务端生成，含年份',
  `pickup_code`    VARCHAR(20)  NOT NULL COMMENT '如 261017-A-01，日期前缀加当日顺序',
  `user_id`        BIGINT       NOT NULL,
  `status`         VARCHAR(16)  NOT NULL COMMENT 'pending_pay/queuing/printing/ready/completed/cancelled/abnormal',
  `file_key`       VARCHAR(255) DEFAULT NULL COMMENT '对象存储路径',
  `file_name`      VARCHAR(255) DEFAULT NULL,
  `page_count`     INT          DEFAULT NULL,
  `print_side`     TINYINT      DEFAULT NULL COMMENT '1=单面 2=双面',
  `color_mode`     TINYINT      DEFAULT NULL COMMENT '0=黑白 1=彩色',
  `copies`         INT          NOT NULL DEFAULT 1,
  `paper_size`     VARCHAR(8)   NOT NULL DEFAULT 'A4',
  `amount_cents`   INT          NOT NULL DEFAULT 0 COMMENT '金额，单位：分',
  `paid`           TINYINT      NOT NULL DEFAULT 0,
  `pay_method`     VARCHAR(16)  DEFAULT NULL COMMENT 'mock / onsite',
  `paid_at`        DATETIME     DEFAULT NULL,
  `created_at`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `requeue_at`     DATETIME     DEFAULT NULL COMMENT '换文件后重新排队时间，为空时按创建时间排队',
  `file_changed_at` DATETIME    DEFAULT NULL COMMENT '当前文件最近一次被替换的时间',
  `files_seen_at`  DATETIME     DEFAULT NULL COMMENT '老师确认看到替换后文件的时间',
  `printed_at`     DATETIME     DEFAULT NULL,
  `picked_at`      DATETIME     DEFAULT NULL,
  `remark`         VARCHAR(255) DEFAULT NULL,
  `cancel_reason`  VARCHAR(255) DEFAULT NULL,
  `site_id`        INT          NOT NULL DEFAULT 1 COMMENT '预留：多文印点',
  `pay_channel`    VARCHAR(16)  DEFAULT NULL COMMENT '预留：mock/wechat/card',
  `external_pay_id` VARCHAR(64) DEFAULT NULL COMMENT '预留：第三方支付流水号',
  `printer_id`     INT          DEFAULT NULL COMMENT '预留：指定打印机',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_orders_order_no` (`order_no`),
  KEY `idx_status_created` (`status`, `created_at`),
  UNIQUE KEY `uq_pickup_code` (`pickup_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单主表';

-- 订单文件版本：只存元数据，不复制文件本体；一单同一时刻只有一个生效版本
CREATE TABLE IF NOT EXISTS `order_file_versions` (
  `id`           BIGINT       NOT NULL AUTO_INCREMENT,
  `order_id`     BIGINT       NOT NULL,
  `file_index`   INT          NOT NULL DEFAULT 1 COMMENT '文件在订单清单里的序号',
  `version_no`   INT          NOT NULL COMMENT '从 1 递增，每次换文件加一',
  `file_key`     VARCHAR(255) NOT NULL COMMENT '对象存储路径',
  `file_name`    VARCHAR(255) DEFAULT NULL,
  `page_count`   INT          NOT NULL,
  `amount_cents` INT          NOT NULL DEFAULT 0 COMMENT '该版本价格快照，单位：分',
  `file_sha256`  VARCHAR(64)  DEFAULT NULL COMMENT '内容指纹，留给去重与同文件判断',
  `is_current`   TINYINT      NOT NULL DEFAULT 1 COMMENT '一单只能有一个生效版本',
  `operator`     VARCHAR(32)  DEFAULT NULL COMMENT 'student / admin / system',
  `note`         VARCHAR(255) DEFAULT NULL,
  `created_at`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_order_file_version` (`order_id`, `version_no`),
  KEY `idx_file_version_key` (`file_key`),
  KEY `idx_file_version_current` (`order_id`, `is_current`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单文件版本，只存元数据';

-- 换文件变更请求：pending_flag 仅在待确认时为 1，配合唯一索引保证一单一个待确认
CREATE TABLE IF NOT EXISTS `order_file_changes` (
  `id`                 BIGINT       NOT NULL AUTO_INCREMENT,
  `order_id`           BIGINT       NOT NULL,
  `status`             VARCHAR(16)  NOT NULL DEFAULT 'pending' COMMENT 'pending/approved/rejected/cancelled',
  `pending_flag`       TINYINT      DEFAULT NULL COMMENT '待确认时为 1，处理后置 NULL',
  `file_index`         INT          NOT NULL DEFAULT 1 COMMENT '要替换第几份文件',
  `old_file_key`       VARCHAR(255) DEFAULT NULL,
  `old_file_name`      VARCHAR(255) DEFAULT NULL,
  `old_page_count`     INT          DEFAULT NULL COMMENT '订单总页数快照',
  `old_amount_cents`   INT          DEFAULT NULL COMMENT '订单总金额快照，单位：分',
  `old_file_page_count` INT         DEFAULT NULL COMMENT '被替换文件自己的页数',
  `old_file_amount_cents` INT       DEFAULT NULL COMMENT '被替换文件自己的行金额，单位：分',
  `new_file_key`       VARCHAR(255) NOT NULL,
  `new_file_name`      VARCHAR(255) DEFAULT NULL,
  `new_file_page_count` INT         DEFAULT NULL COMMENT '新文件自己的页数',
  `new_page_count`     INT          NOT NULL COMMENT '换完后的订单总页数',
  `new_amount_cents`   INT          NOT NULL COMMENT '系统重算的订单总价，单位：分',
  `new_file_amount_cents` INT       DEFAULT NULL COMMENT '新文件自己的行金额，单位：分',
  `final_amount_cents` INT          DEFAULT NULL COMMENT '老师确认后的最终价，单位：分',
  `reason`             VARCHAR(255) DEFAULT NULL,
  `requested_by`       VARCHAR(32)  DEFAULT NULL,
  `reviewed_by`        VARCHAR(64)  DEFAULT NULL,
  `review_note`        VARCHAR(255) DEFAULT NULL,
  `created_at`         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `reviewed_at`        DATETIME     DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_file_change_order_status` (`order_id`, `status`),
  UNIQUE KEY `uq_file_change_pending` (`order_id`, `pending_flag`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='换文件变更请求';

-- 订单当前文件清单：一单多文件按 file_index 排序；换文件只改对应行
CREATE TABLE IF NOT EXISTS `order_files` (
  `id`           BIGINT       NOT NULL AUTO_INCREMENT,
  `order_id`     BIGINT       NOT NULL,
  `file_index`   INT          NOT NULL COMMENT '从 1 开始，老师在队列里看到的顺序',
  `file_key`     VARCHAR(255) NOT NULL COMMENT '对象存储路径',
  `file_name`    VARCHAR(255) DEFAULT NULL,
  `page_count`   INT          NOT NULL,
  `amount_cents` INT          NOT NULL DEFAULT 0 COMMENT '该文件的行金额快照，单位：分',
  `created_at`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_order_file_index` (`order_id`, `file_index`),
  KEY `idx_order_file_key` (`file_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单当前文件清单';

-- 上传记录：记住文件是谁传的，学生撤回未下单文件时做归属校验
CREATE TABLE IF NOT EXISTS `uploaded_files` (
  `id`           BIGINT       NOT NULL AUTO_INCREMENT,
  `user_id`      BIGINT       NOT NULL,
  `file_key`     VARCHAR(255) NOT NULL,
  `file_name`    VARCHAR(255) DEFAULT NULL,
  `size`         INT          NOT NULL DEFAULT 0,
  `content_type` VARCHAR(128) DEFAULT NULL,
  `created_at`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_uploaded_file_key` (`file_key`),
  KEY `idx_uploaded_file_user` (`user_id`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='上传记录，用于撤回未下单文件';

-- 按日发号器：订单号与取件码都从这一行的 last_seq 出。
-- INSERT ... ON DUPLICATE KEY UPDATE 一句完成原子自增，并发不会重号。
CREATE TABLE IF NOT EXISTS `order_daily_seq` (
  `day`      DATE  NOT NULL,
  `last_seq` INT   NOT NULL DEFAULT 0,
  PRIMARY KEY (`day`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='按日发号器';

-- 收款记录
CREATE TABLE IF NOT EXISTS `payment_logs` (
  `id`           BIGINT      NOT NULL AUTO_INCREMENT,
  `order_id`     BIGINT      NOT NULL,
  `amount_cents` INT         NOT NULL COMMENT '单位：分',
  `method`       VARCHAR(16) DEFAULT NULL COMMENT 'mock / onsite',
  `operator`     VARCHAR(64) DEFAULT NULL,
  `created_at`   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_payment_order` (`order_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='收款记录';

-- 收单时段设置：关闭只影响新订单
CREATE TABLE IF NOT EXISTS `queue_sessions` (
  `id`        BIGINT      NOT NULL AUTO_INCREMENT,
  `date`      DATE        NOT NULL,
  `session`   VARCHAR(8)  NOT NULL COMMENT 'am / pm',
  `is_open`   TINYINT     NOT NULL DEFAULT 1,
  `capacity`  INT         NOT NULL DEFAULT 30,
  `used`      INT         NOT NULL DEFAULT 0,
  `closed_at` DATETIME    DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_date_session` (`date`, `session`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='收单时段设置';

-- 公告
CREATE TABLE IF NOT EXISTS `notices` (
  `id`         BIGINT      NOT NULL AUTO_INCREMENT,
  `content`    TEXT        NOT NULL,
  `created_by` VARCHAR(64) DEFAULT NULL,
  `active`     TINYINT     NOT NULL DEFAULT 1,
  `created_at` DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='公告';

-- 订单操作日志：每次状态变更写入一条
CREATE TABLE IF NOT EXISTS `print_logs` (
  `id`          BIGINT       NOT NULL AUTO_INCREMENT,
  `order_id`    BIGINT       NOT NULL,
  `action`      VARCHAR(32)  NOT NULL COMMENT 'state_change / note / abnormal',
  `from_status` VARCHAR(16)  DEFAULT NULL,
  `to_status`   VARCHAR(16)  DEFAULT NULL,
  `note`        VARCHAR(255) DEFAULT NULL,
  `operator`    VARCHAR(64)  DEFAULT NULL,
  `created_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_print_log_order` (`order_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单操作日志';

-- AI 体检报告缓存：中间结果缓存 7 天后清除
CREATE TABLE IF NOT EXISTS `ai_reports` (
  `id`          BIGINT       NOT NULL AUTO_INCREMENT,
  `file_key`    VARCHAR(255) NOT NULL,
  `report_json` JSON         NOT NULL,
  `created_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_file_key` (`file_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AI 体检报告缓存';

-- 待发消息记录：V1 只记录事件，V2 接入推送时直接补发
CREATE TABLE IF NOT EXISTS `notifications` (
  `id`         BIGINT      NOT NULL AUTO_INCREMENT,
  `user_id`    BIGINT      DEFAULT NULL,
  `order_id`   BIGINT      DEFAULT NULL,
  `event`      VARCHAR(64) NOT NULL,
  `payload`    JSON        DEFAULT NULL,
  `status`     VARCHAR(16) NOT NULL DEFAULT 'pending',
  `created_at` DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `sent_at`    DATETIME    DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_notify_status_created` (`status`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='待发消息记录';


-- ============================================================
-- 初始数据（幂等：重复执行不会重复插入）
-- ============================================================

-- 今天的上午/下午收单时段（不存在才插入）
INSERT INTO `queue_sessions` (`date`, `session`, `is_open`, `capacity`, `used`)
SELECT CURDATE(), 'am', 1, 30, 0 FROM DUAL
WHERE NOT EXISTS (
  SELECT 1 FROM `queue_sessions` WHERE `date` = CURDATE() AND `session` = 'am'
);

INSERT INTO `queue_sessions` (`date`, `session`, `is_open`, `capacity`, `used`)
SELECT CURDATE(), 'pm', 1, 30, 0 FROM DUAL
WHERE NOT EXISTS (
  SELECT 1 FROM `queue_sessions` WHERE `date` = CURDATE() AND `session` = 'pm'
);

-- 欢迎公告
INSERT INTO `notices` (`content`, `created_by`, `active`)
SELECT '欢迎使用智印助手。打印前请确认文件名、页数与份数。', 'system', 1 FROM DUAL
WHERE NOT EXISTS (
  SELECT 1 FROM `notices` WHERE `content` LIKE '欢迎使用智印助手%'
);
