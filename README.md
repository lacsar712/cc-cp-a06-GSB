# 冷链探头超温台

记录员上报探头编号与摄氏温度，后台工人用数据库行锁认领待处理队列，按 **8℃** 上限判定 **合格** 或 **超温**。

探头可配温漂校准偏置：提交时服务端把 **原文温度 ＋ 偏置 ＝ 判定用温度**，按判定用温度下结论；原文与判定用温度都写入冻结履历。

## 温漂校准偏置

- 顶栏「校准」进入校准落地页，含**偏置设置提交栏**与**履历区**（校准履历 + 判定履历），记录员与值班员都可进入。
- 偏置允许**闭区间 -10 ～ 10℃**（可为负）。越界时网页与 API 直连都以同一措辞退回：
  `偏置必须在 -10 到 10 摄氏度闭区间内`。
- 仅记录员可改偏置；值班员只读，能看偏置与履历、不能改。
- 提交读数在**单事务**内完成「读当前偏置 → 算判定温度 → 入队 → 写冻结副本」，失败整体回滚，不会只入队无履历。
- 判定链路、偏置提交校验、履历共用 `backend/rules.py` 同一套偏置与判定逻辑。
- 记录员可事后改正在线单据数字（`PATCH /api/readings/{id}`），只重算在线单据；**冻结履历旧值永不动**，在线单据与履历由此分叉、可对拍。
- 例：偏置设为 **-2**，提交原文 **6** → 判定 **4** ≤ 8 **合格**；偏置提交 **30** 因越界退回。

## 接口

| 方法 路径 | 权限 | 说明 |
|-----------|------|------|
| `GET /api/bias` | 登录 | 当前偏置 |
| `POST /api/bias` | 记录员 | 设置偏置（越界 400，统一措辞） |
| `GET /api/readings` | 登录 | 在线单据（含原文/偏置/判定温度） |
| `POST /api/readings` | 记录员 | 原子入队 + 冻结履历 |
| `PATCH /api/readings/{id}` | 记录员 | 事后改正在线单据，不碰履历 |
| `GET /api/history` | 登录 | 校准履历 + 判定冻结副本 |


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
| logger | log123456 | 记录员，可提交读数 |
| watcher | watch123456 | 值班员，只读列表 |

## 启动

```bash
cd projects/18-coldchain-probe-desk
docker compose up --build
```

健康检查：`GET http://localhost:8197/api/health` → `{"status":"ok","service":"coldchain-probe-desk"}`

## 种子数据

| 探头 | 温度 | 结论 |
|------|------|------|
| 探头A01 | 4.2℃ | 合格 |
| 探头B02 | 12.5℃ | 超温 |

## 本地开发（可选）

```bash
# 需本机 PostgreSQL 或仅起 db 容器
cd backend && pip install -r requirements.txt && python api.py
cd backend && python worker.py
cd frontend && npm install && npm run dev
```

接口进程默认监听容器内 **8000**，对外映射 **8197**。
