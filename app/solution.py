"""只读配液方案求解器（纯函数，不依赖数据库与 Web 框架）。

给定 n 个来源样本（每个含整数浓度 ``c_i`` ng/µL 与可用体积 ``u_i`` µL）与
目标体积 ``V`` µL、目标浓度 ``C`` ng/µL，求各来源的整数取用体积 ``x_i``：

    0 <= x_i <= u_i
    Σ x_i = V
    Σ x_i·c_i = V·C          （总质量精确相等，全程整数运算）

优选目标按字典序：

  1. 实际使用（``x_i > 0``）的来源样本数最少；
  2. 按请求顺序排列的取用体积向量 ``(x_1, …, x_n)`` 字典序最小。

不允许浓度四舍五入或贪心近似：按使用来源数 ``k = 1…n`` 逐层枚举，
每层做带剪枝的深度优先搜索，且 ``x_i`` 从 0 起递增尝试——
首个命中两条精确等式的向量即该 k 下字典序最小者，
首个存在可行解的 k 即最小使用来源数，因此首次返回即全局最优；
任一层均无可行解返回 ``None``（接口层映射为 409）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SolutionSource:
    """一个来源样本的浓度（ng/µL）与可用体积（µL），均为非负整数。"""

    concentration_ng_ul: int
    available_volume_ul: int


def solve_plan(
    sources: list[SolutionSource],
    target_volume_ul: int,
    target_concentration_ng_ul: int,
) -> Optional[list[int]]:
    """求最优精确配液取用向量；无精确解返回 ``None``。"""

    n = len(sources)
    caps = [s.available_volume_ul for s in sources]
    concs = [s.concentration_ng_ul for s in sources]
    # 目标总质量（ng）：体积 × 浓度，整数运算，无舍入
    target_mass = target_volume_ul * target_concentration_ng_ul

    # 后缀容量：位置 i..n-1 的可用体积之和
    suffix_cap = [0] * (n + 1)
    # 后缀浓度极值：质量可行性剪枝用；空后缀以 ±inf 占位
    # （remaining_vol == 0 时直接判定，不读取这两个值）
    suffix_min_c = [0] * (n + 1)
    suffix_max_c = [0] * (n + 1)
    suffix_min_c[n] = 10**18
    suffix_max_c[n] = -(10**18)
    for i in range(n - 1, -1, -1):
        suffix_cap[i] = suffix_cap[i + 1] + caps[i]
        suffix_min_c[i] = min(concs[i], suffix_min_c[i + 1])
        suffix_max_c[i] = max(concs[i], suffix_max_c[i + 1])

    volumes = [0] * n

    def dfs(i: int, used: int, vol_sum: int, mass_sum: int) -> Optional[list[int]]:
        # 来源数剪枝：已用来源不能超过 need；剩余位置全取正也凑不够 need
        remaining_slots = n - i
        if used > need or used + remaining_slots < need:
            return None

        remaining_vol = target_volume_ul - vol_sum
        if remaining_vol < 0 or remaining_vol > suffix_cap[i]:
            return None
        remaining_mass = target_mass - mass_sum

        if remaining_vol == 0:
            # 体积已取满，后续位置只能取 0（不会再增加来源数），质量须恰好齐平
            if remaining_mass == 0 and used == need:
                return volumes.copy()
            return None

        if i == n:
            return None

        # 只剩当前一个位置有取值空间：x 被体积唯一确定，直接判定，
        # 消去叶子层逐值回溯（同时严格校验容量、来源数与质量）
        if i == n - 1:
            x = remaining_vol
            final_used = used + (1 if x > 0 else 0)
            if (
                x <= caps[i]
                and final_used == need
                and remaining_mass == x * concs[i]
            ):
                volumes[i] = x
                return volumes.copy()
            return None

        # 质量边界剪枝（必要条件）：剩余体积即使全用后缀最低/最高浓度，
        # 也够不着/压不到目标剩余质量。容量约束只会进一步缩窄可行域，
        # 因此这里用宽松边界绝不会误删可行解。
        if remaining_mass < remaining_vol * suffix_min_c[i]:
            return None
        if remaining_mass > remaining_vol * suffix_max_c[i]:
            return None

        # x 从 0 起升序：Dfs 前序遍历即按向量字典序访问，首个可行解即字典序最小
        upper = min(caps[i], remaining_vol)
        for x in range(upper + 1):
            volumes[i] = x
            found = dfs(
                i + 1,
                used + (1 if x > 0 else 0),
                vol_sum + x,
                mass_sum + x * concs[i],
            )
            if found is not None:
                return found
        volumes[i] = 0
        return None

    # 可用体积为 0 的来源永远取 0，不可能计入使用来源数
    usable_sources = sum(1 for u in caps if u > 0)
    for need in range(1, min(n, usable_sources) + 1):
        result = dfs(0, 0, 0, 0)
        if result is not None:
            return result
    return None
