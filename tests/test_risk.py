"""批次运输风险比较纯函数测试（不依赖 Web/DB）。"""

from __future__ import annotations

import math

import pytest

from app.risk import METRIC_KEYS, RiskMetrics, RiskWeights, compare


def _metrics(cycles, cold, heat, gap, unacked) -> RiskMetrics:
    return RiskMetrics(
        freeze_thaw_cycles=cycles,
        cold_dose_c_min=cold,
        heat_dose_c_min=heat,
        exposure_gap_minutes=gap,
        unacknowledged_alerts=unacked,
    )


def _equal_weights() -> RiskWeights:
    return RiskWeights(1, 1, 1, 1, 1)


def test_min_max_normalization_and_score() -> None:
    # 三样本五项指标分别在各维度取 0 / 中间 / 最大
    data = {
        "a": _metrics(0, 0.0, 0.0, 0.0, 0),
        "b": _metrics(1, 5.0, 50.0, 30.0, 1),
        "c": _metrics(2, 10.0, 100.0, 60.0, 2),
    }
    result = compare(data, _equal_weights())
    items = {it["sample_id"]: it for it in result["items"]}

    assert items["a"]["normalized"] == {k: 0.0 for k in METRIC_KEYS}
    assert items["a"]["total_score"] == 0.0
    assert items["c"]["normalized"] == {k: 1.0 for k in METRIC_KEYS}
    assert items["c"]["total_score"] == 1.0
    # b 每项恰好 0.5 -> 总分 0.5
    for v in items["b"]["normalized"].values():
        assert v == pytest.approx(0.5)
    assert items["b"]["total_score"] == pytest.approx(0.5)

    # 排名 a < b < c
    assert [it["sample_id"] for it in result["items"]] == ["a", "b", "c"]
    assert [it["rank"] for it in result["items"]] == [1, 2, 3]


def test_equal_column_normalized_to_zero() -> None:
    # 循环数三项相同 -> 该列归一化全 0，不参与区分
    data = {
        "a": _metrics(3, 0.0, 0.0, 0.0, 0),
        "b": _metrics(3, 0.0, 0.0, 0.0, 2),
        "c": _metrics(3, 0.0, 0.0, 0.0, 4),
    }
    weights = RiskWeights(1, 1, 1, 1, 1)
    result = compare(data, weights)
    items = {it["sample_id"]: it for it in result["items"]}
    for it in result["items"]:
        assert it["normalized"]["freeze_thaw_cycles"] == 0.0
        assert it["normalized"]["cold_dose_c_min"] == 0.0
        assert it["normalized"]["heat_dose_c_min"] == 0.0
        assert it["normalized"]["exposure_gap_minutes"] == 0.0
    assert items["a"]["normalized"]["unacknowledged_alerts"] == 0.0
    assert items["c"]["normalized"]["unacknowledged_alerts"] == 1.0
    assert items["b"]["total_score"] == pytest.approx(0.1)


def test_all_equal_everything_zero_and_all_on_front() -> None:
    data = {
        "a": _metrics(1, 2.5, 3.5, 10.0, 1),
        "b": _metrics(1, 2.5, 3.5, 10.0, 1),
    }
    result = compare(data, _equal_weights())
    for it in result["items"]:
        assert it["total_score"] == 0.0
        assert it["normalized"] == {k: 0.0 for k in METRIC_KEYS}
        assert it["on_front"] is True
    # 同分按样本 ID 升序
    assert [it["sample_id"] for it in result["items"]] == ["a", "b"]
    assert [it["rank"] for it in result["items"]] == [1, 2]


def test_weighted_sum_divided_by_weight_sum() -> None:
    data = {
        "a": _metrics(0, 0.0, 0.0, 0.0, 0),
        "b": _metrics(2, 10.0, 100.0, 60.0, 2),
    }
    # 只给循环数与缺口权重：b 的总分 = (3*1 + 2*1) / (3+2) = 1.0
    weights = RiskWeights(3, 0, 0, 2, 0)
    result = compare(data, weights)
    items = {it["sample_id"]: it for it in result["items"]}
    assert items["b"]["total_score"] == pytest.approx(1.0)
    assert result["weight_sum"] == 5.0
    # 零权重列仍计算归一化值（仅不影响总分）
    assert items["b"]["normalized"]["heat_dose_c_min"] == 1.0


def test_non_dominated_front() -> None:
    # b/c 互不可比；d 被 b 与 c 支配；a 全 0 支配所有非零点
    data = {
        "a": _metrics(0, 0.0, 0.0, 0.0, 0),
        "b": _metrics(1, 0.0, 100.0, 0.0, 0),
        "c": _metrics(1, 10.0, 50.0, 0.0, 0),
        "d": _metrics(2, 10.0, 100.0, 0.0, 0),
    }
    result = compare(data, _equal_weights())
    front = {it["sample_id"] for it in result["items"] if it["on_front"]}
    assert front == {"a"}

    # 去掉全局最优点后：b/c 互不可比且都在前沿，d 仍被支配
    result = compare({k: v for k, v in data.items() if k != "a"}, _equal_weights())
    front = {it["sample_id"] for it in result["items"] if it["on_front"]}
    assert front == {"b", "c"}
    dominated = {it["sample_id"] for it in result["items"] if not it["on_front"]}
    assert dominated == {"d"}


def test_identical_vectors_do_not_dominate_each_other() -> None:
    data = {
        "a": _metrics(1, 5.0, 5.0, 5.0, 1),
        "b": _metrics(1, 5.0, 5.0, 5.0, 1),
        "c": _metrics(2, 5.0, 5.0, 5.0, 1),
    }
    result = compare(data, _equal_weights())
    front = {it["sample_id"] for it in result["items"] if it["on_front"]}
    # a、b 向量相同：互不支配，都在前沿；c 被二者支配
    assert front == {"a", "b"}


def test_strict_domination_requires_one_strictly_lower() -> None:
    # b 在所有维度 <= c 但无一严格更低即相等向量 -> 不支配
    data = {
        "b": _metrics(1, 1.0, 1.0, 1.0, 1),
        "c": _metrics(1, 1.0, 1.0, 1.0, 1),
    }
    result = compare(data, _equal_weights())
    assert all(it["on_front"] for it in result["items"])


def test_tie_break_by_sample_id() -> None:
    data = {
        "zebra": _metrics(1, 1.0, 1.0, 1.0, 1),
        "alpha": _metrics(1, 1.0, 1.0, 1.0, 1),
        "mid": _metrics(1, 1.0, 1.0, 1.0, 1),
    }
    result = compare(data, _equal_weights())
    assert [it["sample_id"] for it in result["items"]] == ["alpha", "mid", "zebra"]
    assert [it["rank"] for it in result["items"]] == [1, 2, 3]


def test_score_in_unit_interval() -> None:
    data = {
        "a": _metrics(0, 0.0, 0.0, 0.0, 0),
        "b": _metrics(5, 999.9, 12345.6, 77.0, 9),
    }
    weights = RiskWeights(0.1, 0.2, 0.3, 0.4, 0.5)
    result = compare(data, weights)
    for it in result["items"]:
        assert 0.0 - 1e-12 <= it["total_score"] <= 1.0 + 1e-12
    assert math.isfinite(result["weight_sum"])
