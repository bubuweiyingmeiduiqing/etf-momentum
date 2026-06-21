import numpy as np
import pandas as pd
from jqlib.technical_analysis import *

def initialize(context):
    # 扩展后的多资产全球轮动策略池，加入创业板ETF
    g.etf_pool = {
        '510500.XSHG': '500ETF',
        '510890.XSHG': '红利ETF',
        '513100.XSHG': '纳指ETF',        # 跨境资产
        '513520.XSHG': '日经ETF',        # 跨境资产
        '588000.XSHG': '科创50ETF',
        '159915.XSHE': '创业板ETF'       # 新增：创业板在深交所，后缀为XSHE
    }
    g.security = list(g.etf_pool.keys())
    
    # 定义需要进行溢价率监控的跨境ETF列表
    g.cross_border_etfs = ['513100.XSHG', '513520.XSHG']
    # 设置溢价率风险阈值，超过 1.5% 则在当周调仓时放弃买入
    g.MAX_PREMIUM_RATE = 0.015
    
    # 引入国债ETF作为波动率截断后的避险防御资产
    g.BOND_ETF = '511010.XSHG' 
    
    set_benchmark('000300.XSHG')
    set_option('use_real_price', True)
    set_order_cost(OrderCost(open_tax=0, close_tax=0, open_commission=0.00005, close_commission=0.00005, min_commission=0), type='stock')
    
    # 策略总配置资金
    g.TOTAL_STRATEGY_MONEY = 100000.0
    
    # 初始化历史最高价字典，供动态ATR移动止损使用
    if 'highest_prices' not in dir(g):
        g.highest_prices = {}

    # 每周第一个交易日（周一）调仓
    run_weekly(market_handle, weekday=1, time='9:45', reference_security='000300.XSHG')

def check_etf_premium(security):
    """
    检查跨境ETF的场内溢价率。
    聚宽回测中通过对比前一交易日基金净值与当前场内价格计算近似溢价率。
    """
    try:
        # 获取当前场内最新价格
        current_price = get_close_market_data(security)
        
        # 获取对应网下基金份额的净值数据
        q = query(finance.FUND_MAIN_INFO).filter(finance.FUND_MAIN_INFO.main_code == security[:6])
        df_info = finance.run_query(q)
        if not df_info.empty:
            inner_code = df_info['code'].iloc[0]
            # 查询最新的净值数据
            q_nav = query(finance.FUND_NET_VALUE).filter(finance.FUND_NET_VALUE.code == inner_code).order_by(finance.FUND_NET_VALUE.pub_date.desc()).limit(1)
            df_nav = finance.run_query(q_nav)
            if not df_nav.empty:
                net_value = df_nav['net_value'].iloc[0]
                # 计算近似溢价率：(场内现价 - 基金净值) / 基金净值
                premium_rate = (current_price - net_value) / net_value
                return premium_rate
        return 0.0
    except:
        # 若接口由于权限或非交易日无数据报错，默认返回0以防中断策略
        return 0.0

def get_close_market_data(security):
    """获取当前时间点的近似收盘价"""
    return attribute_history(security, 1, '1m', ['close'])['close'].iloc[-1]

def market_handle(context):
    scores = {}
    atr_values = {}
    pool_atrs = []
    
    # ==================== 1. 指标计算与风险调整后动量打分 ====================
    for security in g.security:
        hist = attribute_history(security, 35, '1d', ['close', 'high', 'low'])
        if len(hist) < 35:
            continue
            
        sma20 = hist['close'].rolling(20).mean()
        c_close = hist['close'].iloc[-1]
        
        # 计算 14 日相对 ATR 百分比
        high = hist['high'].iloc[-15:]
        low = hist['low'].iloc[-15:]
        pre_close = hist['close'].shift(1).iloc[-15:]
        tr = pd.concat([high - low, (high - pre_close).abs(), (pre_close - low).abs()], axis=1).max(axis=1)
        atr_pct = tr.mean() / c_close
        atr_values[security] = atr_pct
        pool_atrs.append(atr_pct)
        
        # 核心过滤：价格必须站上20日均线，且20日均线不能明显下行
        is_trend_up = (c_close > sma20.iloc[-1]) and (sma20.iloc[-1] >= sma20.iloc[-2])
        
        if is_trend_up:
            # 跨境ETF高溢价过滤模块
            if security in g.cross_border_etfs:
                premium = check_etf_premium(security)
                if premium > g.MAX_PREMIUM_RATE:
                    log.warning(f"【高溢价剔除】{g.etf_pool[security]} 当前近似溢价率为 {premium*100:.2f}%, 超过阈值 {g.MAX_PREMIUM_RATE*100:.1f}%, 调仓时不予考虑买入。")
                    continue # 跳过该品种，不计入备选库
            
            # 风险调整后收益打分 (20日收益率 / 20日价格标准差)
            ret_20d = (c_close - hist['close'].iloc[-20]) / hist['close'].iloc[-20]
            vol_20d = hist['close'].iloc[-20:].pct_change().std()
            
            scores[security] = ret_20d / vol_20d if vol_20d > 0 else 0

    # ==================== 2. 波动率截断机制 (Volatility Trigger) ====================
    avg_pool_atr = np.mean(pool_atrs) if len(pool_atrs) > 0 else 0
    vol_trigger_active = False
    
    if avg_pool_atr > 0.035:
        vol_trigger_active = True
        log.warning(f"【波动率截断触发】全资产平均ATR达 {avg_pool_atr*100:.2f}%, 超过3.5%阈值，系统转入防御机制。")

    # ==================== 3. 基于 3 倍 ATR 的移动止损与风控 ====================
    for security in list(context.portfolio.positions.keys()):
        if security == g.BOND_ETF:
            continue
            
        pos = context.portfolio.positions[security]
        if pos.total_amount == 0:
            continue
            
        c_close = attribute_history(security, 1, '1d', ['close'])['close'].iloc[-1]
        sma20 = attribute_history(security, 25, '1d', ['close'])['close'].rolling(20).mean().iloc[-1]
        
        # 跌破 20 日均线清仓
        if c_close < sma20:
            log.info(f"【中线破位】{g.etf_pool[security]} 跌破20日均线，执行清仓。")
            order_target(security, 0)
            if security in g.highest_prices: del g.highest_prices[security]
            continue
            
        # 动态更新买入后的历史最高价
        if security not in g.highest_prices:
            g.highest_prices[security] = pos.avg_cost
        g.highest_prices[security] = max(g.highest_prices[security], c_close)
        
        # 3倍ATR动态移动止损
        current_atr = atr_values.get(security, 0.02)
        atr_stop_price = g.highest_prices[security] * (1.0 - 3.0 * current_atr)
        
        if c_close < atr_stop_price:
            log.warning(f"【3倍ATR移动止损】{g.etf_pool[security]} 当前价 {c_close} 跌破由最高价 {g.highest_prices[security]} 计算的ATR止损线 {atr_stop_price:.3f}，强制清仓。")
            order_target(security, 0)
            del g.highest_prices[security]

    # ==================== 4. 仓位动态分配与轮动模块 ====================
    sorted_candidates = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    targets = [item[0] for item in sorted_candidates[:2]]
    
    # 清理非强势、不在目标池中的权益持仓
    for security in list(context.portfolio.positions.keys()):
        if security != g.BOND_ETF and security not in targets and context.portfolio.positions[security].total_amount > 0:
            log.info(f"【调仓调出】{g.etf_pool[security]} 移出动量前两名或触发溢价保护。")
            order_target(security, 0)
            if security in g.highest_prices: del g.highest_prices[security]

    if len(targets) == 0:
        order_target_value(g.BOND_ETF, g.TOTAL_STRATEGY_MONEY)
        return
        
    inv_atr_sum = sum([1.0 / atr_values[sec] for sec in targets])
    target_weights = {sec: (1.0 / atr_values[sec]) / inv_atr_sum for sec in targets}
    
    equity_total_money = g.TOTAL_STRATEGY_MONEY
    
    if vol_trigger_active:
        bond_target_value = g.TOTAL_STRATEGY_MONEY * 0.40
        order_target_value(g.BOND_ETF, bond_target_value)
        equity_total_money = g.TOTAL_STRATEGY_MONEY * 0.60
    else:
        if context.portfolio.positions[g.BOND_ETF].total_amount > 0:
            order_target(g.BOND_ETF, 0)

    for security in targets:
        target_weight = target_weights[security]
        if len(targets) == 1:
            target_weight = min(target_weight, 0.5)
            
        target_value = equity_total_money * target_weight
        log.info(f"【执行最终仓位】标的: {g.etf_pool[security]}, 风险打分: {scores[security]:.2f}, 分配市值: {target_value:.2f}元")
        order_target_value(security, target_value)