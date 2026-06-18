"""Web 可视化界面 —— Flask 应用"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, jsonify, request, redirect, url_for
from config import load_config
from core.database import Database

# 初始化
config = load_config()
db = Database(config["database"]["path"])
app = Flask(__name__)
app.secret_key = config.get("web", {}).get("secret_key", "dev-secret-key")


# ---- 页面路由 ----
@app.route("/")
def index():
    """主仪表盘。"""
    symbols = db.get_monitored_symbols()
    alerts = db.get_recent_alerts(20)
    return render_template("index.html",
                           symbols=symbols,
                           alerts=alerts,
                           alert_count=db.count_alerts_today(),
                           signal_count=db.count_signals_today())


@app.route("/symbol/<symbol>")
def symbol_detail(symbol: str):
    """单标的详情页。"""
    quote = db.get_latest_quote(symbol)
    quotes = db.get_quotes(symbol, limit=60)
    return render_template("detail.html",
                           symbol=symbol,
                           quote=quote,
                           quotes=quotes)


# ---- API 路由 ----
@app.route("/api/quotes/<symbol>")
def api_quotes(symbol: str):
    """获取标的行情数据（JSON）。"""
    limit = request.args.get("limit", 100, type=int)
    quotes = db.get_quotes(symbol, limit=limit)
    return jsonify(quotes)


@app.route("/api/alerts")
def api_alerts():
    """获取告警列表。"""
    limit = request.args.get("limit", 50, type=int)
    alerts = db.get_recent_alerts(limit=limit)
    return jsonify(alerts)


@app.route("/api/alerts/<int:alert_id>/ack", methods=["POST"])
def api_ack_alert(alert_id: int):
    """确认告警。"""
    db.ack_alert(alert_id)
    return jsonify({"status": "ok"})


@app.route("/api/summary")
def api_summary():
    """仪表盘汇总数据。"""
    symbols = db.get_monitored_symbols()
    summary = []
    for s in symbols:
        q = db.get_latest_quote(s["symbol"])
        if q:
            q["name"] = s.get("name", "")
            summary.append(q)
    return jsonify({
        "symbols": summary,
        "alert_count": db.count_alerts_today(),
        "signal_count": db.count_signals_today(),
    })


# ---- 运行入口 ----
def run():
    web_config = config.get("web", {})
    app.run(
        host=web_config.get("host", "0.0.0.0"),
        port=web_config.get("port", 5000),
        debug=web_config.get("debug", False),
    )


if __name__ == "__main__":
    run()
