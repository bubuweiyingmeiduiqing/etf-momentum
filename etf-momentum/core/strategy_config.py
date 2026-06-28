"""策略版本配置 —— 支持 v1（原版）与 v2（优化版）A/B 对比"""

from dataclasses import dataclass


@dataclass
class StrategyConfig:
    """策略版本参数配置"""
    version: str = "v1"

    # P0: 趋势过滤优化
    use_return_10d_for_score: bool = False   # v2: 用10日动量替代20日
    sma20_tolerance_pct: float = 0.0          # v2: SMA20容差(%), 0.5=允许低于均线0.5%仍通过

    # P1: 动态动量加权
    use_momentum_weight: bool = False         # v2: 按风险调整得分加权, 替代等权

    # P2: 最低仓位规则
    min_position_pct: float = 0.0             # v2: 最低持仓比例, 如0.1=10%
    min_filter_threshold: float = 0.2         # v2: 过滤通过率低于此值时触发最低仓位

    # P2: 交易成本
    trade_cost_bps: float = 0.0               # v2: 单边交易成本(基点), 10=0.1%


# 预设配置
V1_CONFIG = StrategyConfig(version="v1")

V2_CONFIG = StrategyConfig(
    version="v2",
    use_return_10d_for_score=True,
    sma20_tolerance_pct=0.5,
    use_momentum_weight=True,
    min_position_pct=0.1,
    min_filter_threshold=0.2,
    trade_cost_bps=10.0,
)
