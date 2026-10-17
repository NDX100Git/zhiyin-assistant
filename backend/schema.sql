-- 智印助手 · 建表脚本（MySQL 8 / InnoDB / utf8mb4）
-- 与 app/models/*.py 的 SQLAlchemy 模型保持一致。
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
