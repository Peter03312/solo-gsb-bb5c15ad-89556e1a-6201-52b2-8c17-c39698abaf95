"""批次样本当前运输风险比较（纯函数，不依赖数据库与 Web 框架）。

五项指标（均为“越低越好”）：

  1. freeze_thaw_cycles   冻融循环数
  2. cold_dose_c_min      低温累计暴露剂量（°C·min）
  3. heat_dose_c_min      高温累计暴露剂量（°C·min）
  4. exposure_gap_minutes 暴露缺口分钟数
  5. unacknowledged_alerts 未确认告警数

归一化：在**本批样本内**逐指标做最小最大归一化
``(x - min) / (max - min)``；该指标所有样本取值相同（分母为 0）时
各项归一化值统一记 0。总分 = 归一化值的加权和 / 权重和。

非支配前沿（Pareto front）基于五项**原始指标**判定：样本 A 的五项指标
均不高于 B 且至少一项严格更低时，A 支配 B；不被任何其他样本支配者
位于前沿（指标向量完全相同的样本互不支配，可同时位于前沿）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskWeights:
    """五项有限非负权重（至少一项为正；调用方负责校验）。"""

    freeze_thaw_cycles: float
    cold_dose_c_min: float
    heat_dose_c_min: float
    exposure_gap_minutes: float
    unacknowledged_alerts: float


@dataclass(frozen=True)
class RiskMetrics:
    """一条样本当前重算状态中的五项原始指标。"""

    freeze_thaw_cycles: float
    cold_dose_c_min: float
    heat_dose_c_min: float
    exposure_gap_minutes: float
    unacknowledged_alerts: float

    def as_tuple(self) -> tuple[float, float, float, float, float]:
        return (
            self.freeze_thaw_cycles,
            self.cold_dose_c_min,
            self.heat_dose_c_min,
            self.exposure_gap_minutes,
            self.unacknowledged_alerts,
        )


# 指标键（固定顺序，归一化/总分/响应均按此顺序组织）
METRIC_KEYS: tuple[str, ...] = (
    "freeze_thaw_cycles",
    "cold_dose_c_min",
    "heat_dose_c_min",
    "exposure_gap_minutes",
    "unacknowledged_alerts",
)


def compare(
    metrics_by_sample: dict[str, RiskMetrics], weights: RiskWeights
) -> dict:
    """计算批次内各样本的归一化值、加权总分与非支配前沿。

    返回 ``{"items", "weight_sum"}``：``items`` 为按总分升序、
    样本 ID 升序排列的结果列表（每项带 1 基 ``rank``）。
    要求至少 2 个样本，且权重和严格为正（调用方负责入参校验）。
    """

    sample_ids = list(metrics_by_sample.keys())
    n = len(sample_ids)

    weight_values = [getattr(weights, key) for key in METRIC_KEYS]
    weight_sum = sum(weight_values)

    # 1) 批内逐指标最小最大归一化；某指标全相同时该项统一记 0
    minima: dict[str, float] = {}
    maxima: dict[str, float] = {}
    spans: dict[str, float] = {}
    for j, key in enumerate(METRIC_KEYS):
        column = [getattr(metrics_by_sample[sid], key) for sid in sample_ids]
        lo, hi = min(column), max(column)
        minima[key] = lo
        maxima[key] = hi
        spans[key] = hi - lo

    normalized: dict[str, dict[str, float]] = {}
    for sid in sample_ids:
        normalized[sid] = {
            key: (
                0.0
                if spans[key] == 0
                else (getattr(metrics_by_sample[sid], key) - minima[key]) / spans[key]
            )
            for key in METRIC_KEYS
        }

    # 2) 总分 = 归一化值加权和 / 权重和（与归一化展示值保持同一精度口径）
    total_scores: dict[str, float] = {}
    for sid in sample_ids:
        weighted_sum = sum(
            weight_values[j] * normalized[sid][METRIC_KEYS[j]]
            for j in range(len(METRIC_KEYS))
        )
        total_scores[sid] = weighted_sum / weight_sum

    # 3) 非支配前沿：基于五项原始指标 O(n²) 两两判定（n <= 50）。
    #    A 支配 B <=> A 各项 <= B 各项且至少一项严格更低。
    vectors = {sid: metrics_by_sample[sid].as_tuple() for sid in sample_ids}
    dominated: set[str] = set()
    for i, a in enumerate(sample_ids):
        for j, b in enumerate(sample_ids):
            if i == j:
                continue
            va, vb = vectors[a], vectors[b]
            if all(x <= y for x, y in zip(va, vb)) and any(
                x < y for x, y in zip(va, vb)
            ):
                dominated.add(b)

    ranked_ids = sorted(sample_ids, key=lambda sid: (total_scores[sid], sid))
    items = [
        {
            "rank": rank,
            "sample_id": sid,
            "metrics": {key: getattr(metrics_by_sample[sid], key) for key in METRIC_KEYS},
            "normalized": {key: normalized[sid][key] for key in METRIC_KEYS},
            "total_score": total_scores[sid],
            "on_front": sid not in dominated,
        }
        for rank, sid in enumerate(ranked_ids, start=1)
    ]
    return {"items": items, "weight_sum": weight_sum}
