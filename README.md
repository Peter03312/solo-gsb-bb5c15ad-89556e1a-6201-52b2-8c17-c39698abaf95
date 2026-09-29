# 实验室样本冻融追踪 API

基于 **FastAPI + SQLite + Docker** 的服务，用于追踪实验室样本（疫苗、生物制剂、细胞株等）在冷链过程中的冻融循环：

- 客户端按样本提交**带事件时间**的温度读数；
- 服务端按 `event_time` 时间顺序**重排迟到数据**后重算，而不是按入库顺序处理；
- 使用可配置的**冻结/解冻阈值 + 滞回（Schmitt 触发器）**规则识别完整冻融循环，避免临界温度抖动造成的误判；
- 重复事件通过客户端 `event_id`（或“同时刻同温度”签名）**幂等去重**；
- 暴露样本**当前状态、完整循环次数/明细、温度越限与循环超限告警**查询与确认接口；
- 按相邻读数线性插值累计**双向温度暴露剂量（超限幅度 × 分钟）**，支持低温/高温剂量预算、
  最大可积分间隔（缺口跳过）与预算超限告警；
- 支持**共享温度记录仪**：登记记录仪与带 `event_id` 的读数，为样本设置记录仪绑定的
  **左闭右开时段**；记录仪读数仅归入发生时间落在绑定时段内的样本（同一读数可归入多个样本），
  与样本直接读数合并到同一条时间轴上重算冻融循环、剂量与告警；迟到读数或绑定时段变更会
  撤销旧归属并重建受影响样本；
- 支持**记录仪校准无效时段**：按左闭右开时段登记（同一记录仪互不重叠），时段内的记录仪读数
  照常幂等入库、可经原始读数接口查询，但不归属样本，也不参与冻融循环、暴露剂量、告警与
  温度一致性核查；时段增删改或迟到读数到达后自动对受影响样本重新对账回算，
  删除时段后有效读数恢复归属；
- 提供**样本 × 记录仪温度一致性核查**：取样本直接读数与当前有效归属的该记录仪读数，
  按事件时间顺序做**不交叉一对一配对**（配对数最多 → 总时间差最小 → 读数 ID 序列稳定取舍），
  返回温差严格大于阈值的配对明细与配对 / 两侧未配对数量；只读查询，不改写任何既有结果；
- 支持**样本告警保管责任归属**：按生效时间登记保管人交接（允许补录较早交接，
  同一样本同一时刻仅一条），相邻交接形成**左闭右开责任区间**（首条之前责任人为空）；
  告警归责查询按当前告警的事件时间匹配责任区间（恰在交接时刻归新保管人），
  返回告警 ID、事件时间、保管人与交接 ID；归责在查询时即时计算、**不持久化**，
  补录交接或迟到读数引起告警重算后查询即反映最新结果，既有告警确认语义不变；
- 提供**只读配液方案求解**：给定 1～6 个已存在样本的整数浓度（0～100 ng/µL）与
  可用体积（0～30 µL）以及目标体积（1～30 µL）/目标浓度（0～100 ng/µL），
  精确枚举满足“取用体积之和 = 目标体积”且“Σ 取用体积 × 浓度 = 目标体积 × 目标浓度”
  的整数取用方案，按**使用来源样本数最少 → 请求顺序取用体积向量字典序最小**择优，
  逐项返回含零用量的配方与使用样本数；纯只读计算，**不扣减库存**，
  无精确解返回 409，样本不存在 / 重复 ID / 参数非法返回 422；
- 提供**只读批次运输风险比较**：接收 2～50 个已存在样本 ID 与五项有限非负权重
  （冻融循环数、低温剂量、高温剂量、暴露缺口分钟数、未确认告警数，至少一项为正），
  从各样本**当前重算状态**读取五项指标，在批内逐指标**最小最大归一化**
  （全相同记 0），总分 = 归一化值加权和 / 权重和；并基于五项**原始指标**求
  **非支配前沿**，返回原始指标、归一化值、总分、前沿标记及按总分升序 / 样本 ID
  升序的排名；纯只读，**不触发重算、不改变样本状态与告警**，样本 ID 重复、
  数量越界、权重非法或样本不存在均返回 422；
- 容器启动即自动初始化数据库（建表、索引），旧版本数据库自动兼容迁移。

## 目录结构

```
app/
  main.py        # FastAPI 路由与启动初始化
  engine.py      # 纯函数冻融状态机（循环识别 + 告警推导），不依赖 Web/DB
  matching.py    # 纯函数一致性配对（不交叉一对一最优配对），不依赖 Web/DB
  solution.py    # 纯函数配液方案求解（整数精确枚举 + 字典序择优），不依赖 Web/DB
  risk.py        # 纯函数批次运输风险比较（批内归一化、加权总分、非支配前沿），不依赖 Web/DB
  database.py    # SQLite 连接与幂等建表脚本
  schemas.py     # Pydantic 请求/响应模型
  services.py    # 业务层：幂等写入、时间重排重算、告警确认、一致性核查、配液求解
tests/           # pytest：引擎/配对/配液单元测试 + API 集成测试
examples/        # requests.http / demo.sh 示例请求
Dockerfile
docker-compose.yml
```

## 快速开始（Docker）

```bash
docker compose up --build
```

服务监听 http://localhost:8000 ：

- Swagger 文档：http://localhost:8000/docs
- ReDoc：http://localhost:8000/redoc
- 健康检查：http://localhost:8000/health

SQLite 数据文件持久化在 named volume `freeze-thaw-data`（容器内 `/data/freeze_thaw.db`），首次启动自动建库。

## 本地开发

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# 启动（默认数据文件 ./data/freeze_thaw.db，可用 DB_PATH 覆盖）
uvicorn app.main:app --reload

# 运行测试
pytest -q
```

## 冻融识别规则（滞回）

为每个样本配置两个阈值：`freeze_threshold_c < thaw_threshold_c`（示例：-20°C / 0°C）。

| 当前状态 | 条件 | 迁移 |
 | --- | --- | --- |
| `thawed`（已解冻） | 温度 **≤ freeze_threshold_c** | → `frozen`，记录冻结起点 |
| `frozen`（冻结中） | 温度 **≥ thaw_threshold_c** | → `thawed`，**闭环一次完整冻融循环** |
| `frozen` | 温度处于两阈值之间（如 -10°C） | 保持 `frozen`（滞回，不翻转） |
| `thawed` | 温度处于两阈值之间 | 保持 `thawed` |

每个完整循环记录：冻结起点时间/温度、解冻时间/温度、循环段内最低/最高温度。
**只冻结未解冻不算循环**；当前仍处于冻结段时，状态接口会返回冻结起点与段内极值。

### 告警规则

1. **`temperature_excursion`（critical）**：读数超出样本允许区间
   `[min_allowed_c, max_allowed_c]`。连续越限只告警一次（去抖），回到区间后再次越限才再次告警。
2. **`cycle_limit_exceeded`（warning）**：完整循环次数超过 `max_freeze_thaw_cycles`，每超出一次循环告警一次。

告警支持单条确认、按样本批量确认；重算会保留确认状态（按稳定去重键 upsert）。

## 累计温度暴露剂量

在冻融状态机之外，服务还会按读数时间轴累计**温度暴露剂量**（单位 °C·min，即
“超出允许区间的幅度 × 持续分钟数”）：

1. 对按 `event_time` 排序后的**相邻读数做线性插值**，把曲线越过 `min_allowed_c`
   或 `max_allowed_c` 的区段在跨越点切分（线性线段在一个区间内至多跨越一次）；
2. 低温方向累计 `min_allowed_c - 温度` 的积分，高温方向累计
   `温度 - max_allowed_c` 的积分（梯形面积，界内幅度计 0）；
3. 相邻读数间隔超过 `max_integration_interval_minutes` 时，该区间**整体跳过不积分**，
   间隔时长计入 `exposure_gap_minutes`（缺口时长）；恰好等于上限的间隔仍参与积分；
4. 任一方向累计剂量**首次达到**对应预算（`cold_dose_budget_c_min` /
   `heat_dose_budget_c_min`）时，产生一条 `exposure_budget_exceeded`（critical）告警，
   告警事件时间为插值求出的精确跨越时刻；每个方向至多一条，去重键为
   `sample_id:exposure_budget:cold|heat`。

剂量、缺口与告警全部由重算流程派生：**迟到读数补录、阈值 / 预算 / 积分间隔 PATCH
修改后都会整体重算**——预算放宽后超限告警会消失（确认状态随去重键保留），预算收紧后
会重新出现，保证剂量与告警可逆更新。

> 配置剂量预算时必须同时提供对应的允许区间边界（冷预算需 `min_allowed_c`，
> 热预算需 `max_allowed_c`）。

## 迟到数据与重算

每次成功写入新读数后，服务都会：

1. 取出该样本**全部**读数，按 `(event_time, id)` 升序重排（迟到数据自然回到正确时间位置）；
2. 用 `engine.derive` 纯函数重放状态机，重建完整循环、暴露剂量与告警；
3. 刷新样本当前状态缓存。

PATCH 样本的阈值、允许区间、循环上限、剂量预算或最大可积分间隔后，也会走同一条重算
流程，因此剂量与预算告警对“迟到数据”和“配置变更”都保持可逆、可重复的更新语义。

也可调用 `POST /samples/{id}/recompute` 手动触发兜底重算。

## 共享温度记录仪与绑定时段

多台物理记录仪（如冷链车厢/冰箱共用的温度探头）可独立登记，其读数通过**绑定时段**
归入一个或多个样本：

1. `POST /loggers` 登记记录仪（自带 ID 或由服务端生成 UUID），
   `POST /loggers/{id}/readings` 提交带 `event_id`、发生时间、温度的读数，
   幂等规则与样本读数一致（`event_id` 唯一，或同时刻同温度判重）；
2. `POST /samples/{id}/logger-bindings?logger_id=...` 为样本设置一个
   **左闭右开 `[start_time, end_time)`** 的绑定时段；
3. 归属规则：记录仪读数当且仅当 `start_time <= event_time < end_time` 时归入该样本。
   - **同一样本**的绑定时段（允许跨不同记录仪）**不得重叠**（端点相接合法），重叠提交返回 422；
   - 不同样本可绑定同一时段，因此**同一条记录仪读数可归入多个样本**；
4. 重算时把记录仪归属读数与样本**直接读数合并到同一条时间轴**（同时间戳直接读数在前），
   按既有滞回状态机、剂量积分、告警规则统一推导；直接读数始终独立保存、不会被记录仪流程修改；
5. **迟到读数**（时间早于已提交数据）或**绑定时段 PATCH/DELETE** 都会对受影响样本做
   **归属对账**（删除失效归属、补建新归属）后整体重算：不产生重复派生读数，
   循环/剂量/告警随之可逆更新；
6. 删除样本或记录仪会级联清理对应绑定与归属；删除记录仪后受影响样本自动重算。

记录仪读数派生的告警带有 `logger_reading_id`（直接读数告警仍为 `reading_id`）；
温度越限告警按“来源 + 读数”去重，因此同一时间轴上直接读数与记录仪读数各自的越限
不会互相覆盖；循环超限（按循环序号）与剂量预算（按方向）告警与读数来源无关，
窗口变化使锚点读数在直接/记录仪间切换时确认状态仍保留。

查询：

- `GET /samples/{id}/logger-bindings` / `GET /loggers/{id}/bindings`：样本 / 记录仪两侧的绑定列表；
- `GET /loggers/{id}/readings`：记录仪原始读数；
- `GET /samples/{id}/readings?source=all|direct|logger`：样本读数，默认 `all`
  为直接读数与记录仪归属读数合并后的时间轴（返回项带 `source`、`logger_id`、`logger_reading_id`）；
- `GET /samples/{id}/state` 的最近读数/循环/剂量同样基于合并时间轴。

## 记录仪校准无效时段

记录仪送校 / 故障 / 维护期间产生的读数不可信，可登记**校准无效时段**将其排除出样本归属：

1. `POST /loggers/{id}/invalid-periods` 登记**左闭右开 `[start_time, end_time)`** 时段
   （`start_time` 必须严格早于 `end_time`）；**同一记录仪**的时段不得重叠
   （端点相接合法），重叠提交返回 422；
2. 落在无效时段内的记录仪读数**仍照常幂等入库**（`event_id` / 同时刻同温度判重规则不变），
   并可通过 `GET /loggers/{id}/readings` 查询原始数据；
3. 但这些读数**不归属任何样本**——对账时一律跳过，因此不参与合并时间轴上的
   冻融循环识别、暴露剂量积分、告警推导与样本 × 记录仪温度一致性核查
   （一致性核查的“当前有效归属”自然不含它们）；
4. 新增、`PATCH`、删除时段或补入迟到读数后，服务对所有绑定该记录仪的样本
   **重新对账归属并整体回算**：循环 / 剂量 / 告警随之可逆更新，
   直接读数与既有告警确认状态按现有规则（稳定去重键 upsert）保留；
5. **删除时段后**，原时段内的有效读数恢复归属并重新参与上述推导；
   删除记录仪会级联清理其全部无效时段。

查询：

- `GET /loggers/{id}/invalid-periods`：该记录仪的无效时段列表
  （`reading_count` 为当前落在时段内、被排除归属的原始读数条数）；
- `GET /invalid-periods/{id}`：单条时段详情；
- `PATCH/DELETE /invalid-periods/{id}`：修改 / 删除时段（自动对账回算受影响样本）。

## 样本 × 记录仪温度一致性核查

`GET /samples/{sample_id}/logger-consistency/{logger_id}?max_time_diff_seconds=300&temp_diff_threshold_c=2.0`

用于核查“样本直接读数”与“共享记录仪读数”是否一致（例如样本自带探头与车厢记录仪互证）：

1. **取数**：样本的全部直接读数，以及**当前有效归属**于该样本的该记录仪读数
   （即落在现行绑定时段内的归属读数；迟到读数、绑定新建 / PATCH / DELETE 后的
   查询立即反映最新归属）；
2. **配对**：两侧分别按 `(event_time, id)` 升序，做**不交叉的一对一配对**
   （保序：若两对配对交叉，即直接读数 a₁ 在 a₂ 前、而其配对记录仪读数却在
   a₂ 的配对读数之后，则不允许）。仅当两条读数
   事件时间差的绝对值 `<= max_time_diff_seconds`（非负，恰好等于也可配对）时才允许配对。
   配对目标按字典序依次取舍：
   1. **配对数最多**；
   2. **总时间差最小**（各配对时间差绝对值之和）；
   3. 仍相同时，**配对读数 ID 序列** `[(直接读数 id, 记录仪读数 id), ...]`
      按字典序取最小——结果稳定、可复现；
3. **判定**：配对温差（绝对值）**严格大于** `temp_diff_threshold_c`（非负）的配对
   计入异常明细；**正常配对同样占用读数**，未配对读数不参与温差判定
   （未配对不算温差异常）。

响应字段：

| 字段 | 说明 |
| --- | --- |
| `paired_count` | 成功配对的读数对数（含温差正常的配对） |
| `unmatched_sample_count` / `unmatched_logger_count` | 两侧各自未配对的读数条数 |
| `anomaly_count` / `anomalies` | 温差超限的配对数与明细（按事件时间顺序） |
| `anomalies[].reading_id` / `logger_reading_id` | 配对两侧读数 ID（`readings.id` / `logger_readings.id`） |
| `anomalies[].sample_event_time` / `logger_event_time` | 两侧事件时间 |
| `anomalies[].sample_temperature_c` / `logger_temperature_c` | 两侧温度（°C） |
| `anomalies[].time_diff_seconds` / `temperature_diff_c` | 该配对的时间差（秒）与温差（°C），均为绝对值 |

本接口为**只读查询**：不改动原始读数、归属关系与既有冻融循环、暴露剂量、告警结果。

示例：

```bash
curl 'http://localhost:8000/samples/sample-demo-001/logger-consistency/logger-truck-07?max_time_diff_seconds=300&temp_diff_threshold_c=2.0'
```

## 样本告警保管责任归属

样本在冷链流转中会经过多位保管人。服务支持按**生效时间**登记保管人交接，
并把样本的**当前告警**按事件时间归责到对应保管人：

1. `POST /samples/{id}/custody-handovers` 登记交接：`custodian`（接任保管人）、
   `effective_time`（生效时间，无时区按 UTC 处理）与可选 `note`；
   - **允许补录较早交接**：生效时间可早于已有交接或当前时间，
     交接列表始终按生效时间升序返回；
   - **同一样本同一生效时刻仅能登记一条**，重复登记返回 409；
2. 相邻交接形成**左闭右开责任区间** `[effective_time_i, effective_time_{i+1})`，
   最后一条交接的区间延续到无穷；**首条交接之前责任人为空**
   （归责结果中 `custodian` / `handover_id` 为 `null`）；
3. `GET /samples/{id}/alert-custody` 返回样本**当前全部告警**的归责明细
   （按事件时间升序）：`alert_id`、`event_time`、`custodian`、`handover_id`；
   告警事件时间**恰好等于交接生效时刻**时归**新**保管人（左闭）；
4. 归责**在查询时即时计算、不持久化**：补录交接、迟到读数或配置变更引起
   告警重算后，再次查询即反映最新的交接记录与告警集合，不会残留过期归责；
   归责查询为只读，告警的确认 / 取消确认语义保持不变。

查询交接记录：`GET /samples/{id}/custody-handovers`（按生效时间升序）。
删除样本会级联清理其交接记录。

示例：

```bash
# 登记交接（先生效 09:00 的张三，再补录更早 08:00 的李四）
curl -X POST http://localhost:8000/samples/sample-demo-001/custody-handovers \
  -H 'Content-Type: application/json' \
  -d '{"custodian": "张三", "effective_time": "2026-09-20T09:00:00Z", "note": "早班接管"}'
curl -X POST http://localhost:8000/samples/sample-demo-001/custody-handovers \
  -H 'Content-Type: application/json' \
  -d '{"custodian": "李四", "effective_time": "2026-09-20T08:00:00Z"}'

# 交接记录列表（按生效时间升序：李四 08:00、张三 09:00）
curl http://localhost:8000/samples/sample-demo-001/custody-handovers

# 告警归责查询：08:00 之前的告警责任人为空，[08:00, 09:00) 归李四，09:00 起归张三
curl http://localhost:8000/samples/sample-demo-001/alert-custody
```

## 幂等去重

- 提供 `event_id` 时：`(sample_id, event_id)` 唯一，重复提交直接跳过；
- 不提供 `event_id` 时：`(sample_id, event_time, temperature_c)` 相同视为重复；
- 同一请求批次内的重复同样会被识别。

批量提交响应返回 `accepted`（新写入）与 `duplicates`（跳过）计数。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| POST | `/samples` | 创建样本（阈值、允许区间、循环上限可配置） |
| GET | `/samples` | 样本列表（含状态/循环数摘要） |
| GET/PATCH/DELETE | `/samples/{id}` | 样本详情 / 修改配置（自动重算） / 删除 |
| POST | `/samples/{id}/readings` | 批量提交读数（幂等，提交后自动重算，返回新告警） |
| GET | `/samples/{id}/readings?source=all\|direct\|logger` | 读数列表（按事件时间排序；默认合并直接读数与记录仪归属读数） |
| POST/GET | `/loggers` | 登记共享温度记录仪 / 记录仪列表 |
| GET/PATCH/DELETE | `/loggers/{id}` | 记录仪详情 / 改名 / 删除（级联清理并重建受影响样本） |
| POST | `/loggers/{id}/readings` | 批量提交记录仪读数（幂等；自动归属并重算受影响样本，返回新告警） |
| GET | `/loggers/{id}/readings` | 记录仪原始读数列表 |
| POST/GET | `/samples/{id}/logger-bindings?logger_id=...` | 设置记录仪左闭右开绑定时段 / 样本绑定列表（同时段不重叠） |
| GET | `/loggers/{id}/bindings` | 该记录仪对各样本的绑定列表 |
| PATCH/DELETE | `/logger-bindings/{id}` | 修改 / 删除绑定时段（撤销旧归属、重建并重算样本） |
| POST/GET | `/loggers/{id}/invalid-periods` | 登记 / 查询记录仪**校准无效时段**（左闭右开；同一记录仪不重叠；时段内读数入库但不归属、不参与推导） |
| GET/PATCH/DELETE | `/invalid-periods/{id}` | 时段详情 / 修改 / 删除（自动对账回算受影响样本；删除后有效读数恢复归属） |
| GET | `/samples/{id}/logger-consistency/{logger_id}?max_time_diff_seconds=...&temp_diff_threshold_c=...` | **温度一致性核查**：直接读数与当前归属记录仪读数不交叉一对一最优配对，返回温差超限配对明细与配对/未配对数量（只读） |
| POST/GET | `/samples/{id}/custody-handovers` | 登记保管人交接（按生效时间，允许补录较早交接；同时刻仅一条，重复 409）/ 交接记录列表（按生效时间升序） |
| GET | `/samples/{id}/alert-custody` | **告警归责查询**：当前告警按事件时间匹配左闭右开责任区间（恰在交接时刻归新保管人，首条之前责任人为空），返回告警 ID、事件时间、保管人与交接 ID；查询时即时计算、不持久化 |
| GET | `/samples/{id}/state` | **当前状态**、循环次数、双向暴露剂量/缺口/预算使用率、未确认告警数、最近读数 |
| GET | `/samples/{id}/cycles` | **完整冻融循环明细** |
| POST | `/samples/{id}/recompute` | 手动重算 |
| GET | `/alerts` | **告警查询**（可按样本、确认状态过滤，分页） |
| GET/POST | `/alerts/{id}` `/alerts/{id}/ack` | 告警详情 / 确认（或取消确认） |
| POST | `/samples/{id}/alerts/ack` | 批量确认样本下所有告警 |
| POST | `/transport-risk-comparisons` | **批次运输风险比较**（2～50 个已存在样本；批内最小最大归一化、加权总分、原始指标非支配前沿、总分/ID 排序排名；只读，不改状态与告警） |

## 示例请求（curl）

```bash
# 1. 创建样本（含低温/高温剂量预算与 120 分钟最大可积分间隔）
curl -X POST http://localhost:8000/samples \
  -H 'Content-Type: application/json' \
  -d '{
    "id": "sample-demo-001",
    "name": "新冠灭活疫苗 批次 A-2026-09",
    "freeze_threshold_c": -20,
    "thaw_threshold_c": 0,
    "min_allowed_c": -80,
    "max_allowed_c": 8,
    "max_freeze_thaw_cycles": 2,
    "cold_dose_budget_c_min": 50,
    "heat_dose_budget_c_min": 200,
    "max_integration_interval_minutes": 120
  }'

# 2. 提交读数（完成一次冻融）
curl -X POST http://localhost:8000/samples/sample-demo-001/readings \
  -H 'Content-Type: application/json' \
  -d '{"readings": [
    {"event_id": "evt-1", "event_time": "2026-09-20T08:00:00Z", "temperature_c": 4.0},
    {"event_id": "evt-2", "event_time": "2026-09-20T08:30:00Z", "temperature_c": -22.0},
    {"event_id": "evt-3", "event_time": "2026-09-20T10:30:00Z", "temperature_c": 2.0}
  ]}'

# 3. 迟到数据（09:00/09:30，服务端自动插入时间轴中间重排）
curl -X POST http://localhost:8000/samples/sample-demo-001/readings \
  -H 'Content-Type: application/json' \
  -d '{"readings": [
    {"event_id": "late-1", "event_time": "2026-09-20T09:00:00Z", "temperature_c": -25.0},
    {"event_id": "late-2", "event_time": "2026-09-20T09:30:00Z", "temperature_c": 3.0}
  ]}'

# 4. 查询当前状态、暴露剂量与循环
curl http://localhost:8000/samples/sample-demo-001/state
curl http://localhost:8000/samples/sample-demo-001/cycles

# 5. 越限告警 / 未确认告警 / 确认
curl 'http://localhost:8000/alerts?sample_id=sample-demo-001'
curl 'http://localhost:8000/alerts?acknowledged=false'
curl -X POST http://localhost:8000/samples/sample-demo-001/alerts/ack \
  -H 'Content-Type: application/json' -d '{"acknowledged": true}'
```

更多可直接执行的示例：

- `examples/requests.http`（VS Code REST Client / IntelliJ HTTP Client）
- `examples/demo.sh`（curl 端到端脚本：正常数据 → 迟到重排 → 幂等去重 → 越限/超限告警）

```bash
chmod +x examples/demo.sh && ./examples/demo.sh
```

### 共享记录仪示例（curl）

```bash
# 1. 登记记录仪
curl -X POST http://localhost:8000/loggers \
  -H 'Content-Type: application/json' \
  -d '{"id": "logger-truck-07", "name": "7 号冷链车厢记录仪"}'

# 2. 提交记录仪读数（event_id、发生时间、温度）
curl -X POST http://localhost:8000/loggers/logger-truck-07/readings \
  -H 'Content-Type: application/json' \
  -d '{"readings": [
    {"event_id": "lg-1", "event_time": "2026-09-20T08:00:00Z", "temperature_c": 4.0},
    {"event_id": "lg-2", "event_time": "2026-09-20T08:30:00Z", "temperature_c": -22.0},
    {"event_id": "lg-3", "event_time": "2026-09-20T10:30:00Z", "temperature_c": 2.0}
  ]}'

# 3. 为样本设置左闭右开绑定时段（同一样本不得重叠）
curl -X POST 'http://localhost:8000/samples/sample-demo-001/logger-bindings?logger_id=logger-truck-07' \
  -H 'Content-Type: application/json' \
  -d '{"start_time": "2026-09-20T08:00:00Z", "end_time": "2026-09-20T12:00:00Z"}'

# 4. 查询绑定（样本侧 / 记录仪侧）与合并后的样本读数
curl http://localhost:8000/samples/sample-demo-001/logger-bindings
curl http://localhost:8000/loggers/logger-truck-07/bindings
curl 'http://localhost:8000/samples/sample-demo-001/readings?source=all'

# 5. 迟到读数：提交 09:00 的补录数据后，受影响样本自动撤销旧归属并重建
curl -X POST http://localhost:8000/loggers/logger-truck-07/readings \
  -H 'Content-Type: application/json' \
  -d '{"readings": [
    {"event_id": "lg-late", "event_time": "2026-09-20T09:00:00Z", "temperature_c": -25.0}
  ]}'

# 6. 温度一致性核查：样本直接读数 vs 当前归属的记录仪读数
#    （最大时间差 300 秒、温差阈值 2°C；只读，不影响冻融/剂量/告警）
curl 'http://localhost:8000/samples/sample-demo-001/logger-consistency/logger-truck-07?max_time_diff_seconds=300&temp_diff_threshold_c=2.0'

# 7. 校准无效时段：09:30–10:00 记录仪送校，时段内读数不归属样本（原始读数仍入库可查）
curl -X POST http://localhost:8000/loggers/logger-truck-07/invalid-periods \
  -H 'Content-Type: application/json' \
  -d '{"start_time": "2026-09-20T09:30:00Z", "end_time": "2026-09-20T10:00:00Z"}'

# 8. 查询 / 修改 / 删除无效时段（删除后有效读数恢复归属，受影响样本自动回算）
curl http://localhost:8000/loggers/logger-truck-07/invalid-periods
curl -X PATCH http://localhost:8000/invalid-periods/1 \
  -H 'Content-Type: application/json' -d '{"end_time": "2026-09-20T09:45:00Z"}'
curl -X DELETE http://localhost:8000/invalid-periods/1
```

## 时间处理

- 输入接受任意 ISO 8601 时间；带时区的统一换算为 UTC，**无时区时间按 UTC 处理**。
- 内部以 UTC ISO 8601 字符串存储，字典序即时间序，迟到数据只需插入即可参与正确重算。

## 配置项

| 环境变量 | 默认值 | 说明 |
 | --- | --- | --- |
| `DB_PATH` | 本地 `data/freeze_thaw.db`；镜像内 `/data/freeze_thaw.db` | SQLite 文件路径，启动自动建库 |

样本级配置（创建时指定，可 PATCH 修改）：`freeze_threshold_c`、`thaw_threshold_c`、
`min_allowed_c`、`max_allowed_c`、`max_freeze_thaw_cycles`、
`cold_dose_budget_c_min`、`heat_dose_budget_c_min`、
`max_integration_interval_minutes`。

暴露剂量相关字段：

| 字段 | 单位 | 说明 |
| --- | --- | --- |
| `cold_dose_budget_c_min` | °C·min | 低温累计剂量预算，首次跨越产生 `exposure_budget_exceeded`（需同时配置 `min_allowed_c`） |
| `heat_dose_budget_c_min` | °C·min | 高温累计剂量预算（需同时配置 `max_allowed_c`） |
| `max_integration_interval_minutes` | 分钟 | 相邻读数最大可积分间隔，超过则跳过该区间并计入缺口 |
| `cold_dose_c_min` / `heat_dose_c_min` | °C·min | 状态接口返回的双向累计剂量（派生值） |
| `exposure_gap_minutes` | 分钟 | 因间隔超过上限而跳过的缺口总时长（派生值） |
| `cold_budget_usage` / `heat_budget_usage` | 比值 | 预算使用率 = 剂量 / 预算，`>= 1` 表示已超限；未配置预算为 `null` |

### 数据库兼容迁移

容器启动（或本地 `init_db`）时会先幂等建表，再检测旧库缺失的列并以 `ALTER TABLE`
补齐（暴露剂量配置/缓存列、告警方向与剂量列、告警的记录仪读数引用列
`alerts.logger_reading_id`），并补建共享记录仪相关表
（`loggers` / `logger_readings` / `logger_bindings` / `reading_attributions` /
`logger_invalid_periods`）与保管人交接表（`custody_handovers`）。
旧版本的 SQLite 数据文件无需手工处理即可平滑升级，历史样本与读数保留，
首次重算后剂量缓存自动填充。

## 只读配液方案（`POST /solution-plans`）

关联**已存在的样本**做一次纯只读的配液计算：服务不维护也不扣减任何库存，
浓度与可用体积均由请求按场景给出，样本仅做存在性关联校验。

请求体（所有体积/浓度字段均为**整数**）：

| 字段 | 范围 | 说明 |
 | --- | --- | --- |
| `sources` | 1～6 项 | 来源列表，`sample_id` 不可重复且必须已存在 |
| `sources[].sample_id` | 1～128 字符 | 已登记样本 ID |
| `sources[].concentration_ng_ul` | 0～100 整数 | 该来源浓度（ng/µL） |
| `sources[].available_volume_ul` | 0～30 整数 | 该来源本次可用体积（µL） |
| `target_volume_ul` | 1～30 整数 | 目标配液体积（µL） |
| `target_concentration_ng_ul` | 0～100 整数 | 目标浓度（ng/µL） |

对各来源的整数取用体积 `x_i` µL 施加两条**精确**等式（全程整数运算，
不做浓度四舍五入，也不使用贪心近似）：

- `0 ≤ x_i ≤ sources[i].available_volume_ul`
- `Σ x_i = target_volume_ul`
- `Σ x_i · concentration_ng_ul = target_volume_ul · target_concentration_ng_ul`

搜索全部可行方案后按以下顺序择优（前者优先）：

1. **使用来源样本数最少**（`x_i > 0` 的项最少）；
2. 使用数相同时，按请求 `sources` 顺序组成的取用体积向量
   `(x_1, …, x_n)` 取**字典序最小**者（即尽量把用量压到更靠后的来源）。

响应逐项返回配方（**包含零用量项**，顺序与请求一致）及 `used_sample_count`、
`total_volume_ul`、`total_mass_ng`（便于直接核验双等式）：

```bash
curl -sS -X POST http://localhost:8000/solution-plans \
  -H 'Content-Type: application/json' \
  -d '{
    "sources": [
      {"sample_id": "sample-a", "concentration_ng_ul": 0,   "available_volume_ul": 10},
      {"sample_id": "sample-b", "concentration_ng_ul": 50,  "available_volume_ul": 10},
      {"sample_id": "sample-c", "concentration_ng_ul": 100, "available_volume_ul": 10}
    ],
    "target_volume_ul": 10,
    "target_concentration_ng_ul": 50
  }'
```

```json
{
  "target_volume_ul": 10,
  "target_concentration_ng_ul": 50,
  "used_sample_count": 1,
  "total_volume_ul": 10,
  "total_mass_ng": 500,
  "items": [
    {"sample_id": "sample-a", "concentration_ng_ul": 0,   "available_volume_ul": 10, "volume_ul": 0},
    {"sample_id": "sample-b", "concentration_ng_ul": 50,  "available_volume_ul": 10, "volume_ul": 10},
    {"sample_id": "sample-c", "concentration_ng_ul": 100, "available_volume_ul": 10, "volume_ul": 0}
  ]
}
```

状态码：

- `200`：存在精确方案，返回上述配方；
- `409`：不存在满足双等式的整数方案（例如目标浓度超出所有来源浓度可配平的范围、
  或只存在非整数解）；
- `422`：参数非法（类型/范围/来源个数）、来源 `sample_id` 重复、或任一来源样本不存在。

## 批次运输风险比较（`POST /transport-risk-comparisons`）

在一批**已存在**样本之间横向比较当前运输风险。接口为**纯只读计算**：
不触发重算、不写入任何数据，样本当前状态、读数、循环与告警（含确认状态）均不变。

### 请求

| 字段 | 范围 | 说明 |
| --- | --- | --- |
| `sample_ids` | 2～50 个字符串 | 参与比较的样本 ID，**不可重复**，且必须都已存在 |
| `weights.freeze_thaw_cycles` | 有限非负数字 | 冻融循环数权重 |
| `weights.cold_dose_c_min` | 有限非负数字 | 低温累计暴露剂量权重（°C·min） |
| `weights.heat_dose_c_min` | 有限非负数字 | 高温累计暴露剂量权重（°C·min） |
| `weights.exposure_gap_minutes` | 有限非负数字 | 暴露缺口分钟数权重 |
| `weights.unacknowledged_alerts` | 有限非负数字 | 未确认告警数权重 |

五项权重**至少一项严格为正**；接受 JSON 整数或小数（如 `2`、`0.5`），
不接受负数、字符串、布尔、`null`、`NaN` / `Infinity`。

### 指标与算法

五项指标均为**越低越安全**，直接取自各样本的**当前重算状态**
（即 `GET /samples/{id}/state` 返回的同名字段；未确认告警数为当前
`acknowledged=false` 的告警条数，确认告警后再次比较立即反映）：

1. **批内最小最大归一化**：对每项指标 `j`，
   `norm_ij = (x_ij - min_j) / (max_j - min_j)`，归一化范围 `[0, 1]`；
   该指标在本批所有样本上取值相同（分母为 0）时，该项统一记 **0**；
2. **总分**：`score_i = Σ_j w_j · norm_ij / Σ_j w_j`，范围 `[0, 1]`；
3. **非支配前沿**（基于五项**原始指标**，与权重无关）：当样本 A 的五项指标
   均不高于样本 B、且至少一项严格更低时，A **支配** B；不被任何其他样本支配者
   `on_front = true`。指标向量完全相同的样本互不支配，可同时位于前沿；
4. **排名**：结果按总分**升序**、总分相同时按样本 ID 字典序**升序**排列，
   `rank` 从 1 起（相同总分各自占一个名次）。

### 响应

- `sample_ids`：按**请求顺序**回显参与比较的 ID；
- `weights` / `weight_sum`：回显权重与权重和（严格为正，等于总分分母）；
- `items[]`：排名结果，每项含 `rank`、`sample_id`、
  `metrics`（五项原始指标）、`normalized`（五项批内归一化值）、
  `total_score`（总分）与 `on_front`（前沿标记）。

状态码：`200` 成功；`422` 样本数越界（2～50）、ID 重复、权重非法
（负数 / 非数字 / 非有限 / 全为 0 / 缺字段），或任一样本不存在
（错误信息中点明首个缺失 ID，如 `样本 ghost 不存在`）。

### 调用示例

```bash
curl -sS -X POST http://localhost:8000/transport-risk-comparisons \
  -H 'Content-Type: application/json' \
  -d '{
    "sample_ids": ["sample-demo-001", "sample-demo-002", "sample-demo-003"],
    "weights": {
      "freeze_thaw_cycles": 2,
      "cold_dose_c_min": 1,
      "heat_dose_c_min": 1,
      "exposure_gap_minutes": 1,
      "unacknowledged_alerts": 3
    }
  }'
```

```json
{
  "sample_ids": ["sample-demo-001", "sample-demo-002", "sample-demo-003"],
  "weights": {"freeze_thaw_cycles": 2.0, "cold_dose_c_min": 1.0, "heat_dose_c_min": 1.0,
              "exposure_gap_minutes": 1.0, "unacknowledged_alerts": 3.0},
  "weight_sum": 8.0,
  "items": [
    {
      "rank": 1,
      "sample_id": "sample-demo-001",
      "metrics": {"freeze_thaw_cycles": 1, "cold_dose_c_min": 0.0, "heat_dose_c_min": 0.0,
                  "exposure_gap_minutes": 0.0, "unacknowledged_alerts": 0},
      "normalized": {"freeze_thaw_cycles": 0.0, "cold_dose_c_min": 0.0, "heat_dose_c_min": 0.0,
                     "exposure_gap_minutes": 0.0, "unacknowledged_alerts": 0.0},
      "total_score": 0.0,
      "on_front": true
    }
  ]
}
```

Python 示例：

```python
import requests

resp = requests.post(
    "http://localhost:8000/transport-risk-comparisons",
    json={
        "sample_ids": ["sample-demo-001", "sample-demo-002"],
        "weights": {
            "freeze_thaw_cycles": 2,
            "cold_dose_c_min": 1,
            "heat_dose_c_min": 1,
            "exposure_gap_minutes": 1,
            "unacknowledged_alerts": 3,
        },
    },
    timeout=10,
)
resp.raise_for_status()
for item in resp.json()["items"]:
    print(item["rank"], item["sample_id"], round(item["total_score"], 4), item["on_front"])
```

## 测试

```bash
pytest -q
```

覆盖内容：阈值边界与滞回、死区防抖、不完整冻结不计循环、温度越限去抖告警、
循环超限告警、迟到数据重排、`event_id`/签名双重幂等、告警确认在重算后保留、404 处理，
以及暴露剂量的线性插值切分、双向累计、缺口跳过、预算跨越插值时刻告警、稳定去重与
配置变更可逆重算、旧版数据库 ALTER TABLE 迁移；共享记录仪部分覆盖登记/读数幂等、
左闭右开窗口与同一样本时段重叠拒绝、一读多样本归属、与直接读数合并重算冻融/剂量、
迟到读数与窗口 PATCH/DELETE 的归属撤销与无重复重建、直接读数不被改动、
记录仪来源告警的去重与确认保留、样本/记录仪删除级联；一致性核查部分覆盖
不交叉一对一配对的三级目标取舍（配对数最多、总时间差最小、读数 ID 序列稳定）、
时间差边界与零窗口、温差严格超限判定、两侧未配对计数、迟到读数与绑定调整后的
最新归属反映、核查只读不改写既有结果、参数校验（负值 422、缺失 422、不存在 404）；
校准无效时段部分覆盖时段增删改查与重叠/倒挂/端点相接校验、时段内读数照常幂等入库
但不归属样本且不参与冻融/剂量/告警/一致性核查、迟到读数与时段 PATCH/DELETE 后的
归属对账与恢复、直接读数与既有告警确认状态保留、逐记录仪隔离与删除级联；
保管责任归属部分覆盖交接登记与补录排序、同时刻重复登记 409、时区归一化与参数校验、
左闭右开责任区间匹配（恰在交接时刻归新保管人、首条之前责任人为空）、
补录交接与迟到读数/配置变更重算后归责查询即时反映最新结果且不残留过期归责、
既有告警确认语义保留、删除样本级联清理交接记录；
配液方案部分覆盖双等式精确性（无四舍五入/贪心）、容量上界与零可用体积、
最少来源数优先与同级向量字典序最小（含强制多来源场景）、目标零浓度与边界体积、
无解（浓度越界/非整数解/容量不足）返回 409、样本不存在与重复 ID 返回 422、
整数字段严格类型与取值范围校验（1～6 个来源、目标体积 1～30、浓度 0～100）、
只读不改写任何数据，并用暴力枚举在随机小空间上交叉验证求解器择优正确性；
运输风险比较部分覆盖批内最小最大归一化（含全相同列记 0、全相同向量）、
加权总分除以权重和（零权重列不影响总分但仍归一化）、总分落在 [0,1]、
非支配前沿的严格支配定义（全 0 点支配全部、相同向量互不支配、互不可比点共存）、
总分升序 + 样本 ID 升序排名，API 层覆盖五项指标与 `/state` 一致、
确认告警后未确认数即时归 0、缺口分钟数指标、重复比较前后状态/告警完全不变
（纯只读、不触发重算）、样本不存在/ID 重复/数量越界（1 与 51）/权重非法
（负数、字符串、布尔、null、全 0、NaN/Infinity、缺字段）均返回 422，
另覆盖非有限浮点出现在 422 错误回显中时仍能正常序列化返回（不回退成 500）。
