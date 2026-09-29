"""FastAPI 入口：启动即建库，并暴露样本 / 读数 / 状态 / 循环 / 告警接口。"""

from __future__ import annotations

import math
from typing import Optional

import sqlite3
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse

from . import __version__, services
from .database import init_db
from .schemas import (
    AckRequest,
    AlertCustodyItem,
    AlertOut,
    BatchReadingsResponse,
    CustodyHandoverIn,
    CustodyHandoverOut,
    CycleOut,
    LoggerBindingIn,
    LoggerBindingOut,
    LoggerBindingUpdate,
    LoggerConsistencyResponse,
    LoggerCreate,
    LoggerInvalidPeriodIn,
    LoggerInvalidPeriodOut,
    LoggerInvalidPeriodUpdate,
    LoggerOut,
    LoggerReadingOut,
    LoggerReadingsResponse,
    LoggerUpdate,
    ReadingOut,
    RecomputeResponse,
    ReadingIn,
    ReadingsBatch,
    SampleCreate,
    SampleOut,
    SampleStateOut,
    SampleUpdate,
    SolutionPlanRequest,
    SolutionPlanResponse,
    TransportRiskComparisonRequest,
    TransportRiskComparisonResponse,
)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 启动即初始化数据库（幂等建表、索引）
    init_db()
    yield


app = FastAPI(
    title="实验室样本冻融追踪 API",
    description=(
        "接收带事件时间的样本温度读数，按时间顺序重排迟到数据，"
        "以可配置的冻结/解冻阈值和滞回规则识别完整冻融循环，"
        "提供幂等去重、当前状态、循环次数与超限告警查询。"
        "支持共享温度记录仪：登记记录仪与读数，并以左闭右开时段绑定到样本，"
        "记录仪读数按发生时间归入绑定窗口内的样本（一读多样本），"
        "与样本直接读数一起重算冻融循环、暴露剂量与告警。"
        "记录仪可登记校准无效时段（左闭右开、互不重叠）：时段内读数照常入库可查，"
        "但不归属样本、不参与冻融/剂量/告警与一致性核查，时段删除后有效读数恢复归属。"
        "另提供样本 × 记录仪温度一致性核查：对样本直接读数与当前归属的记录仪读数"
        "做不交叉一对一最优配对（配对数最多、总时间差最小、读数 ID 序列稳定取舍），"
        "返回温差超限配对明细与配对/未配对数量。"
        "支持样本告警保管责任归属：按生效时间登记保管人交接（允许补录，同时刻仅一条），"
        "相邻交接形成左闭右开责任区间；告警归责查询按当前告警事件时间匹配区间、"
        "查询时即时计算不持久化，补录交接或告警重算后即反映最新结果。"
        "另提供只读配液方案求解：给定 1～6 个已存在样本的整数浓度与可用体积以及"
        "目标体积/浓度，精确枚举满足体积与质量（浓度×体积）双等式的整数取用方案，"
        "按使用来源样本数最少、取用体积向量字典序最小择优；只读不扣减库存，"
        "无精确解返回 409，样本不存在、重复 ID 或参数非法返回 422。"
        "另提供只读批次运输风险比较：给定 2～50 个已存在样本与五项有限非负权重"
        "（冻融循环数、低温/高温剂量、暴露缺口分钟数、未确认告警数，至少一项为正），"
        "从各样本当前重算状态读取五项指标，在批内逐指标最小最大归一化"
        "（全相同记零），加权和除以权重和得总分，并基于原始指标求非支配前沿，"
        "按总分升序、样本 ID 升序排名；纯只读，不触发重算、不改状态与告警。"
    ),
    version=__version__,
    lifespan=lifespan,
)


def _sanitize_json_value(value):
    """把非有限浮点（NaN/Infinity）替换为字符串占位，保证错误体可序列化。

    Starlette 默认 JSON 解析接受 ``NaN`` / ``Infinity`` token（Python
    ``json`` 语义），Pydantic 拒绝后默认 422 处理器会回显该输入值，
    而标准 JSON 无法编码非有限浮点，反而导致 500；这里净化后正常返回 422。
    """

    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value
    if isinstance(value, dict):
        return {k: _sanitize_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_json_value(v) for v in value]
    return value


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(_request: Request, exc: RequestValidationError):
    # 先净化 NaN/Infinity 等非有限浮点回显值（jsonable_encoder 对它们
    # 仍输出非标准 token，starlette JSONResponse 以 allow_nan=False 序列化会 500），
    # 再沿用官方 jsonable_encoder 处理其余错误结构（如 ctx 中的异常对象）。
    safe_errors = _sanitize_json_value(exc.errors())
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": jsonable_encoder(safe_errors)},
    )


def _require_sample(conn: sqlite3.Connection, sample_id: str) -> sqlite3.Row:
    sample = services.get_sample(conn, sample_id)
    if sample is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="样本不存在")
    return sample


def _require_logger(conn: sqlite3.Connection, logger_id: str) -> sqlite3.Row:
    logger = services.get_logger(conn, logger_id)
    if logger is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="记录仪不存在")
    return logger


# ---------------- 健康检查 / 文档 ----------------


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


# ---------------- 样本管理 ----------------


@app.post("/samples", response_model=SampleOut, status_code=201, tags=["samples"])
def create_sample(payload: SampleCreate, conn=Depends(services.get_db_connection)):
    data = payload.model_dump()
    if data.get("id"):
        if services.get_sample(conn, data["id"]) is not None:
            raise HTTPException(status_code=409, detail="样本 ID 已存在")
    return services.create_sample(conn, data)


@app.get("/samples", response_model=list[SampleOut], tags=["samples"])
def list_samples(conn=Depends(services.get_db_connection)):
    return services.list_samples(conn)


@app.get("/samples/{sample_id}", response_model=SampleOut, tags=["samples"])
def get_sample(sample_id: str, conn=Depends(services.get_db_connection)):
    return _require_sample(conn, sample_id)


@app.patch("/samples/{sample_id}", response_model=SampleOut, tags=["samples"])
def update_sample(
    sample_id: str, payload: SampleUpdate, conn=Depends(services.get_db_connection)
):
    _require_sample(conn, sample_id)
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is None:
        # name 为显式 null 时拒绝（其余可空配置字段允许显式 null 清空）
        raise HTTPException(status_code=422, detail="name 不能为空")
    existing = services.get_sample(conn, sample_id)
    merged = {
        "freeze_threshold_c": existing["freeze_threshold_c"],
        "thaw_threshold_c": existing["thaw_threshold_c"],
        "min_allowed_c": existing["min_allowed_c"],
        "max_allowed_c": existing["max_allowed_c"],
        "cold_dose_budget_c_min": existing["cold_dose_budget_c_min"],
        "heat_dose_budget_c_min": existing["heat_dose_budget_c_min"],
    }
    merged.update(changes)  # 显式 null 也参与合并，用于清空边界/预算
    if merged["freeze_threshold_c"] >= merged["thaw_threshold_c"]:
        raise HTTPException(
            status_code=422,
            detail="freeze_threshold_c 必须严格小于 thaw_threshold_c",
        )
    if (
        merged.get("cold_dose_budget_c_min") is not None
        and merged.get("min_allowed_c") is None
    ):
        raise HTTPException(
            status_code=422,
            detail="配置 cold_dose_budget_c_min 时必须同时提供 min_allowed_c",
        )
    if (
        merged.get("heat_dose_budget_c_min") is not None
        and merged.get("max_allowed_c") is None
    ):
        raise HTTPException(
            status_code=422,
            detail="配置 heat_dose_budget_c_min 时必须同时提供 max_allowed_c",
        )
    services.update_sample(conn, sample_id, changes)
    # 阈值/区间/循环上限/暴露剂量预算或积分间隔变化后按新配置重算
    # （告警确认状态通过 dedup_key 保留）
    if any(
        k in changes
        for k in (
            "freeze_threshold_c",
            "thaw_threshold_c",
            "min_allowed_c",
            "max_allowed_c",
            "max_freeze_thaw_cycles",
            "cold_dose_budget_c_min",
            "heat_dose_budget_c_min",
            "max_integration_interval_minutes",
        )
    ):
        services.recompute(conn, sample_id)
    return services.get_sample(conn, sample_id)


@app.delete("/samples/{sample_id}", status_code=204, tags=["samples"])
def delete_sample(sample_id: str, conn=Depends(services.get_db_connection)):
    if not services.delete_sample(conn, sample_id):
        raise HTTPException(status_code=404, detail="样本不存在")


# ---------------- 读数提交（幂等） ----------------


@app.post(
    "/samples/{sample_id}/readings",
    response_model=BatchReadingsResponse,
    status_code=200,
    tags=["readings"],
)
def submit_readings(
    sample_id: str,
    payload: ReadingsBatch,
    conn=Depends(services.get_db_connection),
):
    _require_sample(conn, sample_id)
    accepted, duplicates, new_alerts = services.add_readings(
        conn,
        sample_id,
        [r.model_dump() for r in payload.readings],
    )
    sample = services.get_sample(conn, sample_id)
    return BatchReadingsResponse(
        sample_id=sample_id,
        accepted=accepted,
        duplicates=duplicates,
        current_state=sample["current_state"],
        cycle_count=sample["cycle_count"],
        new_alerts=new_alerts,
    )


@app.get(
    "/samples/{sample_id}/readings",
    response_model=list[ReadingOut],
    tags=["readings"],
)
def get_readings(
    sample_id: str,
    source: str = Query(
        "all",
        pattern="^(all|direct|logger)$",
        description="all=直接读数与记录仪归属读数合并时间轴；direct=仅直接读数；logger=仅记录仪归属读数",
    ),
    conn=Depends(services.get_db_connection),
):
    _require_sample(conn, sample_id)
    return services.list_readings(conn, sample_id, source=source)


# ---------------- 状态与循环 ----------------


@app.get(
    "/samples/{sample_id}/state",
    response_model=SampleStateOut,
    tags=["state"],
)
def get_sample_state(sample_id: str, conn=Depends(services.get_db_connection)):
    _require_sample(conn, sample_id)
    return services.get_state(conn, sample_id)


@app.get(
    "/samples/{sample_id}/cycles",
    response_model=list[CycleOut],
    tags=["state"],
)
def get_cycles(sample_id: str, conn=Depends(services.get_db_connection)):
    _require_sample(conn, sample_id)
    return services.list_cycles(conn, sample_id)


@app.post(
    "/samples/{sample_id}/recompute",
    response_model=RecomputeResponse,
    tags=["state"],
)
def recompute_sample(sample_id: str, conn=Depends(services.get_db_connection)):
    """手动触发重算（正常提交读数后会自动重算，此接口用于兜底/运维）。"""

    _require_sample(conn, sample_id)
    before = {
        row["dedup_key"]
        for row in conn.execute(
            "SELECT dedup_key FROM alerts WHERE sample_id = ?", (sample_id,)
        )
    }
    new_rows = services.recompute(conn, sample_id)
    sample = services.get_sample(conn, sample_id)
    alert_count = conn.execute(
        "SELECT COUNT(*) AS n FROM alerts WHERE sample_id = ?", (sample_id,)
    ).fetchone()["n"]
    new_count = sum(1 for row in new_rows if row["dedup_key"] not in before)
    return RecomputeResponse(
        sample_id=sample_id,
        cycle_count=sample["cycle_count"],
        current_state=sample["current_state"],
        alert_count=alert_count,
        new_alert_count=new_count,
    )


# ---------------- 告警 ----------------


@app.get("/alerts", response_model=list[AlertOut], tags=["alerts"])
def query_alerts(
    sample_id: Optional[str] = None,
    acknowledged: Optional[bool] = Query(None, description="true/false 过滤确认状态"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    conn=Depends(services.get_db_connection),
):
    return services.list_alerts(
        conn,
        sample_id=sample_id,
        acknowledged=acknowledged,
        limit=limit,
        offset=offset,
    )


@app.get("/alerts/{alert_id}", response_model=AlertOut, tags=["alerts"])
def get_alert(alert_id: int, conn=Depends(services.get_db_connection)):
    alert = services.get_alert(conn, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="告警不存在")
    return alert


@app.post("/alerts/{alert_id}/ack", response_model=AlertOut, tags=["alerts"])
def acknowledge_alert(
    alert_id: int,
    payload: AckRequest,
    conn=Depends(services.get_db_connection),
):
    alert = services.acknowledge_alert(conn, alert_id, payload.acknowledged)
    if alert is None:
        raise HTTPException(status_code=404, detail="告警不存在")
    return alert


@app.post("/samples/{sample_id}/alerts/ack", tags=["alerts"])
def acknowledge_sample_alerts(
    sample_id: str,
    payload: AckRequest,
    conn=Depends(services.get_db_connection),
):
    _require_sample(conn, sample_id)
    count = services.acknowledge_sample_alerts(conn, sample_id, payload.acknowledged)
    return {"sample_id": sample_id, "updated": count, "acknowledged": payload.acknowledged}


# ---------------- 共享温度记录仪 ----------------


@app.post("/loggers", response_model=LoggerOut, status_code=201, tags=["loggers"])
def create_logger(payload: LoggerCreate, conn=Depends(services.get_db_connection)):
    data = payload.model_dump()
    if data.get("id"):
        if services.get_logger(conn, data["id"]) is not None:
            raise HTTPException(status_code=409, detail="记录仪 ID 已存在")
    return services.create_logger(conn, data)


@app.get("/loggers", response_model=list[LoggerOut], tags=["loggers"])
def list_loggers(conn=Depends(services.get_db_connection)):
    return services.list_loggers(conn)


@app.get("/loggers/{logger_id}", response_model=LoggerOut, tags=["loggers"])
def get_logger(logger_id: str, conn=Depends(services.get_db_connection)):
    return _require_logger(conn, logger_id)


@app.patch("/loggers/{logger_id}", response_model=LoggerOut, tags=["loggers"])
def update_logger(
    logger_id: str, payload: LoggerUpdate, conn=Depends(services.get_db_connection)
):
    _require_logger(conn, logger_id)
    return services.update_logger(conn, logger_id, payload.name)


@app.delete("/loggers/{logger_id}", status_code=204, tags=["loggers"])
def delete_logger(logger_id: str, conn=Depends(services.get_db_connection)):
    if not services.delete_logger(conn, logger_id):
        raise HTTPException(status_code=404, detail="记录仪不存在")


@app.post(
    "/loggers/{logger_id}/readings",
    response_model=LoggerReadingsResponse,
    status_code=200,
    tags=["loggers"],
)
def submit_logger_readings(
    logger_id: str,
    payload: ReadingsBatch,
    conn=Depends(services.get_db_connection),
):
    _require_logger(conn, logger_id)
    accepted, duplicates, new_alerts = services.add_logger_readings(
        conn, logger_id, [r.model_dump() for r in payload.readings]
    )
    affected = services.samples_bound_to_logger(conn, logger_id)
    return LoggerReadingsResponse(
        logger_id=logger_id,
        accepted=accepted,
        duplicates=duplicates,
        affected_samples=affected,
        new_alerts=new_alerts,
    )


@app.get(
    "/loggers/{logger_id}/readings",
    response_model=list[LoggerReadingOut],
    tags=["loggers"],
)
def get_logger_readings(logger_id: str, conn=Depends(services.get_db_connection)):
    _require_logger(conn, logger_id)
    return services.list_logger_readings(conn, logger_id)


# ---------------- 记录仪 ↔ 样本绑定（左闭右开时段） ----------------


@app.get(
    "/samples/{sample_id}/logger-bindings",
    response_model=list[LoggerBindingOut],
    tags=["loggers"],
)
def list_sample_bindings(sample_id: str, conn=Depends(services.get_db_connection)):
    _require_sample(conn, sample_id)
    return services.list_bindings_for_sample(conn, sample_id)


@app.get(
    "/loggers/{logger_id}/bindings",
    response_model=list[LoggerBindingOut],
    tags=["loggers"],
)
def list_logger_bindings(logger_id: str, conn=Depends(services.get_db_connection)):
    _require_logger(conn, logger_id)
    return services.list_bindings_for_logger(conn, logger_id)


@app.post(
    "/samples/{sample_id}/logger-bindings",
    response_model=LoggerBindingOut,
    status_code=201,
    tags=["loggers"],
)
def create_binding(
    sample_id: str,
    logger_id: str,
    payload: LoggerBindingIn,
    conn=Depends(services.get_db_connection),
):
    _require_sample(conn, sample_id)
    _require_logger(conn, logger_id)
    try:
        return services.create_binding(
            conn, sample_id, logger_id, payload.start_time, payload.end_time
        )
    except services.ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.patch(
    "/logger-bindings/{binding_id}",
    response_model=LoggerBindingOut,
    tags=["loggers"],
)
def update_binding(
    binding_id: int,
    payload: LoggerBindingUpdate,
    conn=Depends(services.get_db_connection),
):
    if services.get_binding(conn, binding_id) is None:
        raise HTTPException(status_code=404, detail="绑定不存在")
    changes = payload.model_dump(exclude_unset=True)
    try:
        return services.update_binding(conn, binding_id, changes)
    except services.ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.delete("/logger-bindings/{binding_id}", status_code=204, tags=["loggers"])
def delete_binding(binding_id: int, conn=Depends(services.get_db_connection)):
    if not services.delete_binding(conn, binding_id):
        raise HTTPException(status_code=404, detail="绑定不存在")


# ---------------- 记录仪校准无效时段 ----------------


@app.post(
    "/loggers/{logger_id}/invalid-periods",
    response_model=LoggerInvalidPeriodOut,
    status_code=201,
    tags=["loggers"],
)
def create_invalid_period(
    logger_id: str,
    payload: LoggerInvalidPeriodIn,
    conn=Depends(services.get_db_connection),
):
    """登记校准无效时段（左闭右开；同一记录仪时段不重叠）。

    落在时段内的记录仪读数仍照常幂等入库、可经原始读数接口查询，
    但不归属样本，不参与冻融循环、暴露剂量、告警与一致性核查；
    登记后自动对绑定该记录仪的样本重新对账并回算。
    """

    _require_logger(conn, logger_id)
    try:
        return services.create_invalid_period(
            conn, logger_id, payload.start_time, payload.end_time
        )
    except services.ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get(
    "/loggers/{logger_id}/invalid-periods",
    response_model=list[LoggerInvalidPeriodOut],
    tags=["loggers"],
)
def list_invalid_periods(logger_id: str, conn=Depends(services.get_db_connection)):
    _require_logger(conn, logger_id)
    return services.list_invalid_periods(conn, logger_id)


@app.get(
    "/invalid-periods/{period_id}",
    response_model=LoggerInvalidPeriodOut,
    tags=["loggers"],
)
def get_invalid_period(period_id: int, conn=Depends(services.get_db_connection)):
    period = services.get_invalid_period(conn, period_id)
    if period is None:
        raise HTTPException(status_code=404, detail="校准无效时段不存在")
    return period


@app.patch(
    "/invalid-periods/{period_id}",
    response_model=LoggerInvalidPeriodOut,
    tags=["loggers"],
)
def update_invalid_period(
    period_id: int,
    payload: LoggerInvalidPeriodUpdate,
    conn=Depends(services.get_db_connection),
):
    if services.get_invalid_period(conn, period_id) is None:
        raise HTTPException(status_code=404, detail="校准无效时段不存在")
    changes = payload.model_dump(exclude_unset=True)
    try:
        return services.update_invalid_period(conn, period_id, changes)
    except services.ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.delete("/invalid-periods/{period_id}", status_code=204, tags=["loggers"])
def delete_invalid_period(period_id: int, conn=Depends(services.get_db_connection)):
    """删除校准无效时段：原时段内的有效读数恢复归属，受影响样本自动回算。"""

    if not services.delete_invalid_period(conn, period_id):
        raise HTTPException(status_code=404, detail="校准无效时段不存在")


# ---------------- 样本 × 记录仪温度一致性核查 ----------------


@app.get(
    "/samples/{sample_id}/logger-consistency/{logger_id}",
    response_model=LoggerConsistencyResponse,
    tags=["loggers"],
)
def check_logger_consistency(
    sample_id: str,
    logger_id: str,
    max_time_diff_seconds: float = Query(
        ..., ge=0, description="允许配对的最大事件时间差（秒，非负；恰好等于也可配对）"
    ),
    temp_diff_threshold_c: float = Query(
        ..., ge=0, description="温差异常阈值（°C，非负）；配对温差严格大于该值才计入异常"
    ),
    conn=Depends(services.get_db_connection),
):
    """样本直接读数 vs 当前有效归属的该记录仪读数：不交叉一对一最优配对。

    配对目标按字典序：配对数最多 → 总时间差最小 → 配对读数 ID 序列稳定取舍。
    只读接口：迟到读数 / 绑定调整后的查询反映最新归属，
    不改动原读数与既有冻融、剂量、告警结果；未配对读数不算温差异常。
    """

    _require_sample(conn, sample_id)
    _require_logger(conn, logger_id)
    return services.logger_consistency_check(
        conn, sample_id, logger_id, max_time_diff_seconds, temp_diff_threshold_c
    )


# ---------------- 保管人交接与告警归责 ----------------


@app.post(
    "/samples/{sample_id}/custody-handovers",
    response_model=CustodyHandoverOut,
    status_code=201,
    tags=["custody"],
)
def create_custody_handover(
    sample_id: str,
    payload: CustodyHandoverIn,
    conn=Depends(services.get_db_connection),
):
    """按生效时间登记保管人交接（允许补录较早交接）。

    同一样本同一生效时刻仅能登记一条，重复登记返回 409；
    交接只参与查询时的归责匹配，不触发告警重算。
    """

    _require_sample(conn, sample_id)
    try:
        return services.create_handover(
            conn, sample_id, payload.custodian, payload.effective_time, payload.note
        )
    except services.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get(
    "/samples/{sample_id}/custody-handovers",
    response_model=list[CustodyHandoverOut],
    tags=["custody"],
)
def list_custody_handovers(sample_id: str, conn=Depends(services.get_db_connection)):
    """样本的交接记录列表（按生效时间升序；相邻记录形成左闭右开责任区间）。"""

    _require_sample(conn, sample_id)
    return services.list_handovers(conn, sample_id)


@app.get(
    "/samples/{sample_id}/alert-custody",
    response_model=list[AlertCustodyItem],
    tags=["custody"],
)
def get_alert_custody(sample_id: str, conn=Depends(services.get_db_connection)):
    """样本当前告警的归责查询：按告警事件时间匹配左闭右开责任区间。

    恰在交接生效时刻归新保管人；首条交接之前责任人为空（custodian/handover_id 为 null）。
    归责在查询时即时计算、不持久化：补录交接或迟到读数引起告警重算后，
    再次查询即反映最新结果；不影响既有告警确认语义。
    """

    _require_sample(conn, sample_id)
    return services.alert_custody(conn, sample_id)


# ---------------- 只读配液方案 ----------------


@app.post(
    "/solution-plans",
    response_model=SolutionPlanResponse,
    tags=["solution-plans"],
)
def create_solution_plan(
    payload: SolutionPlanRequest,
    conn=Depends(services.get_db_connection),
):
    """只读配液方案：精确枚举，不扣减任何库存。

    给定 1～6 个**已存在**来源样本（请求中各自给出整数浓度 ng/µL 与可用体积 µL）
    与目标体积（1～30 µL）/目标浓度（0～100 ng/µL），求整数取用体积 ``x_i``：

    * ``0 ≤ x_i ≤ 可用体积``；
    * ``Σ x_i = 目标体积``；
    * ``Σ x_i × 来源浓度 = 目标体积 × 目标浓度``（精确相等，无四舍五入、无贪心近似）。

    先按**使用来源样本数最少**、再按请求顺序的取用体积向量**字典序最小**择优，
    逐项返回配方（含零用量项）与使用样本数。无精确解返回 409；
    样本不存在、来源 ID 重复或参数（范围/类型/个数）非法返回 422。
    接口为纯只读计算：不读取库存字段、不修改任何数据。
    """

    try:
        result = services.plan_solution(conn, payload.model_dump())
    except services.ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=409, detail="不存在满足目标体积与浓度的精确配液方案")
    return result


# ---------------- 批次运输风险比较（只读） ----------------


@app.post(
    "/transport-risk-comparisons",
    response_model=TransportRiskComparisonResponse,
    tags=["transport-risk"],
)
def compare_transport_risk(
    payload: TransportRiskComparisonRequest,
    conn=Depends(services.get_db_connection),
):
    """只读比较 2～50 个**已存在**样本的当前运输风险。

    从各样本的**当前重算状态**读取五项指标（越低越好）：
    冻融循环数、低温剂量（°C·min）、高温剂量（°C·min）、
    暴露缺口分钟数、未确认告警数。

    * **归一化**：在本批样本内逐指标做最小最大归一化
      ``(x - min) / (max - min)``；某指标所有样本取值相同时该项记 0；
    * **总分**：五项归一化值的加权和除以权重和（权重为五项有限非负数，
      至少一项为正）；
    * **非支配前沿**：基于五项原始指标，一条样本所有指标不高于另一条且
      至少一项严格更低时支配后者，`on_front=true` 表示不被任何样本支配；
    * **排名**：结果按总分升序、样本 ID 升序排列（`rank` 从 1 起）。

    纯只读计算：不触发重算，不改变样本状态、读数、循环与告警（含确认状态）。
    样本 ID 重复、数量越界（2～50）或权重非法（负数 / NaN / 全为 0）返回 422；
    任一样本不存在同样返回 422（在错误信息中点明缺失的样本 ID）。
    """

    try:
        return services.compare_transport_risk(conn, payload.model_dump())
    except services.ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
