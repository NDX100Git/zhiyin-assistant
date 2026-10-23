# 性能与转换评估结果

本目录留两套可重复运行的性能资产与当次结果。每次运行生成带时间戳的 Markdown，不覆盖历史文件。

## 30 秒轮询下的读取容量

脚本：`backend/scripts/perf_poll_capacity.py`

```bash
# 1. 建一个压测库（库名必须以 _stress 结尾）
docker exec zhiyin-mysql mysql -uroot -p<DB_PASSWORD> \
  -e "CREATE DATABASE IF NOT EXISTS zhiyin_stress DEFAULT CHARACTER SET utf8mb4"

# 2. 起一个指向压测库的实例（对照多进程时加 --workers 4）
cd backend && DB_NAME=zhiyin_stress \
  ../../../Temp/zhiyin-venv/bin/python -m uvicorn app.main:app --port 8001

# 3. 灌一万单并测三个并发档位
cd backend && DB_NAME=zhiyin_stress \
  ../../../Temp/zhiyin-venv/bin/python scripts/perf_poll_capacity.py \
  --base-url http://127.0.0.1:8001 --total 10000 --levels 16,32,64

# 4. 库已经灌好时可以直接复测读数，不必再灌一遍
cd backend && DB_NAME=zhiyin_stress \
  ../../../Temp/zhiyin-venv/bin/python scripts/perf_poll_capacity.py \
  --base-url http://127.0.0.1:8001 --skip-fill --levels 16,32,64
```

灌单走真实下单接口，每个压测用户各上传一份 PDF 再下单：下单会校验文件还在、没过宽限期，也校验文件归属，共用别人上传的 key 会被拒。

### 当前实现：队列快照缓存

`backend/app/services/queue_snapshot.py` + `queue_service.queue_state()`：整队的位置、前序页数前缀和、单均耗时做成一份快照，有效期
`config/order_rules.json` 的 `wait.queue_snapshot_seconds`（1.5 秒）。有效期只是兜底，会改变队列的动作都会显式调 `invalidate()`，
旧快照立刻作废：下单、支付、取消、换文件确认、开始打印、完成打印、异常单恢复排队。快照重建只放一个请求进去（单飞），
有效期到期时不会十几个并发一起扫全队。学生订单列表直接返回 `JSONResponse`，省掉框架对整棵响应树的一层通用编码。

### 结果

| 结果文件 | 场景 | 并发 16 | 并发 32 | 并发 64 | p95（16） |
|:---|:---|:---:|:---:|:---:|:---:|
| `poll_capacity-20261019-115727.md` | 缓存之前，单进程 | 61.4 | 59.6 | 58.7 | 363 ms |
| `poll_capacity-20261019-132247.md` | 缓存之后，单进程 | **154.0** | 153.3 | 147.4 | 143 ms |
| `poll_capacity-20261019-133000.md` | 缓存之后，单进程，重新灌单后复测 | 103.0 | 104.4 | 101.2 | 220 ms |
| `poll_capacity-workers4-20261019-133800.md` | 同一实例 4 个 worker | **445.4** | 473.0 | 481.3 | 50 ms |

一万单排队、单进程时，学生订单列表 103 到 154 次/秒，是「一万人每 30 秒轮询一次」333.3 次/秒的 **31% 到 46%**，判定不通过。
两次单进程读数差在同一台机器上出现，原因是机器同时还在跑别的活儿，读数的绝对值随机器状态浮动；三次测量里队列长度都不再影响读数
（三个并发档位基本一条线），差别只在单进程的 Python CPU。同一实例起 4 个 worker 是 445.4 次/秒、p95 50 毫秒，判定通过。

### 瓶颈定位（实测，不是推测）

一万单排队、16 并发时，按 `/proc/<pid>/stat` 读服务进程自己的 CPU 时间（两次测量，机器状态不同）：

| 接口 | 每次请求的 CPU | 说明 |
|:---|:---:|:---|
| `/openapi.json`（框架底噪） | 0.9 ms | FastAPI + uvicorn 的固定开销 |
| `/api/notice/list`（鉴权 + 一条小查询） | 3.5 ms | 鉴权查用户、拼响应 |
| `/api/order/list` | 7.2 ~ 10.7 ms | 队列快照缓存之后 |

单进程吞吐就是 `1 / 每次请求 CPU`：7.2 毫秒对应 139 次/秒（实测 154 次/秒），10.7 毫秒对应 93 次/秒（实测 103 次/秒）。
请求里已经没有全队扫描，剩下的时间是框架与鉴权约 2 毫秒、几条 SQL 的 Python 侧解析约 1.5 到 3 毫秒、
20 行订单转字典约 2 毫秒、序列化约 1 毫秒。抓 profile 看，pymysql 逐行解析、SQLAlchemy 取 ORM 对象、
再把 20 行转成对外的字典，这几件事占大头；换成 `JSONResponse` 直出之前，框架对整棵响应树过一遍
`jsonable_encoder` 还要多花一个多毫秒。这些都与 MySQL 无关——给 `orders` 补 `(user_id, created_at, id)` 索引只让
p50 从 104 毫秒降到 100 毫秒、QPS 一点没涨，说明卡的不是数据库或延迟，是单进程的 GIL。

### 下一步

1. 同一实例起多个 worker（实测 4 个已经达标）。代价：快照缓存现在进程内，失效只到处理写请求的那个 worker，
   其余 worker 最多旧 1.5 秒；要满足「换文件后立刻看到新位置」，得配一个跨进程的失效信号（版本号行或运行时文件），
   并同步下调每个 worker 的连接池（4 × 50 会顶到 MySQL 默认 max_connections）。
2. 读路径继续减 CPU：分页不满时不发 `count(*)`、订单页改成按列取而不是取整个 ORM 对象。
   这两项能省的是一次请求 1 到 2 毫秒，单进程仍到不了 333。
3. 条件请求（ETag / `If-None-Match`）让未变化的轮询直接 304，把响应体与序列化从热路径上拿掉。

## LibreOffice 转换进程复用评估

脚本：`backend/scripts/bench_soffice_reuse.py`

```bash
cd backend && ../../../Temp/zhiyin-venv/bin/python scripts/bench_soffice_reuse.py --samples 3
```

结果文件：`soffice_reuse-20261019-120146.md`。当次在 `zhiyin-libreoffice` 容器上测得：

| 场景 | 产出 | 观察 |
|:---|:---:|:---|
| 独立 profile 串行 3 次 | 3/3 | 总 5.64 秒，平均每次 1.88 秒，含 soffice 冷启动 |
| 独立 profile 并发 2 次 | 2/2 | 总耗时 1.97 秒，两单真正并行 |
| 共用 profile 并发 2 次 | 1/2 | 总耗时 1.84 秒，一单退出码 1，输出静默丢失 |
| 常驻实例 + 委托转换 | 2/2 | 总耗时 2.28 秒，常驻实例在第一次转换后退出，第二次回到冷启动 |

结论：当前这版复用不可靠。代码里用并发闸门限制进程数（`SOFFICE_MAX_CONCURRENCY`，默认 2），排队等空位（`SOFFICE_QUEUE_WAIT_SECONDS`，默认 20 秒），等不到返回 50046；单次转换超时仍是 `SOFFICE_TIMEOUT_SECONDS`（默认 180 秒）。评估依据同时写在 `backend/app/services/doc_parser.py` 的转换函数上方。

## 安卓真机

本机未安装 `adb`，也没有连接安卓设备，选文件与拍照链路没有真机验证。这是阻塞项，不阻塞浏览器回归与后端性能测试。
