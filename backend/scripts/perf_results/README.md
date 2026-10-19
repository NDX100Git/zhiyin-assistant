# 性能与转换评估结果

本目录留两套可重复运行的性能资产与当次结果。每次运行生成带时间戳的 Markdown，不覆盖历史文件。

## 30 秒轮询下的读取容量

脚本：`backend/scripts/perf_poll_capacity.py`

```bash
# 1. 建一个压测库（库名必须以 _stress 结尾）
docker exec zhiyin-mysql mysql -uroot -p<DB_PASSWORD> \
  -e "CREATE DATABASE IF NOT EXISTS zhiyin_stress DEFAULT CHARACTER SET utf8mb4"

# 2. 起一个指向压测库的实例
cd backend && DB_NAME=zhiyin_stress \
  ../../../Temp/zhiyin-venv/bin/python -m uvicorn app.main:app --port 8001

# 3. 灌一万单并测三个并发档位
cd backend && DB_NAME=zhiyin_stress \
  ../../../Temp/zhiyin-venv/bin/python scripts/perf_poll_capacity.py \
  --base-url http://127.0.0.1:8001 --total 10000 --levels 16,32,64
```

结果文件：`poll_capacity-20261019-115727.md`（代码提交 `1854a22`，一万单排队）。

### 当次结论

一万单排队时，学生订单列表单实例峰值 **61.4 次/秒**，低于「一万人每 30 秒轮询一次」的 **333.3 次/秒**，判定不通过。三个并发档位的 QPS 都在 59 到 61 之间，加并发不涨吞吐，只把 p95 从 363 毫秒推到 1362 毫秒。

### 瓶颈定位（一次性诊断，不是压测脚本的一部分）

同一实例上只改排队订单数，其余不变，用 4 秒一档测学生订单列表：

| 排队订单数 | 并发 | 实测 QPS | p95 |
|:---:|:---:|:---:|:---:|
| 100 | 16 | 249.7 | 86 ms |
| 200 | 16 | 235.0 | 93 ms |
| 500 | 16 | 223.2 | 99 ms |
| 1000 | 16 | 196.2 | 110 ms |
| 10000 | 16 | 61.4 | 363 ms |

QPS 随排队长度单调下降，说明不是连接池或客户端问题。单次请求拆开看：列表接口约 22.7 毫秒，其中 `queue_service.batch_wait_info` 约 15 毫秒；这一函数每个请求都把全部在途订单（一万单）读成一百万个行对象再算前缀和，SQL 约 13 毫秒、Python 约 1.7 毫秒。单独打这条 SQL，单线程约 77 次/秒，16 并发也只有约 104 次/秒。

REVIEW 第 12 条的 340 次/秒在本次机器上没有复现：同一口径下排队 100 单是 249.7 次/秒，一万单是 61.4 次/秒。容量随排队长度变化，引用 340 时需要同时写明排队规模与机器。

### 可选修法（未实施）

这里只记录测量与回归，订单读取链路保持原样。要让 333 次/秒成立，候选方向：

1. 给队列快照加 1 到 2 秒的短时缓存，列表请求复用同一份前缀和；
2. 把排队位置改成 SQL 侧聚合或窗口函数，不再把整队读进 Python；
3. 列表接口加条件请求或 ETag，轮询未变化时返回 304。

前两条要改订单读取链路，属于另一项工作。

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
