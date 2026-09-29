"""只读配液方案求解器单元测试（纯函数，不依赖 Web/DB）。

正确性以暴力枚举（按“来源数最少 → 向量字典序最小”从全空间选最优）交叉验证。
"""

from __future__ import annotations

from itertools import product

from app.solution import SolutionSource, solve_plan


def _brute_force(sources, target_volume_ul, target_concentration_ng_ul):
    """枚举所有 0<=x_i<=u_i 的整数向量，返回最优解；无可行解返回 None。"""

    caps = [s.available_volume_ul for s in sources]
    concs = [s.concentration_ng_ul for s in sources]
    target_mass = target_volume_ul * target_concentration_ng_ul

    best = None
    best_key = None
    for vec in product(*(range(u + 1) for u in caps)):
        if sum(vec) != target_volume_ul:
            continue
        if sum(v * c for v, c in zip(vec, concs)) != target_mass:
            continue
        used = sum(1 for v in vec if v > 0)
        key = (used, list(vec))
        if best_key is None or key < best_key:
            best_key = key
            best = list(vec)
    return best


def _sources(spec):
    return [SolutionSource(concentration_ng_ul=c, available_volume_ul=u) for c, u in spec]


def test_single_source_exact_match() -> None:
    # 10 µL @ 50 恰好达标：单来源
    assert solve_plan(_sources([(50, 10)]), 10, 50) == [10]


def test_single_source_concentration_mismatch() -> None:
    # 唯一来源浓度不等于目标浓度，无论如何取用都不达标
    assert solve_plan(_sources([(40, 10)]), 10, 50) is None


def test_min_source_count_preferred() -> None:
    # 0 与 100 混合可凑 50（两来源），但存在恰好 50 的单来源 b：优先单来源
    assert solve_plan(_sources([(0, 10), (50, 10), (100, 10)]), 10, 50) == [0, 10, 0]


def test_lexicographically_smallest_within_same_source_count() -> None:
    # 两个 50 来源各 10 µL，目标 10 µL @ 50：两种单来源方案，
    # 字典序最小把用量压到更靠后的来源 -> [0, 10]
    assert solve_plan(_sources([(50, 10), (50, 10)]), 10, 50) == [0, 10]


def test_lexicographic_with_two_sources_50_50() -> None:
    # 目标 10 µL @ 50：0/100 两源可直接配平 [5, 0, 5]（2 个来源，
    # 比“0/50 或 50/100 两源混合”同级但字典序最小向量一致）
    assert solve_plan(_sources([(0, 10), (50, 6), (100, 10)]), 10, 50) == [5, 0, 5]
    # 100 浓度源容量只有 4 时无法只用 0/100 配平（需 5 µL @ 100），
    # 必须三来源：[2, 6, 2]（2·0 + 6·50 + 2·100 = 500，用 3 源），字典序最小
    assert solve_plan(_sources([(0, 30), (50, 6), (100, 4)]), 10, 50) == [2, 6, 2]


def test_zero_available_sources_never_used() -> None:
    # 唯一浓度达标的来源可用体积为 0
    assert solve_plan(_sources([(50, 0), (0, 10), (100, 10)]), 10, 50) == [0, 5, 5]
    # 所有来源可用体积均为 0，目标体积 > 0：无解
    assert solve_plan(_sources([(50, 0)]), 1, 50) is None


def test_target_concentration_zero() -> None:
    assert solve_plan(_sources([(10, 10), (0, 10), (5, 10)]), 10, 0) == [0, 10, 0]


def test_target_volume_bounds() -> None:
    assert solve_plan(_sources([(7, 30)]), 30, 7) == [30]
    assert solve_plan(_sources([(7, 29)]), 30, 7) is None


def test_capacity_constraints_respected() -> None:
    # 目标 10 µL @ 50：50 浓度源容量 9 不足 10，单来源不可行；
    # 0 与 100 等量配对 -> [5, 0, 5]，2 个来源
    assert solve_plan(_sources([(0, 30), (50, 9), (100, 30)]), 10, 50) == [5, 0, 5]
    # 0 浓度源容量仅 4 时，两来源配平（需 5 µL @ 0）不可行，但存在三来源
    # 方案 [1, 8, 1]（1·0 + 8·50 + 1·100 = 500），证明容量约束被严格执行
    assert solve_plan(_sources([(0, 4), (50, 9), (100, 10)]), 10, 50) == [1, 8, 1]


def test_no_solution_target_below_min_concentration() -> None:
    # 目标浓度 10 低于所有来源浓度：无法稀释
    assert solve_plan(_sources([(20, 10), (30, 10)]), 5, 10) is None


def test_no_solution_above_max_concentration() -> None:
    # 目标浓度 90 高于所有来源浓度：无法浓缩
    assert solve_plan(_sources([(20, 10), (80, 10)]), 5, 90) is None


def test_near_miss_integer_mass_not_rounded() -> None:
    # 19 µL @ 50 与 1 µL @ 100 混合：总质量 1050 / 20 = 52.5，不等于 52
    assert solve_plan(_sources([(50, 19), (100, 1)]), 20, 52) is None
    # 目标 50 时须取 20 µL @ 50，但该来源容量仅 19；19+1 质量 1050 ≠ 1000，仍无解
    assert solve_plan(_sources([(50, 19), (100, 1)]), 20, 50) is None
    # 容量放宽到 20 即精确可行
    assert solve_plan(_sources([(50, 20), (100, 1)]), 20, 50) == [20, 0]


def test_six_sources_maximum_input_size() -> None:
    # 6 个来源、容量全满 30：只取最后一个 50 来源即单来源最优
    spec = [(10, 30), (20, 30), (30, 30), (40, 30), (60, 30), (50, 30)]
    assert solve_plan(_sources(spec), 30, 50) == [0, 0, 0, 0, 0, 30]


def test_mixed_solution_counts() -> None:
    # 目标 20 µL @ 50；50 浓度源容量仅 6，单来源不可行；0/100 两源配平
    # 需 100 源 10 µL，而其容量只有 8，两来源也不可行 -> 必须三来源。
    # 字典序最小三来源向量 [7, 6, 7]：7·0 + 6·50 + 7·100 = 1000
    result = solve_plan(_sources([(0, 30), (50, 6), (100, 8)]), 20, 50)
    assert result == [7, 6, 7]
    assert sum(1 for v in result if v > 0) == 3
    # 100 容量放宽到 10 后，两来源 [10, 0, 10] 即字典序最小最优
    assert solve_plan(_sources([(0, 30), (50, 6), (100, 10)]), 20, 50) == [10, 0, 10]


def test_brute_force_agrees_small_space() -> None:
    import random

    rng = random.Random(20260928)
    checked = 0
    for _ in range(3000):
        n = rng.randint(1, 4)
        # 浓度取 0/25/50/75/100 以提高精确可行实例密度
        spec = [
            (rng.choice([0, 25, 50, 75, 100]), rng.randint(0, 8)) for _ in range(n)
        ]
        sources = _sources(spec)
        target_volume = rng.randint(1, 12)
        target_conc = rng.choice([0, 25, 50, 75, 100])
        expected = _brute_force(sources, target_volume, target_conc)
        got = solve_plan(sources, target_volume, target_conc)
        assert got == expected, (spec, target_volume, target_conc, expected, got)
        if got is not None:
            checked += 1
            # 不变量：体积与质量双等式、容量约束、来源数最少
            assert sum(got) == target_volume
            assert sum(v * c for v, c in zip(got, [s.concentration_ng_ul for s in sources])) == target_volume * target_conc
            assert all(0 <= v <= u for v, u in zip(got, [s.available_volume_ul for s in sources]))
            # 来源数与最优解一致（got 本身即暴力法选出的最优）
            assert sum(1 for v in got if v > 0) == sum(1 for v in expected if v > 0)
    assert checked > 300  # 确保确实交叉验证了大量可行实例
