"""Web 可视化界面 —— Flask 应用（云端生产版：健康检查 + waitress）"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, jsonify, request
from config import load_config
from core.database import Database

config = load_config()
db = Database(
    config["database"]["path"],
    backup_enabled=config.get("database", {}).get("backup_enabled", True),
    backup_dir=config.get("database", {}).get("backup_dir", "data/backups"),
)

app = Flask(__name__)
app.secret_key = config.get("web", {}).get("secret_key", "dev-secret-key")


# ====== 页面路由 ======
@app.route("/")
def index():
    symbols = db.get_monitored_symbols()
    alerts = db.get_recent_alerts(20)
    return render_template("index.html",
                           symbols=symbols, alerts=alerts,
                           alert_count=db.count_alerts_today(),
                           signal_count=db.count_signals_today())


@app.route("/symbol/<symbol>")
def symbol_detail(symbol: str):
    quote = db.get_latest_quote(symbol)
    quotes = db.get_quotes(symbol, limit=60)
    return render_template("detail.html",
                           symbol=symbol, quote=quote, quotes=quotes)


# ====== API 路由 ======
@app.route("/api/quotes/<symbol>")
def api_quotes(symbol: str):
    limit = request.args.get("limit", 100, type=int)
    quotes = db.get_quotes(symbol, limit=limit)
    return jsonify(quotes)


@app.route("/api/alerts")
def api_alerts():
    limit = request.args.get("limit", 50, type=int)
    alerts = db.get_recent_alerts(limit=limit)
    return jsonify(alerts)


@app.route("/api/alerts/<int:alert_id>/ack", methods=["POST"])
def api_ack_alert(alert_id: int):
    db.ack_alert(alert_id)
    return jsonify({"status": "ok"})


@app.route("/api/summary")
def api_summary():
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


# ====== 健康检查端点（云端部署关键） ======
@app.route("/health")
def health():
    """综合健康检查端点，供负载均衡/监控系统使用。"""
    db_ok, db_detail = db.health_check()
    return jsonify({
        "status": "healthy" if db_ok else "degraded",
        "checks": {
            "database": {"healthy": db_ok, "detail": db_detail},
        },
        "symbols_monitored": len(config.get("fetcher", {}).get("symbols", [])),
    })


@app.route("/health/live")
def health_live():
    """存活探针（Kubernetes liveness probe）。"""
    return jsonify({"status": "alive"}), 200


@app.route("/health/ready")
def health_ready():
    """就绪探针（Kubernetes readiness probe）。"""
    db_ok, _ = db.health_check()
    if db_ok:
        return jsonify({"status": "ready"}), 200
    return jsonify({"status": "not_ready"}), 503


@app.route("/health/metrics")
def health_metrics():
    """简单指标端点。"""
    return jsonify({
        "alerts_today": db.count_alerts_today(),
        "signals_today": db.count_signals_today(),
        "symbols": len(db.get_monitored_symbols()),
    })


# ---- 错误处理 ----
@app.errorhandler(500)
def handle_500(e):
    return jsonify({"error": "internal_error", "message": str(e)}), 500


@app.errorhandler(404)
def handle_404(e):
    return jsonify({"error": "not_found"}), 404


# ====== 生产运行入口 ======
def run(host: str = None, port: int = None, debug: bool = False):
    web_config = config.get("web", {})
    host = host or web_config.get("host", "0.0.0.0")
    port = port or web_config.get("port", 5000)
    use_production = web_config.get("production_server", False)

    if use_production and not debug:
        try:
            from waitress import serve
            print(f" [INFO] 使用 waitress 生产服务器: http://{host}:{port}")
            serve(app, host=host, port=port, threads=4)
        except ImportError:
            print(" [WARN] waitress 未安装，回退到 Flask 开发服务器")
            app.run(host=host, port=port, debug=False, use_reloader=False)
    else:
        app.run(host=host, port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    run()
