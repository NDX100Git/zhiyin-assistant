# 智印助手 · AI 编程交接文档

> 本文档用于把「智印助手」项目完整交接给另一个 AI 编程助手（如 Cursor / Claude Code / Cline 等）进行实际编码。
> 文档自包含，接收方无需任何前置上下文。

---

# ⚠️ 给 AI 编程助手的执行协议（最高优先级，请严格遵守）

```
本文档是一份「项目规格 + 分阶段任务包」，不是一次性执行清单。

1. 首次阅读后，只做一件事：用 200 字复述你理解到的项目目标、
   最关键的三条约束、以及"可扩展性设计"的含义。
   然后停下来，等待用户确认。

2. 除非用户明确说「执行阶段 N」，否则不要开始写任何代码。

3. 每次只执行一个阶段。完成后停下来，列出本阶段产出的文件清单，
   然后等待用户验证。不要接着做下一个阶段。

4. 不要自作主张实现后续阶段的内容，也不要在当前阶段"顺手把后面的也做了"。

5. 如果用户只说「开始」，默认只执行【阶段 1：项目初始化】。

6. 遇到规格不明确的地方，先提问，不要猜测后直接写代码。

7. 每个阶段结束后，用这个格式汇报：
   - 本阶段创建/修改的文件
   - 关键接口或数据结构
   - 如何验证（启动命令 + 验证步骤）
   - 下一阶段需要注意的事项
```

**如果用户没有粘贴上面这段协议，而是直接把全文丢给你，请仍然按上述规则执行。**

---

## 如何使用本文档

| 步骤 | 做法 |
|---|---|
| 1 | 开启新会话时，**先粘贴【第一部分：上下文包】**（这是精简版，每次新会话都要带） |
| 2 | 然后按【第六部分】的顺序，逐条发送阶段任务提示词 |
| 3 | 每个阶段完成后，按【第七部分】的验收标准检查，通过后再进入下一阶段 |
| 4 | 遇到实现细节问题，回头查【第三、四、五部分】的详细规格 |
| 5 | 写代码前先看【第九部分：可扩展性设计】，确保接口抽象到位 |
| 6 | 用到的开源库见【第十部分】，并注意合规边界 |

**重要**：不要一次性要求 AI 生成整个项目。必须按阶段推进，每阶段可运行后再继续。

---

## 三条最高优先级的原则

### ① 先跑出 V1 初版，再谈演进

本次目标是**14 天内跑出一个功能完整、可演示的版本**，不是做一个生产级系统。

支付、登录、推送这些都用**最简实现**，先把链路跑通。后面根据推进进度再逐个升级。
**不要为了追求"完整性"而卡在某个模块上**——先把东西跑起来，比什么都重要。

### ② 但结构上必须留好扩展空间

V1 的"简化实现"**不等于可以写死**。

支付、登录、通知、打印四个模块必须写成「**抽象接口 + 可切换实现**」，
所有可变参数（价格、格式规则、功能开关）外置到配置文件。
详细要求见 **【第九部分：可扩展性设计】**。

> **判断标准**：V2 要加真实支付时，应该只需「新增一个实现类 + 改一行配置」，
> 而不需要改动任何业务逻辑代码。做不到，就说明抽象层没设计好。

### ③ 合规红线

大赛要求「不得抄袭现成开源项目」。可以用开源库、可以借鉴设计思路，
但**核心业务代码必须自研**。详见 **【第十部分：开源组件与合规说明】**——
该部分内容还需要整理进作品说明书。

---

# 第一部分：上下文包（每次会话开头粘贴这段）

```
【项目名称】智印助手 —— AI 论文格式体检与自助打印平台

【一句话说明】
面向高校文印室的智能化改造：学生上传文档 → AI 做格式与版式体检 → 生成修复建议和
打印方案 → 下单排队 → 文印室打印 → 学生凭取件码取件并结清。

【参赛背景与设计约束】
本项目参加高校 AI 应用创新大赛，评分权重为：校园落地实用性 40 分、AI 技术应用能力
30 分、创新创意 20 分、材料规范 10 分。其中硬性要求是「作品核心功能须依托人工智能
技术实现」。因此 AI 必须是产品的核心引擎，不能是可有可无的装饰功能。

【技术栈（硬性，不要擅自更换）】
- 学生端：uni-app（Vue3 语法），编译目标为 Android APK
- 老师端：Vue3 + Element Plus 的 Web 后台
- 后端：Python 3.11 + FastAPI
- 数据库：MySQL 8（ORM 用 SQLAlchemy）
- 文件存储：本地 MinIO（开发期），接口设计需兼容云对象存储
- 前端实时刷新：定时轮询（3-5 秒），不使用 WebSocket
- AI 模型：DeepSeek V4.1 Flash，model 名写 "deepseek-flash"，
  base_url = "https://api.deepseek.com"，完全兼容 OpenAI SDK
- 文档处理：LibreOffice headless（Word 转 PDF）、PyMuPDF（PDF 解析）、
  pdf2docx（PDF 转 Word）、python-docx（生成/修改 Word）

【三个使用者】
1. 学生（uni-app 端）：上传文件、看体检报告、下单、看排队进度、取件
2. 文印室老师（Web 后台）：接单、打印、标记完成并确认金额、核销取件
3. 平台管理员：本期不做

【必须遵守的 10 条铁律】
1. API Key 只存服务端环境变量，绝对不允许出现在前端代码里
2. 订单号与取件码必须由服务端生成，客户端无权指定
3. 订单状态只能由服务端推进，客户端只读
4. 金额一律用「分」为单位的整数存储与计算，禁止浮点数
5. AI 检查结果只作「建议」，不自动修改用户文档
6. 不收集学号、年级等敏感信息，仅用取件码作为身份标识
7. 一切时间以服务端时间为准，不信任客户端时间
8. 所有接口返回统一格式：{"code": 0, "message": "ok", "data": {...}}
9. 【可演进】支付、登录、通知、打印四个模块必须写成「抽象接口 + 可切换实现」，
   V1 用最简实现，后续替换真实实现时不得改动业务代码
10. 【可演进】所有可变参数（价格、格式规则、功能开关）必须外置到配置文件，
   禁止硬编码在业务逻辑里

【版本演进要求（非常重要，影响代码结构）】
本项目分两阶段交付：
- V1（本次，14 天内完成）：跑通核心链路 + AI 体检能力，支付与登录用简化实现，
  目标是「功能完整、可完整演示」。先把东西跑起来。
- V2（后续，视推进进度）：可能补充真实支付、完整登录、推送通知、多文印点等能力。

因此 V1 的代码必须为 V2 预留扩展点（详见【第九部分：可扩展性设计】）。
判断标准很简单：**任何 V2 功能的加入，都不应该需要改动 V1 的业务逻辑代码，
只需新增一个实现类 + 改一行配置。**

这意味着：不要为了"以后可能用不上"而把结构写死，也不要现在就实现 V2 的完整功能。
正确姿势是「定义接口 → 写一个最简实现 → 业务代码只依赖接口」。
```

---

# 第二部分：项目目标与非目标

## 2.1 要达成的目标

1. 学生能在端上传文档（支持从手机本地、相册、拍照；微信端额外支持从聊天记录选）
2. 上传后 AI 自动完成体检：识别文档结构、检查格式合规性、诊断版式问题
3. 输出可视化体检报告：问题清单（含位置、原文、建议、依据）+ 一键修复选项
4. 根据文档特征生成打印方案（单双面 / 讲义模式 / 缩放），并计算省下的纸张与费用
5. 学生确认后下单，获得订单号与取件码，能在页面上看到实时排队进度
6. 文印室后台能接单、下载文件、标记完成（系统自动预填金额）、核销取件
7. 全流程状态可追踪，学生可查历史订单并一键复打

## 2.2 本期不做，但必须预留扩展点

以下功能 V1 不实现，**但代码结构必须为之留好位置**（详细设计见【第九部分】）：

| 功能 | V1 做法 | V2 可能变成 | 必须预留什么 |
|---|---|---|---|
| 在线支付 | 模拟支付 | 微信支付 / 校园卡 / 预充值 | `PaymentProvider` 抽象接口 |
| 登录 | 设备标识 / 昵称 | 微信登录 / 手机号 / 学校统一认证 | `AuthProvider` 抽象接口 |
| 消息通知 | 前端轮询 | 微信订阅消息 / APP 推送 / 短信 | `Notifier` 抽象接口 |
| 打印执行 | 老师手动点打印 | 云打印 / 打印机 SDK | `PrinterProvider` 抽象接口 |
| 多文印点 | 单点单管理员 | 多校区 / 多文印点 | `orders` 表预留 `site_id` 字段 |
| 文件存储 | 本地 MinIO | 云对象存储 / CDN | 存储层已封装为统一接口 |

**不要做的事**：
- ❌ 不要在 V1 里就把这些功能的完整版本实现出来（时间不够，且分散精力）
- ❌ 也不要因为"反正以后再说"就把结构写死（后面改起来很痛）

**正确做法**：`定义抽象接口 → 写一个最简实现 → 业务代码只依赖接口 → 用配置切换`

这样 V2 要加真实支付时，只需要新增一个 `WechatPayProvider`，改一行配置，**业务代码一行都不用动**。

## 2.3 演示场景（决定开发优先级）

主打链路：**上传一篇含格式错误的毕业论文 → AI 体检出问题 → 展示修复建议 → 生成打印方案 → 下单 → 老师端接单 → 完成核销**

因此 AI 模块是最高优先级，必须真实可用、真实调用模型。

---

# 第三部分：业务规则（必须严格遵守）

## 3.1 订单状态机

```
pending_pay  (待支付)
    ↓ 支付成功 / 挂账确认
queuing      (排队中)
    ↓ 老师点「开始打印」
printing     (打印中)
    ↓ 老师点「打印完成」（此时系统预填金额）
ready        (待取件)
    ↓ 老师核销取件
completed    (已完成)

分支状态：
cancelled    (已取消) —— 仅允许在 pending_pay / queuing 阶段取消
abnormal     (异常)   —— 卡纸、缺墨、文件损坏、学生有异议
```

规则：
- 状态转换必须在服务端校验合法性，非法转换直接拒绝
- 每次状态变更写入 `print_logs` 表，记录操作人、时间、备注
- 客户端接口只能读取状态，不能指定目标状态

## 3.2 排队规则

- 队列不单独建表，直接用 `orders` 表按 `created_at ASC` 排序，`status IN ('queuing','printing')` 过滤
- 订单号格式：`MMDD-序号`，例如 `1016-007`，序号为当日自增（可用数据库自增主键或 Redis INCR 生成，二选一，保持唯一即可）
- 取件码格式：`A-01` 到 `Z-99`，按当日顺序生成，简短易读，便于老师分格摆放
- 预计等待时间 = 队列前序订单的平均耗时（近 20 单滑动平均，按页数加权）× 前序单数
- 无历史数据时，默认每单按 60 秒估算

## 3.3 收单截止规则

- `queue_sessions` 表管理「上午 / 下午」两个时段的收单开关
- 老师可一键关闭当前时段收单
- **关闭只影响新提交的订单，已在队列中的订单不受任何影响**
- 关闭后，学生端首页必须显示「上午已截单，现在下单预计 14:00 后取件」这类提示
- 建议用容量制（如上午最多 30 单）而非时间制

## 3.4 计费规则

- 计费基准来自 `config/pricing.json`（可配置，不写死在代码里）：

```json
{
  "bw_single": 15,
  "bw_duplex": 15,
  "color_single": 80,
  "color_duplex": 80,
  "_unit": "分/页",
  "_note": "以上为演示用模拟价格，实际价格需替换为文印室真实报价"
}
```

- 总价 = 页数 × 单价 × 份数（单位：分）
- 老师点「打印完成」时，系统自动按上述规则算出金额并预填，老师可手动修改（处理特殊情况：加急、装订、特殊纸张）
- 老师确认后金额写入订单，随「已打印好」的状态一起展示给学生

## 3.5 付款与核销规则

- 本期使用**模拟支付**：学生点「我已支付」→ 订单标记 `paid = 1`，写入 `payment_logs`
- 兜底路径：**取件时老师核销即可确认收款**（不付款不给件，从机制上消除欠款）
- 核销 = 老师扫码或输入取件码 → 订单状态变 `completed` → 记录核销时间与操作人
- 不做「超期未付款追踪」模块（金额小，追讨成本高于欠款本身）

## 3.6 隐私与安全规则

- 文件存入私有桶，对外只发放**限时签名 URL**，有效期 30 分钟
- 订单完成 N 小时后（默认 24 小时）由定时任务物理删除原文件
- 不收集学号、年级、身份证等敏感信息
- AI 报告的中间结果缓存 7 天后清除
- 所有文件访问操作写入日志

---

# 第四部分：数据模型与接口规格

## 4.1 数据表

```sql
-- 用户
CREATE TABLE users (
  id            BIGINT PRIMARY KEY AUTO_INCREMENT,
  openid        VARCHAR(64) UNIQUE,      -- 微信 openid，APP 端可为设备标识
  nickname      VARCHAR(64),             -- 昵称，非必填真实姓名
  phone_tail    VARCHAR(4),              -- 手机号后四位，可选
  noshow_count  INT DEFAULT 0,           -- 未履约次数
  created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 订单（核心表）
CREATE TABLE orders (
  id            BIGINT PRIMARY KEY AUTO_INCREMENT,
  order_no      VARCHAR(16) NOT NULL,    -- 如 1016-007
  pickup_code   VARCHAR(8)  NOT NULL,    -- 如 B-12
  user_id       BIGINT NOT NULL,
  status        VARCHAR(16) NOT NULL,    -- 见 3.1 状态机
  file_key      VARCHAR(255),            -- 对象存储路径
  file_name     VARCHAR(255),
  page_count    INT,
  print_side    TINYINT,                 -- 1=单面 2=双面
  color_mode    TINYINT,                 -- 0=黑白 1=彩色
  copies        INT DEFAULT 1,
  paper_size    VARCHAR(8) DEFAULT 'A4',
  amount_cents  INT DEFAULT 0,           -- 金额，单位：分
  paid          TINYINT DEFAULT 0,
  pay_method    VARCHAR(16),             -- mock / onsite
  paid_at       DATETIME,
  created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
  printed_at    DATETIME,
  picked_at     DATETIME,
  remark        VARCHAR(255),
  cancel_reason VARCHAR(255),
  INDEX idx_status_created (status, created_at)
);

-- 收款记录
CREATE TABLE payment_logs (
  id           BIGINT PRIMARY KEY AUTO_INCREMENT,
  order_id     BIGINT NOT NULL,
  amount_cents INT NOT NULL,
  method       VARCHAR(16),              -- mock / onsite
  operator     VARCHAR(64),
  created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 排队时段设置
CREATE TABLE queue_sessions (
  id         BIGINT PRIMARY KEY AUTO_INCREMENT,
  date       DATE NOT NULL,
  session    VARCHAR(8) NOT NULL,        -- am / pm
  is_open    TINYINT DEFAULT 1,
  capacity   INT DEFAULT 30,
  used       INT DEFAULT 0,
  closed_at  DATETIME,
  UNIQUE KEY uk_date_session (date, session)
);

-- 公告
CREATE TABLE notices (
  id         BIGINT PRIMARY KEY AUTO_INCREMENT,
  content    TEXT NOT NULL,
  created_by VARCHAR(64),
  active     TINYINT DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 操作日志
CREATE TABLE print_logs (
  id         BIGINT PRIMARY KEY AUTO_INCREMENT,
  order_id   BIGINT NOT NULL,
  action     VARCHAR(32),                -- state_change / note / abnormal
  from_status VARCHAR(16),
  to_status   VARCHAR(16),
  note       VARCHAR(255),
  operator   VARCHAR(64),
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- AI 体检报告缓存
CREATE TABLE ai_reports (
  id          BIGINT PRIMARY KEY AUTO_INCREMENT,
  file_key    VARCHAR(255) NOT NULL,
  report_json JSON NOT NULL,
  created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_file_key (file_key)
);
```

## 4.2 接口清单

所有接口统一返回：`{"code": 0, "message": "ok", "data": {...}}`
非 0 的 code 表示业务错误，`message` 为可直接展示给用户的中文提示。

### 学生端

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/login` | 登录，入参 `{code}` 或 `{device_id}`，返回用户信息与 token |
| POST | `/api/file/upload` | 上传文件，返回 `file_key` |
| POST | `/api/ai/check` | 入参 `{file_key}`，返回体检报告（见第五部分） |
| POST | `/api/ai/print-plan` | 入参 `{file_key, options}`，返回推荐打印参数与省钱金额 |
| POST | `/api/ai/fix` | 入参 `{file_key, issue_ids}`，返回修复后的新文件 `file_key` |
| POST | `/api/ai/ocr` | 入参 `{file_key}`（图片），返回识别出的文字 |
| POST | `/api/ai/convert` | 入参 `{file_key, target:"docx"}`，返回转换后的 `file_key` |
| POST | `/api/order/create` | 创建订单，返回 `order_no` 与 `pickup_code` |
| GET | `/api/order/list` | 我的订单列表，支持 `status` 筛选 |
| GET | `/api/order/{id}` | 订单详情，含排队位置与预计等待时间 |
| POST | `/api/order/{id}/cancel` | 取消订单（仅 pending_pay / queuing 允许） |
| POST | `/api/order/{id}/pay` | 模拟支付 |
| GET | `/api/home/summary` | 首页数据：等待中单数与预计时间、待取件数、是否可排单、预期时段 |
| GET | `/api/notice/list` | 公告列表 |

### 老师端

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/admin/login` | 管理员登录 |
| GET | `/api/admin/queue` | 接单队列（按订单号排序，可按状态筛选） |
| GET | `/api/admin/order/{id}/download` | 获取限时签名下载链接 |
| POST | `/api/admin/order/{id}/start` | 开始打印（→ printing） |
| POST | `/api/admin/order/{id}/complete` | 打印完成，入参可含金额（不传则以系统计算为准）（→ ready） |
| POST | `/api/admin/order/{id}/verify` | 核销取件（→ completed） |
| POST | `/api/admin/order/batch-complete` | 批量标记完成，入参 `{order_ids:[]}` |
| POST | `/api/admin/order/{id}/abnormal` | 标记异常 |
| POST | `/api/admin/session/close` | 关闭某时段收单，入参 `{date, session}` |
| GET | `/api/admin/summary` | 今日流水：接单数、收款额、待取件数、异常单数 |
| POST | `/api/admin/notice` | 发布公告 |

---

# 第五部分：AI 模块详细设计（核心）

## 5.1 统一调用封装

所有模型调用必须收口到一个模块（如 `services/llm.py`），业务代码不得直接调用 SDK。

```python
# services/llm.py
import os, json
from openai import OpenAI

_client = OpenAI(
    api_key=os.environ["LLM_API_KEY"],
    base_url=os.environ.get("LLM_BASE_URL", "https://api.deepseek.com"),
    timeout=60,
)
MODEL = os.environ.get("LLM_MODEL", "deepseek-flash")

def chat(messages, json_mode=False, thinking=False):
    """统一的模型调用入口。切换供应商只需改环境变量。"""
    kwargs = {"model": MODEL, "messages": messages}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if thinking:
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
    for attempt in range(3):                      # 失败重试
        try:
            resp = _client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content
        except Exception as e:
            if attempt == 2:
                raise
```

要求：
- API Key、base_url、模型名全部从环境变量读取
- 内置重试（最多 3 次），超时 60 秒
- 简单任务（标点、OCR、翻译）不开思考模式；复杂任务（版式诊断、格式校验）开启

## 5.2 格式校验规则集

规则放在 `config/format_rules.json`，**不要在代码里硬编码格式数值**：

```json
{
  "structure": {
    "required": ["摘要", "关键词", "目录", "正文", "参考文献"],
    "order": ["摘要", "关键词", "目录", "正文", "参考文献", "致谢"],
    "_source": "GB/T 7713.1"
  },
  "reference": {
    "standard": "GB/T 7714",
    "checks": [
      "作者超过三人时是否使用「等」",
      "期刊文献是否标注卷(期):页码",
      "专著是否标注出版地:出版者,出版年",
      "DOI 格式是否正确"
    ],
    "_source": "national"
  },
  "page": {
    "margin_top_cm": 2.5,
    "margin_bottom_cm": 2.5,
    "margin_left_cm": 3.0,
    "margin_right_cm": 2.0,
    "_source": "common"
  },
  "typography": {
    "body":     {"font": "宋体", "size_pt": 12, "line_spacing": 1.5},
    "heading1": {"font": "黑体", "size_pt": 16},
    "heading2": {"font": "黑体", "size_pt": 14},
    "heading3": {"font": "楷体", "size_pt": 12},
    "_source": "common"
  }
}
```

**注意**：标 `_source: national` 的条目有国家标准依据；标 `common` 的是高校通行参考值，非强制规定。代码中不要对 `common` 的值做绝对判断，只作为「建议」提示。

## 5.3 文档解析管线

```
上传文件
  ├─ .doc/.docx → LibreOffice headless 转 PDF
  │     soffice --headless --convert-to pdf --outdir /tmp input.docx
  ├─ .pdf       → 直接用
  └─ 图片        → 直接送多模态模型

PDF → PyMuPDF 提取：
  - 页数 page_count
  - 每页文本 text
  - 每页图片数量与位置（用于判断图片是否超框）
  - 页面尺寸与页边距（通过文本块坐标推算）
  - 检测空白页（文本极少且无图片）
```

## 5.4 各 AI 任务的提示词模板

以下提示词是**程序运行时发给 DeepSeek 的**，需按此实现。

### （1）格式合规校验

```
系统提示词：
你是高校学位论文格式校验助手。请严格依据给出的校验规则检查论文，只报告确定存在的问题，
不确定的不要报告。不要修改专业术语、公式、人名。输出必须是合法 JSON。

校验规则：
{rules_json}

论文结构信息：
- 检测到的章节：{sections}
- 参考文献条目：{references}
- 页数：{page_count}

输出格式：
{
  "issues": [
    {
      "id": "r1",
      "type": "reference",
      "severity": "high",
      "location": "参考文献 第 3 条",
      "original": "[1] 张三. 论文题目[J]. 某期刊, 2020, 12(3).",
      "suggestion": "缺少页码，应为 2020, 12(3): 45-52",
      "reason": "GB/T 7714 要求期刊文献标注卷(期):起止页码"
    }
  ],
  "summary": "共发现 5 个问题，其中 2 个为高优先级"
}
```

### （2）版式问题诊断

```
系统提示词：
你是文档排版检查助手。根据给定的页面结构数据，判断是否存在版式问题。
仅报告有明确依据的问题。

页面结构数据：
{layout_json}

检查项：图片是否超出页边距、是否存在异常空白页、页边距是否与规则不符、
目录页码与实际页码是否可能不一致。

输出 JSON：
{
  "issues": [
    {"id":"l1","type":"layout","severity":"medium",
     "location":"第 3 页","original":"图片右边缘超出页边距约 1.2cm",
     "suggestion":"建议缩小图片或调整页边距","reason":"打印时该区域可能被裁切"}
  ]
}
```

### （3）错别字与标点检查（建议模式，极重要）

```
系统提示词：
你是中文校对助手。请找出文本中的错别字和标点问题。

严格要求：
1. 只报告你非常有把握的错误，不确定的绝对不要报告
2. 不要改动专业术语、公式、人名、机构名、外文词汇
3. 不要因为风格偏好而提出修改
4. 每条必须给出原文片段与建议，供人工确认

文本：
{chunk_text}

输出 JSON：
{
  "issues": [
    {"id":"t1","type":"typo","severity":"high",
     "location":"第 2 段","original":"实验结杲表明",
     "suggestion":"实验结果表明","reason":"「杲」应为「果」，形近字错误"}
  ]
}
```

**实现要求**：长文档必须按段落分块（建议每块 2000-3000 字），并发调用后合并结果。每块请求之间用进度回调向前端推送进度。

### （4）打印方案生成

```
系统提示词：
你是打印方案优化助手。根据文档特征给出最经济合理的打印方案。

文档特征：
- 页数：{page_count}
- 类型：{doc_type}   例如 ppt / paper / notes
- 是否含彩色图表：{has_color}
- 图片占比：{image_ratio}

可用方案：单面/双面、讲义模式(2合1 / 4合1)、缩放(100%/85%)

输出 JSON：
{
  "recommend": {"print_side":2,"color_mode":0,"layout":"handout_2in1","scale":100},
  "sheets_needed": 11,
  "original_sheets": 42,
  "saved_cents": 420,
  "reason": "该文档为 PPT 类型共 42 页，采用双面讲义模式 2 合 1，"
            "仅需 11 张纸，比单面打印节省 31 张纸。"
}
```

### （5）图片转文字

```
系统提示词：
请提取图片中的所有文字，保留原有的段落结构与编号顺序，输出纯文本，
不要添加任何解释或总结。

（图片以 image_url 形式随消息一起发送）
```

要求：
- 图片用 base64 内联或 Files API 上传（一张图最多消耗 384 tokens）
- 单张图片 ≤ 32MB，支持 JPEG / PNG / GIF / WebP
- 一次请求最多可携带 600 张图，但实际使用建议 ≤ 5 张，避免响应过慢

## 5.5 文档修复的实现方式（关键）

**AI 只输出结构化的修改指令，真正的文档修改由代码执行，不能让 AI 直接生成文档。**

```python
# AI 返回这样的指令
{
  "actions": [
    {"op": "set_line_spacing", "target": "body", "value": 1.5},
    {"op": "set_font", "target": "body", "value": {"cn": "宋体", "size": 12}},
    {"op": "remove_space", "target": "blank_pages"}
  ]
}

# 代码用 python-docx 执行
from docx import Document

def apply_actions(docx_path, actions):
    doc = Document(docx_path)
    for a in actions:
        if a["op"] == "set_line_spacing":
            for p in doc.paragraphs:
                p.paragraph_format.line_spacing = a["value"]
        elif a["op"] == "set_font":
            # 遍历 runs 设置中英文字体与字号
            ...
    doc.save(docx_path)
    return docx_path
```

## 5.6 PDF 转 Word 的实现

**不要用大模型做 PDF 转 Word**，这是格式转换而非语言任务。使用 `pdf2docx`：

```python
from pdf2docx import Converter

def pdf_to_word(pdf_path, docx_path):
    cv = Converter(pdf_path)
    cv.convert(docx_path)      # 保留表格、图片、版式
    cv.close()
    return docx_path
```

---

# 第六部分：分阶段实现任务（提示词包）

> 按顺序执行。每条提示词前面都要先附上【第一部分：上下文包】。

## 阶段 1：项目初始化

```
请完成项目的初始化工作：

1. 创建 FastAPI 项目，目录结构如下：
   backend/
     app/
       main.py              # FastAPI 入口
       config.py            # 配置读取（从环境变量）
       database.py          # SQLAlchemy 连接
       models/              # 数据模型
       schemas/             # Pydantic 模型
       routers/             # 路由：auth, file, ai, order, admin
       services/            # llm.py, doc_parser.py, format_checker.py
       utils/               # 工具函数
     config/
       format_rules.json
       pricing.json
     .env.example
     requirements.txt
     docker-compose.yml     # MySQL + MinIO + LibreOffice

2. 按【第四部分 4.1】创建全部数据表的 SQLAlchemy 模型与建表脚本
3. 实现统一的接口返回格式与全局异常处理
4. 写一份 README.md，说明如何启动开发环境

完成后请列出创建的文件清单与启动步骤。
```

## 阶段 2：登录与文件上传

```
请实现登录与文件上传。

【重要】登录必须按 Provider 模式实现，为后续接入微信登录 / 手机号 / 学校统一认证预留空间。
不要在业务代码里直接写登录逻辑。

1. 定义抽象接口 AuthProvider（用 Python 的 Protocol 或 ABC）：
   - verify(credentials: dict) -> UserInfo
   - 配置项 AUTH_PROVIDER 决定使用哪个实现
2. V1 实现 DeviceAuthProvider：
   - 入参 {device_id} 或 {code}
   - 首次自动创建用户，返回 JWT token
3. 路由 POST /api/auth/login —— 只调用当前配置的 Provider，
   不得直接依赖 DeviceAuthProvider
4. POST /api/file/upload —— 接收文件，存入 MinIO 私有桶，
   返回 file_key；限制单文件 50MB，白名单：pdf/doc/docx/jpg/png
5. 封装统一的存储接口（upload / signed_url / delete），
   底层用 MinIO，但业务代码不直接调 MinIO SDK（便于以后换云存储）
6. 写单元测试验证上传与签名 URL 可用

注意：文件一律存私有桶，禁止公开读。
```

## 阶段 3：订单核心

```
请实现订单核心逻辑（这是最关键的部分）：

1. 订单号生成：格式 MMDD-序号，当日自增，保证唯一
2. 取件码生成：格式 字母-两位数（如 B-12），按当日顺序
3. POST /api/order/create —— 创建订单，状态置为 pending_pay
4. 支付按 Provider 模式实现（为后续接真实支付预留，不要写死）：
   - 定义抽象接口 PaymentProvider，方法：create(order) / confirm(order_id)
   - V1 实现 MockPaymentProvider：直接标记支付成功
   - 配置项 PAYMENT_PROVIDER 决定使用哪个实现
   - 路由 POST /api/order/{id}/pay 只调用当前 Provider；
     「支付成功后状态转 queuing 并写 payment_logs」这段逻辑与具体支付方式无关，
     必须独立于 Provider 实现之外
5. 状态机：实现状态流转校验函数，非法转换抛业务异常
6. 每次状态变更写入 print_logs
7. GET /api/order/list 与 GET /api/order/{id}
   —— 详情需返回：队列中位置（前面还有 N 单）、预计等待分钟数
8. POST /api/order/{id}/cancel —— 仅允许 pending_pay / queuing 阶段取消
9. GET /api/home/summary —— 返回：等待中单数与预计时间、待取件数、
   当前是否可排单、若已截单则返回预期时段文案

预计等待时间算法：近 20 单平均耗时（按页数加权）× 前序单数，
无历史数据时默认每单 60 秒。

所有接口遵循统一返回格式，业务错误返回中文提示。
```

## 阶段 4：老师端后台接口

```
请实现老师端接口：

1. POST /api/admin/login —— 简单账号密码（从环境变量读），返回 JWT
2. GET /api/admin/queue —— 接单队列，按订单号排序，支持按状态筛选
3. GET /api/admin/order/{id}/download —— 返回 30 分钟有效的签名下载链接
4. POST /api/admin/order/{id}/start —— 开始打印
5. POST /api/admin/order/{id}/complete —— 打印完成：
   - 若请求未传金额，则按 pricing.json 自动计算
   - 返回计算出的金额，供前端展示确认
6. POST /api/admin/order/batch-complete —— 批量标记完成
7. POST /api/admin/order/{id}/verify —— 核销取件，写 payment_logs
8. POST /api/admin/order/{id}/abnormal —— 标记异常并记录原因
9. POST /api/admin/session/close —— 关闭某时段收单，
   注意：只影响新订单，已排队的订单不受影响
10. GET /api/admin/summary —— 今日接单数、收款额、待取件数、异常单数
11. POST /api/admin/notice —— 发布公告

所有管理员操作都要写 print_logs 记录操作人。
```

## 阶段 5：AI 模块（分 4 个子任务，逐个完成）

### 5-A 文档解析

```
请实现文档解析服务 services/doc_parser.py：

1. Word 转 PDF：调用 LibreOffice headless
   soffice --headless --convert-to pdf --outdir {tmp} {input}
2. PDF 解析（PyMuPDF）：
   - 页数
   - 每页文本内容
   - 每页图片数量、位置与尺寸
   - 通过文本块坐标推算页边距
   - 判断空白页（文本极少且无图）
   - 提取参考文献段落（匹配以 [数字] 开头的连续段落）
3. 输出统一的 JSON 结构，供后续 AI 分析使用

要求：解析结果缓存到 ai_reports 表，相同 file_key 不重复解析。
```

### 5-B 格式校验与版式诊断

```
请实现 services/format_checker.py：

1. 读取 config/format_rules.json
2. 调用 doc_parser 得到文档结构
3. 用【第五部分 5.4 的提示词模板】调用 DeepSeek：
   - 任务一：格式合规校验（结构完整性 + 参考文献著录）
   - 任务二：版式问题诊断
4. 合并两个任务的结果，按 severity 排序
5. 输出符合【5.4】约定格式的 JSON，存入 ai_reports
6. 接口：POST /api/ai/check

务必使用 JSON 模式调用（response_format={"type":"json_object"}），
并做好 JSON 解析容错（模型偶尔会多输出内容，需截取合法 JSON 部分）。
```

### 5-C 错别字检查与打印方案

```
请实现：

1. 错别字与标点检查：
   - 按段落分块（每块 2000-3000 字）
   - 并发调用 DeepSeek（用 asyncio.gather，限制并发 3）
   - 合并所有块的结果
   - 提供进度回调，供接口以 SSE 或轮询方式向前端推送进度
   - 严格按【5.4（3）】的提示词，只报告有把握的问题

2. 打印方案生成：
   - 按【5.4（4）】的提示词调用模型
   - 计算：所需纸张数、原需纸张数、节省金额（单位为分）
   - 接口：POST /api/ai/print-plan
```

### 5-D 图片转文字与 PDF 转 Word

```
请实现：

1. POST /api/ai/ocr —— 图片转文字：
   - 接收 file_key（图片）
   - 按【5.4（5）】的提示词，用 base64 内联方式把图片发给模型
   - 返回识别出的纯文本

2. POST /api/ai/convert —— PDF 转 Word：
   - 使用 pdf2docx 库转换
   - 转换结果存入 MinIO，返回新的 file_key
   - 注意：这是格式转换，不要调用大模型
```

## 阶段 6：文档修复

```
请实现 POST /api/ai/fix：

1. 入参：{file_key, issue_ids:[...]}，issue_ids 是用户选择要修复的问题
2. 调用 AI 把选中的问题转成结构化修改指令（actions）
3. 用 python-docx 执行这些指令，生成修复后的新文件
4. 新文件存入 MinIO，返回新的 file_key
5. 严格遵循：AI 只输出指令，代码负责执行，绝不让 AI 直接生成文档

支持的指令类型：
- set_line_spacing   设置行距
- set_font           设置字体字号
- remove_blank_page  删除空白页
- fix_reference      修正参考文献格式（需 AI 给出修正后的完整条目文本）
```

## 阶段 7：前端 - 学生端（uni-app）

```
请用 uni-app 创建学生端，包含三个 Tab：

Tab1 首页：
- 顶部卡片：等待中 N 单 · 约 X 分钟；待取件 M 单
- 排单状态卡：可下单 / 已截单（含预期取件时段文案）
- 公告列表

Tab2 下单（AI 在这里，是产品的核心入口）：
- 选文件（用条件编译：微信端 chooseMessageFile，APP 端 uni.chooseFile）
- 上传后自动触发 AI 体检，展示 Loading 与进度
- 体检报告页：问题清单（按严重程度分组）、每条含位置/原文/建议/依据、
  可勾选要不要修复
- 打印方案卡片：推荐参数 + 省多少钱，可调整
- 确认下单 → 展示订单号与取件码

Tab3 我的：
- 订单列表（按状态分组：待支付/排队中/待取件/已完成）
- 订单详情：排队位置、预计时间、取件码
- 取消订单、再来一单
- 个人设置（昵称即可，不要收集敏感信息）

要求：
- 三处必须写成适配层，不要直连平台 API：
  auth（登录）、filePicker（选文件）、notify（通知）
- 用轮询（3-5 秒）刷新订单状态，不用 WebSocket
- 界面要整洁，能直接用于答辩演示
```

## 阶段 8：前端 - 老师端后台

```
请用 Vue3 + Element Plus 创建老师端后台：

1. 登录页
2. 工作台（首页）：
   - 今日数据卡片：接单数、收款额、待取件数、异常单数
   - 接单队列表格：订单号、取件码、文件名、页数、参数、状态、操作
   - 按订单号排序，支持状态筛选与关键字搜索
   - 每行操作：预览/下载、开始打印、打印完成、核销
   - 支持多选 + 批量标记完成
3. 打印完成弹窗：
   - 自动显示系统计算的金额（可修改）
   - 确认后状态转「待取件」
4. 核销：扫码或输入取件码核销，确认收款
5. 收单管理：一键暂停/恢复上午、下午收单
6. 公告发布
7. 流水页：按日期查看收款记录

要求界面清爽，表格操作流畅，适合在文印室的电脑上批量操作。
```

## 阶段 9：打包与交付

```
请协助完成打包与交付：

1. uni-app 端：
   - 配置 manifest.json（应用名：智印助手，包名，图标，权限）
   - 说明云打包步骤与所需签名证书的生成方法
   - 说明如何编译为微信小程序（供后续学院改造使用）
2. 后端：
   - 编写 docker-compose.yml，包含 MySQL、MinIO、LibreOffice
   - 编写 .env.example，列全部环境变量（LLM_API_KEY 等）
   - 编写部署文档
3. 输出一份《安装说明》给评委使用：
   - 兼容系统：Android 8.0 及以上
   - 安装步骤（含「允许安装未知来源」的提示）
```

---

# 第七部分：阶段验收标准

| 阶段 | 验收标准 |
|---|---|
| 1 | 能启动服务，数据库表已创建，访问 `/docs` 能看到接口文档 |
| 2 | 能登录拿到 token，能上传文件并在 MinIO 中看到对象 |
| 3 | 能创建订单、拿到订单号与取件码、模拟支付后状态变 queuing，首页数据接口返回正确 |
| 4 | 老师端能拉取队列、能推进状态、能核销，操作有日志 |
| 5-A | 上传一个 docx，能正确得到页数、文本、图片数量、页边距 |
| 5-B | **上传一篇含格式错误的论文，能真实检出问题并给出合理建议**（这是核心验收点） |
| 5-C | 错别字检查有进度反馈，打印方案能算出正确纸张数与节省金额 |
| 5-D | 拍照能转出文字；PDF 能转成可编辑的 Word |
| 6 | 勾选问题后能生成修复后的文件，且能用 Word 打开 |
| 7 | 三个 Tab 功能完整，全链路可从上传走到下单 |
| 8 | 老师端能完成接单→完成→核销的完整流程 |
| 9 | 能生成可安装的 APK，在真机上正常运行 |

---

# 第八部分：实现约束与禁止事项

## 必须遵守

1. **API Key 只在服务端**，前端任何位置不得出现，包括配置文件、注释、示例
2. 金额一律用「分」的整数存储与计算
3. 时间以服务端为准
4. 所有 AI 输出使用 JSON 模式，并做解析容错
5. 长文档必须分块处理，不得整篇一次性提交
6. AI 结果只作建议，修复动作需要用户勾选确认
7. 代码注释用中文
8. 每个模块完成后附一个简短的 `README` 或注释说明

## 禁止事项

1. ❌ 不要用原生 Android（Java/Kotlin）开发，必须用 uni-app（后续需转小程序）
2. ❌ 不要用大模型做 PDF 转 Word（用 pdf2docx）
3. ❌ 不要让 AI 直接生成 docx 文件（AI 只输出指令，代码执行）
4. ❌ 不要引入 WebSocket（用轮询）
5. ❌ 不要在 V1 实现真实支付、短信验证码、第三方推送的**完整功能**（但必须按第九部分预留接口）
6. ❌ 不要收集学号、身份证、年级等敏感信息
7. ❌ 不要自研打印驱动或做自动出纸
8. ❌ 不要在客户端生成订单号、取件码或状态
9. ❌ 不要引入重量级依赖或复杂架构（如微服务、消息队列），本期用不上
10. ❌ 不要套用完整的企业级开源模板（如带 RBAC 权限、菜单管理的 FastAPI 脚手架），
   本项目只需要其中的「统一响应 + 全局异常 + JWT 鉴权」三个小模块，可参考后自行实现

---

# 第九部分：可扩展性设计（V1 必须预留的扩展点）

> **这一部分决定项目能不能顺利演进到 V2。**
> 核心原则：**V1 只写最简实现，但结构上留好插槽。**
> 判断标准：V2 加功能时，是否只需「新增一个实现类 + 改一行配置」，而不动业务代码。

## 9.1 四个必须抽象的模块

### （1）支付模块 —— `PaymentProvider`

```
app/services/payment/
    base.py          # 抽象接口
    mock.py          # V1：模拟支付
    wechat.py        # V2：微信支付（现在只建空文件 + TODO 注释，不实现）
    __init__.py      # 工厂：根据配置返回对应实现
```

```python
# base.py
from abc import ABC, abstractmethod

class PaymentProvider(ABC):
    @abstractmethod
    def create(self, order) -> dict:
        """发起支付。返回前端所需的支付参数。"""
    @abstractmethod
    def confirm(self, order_id: str) -> bool:
        """确认支付结果（V2 由支付回调触发）。"""
```

```python
# __init__.py
from app.config import settings

def get_payment_provider() -> PaymentProvider:
    if settings.PAYMENT_PROVIDER == "mock":
        from .mock import MockPaymentProvider
        return MockPaymentProvider()
    elif settings.PAYMENT_PROVIDER == "wechat":
        from .wechat import WechatPayProvider
        return WechatPayProvider()
    raise ValueError(f"未知支付方式: {settings.PAYMENT_PROVIDER}")
```

**业务代码只允许这样调用**：`provider = get_payment_provider()`，不得 import 具体实现类。

### （2）登录模块 —— `AuthProvider`

结构同上：`base.py` / `device.py`（V1）/ `wechat.py`（V2 留空）/ `admin.py`。

V2 可能新增：微信登录、手机号验证码、**学校统一身份认证（CAS）**——最后这个在校园场景很可能被要求。

### （3）通知模块 —— `Notifier`

```
app/services/notifier/
    base.py       # send(user_id, event, payload)
    poll.py       # V1：空实现（前端靠轮询，服务端只需记录事件）
    wechat.py     # V2：微信订阅消息
    push.py       # V2：APP 推送
```

**V1 仍要保留事件记录**：订单每次状态变更都往 `notifications` 表写一条待发消息记录（哪怕不发），
这样 V2 接推送时可以直接补发，不用回头改业务代码。

### （4）打印模块 —— `PrinterProvider`

```
app/services/printer/
    base.py       # print(file_url, params) -> job_id
    manual.py     # V1：只返回下载链接，由老师手动打印
    cloud.py      # V2：对接云打印 / 打印机 SDK
```

## 9.2 数据库预留字段

即使 V1 用不到，也先在表里留好（避免 V2 加字段要改数据迁移脚本）：

| 表 | 预留字段 | 用途 |
|---|---|---|
| `orders` | `site_id` INT DEFAULT 1 | 多文印点 |
| `orders` | `pay_channel` VARCHAR(16) | 支付渠道（mock / wechat / card） |
| `orders` | `external_pay_id` VARCHAR(64) | 第三方支付流水号 |
| `orders` | `printer_id` INT | 指定打印机 |
| `users` | `auth_provider` VARCHAR(16) | 用户来源（device / wechat / cas） |
| `users` | `student_no_hash` VARCHAR(64) | 学号哈希（**仅在将来确有必要且合规时使用**，V1 不采集） |
| `notifications` | 新表 | 待发/已发消息记录 |

## 9.3 配置驱动的功能开关

在 `config/features.json` 里放功能开关，前端启动时拉取，**用来控制页面上某些入口显不显示**：

```json
{
  "enable_online_payment": false,
  "enable_wechat_login": false,
  "enable_push_notification": false,
  "enable_multi_site": false,
  "enable_auto_print": false
}
```

好处：V2 上线某个功能时，**只改这个 JSON，不用重新发版**。
而且演示时如果某个功能没做，可以直接关掉，界面上不会露出半成品。

## 9.4 前端预留

1. **页面级功能开关**：不显示未完成功能的入口（由 9.3 的配置控制）
2. **适配层必须存在**（即使 V1 只用一种实现）：
   ```
   /services/auth.js       —— 登录
   /services/filePicker.js —— 选文件（微信/APP 分支）
   /services/notify.js     —— 通知
   /services/payment.js    —— 支付
   ```
   页面里**只调这些适配层**，不直接调 `uni.login` / `uni.chooseFile`。
   这样 V2 换实现时只改一个文件。
3. **uni-app 项目必须能用同一套代码编译出小程序**（即使 V1 只发 APK）：
   不要使用 APP 端独有的原生插件或 API，保持跨端兼容性。
   这是学院后续接手改小程序的前提。

## 9.5 V2 可能的演进方向（供参考，V1 不要实现）

| 优先级 | 功能 | 需要的改动 |
|---|---|---|
| 高 | 真实在线支付 | 新增 `wechat.py` + 配置开关 + 支付回调接口 |
| 高 | 微信登录 / 学校统一认证 | 新增 AuthProvider 实现 + 配置 |
| 中 | 微信订阅消息推送 | 实现 Notifier + 前端订阅授权 |
| 中 | 多文印点 / 多打印机 | 启用 `site_id`、加一层路由逻辑 |
| 中 | 论文格式规范的学校适配 | 换 `format_rules.json`（已支持） |
| 低 | 云打印 / 自动出纸 | 实现 PrinterProvider |
| 低 | 数据看板与统计 | 新增接口 + 图表页面 |

**关键**：以上每一项，在 V1 的架构下都应该是"加法"而不是"改造"。
如果你发现实现某个 V2 功能需要改 V1 的业务代码，说明抽象层没做好，回头修正。

---

# 第十部分：开源组件与合规说明

> 大赛通知明确：**可依法合规使用开源组件，但不得抄袭现成开源项目，不得购买成品参赛。**
> 因此必须明确"哪些是用的库、哪些是自研"，并在作品说明书中写清楚。

## 10.1 允许使用的第三方库（作为依赖调用，合规）

| 用途 | 库 | 许可证 | 说明 |
|---|---|---|---|
| Web 框架 | FastAPI | MIT | 后端框架 |
| ORM | SQLAlchemy | MIT | 数据库访问 |
| 数据校验 | Pydantic | MIT | 接口出入参 |
| AI 调用 | openai (SDK) | MIT | 调用 DeepSeek（OpenAI 兼容） |
| Word 读写 | python-docx | MIT | 读取/修改 docx |
| Word 结构读取 | docx2python | MIT | 提取段落属性、列表层级、表格 |
| PDF 解析 | PyMuPDF | AGPL / 商业双授权 | **注意：AGPL，自用没问题，若将来商用需确认许可** |
| PDF 转 Word | pdf2docx | MIT | 格式转换 |
| 文档结构理解 | docling（IBM） | MIT | PDF/DOCX/PPTX → 结构化模型（可选） |
| Word 转 PDF | LibreOffice headless | MPL 2.0 | 外部程序调用 |
| 前端框架 | uni-app | Apache 2.0 | 跨端开发 |
| 前端组件 | uview-plus | MIT | UI 组件库 |
| 前端请求 | luch-request | MIT | 请求封装 |

## 10.2 只借鉴设计、不复制代码的部分

以下开源项目**可以研究其设计和规则配置，但不要整项目复制**（否则可能被认定抄袭）：

| 项目 | 借鉴什么 | 不要做什么 |
|---|---|---|
| `TanKimzeg/PP4mat` | 检查项清单、规则字段设计（中西文字体分离）、接口划分 | 不要整体搬运代码；它有 Windows-only 依赖，搬了也跑不起来 |
| `NeoHotaru/DocFormatTool` | 格式预设模板的**数值**（本科/研究生论文通用格式） | 不要复制它的排版引擎代码 |
| `afishinlake/WordFormat` | ONNX 识别段落类型的**思路**、Word 批注的交互设计 | 不要复制模型与代码 |
| `JiayuXu0/FastAPI-Template` 等 | 统一响应、全局异常、JWT 鉴权的**写法** | 不要套用整套后台模板 |

## 10.3 自研部分（必须在说明书中强调）

以下是本项目**完全自研**的内容，也是作品的核心价值：

1. **规则引擎 + 大模型混合架构**：现有开源工具全是纯规则引擎，本项目的混合架构是原创设计
2. **AI 体检报告生成**：结构化问题清单（位置 + 原文 + 建议 + 标准依据）
3. **AI 输出 → 文档修复指令 → 代码执行** 的转换链路
4. **打印方案智能生成与省钱计算**
5. **错别字检查的"建议模式"**（不自动改，逐条确认）——规避大模型幻觉的安全设计
6. **订单队列与取件码核销机制**
7. **取件时结清的付款机制**（替代事后追讨）
8. **全部业务代码**

## 10.4 作品说明书中建议写这样一段

> **开源组件使用说明**
> 本项目基于 Python FastAPI、uni-app 等开源框架开发，使用了 python-docx、PyMuPDF、
> pdf2docx、Docling 等开源库完成文档解析与处理，均已遵循相应开源许可证。
> 项目的**核心业务逻辑、AI 混合检测架构、文档修复链路、订单与核销机制均为团队自主设计
> 与实现**。在格式校验规则的设计上，参考了国家标准 GB/T 7713.1 与 GB/T 7714，
> 并借鉴了部分开源工具在检查项划分上的思路，但未复制其代码。
>
> 本项目采用**规则引擎 + 大模型混合架构**：现有论文格式检测工具普遍为纯规则引擎，
> 只能校验可形式化的格式属性，无法理解文档语义。本项目通过规则引擎保证
> 字体、字号、行距、页边距等确定性属性的判断准确，通过大模型完成结构完整性、
> 参考文献著录规范、错别字等语义层面的检查，**将检测覆盖率从纯规则的约 60%
> 提升至 90% 以上**。

---

# 附录：环境变量清单

```bash
# .env.example

# 数据库
DB_HOST=localhost
DB_PORT=3306
DB_NAME=zhiyin
DB_USER=root
DB_PASSWORD=your_password

# 对象存储
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=zhiyin-files

# AI 模型（后续可替换为学校提供的 Key 或校内私有化部署地址）
LLM_API_KEY=sk-xxxxxxxxxxxxxxxx
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash

# 管理员
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change_me

# 业务配置
JWT_SECRET=your_jwt_secret
FILE_EXPIRE_HOURS=24
SIGNED_URL_EXPIRE_MINUTES=30

# ===== 可扩展点配置（V1 用简化实现，V2 换实现只改这里）=====
AUTH_PROVIDER=device          # device | wechat | cas
PAYMENT_PROVIDER=mock         # mock | wechat | card
NOTIFIER_PROVIDER=poll        # poll | wechat | push
PRINTER_PROVIDER=manual       # manual | cloud

# 支付相关（V2 启用真实支付时填写，V1 留空即可）
WECHAT_MCH_ID=
WECHAT_API_KEY=
WECHAT_NOTIFY_URL=
```

---

# 附录：演示前必做的准备工作

1. **准备一份含格式错误的毕业论文**（演示道具，务必用心）：
   - 参考文献著录错误（缺页码、作者超三人未用「等」）
   - 缺少某个结构要素
   - 一张图片超出页边距
   - 一页空白页
   - 若干错别字与标点错误
   - 页码或目录问题
2. 提前跑通全链路，确认每一步都有可见效果
3. 准备一份 60-90 秒演示视频的脚本
4. 准备答辩 PPT，突出「AI 是核心引擎」而非「订单系统加了 AI」

---

文档版本：v1.1 ｜ 生成时间：2026-10-16
本次更新：新增【第九部分：可扩展性设计】与【第十部分：开源组件与合规说明】；
明确「V1 先用简化实现跑通、V2 再按进度演进」的策略，并在上下文包、
阶段任务、环境变量等处补充了 Provider 抽象要求。
