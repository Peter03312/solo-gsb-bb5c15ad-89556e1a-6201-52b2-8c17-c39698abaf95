"""请求 / 响应 Pydantic 模型。"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, model_validator


def _freeze_lt_thaw(data: dict) -> dict:
    freeze = data.get("freeze_threshold_c")
    thaw = data.get("thaw_threshold_c")
    if freeze is not None and thaw is not None and freeze >= thaw:
        raise ValueError("freeze_threshold_c 必须严格小于 thaw_threshold_c")
    return data


def _validate_dose_fields(data: dict) -> dict:
    """暴露剂量相关字段的跨字段校验。"""

    for name in (
        "cold_dose_budget_c_min",
        "heat_dose_budget_c_min",
        "max_integration_interval_minutes",
    ):
        value = data.get(name)
        if value is not None and value <= 0:
            raise ValueError(f"{name} 必须为正数")

    if data.get("cold_dose_budget_c_min") is not None and data.get("min_allowed_c") is None:
        raise ValueError("配置 cold_dose_budget_c_min 时必须同时提供 min_allowed_c")
    if data.get("heat_dose_budget_c_min") is not None and data.get("max_allowed_c") is None:
        raise ValueError("配置 heat_dose_budget_c_min 时必须同时提供 max_allowed_c")
    return data


# ---------------- 请求模型 ----------------


class SampleCreate(BaseModel):
    id: Optional[str] = Field(None, max_length=128, description="不填则自动生成 UUID")
    name: str = Field(..., max_length=256)
    freeze_threshold_c: float = Field(..., description="温度 <= 该值判定为冻结（默认建议 -20）")
    thaw_threshold_c: float = Field(..., description="温度 >= 该值判定为解冻（默认建议 0）")
    min_allowed_c: Optional[float] = Field(None, description="允许的最低温度，低于即告警")
    max_allowed_c: Optional[float] = Field(None, description="允许的最高温度，高于即告警")
    max_freeze_thaw_cycles: Optional[int] = Field(None, ge=0, description="允许的最大冻融循环次数")
    cold_dose_budget_c_min: Optional[float] = Field(
        None,
        gt=0,
        description="低温累计暴露剂量预算（°C·min），首次跨越产生 exposure_budget_exceeded 告警",
    )
    heat_dose_budget_c_min: Optional[float] = Field(
        None, gt=0, description="高温累计暴露剂量预算（°C·min）"
    )
    max_integration_interval_minutes: Optional[float] = Field(
        None,
        gt=0,
        description="相邻读数最大可积分间隔（分钟）；超过则跳过该区间并计入缺口时长",
    )

    @model_validator(mode="after")
    def _validate(self):
        if self.freeze_threshold_c >= self.thaw_threshold_c:
            raise ValueError("freeze_threshold_c 必须严格小于 thaw_threshold_c")
        _validate_dose_fields(self.model_dump())
        return self


class SampleUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=256)
    freeze_threshold_c: Optional[float] = None
    thaw_threshold_c: Optional[float] = None
    min_allowed_c: Optional[float] = None
    max_allowed_c: Optional[float] = None
    max_freeze_thaw_cycles: Optional[int] = Field(None, ge=0)
    cold_dose_budget_c_min: Optional[float] = Field(None, gt=0)
    heat_dose_budget_c_min: Optional[float] = Field(None, gt=0)
    max_integration_interval_minutes: Optional[float] = Field(None, gt=0)

    @model_validator(mode="after")
    def _validate(self):
        data = self.model_dump(exclude_unset=True)
        _freeze_lt_thaw(data)
        # 是否同时提供允许区间边界的跨字段检查由路由层合并现有配置后完成
        for name in (
            "cold_dose_budget_c_min",
            "heat_dose_budget_c_min",
            "max_integration_interval_minutes",
        ):
            if data.get(name) is not None and data[name] <= 0:
                raise ValueError(f"{name} 必须为正数")
        return self


class ReadingIn(BaseModel):
    temperature_c: float = Field(..., description="摄氏温度读数")
    event_time: datetime = Field(..., description="读数发生时间（ISO 8601，无时区按 UTC 处理）")
    event_id: Optional[str] = Field(
        None,
        max_length=128,
        description="客户端幂等键；不填则以 (event_time, temperature_c) 去重",
    )


class ReadingsBatch(BaseModel):
    readings: list[ReadingIn] = Field(..., min_length=1, max_length=10_000)


# ---------------- 共享记录仪 ----------------


class LoggerCreate(BaseModel):
    id: Optional[str] = Field(None, max_length=128, description="不填则自动生成 UUID")
    name: str = Field(..., max_length=256)


class LoggerUpdate(BaseModel):
    name: str = Field(..., max_length=256)


class LoggerBindingIn(BaseModel):
    start_time: datetime = Field(..., description="绑定时段起点（含，左闭；ISO 8601，无时区按 UTC 处理）")
    end_time: datetime = Field(..., description="绑定时段终点（不含，右开）")

    @model_validator(mode="after")
    def _validate(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time 必须严格晚于 start_time（左闭右开时段）")
        return self


class LoggerBindingUpdate(BaseModel):
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    @model_validator(mode="after")
    def _validate(self):
        start = self.start_time
        end = self.end_time
        if start is not None and end is not None and end <= start:
            raise ValueError("end_time 必须严格晚于 start_time（左闭右开时段）")
        return self


class LoggerInvalidPeriodIn(BaseModel):
    start_time: datetime = Field(..., description="校准无效时段起点（含，左闭；ISO 8601，无时区按 UTC 处理）")
    end_time: datetime = Field(..., description="校准无效时段终点（不含，右开）")

    @model_validator(mode="after")
    def _validate(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time 必须严格晚于 start_time（左闭右开时段）")
        return self


class LoggerInvalidPeriodUpdate(BaseModel):
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    @model_validator(mode="after")
    def _validate(self):
        start = self.start_time
        end = self.end_time
        if start is not None and end is not None and end <= start:
            raise ValueError("end_time 必须严格晚于 start_time（左闭右开时段）")
        return self


class AckRequest(BaseModel):
    acknowledged: bool = True


# ---------------- 保管人交接与告警归责 ----------------


class CustodyHandoverIn(BaseModel):
    custodian: str = Field(..., min_length=1, max_length=128, description="接任保管人姓名/标识")
    effective_time: datetime = Field(
        ...,
        description="交接生效时间（ISO 8601，无时区按 UTC 处理）；允许补录较早时刻",
    )
    note: Optional[str] = Field(None, max_length=512, description="备注（可选）")


class CustodyHandoverOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    sample_id: str
    custodian: str
    effective_time: datetime
    note: Optional[str]
    created_at: datetime


class AlertCustodyItem(BaseModel):
    """一条当前告警的归责结果：按事件时间匹配左闭右开责任区间。"""

    alert_id: int = Field(..., description="告警 ID（alerts.id）")
    event_time: datetime = Field(..., description="告警事件时间")
    custodian: Optional[str] = Field(
        ..., description="责任保管人；首条交接之前为 null（责任人为空）"
    )
    handover_id: Optional[int] = Field(
        ..., description="匹配到的交接记录 ID；首条交接之前为 null"
    )


# ---------------- 响应模型 ----------------


class SampleOut(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    name: str
    freeze_threshold_c: float
    thaw_threshold_c: float
    min_allowed_c: Optional[float]
    max_allowed_c: Optional[float]
    max_freeze_thaw_cycles: Optional[int]
    cold_dose_budget_c_min: Optional[float]
    heat_dose_budget_c_min: Optional[float]
    max_integration_interval_minutes: Optional[float]
    current_state: str
    active_freeze_event_time: Optional[datetime]
    active_freeze_min_c: Optional[float]
    active_freeze_max_c: Optional[float]
    cycle_count: int
    cold_dose_c_min: float
    heat_dose_c_min: float
    exposure_gap_minutes: float
    created_at: datetime
    updated_at: datetime


class ReadingOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    sample_id: Optional[str] = None
    event_id: Optional[str]
    event_time: datetime
    received_at: datetime
    temperature_c: float
    # 读数来源：direct=样本直接读数；logger=记录仪读数经绑定时段归入
    source: str = "direct"
    logger_id: Optional[str] = None
    logger_reading_id: Optional[int] = None


class LoggerOut(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    name: str
    created_at: datetime
    updated_at: datetime


class LoggerReadingOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    logger_id: str
    event_id: Optional[str]
    event_time: datetime
    received_at: datetime
    temperature_c: float


class LoggerBindingOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    sample_id: str
    logger_id: str
    start_time: datetime
    end_time: datetime
    created_at: datetime
    updated_at: datetime
    # 联表查询时附带名称，便于直接展示
    sample_name: Optional[str] = None
    logger_name: Optional[str] = None
    # 该时段当前归入的记录仪读数条数
    reading_count: Optional[int] = None


class LoggerInvalidPeriodOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    logger_id: str
    start_time: datetime
    end_time: datetime
    created_at: datetime
    updated_at: datetime
    # 联表查询时附带名称，便于直接展示
    logger_name: Optional[str] = None
    # 落在该时段内、被排除归属的记录仪原始读数条数
    reading_count: Optional[int] = None


class CycleOut(BaseModel):
    model_config = {"from_attributes": True}

    cycle_index: int
    freeze_event_time: datetime
    freeze_temperature_c: float
    thaw_event_time: datetime
    thaw_temperature_c: float
    min_temperature_c: float
    max_temperature_c: float


class AlertOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    sample_id: str
    alert_type: str
    severity: str
    event_time: datetime
    temperature_c: Optional[float]
    cycle_index: Optional[int]
    reading_id: Optional[int]
    logger_reading_id: Optional[int] = None
    direction: Optional[str]
    dose_c_min: Optional[float]
    budget_c_min: Optional[float]
    message: str
    acknowledged: bool
    created_at: datetime


class SampleStateOut(BaseModel):
    sample_id: str
    name: str
    current_state: str
    cycle_count: int
    active_freeze_event_time: Optional[datetime]
    active_freeze_min_c: Optional[float]
    active_freeze_max_c: Optional[float]
    # 双向累计暴露剂量（°C·min）、缺口总时长（分钟）与预算使用率（0~∞，>=1 即超限）
    cold_dose_c_min: float
    heat_dose_c_min: float
    exposure_gap_minutes: float
    cold_dose_budget_c_min: Optional[float]
    heat_dose_budget_c_min: Optional[float]
    max_integration_interval_minutes: Optional[float]
    cold_budget_usage: Optional[float]
    heat_budget_usage: Optional[float]
    last_event_time: Optional[datetime]
    last_temperature_c: Optional[float]
    unacknowledged_alert_count: int


class BatchReadingsResponse(BaseModel):
    sample_id: str
    accepted: int
    duplicates: int
    current_state: str
    cycle_count: int
    new_alerts: list[AlertOut]


class RecomputeResponse(BaseModel):
    sample_id: str
    cycle_count: int
    current_state: str
    alert_count: int
    new_alert_count: int


class LoggerReadingsResponse(BaseModel):
    logger_id: str
    accepted: int
    duplicates: int
    # 受迟到读数影响、已撤销旧归属并重建重算的样本
    affected_samples: list[str]
    # 这些样本重算后新产生的告警（跨样本）
    new_alerts: list[AlertOut]


# ---------------- 样本 × 记录仪温度一致性核查 ----------------


class ConsistencyPairOut(BaseModel):
    """一对配对读数中温差超限的明细（两侧读数 ID、时间、温度与时间差/温差）。"""

    reading_id: int = Field(..., description="样本直接读数 ID（readings.id）")
    logger_reading_id: int = Field(..., description="记录仪读数 ID（logger_readings.id）")
    sample_event_time: datetime
    logger_event_time: datetime
    sample_temperature_c: float
    logger_temperature_c: float
    time_diff_seconds: float = Field(..., description="两侧事件时间差的绝对值（秒）")
    temperature_diff_c: float = Field(..., description="两侧温差的绝对值（°C）")


class LoggerConsistencyResponse(BaseModel):
    sample_id: str
    logger_id: str
    max_time_diff_seconds: float
    temp_diff_threshold_c: float
    paired_count: int = Field(..., description="成功配对的读数对数（含温差正常的配对）")
    unmatched_sample_count: int = Field(..., description="未配对的样本直接读数条数")
    unmatched_logger_count: int = Field(..., description="未配对的记录仪归属读数条数")
    anomaly_count: int = Field(..., description="温差严格大于阈值的配对数")
    anomalies: list[ConsistencyPairOut]


# ---------------- 只读配液方案 ----------------


class SolutionSourceIn(BaseModel):
    """配液来源：已存在样本 ID + 该样本当前浓度（ng/µL）+ 可用体积（µL）。"""

    sample_id: str = Field(..., min_length=1, max_length=128)
    concentration_ng_ul: int = Field(
        ...,
        strict=True,
        ge=0,
        le=100,
        description="来源样本浓度（整数 ng/µL，0～100）",
    )
    available_volume_ul: int = Field(
        ...,
        strict=True,
        ge=0,
        le=30,
        description="该来源本次可用体积（整数 µL，0～30）",
    )


class SolutionPlanRequest(BaseModel):
    sources: list[SolutionSourceIn] = Field(
        ..., min_length=1, max_length=6, description="1～6 个来源样本（ID 不可重复）"
    )
    target_volume_ul: int = Field(
        ...,
        strict=True,
        ge=1,
        le=30,
        description="目标配液体积（整数 µL，1～30）",
    )
    target_concentration_ng_ul: int = Field(
        ...,
        strict=True,
        ge=0,
        le=100,
        description="目标浓度（整数 ng/µL，0～100）",
    )

    @model_validator(mode="after")
    def _validate_unique_sample_ids(self):
        ids = [s.sample_id for s in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("来源样本 ID 不可重复")
        return self


class SolutionPlanItemOut(BaseModel):
    """逐项配方：即使取用体积为 0 也按请求顺序逐项返回。"""

    sample_id: str = Field(..., description="来源样本 ID（与请求顺序一致）")
    concentration_ng_ul: int = Field(..., description="请求中声明的该来源浓度（ng/µL）")
    available_volume_ul: int = Field(..., description="请求中声明的可用体积（µL）")
    volume_ul: int = Field(..., description="从该来源取用的整数微升数（可为 0）")


class SolutionPlanResponse(BaseModel):
    target_volume_ul: int = Field(..., description="目标配液体积（µL）")
    target_concentration_ng_ul: int = Field(..., description="目标浓度（ng/µL）")
    used_sample_count: int = Field(
        ..., description="实际使用（取用体积 > 0）的来源样本数"
    )
    total_volume_ul: int = Field(
        ..., description="各来源取用体积之和（等于目标体积）"
    )
    total_mass_ng: int = Field(
        ..., description="各来源 取用体积×浓度 的总和（ng，等于 目标体积×目标浓度）"
    )
    items: list[SolutionPlanItemOut] = Field(
        ..., description="逐项配方（含零用量项，顺序与请求 sources 一致）"
    )


# ---------------- 批次运输风险比较（只读） ----------------


class RiskWeightsIn(BaseModel):
    """五项指标的有限非负权重，至少一项严格为正。"""

    freeze_thaw_cycles: float = Field(
        ..., ge=0, description="冻融循环数权重（有限非负）"
    )
    cold_dose_c_min: float = Field(
        ..., ge=0, description="低温累计暴露剂量权重（°C·min；有限非负）"
    )
    heat_dose_c_min: float = Field(
        ..., ge=0, description="高温累计暴露剂量权重（°C·min；有限非负）"
    )
    exposure_gap_minutes: float = Field(
        ..., ge=0, description="暴露缺口分钟数权重（有限非负）"
    )
    unacknowledged_alerts: float = Field(
        ..., ge=0, description="未确认告警数权重（有限非负）"
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_non_numeric(cls, data):
        # Pydantic lax 模式会把数字字符串 / 布尔强转为 float，
        # 这里显式拒绝，要求 JSON number（整数可接受）。
        if isinstance(data, dict):
            for name in (
                "freeze_thaw_cycles",
                "cold_dose_c_min",
                "heat_dose_c_min",
                "exposure_gap_minutes",
                "unacknowledged_alerts",
            ):
                if name not in data:
                    continue
                value = data[name]
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(f"{name} 必须为有限非负数字")
        return data

    @model_validator(mode="after")
    def _validate_finite_and_positive(self):
        values = [
            self.freeze_thaw_cycles,
            self.cold_dose_c_min,
            self.heat_dose_c_min,
            self.exposure_gap_minutes,
            self.unacknowledged_alerts,
        ]
        # ge=0 已拒绝负值；这里拒绝 NaN / ±Infinity（它们不应作为有限权重）
        if any(not math.isfinite(v) for v in values):
            raise ValueError("权重必须为有限数，不接受 NaN 或 Infinity")
        if all(v == 0 for v in values):
            raise ValueError("五项权重至少需要一项为正")
        return self


class TransportRiskComparisonRequest(BaseModel):
    sample_ids: list[str] = Field(
        ..., min_length=2, max_length=50, description="参与比较的已存在样本 ID（2～50 个，不可重复）"
    )
    weights: RiskWeightsIn

    @model_validator(mode="after")
    def _validate_unique_sample_ids(self):
        if len(self.sample_ids) != len(set(self.sample_ids)):
            raise ValueError("样本 ID 不可重复")
        return self


class RiskMetricsOut(BaseModel):
    """样本当前重算状态的五项原始指标。"""

    freeze_thaw_cycles: int
    cold_dose_c_min: float
    heat_dose_c_min: float
    exposure_gap_minutes: float
    unacknowledged_alerts: int


class RiskNormalizedOut(BaseModel):
    """批内最小最大归一化值（某指标全相同时统一为 0）。"""

    freeze_thaw_cycles: float
    cold_dose_c_min: float
    heat_dose_c_min: float
    exposure_gap_minutes: float
    unacknowledged_alerts: float


class TransportRiskItemOut(BaseModel):
    rank: int = Field(..., description="总分升序排名（1 基；同分按样本 ID 升序）")
    sample_id: str
    metrics: RiskMetricsOut = Field(..., description="五项原始指标（取自当前重算状态）")
    normalized: RiskNormalizedOut = Field(..., description="五项批内归一化值")
    total_score: float = Field(
        ..., description="归一化值加权和 / 权重和，范围 [0, 1]"
    )
    on_front: bool = Field(..., description="是否位于五项原始指标的非支配前沿")


class TransportRiskComparisonResponse(BaseModel):
    sample_ids: list[str] = Field(
        ..., description="参与比较的样本 ID（顺序与请求一致）"
    )
    weights: RiskWeightsIn
    weight_sum: float = Field(..., description="五项权重之和（严格为正）")
    items: list[TransportRiskItemOut] = Field(
        ..., description="按总分升序、样本 ID 升序排列的比较结果"
    )
