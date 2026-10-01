# 冷链探头超温台

记录员上报探头编号与摄氏温度；探头可配**温漂校准偏置**，服务端把「原文温度 + 偏置」得到**判定用温度**，后台工人用数据库行锁认领待处理队列，按 **8℃** 上限判定 **合格** 或 **超温**。

- 顶栏「校准落地页」：偏置设置提交栏、当前偏置表、在线单据 × 冻结副本对拍、履历区。
- 偏置必须落在允许的**闭区间 [-10℃, 10℃]**（含端点），越界网页与直连都以同一句服务端措辞退回。
- 提交入队与履历落笔**同一事务原子完成**，失败整体回滚，绝不只入队无履历。
- 履历只追加不改写；事后改正只改在线单据数字并追加冻结副本，旧履历不动，可逐列对拍。
- 偏置校验、读数提交、履历冻结、工人判定共用 `rules.py` 同一套偏置/判定规则。

## 技术栈

| 层 | 选型 |
|----|------|
| 接口 | Python aiohttp + asyncpg |
| 工人 | `worker.py`（psycopg，`FOR UPDATE SKIP LOCKED`） |
| 页面 | Preact + Vite，nginx 反代 `/api` |
| 数据库 | PostgreSQL 16 |

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3197 |
| 接口 | http://localhost:8197 |
| PostgreSQL | localhost:54397（库名 `coldchain`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| logger | log123456 | 记录员，可提交读数、设置偏置、改正单据 |
| watcher | watch123456 | 值班员，只读列表 / 偏置 / 履历，不能改偏置 |

## 启动

```bash
docker compose up --build
```

健康检查：`GET http://localhost:8197/api/health` → `{"status":"ok","service":"coldchain-probe-desk"}`

## 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/login` | 登录取 token |
| GET | `/api/readings` | 在线单据列表（含原文 `temp_c`、`bias_c`、判定 `judged_temp_c`） |
| POST | `/api/readings` | 记录员提交；同事务写在线行 + `created` 冻结履历 |
| PATCH | `/api/readings/{id}` | 事后改正原文温度，按提交时冻结偏置重判，追加 `corrected` 副本 |
| GET | `/api/biases` | 当前生效偏置表（登录即可看） |
| PUT | `/api/biases` | 记录员设置偏置；越界 400，措辞 `偏置必须在 -10℃ 到 10℃ 的闭区间内` |
| GET | `/api/history[?reading_id=]` | 履历流水（`created` / `judged` / `corrected`，只追加） |

## 判定与履历

- 判定温度 = 原文温度 + 提交时该探头的偏置（偏置随单据冻结，之后改偏置不回溯）。
- `created`：提交即冻结原文 / 偏置 / 判定温度（结论待判定）。
- `judged`：工人判定后同事务冻结结论。
- `corrected`：事后改正追加的新冻结副本；在线单据与旧副本就此分叉，校准页可对拍。

### 验收示例

给探头设偏置 **-2℃**，提交原文 **6℃** → 判定温度 **4℃ ≤ 8** → **合格**。
若提交偏置 **30℃**，因超出闭区间被退回；网页与直连返回同一句措辞。

## 种子数据

| 探头 | 原文温度 | 偏置 | 判定温度 | 结论 |
|------|------|------|------|------|
| 探头A01 | 4.2℃ | 0℃ | 4.2℃ | 合格 |
| 探头B02 | 12.5℃ | 0℃ | 12.5℃ | 超温 |

## 本地开发（可选）

```bash
# 需本机 PostgreSQL 或仅起 db 容器
cd backend && pip install -r requirements.txt && python api.py
cd backend && python worker.py
cd frontend && npm install && npm run dev
```

接口进程默认监听容器内 **8000**，对外映射 **8197**。
