#!/usr/bin/env python3
"""Generate a scheduled ETF daily report with DeepSeek analysis and Gmail delivery."""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import random
import re
import smtplib
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.header import Header
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

import akshare as ak
import pandas as pd
import requests

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = BASE_DIR / "config.json"
EXAMPLE_CONFIG_PATH = BASE_DIR / "config.example.json"
STATE_DIR = BASE_DIR / "state"
LOG_DIR = BASE_DIR / "logs"
HISTORY_PATH = STATE_DIR / "history.json"
LOCK_PATH = STATE_DIR / "etf_report.lock"

RELEVANT_COLUMNS = ["日期", "开盘", "收盘", "最高", "最低", "成交量", "成交额", "涨跌幅"]
DEFAULT_SYSTEM_PROMPT = "你是严谨的金融数据分析助手。输出必须客观、可执行、避免夸大。"


class ConfigError(RuntimeError):
    """Raised when local configuration is incomplete."""


class LockError(RuntimeError):
    """Raised when another run appears to be active."""


@dataclass
class EtfSnapshot:
    code: str
    name: str
    latest_date: str
    close: float
    previous_close: float | None
    open_price: float | None
    high: float | None
    low: float | None
    volume: float | None
    turnover: float | None
    daily_pct: float | None
    return_5d: float | None
    return_10d: float | None
    return_20d: float | None
    period_high: float | None
    period_low: float | None
    table_text: str


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_format = "%(asctime)s [%(levelname)s] %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_DIR / "etf_report.log", encoding="utf-8"),
        ],
    )


def load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        raise ConfigError(
            f"配置文件不存在：{config_path}\n"
            f"请先复制 {EXAMPLE_CONFIG_PATH.name} 为 config.json，并按 README 填写配置。"
        )
    with config_path.open("r", encoding="utf-8") as file_obj:
        config = json.load(file_obj)

    deepseek_conf = config.setdefault("deepseek", {})
    email_conf = config.setdefault("email", {})

    api_key_env = deepseek_conf.get("api_key_env", "DEEPSEEK_API_KEY")
    password_env = email_conf.get("password_env", "GMAIL_APP_PASSWORD")

    deepseek_conf["api_key"] = os.getenv(api_key_env) or deepseek_conf.get("api_key")
    email_conf["password"] = os.getenv(password_env) or email_conf.get("password")

    if not deepseek_conf.get("api_key"):
        raise ConfigError(f"未配置 DeepSeek API Key。请设置环境变量 {api_key_env}，或在 config.json 的 deepseek.api_key 填写。")
    if not email_conf.get("password"):
        raise ConfigError(f"未配置 Gmail 应用专用密码。请设置环境变量 {password_env}，或在 config.json 的 email.password 填写。")

    return config


class SingleRunLock:
    def __init__(self, path: Path, stale_seconds: int = 6 * 60 * 60) -> None:
        self.path = path
        self.stale_seconds = stale_seconds
        self.acquired = False

    def __enter__(self) -> "SingleRunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                lock_data = json.loads(self.path.read_text(encoding="utf-8"))
                created_at = float(lock_data.get("created_at", 0))
            except Exception:
                created_at = 0
            if time.time() - created_at > self.stale_seconds:
                logging.warning("发现过期锁文件，自动清理：%s", self.path)
                self.path.unlink(missing_ok=True)
            else:
                raise LockError(f"检测到另一个任务可能正在运行，锁文件：{self.path}")

        fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as file_obj:
            json.dump({"pid": os.getpid(), "created_at": time.time()}, file_obj)
        self.acquired = True
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)


def to_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("%", "").strip()
        if cleaned in {"", "-", "--"}:
            return None
        value = cleaned
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current / previous - 1) * 100


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.2f}%"


def fmt_num(value: float | None, digits: int = 3) -> str:
    if value is None:
        return "N/A"
    return f"{value:.{digits}f}"


def normalize_history_df(df: pd.DataFrame, days: int) -> pd.DataFrame:
    if df is None or df.empty:
        raise ValueError("行情接口返回空数据")

    normalized = df.copy()
    for column in RELEVANT_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = None

    normalized = normalized[RELEVANT_COLUMNS].copy()
    for column in ["开盘", "收盘", "最高", "最低", "成交量", "成交额", "涨跌幅"]:
        normalized[column] = normalized[column].map(to_float)
    normalized["日期"] = normalized["日期"].astype(str)
    normalized = normalized[normalized["收盘"].notna()].tail(days).reset_index(drop=True)
    if normalized.empty:
        raise ValueError("行情接口返回数据中没有有效收盘价")
    return normalized


def fetch_akshare_quote_history(code: str, days: int) -> pd.DataFrame:
    """Fetch ETF daily K-line data via AKShare fund_etf_hist_em."""
    df = ak.fund_etf_hist_em(symbol=code, period="daily", start_date="19000101", end_date="20500101", adjust="qfq")
    return normalize_history_df(df, days)


def yahoo_symbol(code: str) -> str:
    """Return Yahoo Finance symbol for common A-share ETF codes."""
    suffix = "SS" if code.startswith(("5", "6", "9")) else "SZ"
    return f"{code}.{suffix}"


YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}


def fetch_yahoo_quote_history(code: str, days: int, timeout: int = 20) -> pd.DataFrame:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol(code)}"
    # Compute period1 from `days` (+ buffer) instead of fetching all history from epoch 0
    buffer_days = max(30, int(days * 0.5))
    period1 = int(time.time()) - (days + buffer_days) * 86400
    params = {
        "period1": str(period1),
        "period2": str(int(time.time()) + 86400),
        "interval": "1d",
        "events": "history",
    }
    response = requests.get(url, params=params, headers=YAHOO_HEADERS, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    result = (payload.get("chart", {}).get("result") or [None])[0]
    if not result:
        raise ValueError("Yahoo Finance 返回空 K 线")

    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators", {}).get("quote") or [{}])[0])
    opens = quote.get("open") or []
    closes = quote.get("close") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    volumes = quote.get("volume") or []

    rows: list[dict[str, Any]] = []
    for index, timestamp in enumerate(timestamps):
        close = closes[index] if index < len(closes) else None
        if close is None:
            continue
        rows.append(
            {
                "日期": datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d"),
                "开盘": opens[index] if index < len(opens) else None,
                "收盘": close,
                "最高": highs[index] if index < len(highs) else None,
                "最低": lows[index] if index < len(lows) else None,
                "成交量": volumes[index] if index < len(volumes) else None,
                "成交额": None,
                "涨跌幅": None,
            }
        )
    return normalize_history_df(pd.DataFrame(rows), days)


def backoff_sleep(attempt: int, base: float = 2.0, cap: float = 30.0) -> None:
    """Exponential backoff with jitter to avoid thundering herd."""
    delay = min(cap, base ** attempt)
    jitter = random.uniform(0, delay * 0.5)
    time.sleep(delay + jitter)


def fetch_quote_history(code: str, days: int) -> pd.DataFrame:
    errors: list[str] = []

    for attempt in range(1, 4):
        try:
            logging.info("尝试通过 AKShare 获取 %s 日线数据（第 %s/3 次）", code, attempt)
            return fetch_akshare_quote_history(code, days)
        except Exception as exc:
            errors.append(f"akshare attempt {attempt}: {exc}")
            if attempt < 3:
                backoff_sleep(attempt)

    for attempt in range(1, 4):
        try:
            logging.info("尝试通过 Yahoo Finance 获取 %s 日线数据（第 %s/3 次）", code, attempt)
            return fetch_yahoo_quote_history(code, days)
        except Exception as exc:
            errors.append(f"yahoo attempt {attempt}: {exc}")
            if attempt < 3:
                # Use longer backoff for Yahoo to respect rate limits
                backoff_sleep(attempt, base=4.0, cap=60.0)

    raise RuntimeError(f"无法获取 {code} 行情：" + "; ".join(errors))


def build_snapshot(code: str, name: str, df: pd.DataFrame) -> EtfSnapshot:
    latest = df.iloc[-1]
    previous = df.iloc[-2] if len(df) >= 2 else None

    close = to_float(latest["收盘"])
    if close is None:
        raise ValueError(f"{code} 最新收盘价为空")

    previous_close = to_float(previous["收盘"]) if previous is not None else None
    closes = [to_float(value) for value in df["收盘"].tolist()]
    highs = [to_float(value) for value in df["最高"].tolist()]
    lows = [to_float(value) for value in df["最低"].tolist()]

    def return_from_n_sessions(n: int) -> float | None:
        if len(closes) <= n:
            return None
        return pct_change(closes[-1], closes[-(n + 1)])

    table_text = df.to_string(index=False, na_rep="N/A")
    return EtfSnapshot(
        code=code,
        name=name,
        latest_date=str(latest["日期"]),
        close=close,
        previous_close=previous_close,
        open_price=to_float(latest["开盘"]),
        high=to_float(latest["最高"]),
        low=to_float(latest["最低"]),
        volume=to_float(latest["成交量"]),
        turnover=to_float(latest["成交额"]),
        daily_pct=to_float(latest["涨跌幅"]) if to_float(latest["涨跌幅"]) is not None else pct_change(close, previous_close),
        return_5d=return_from_n_sessions(5),
        return_10d=return_from_n_sessions(10),
        return_20d=return_from_n_sessions(20),
        period_high=max([value for value in highs if value is not None], default=None),
        period_low=min([value for value in lows if value is not None], default=None),
        table_text=table_text,
    )


def snapshot_to_history(snapshot: EtfSnapshot) -> dict[str, Any]:
    return {
        "name": snapshot.name,
        "latest_date": snapshot.latest_date,
        "close": snapshot.close,
        "previous_close": snapshot.previous_close,
        "daily_pct": snapshot.daily_pct,
        "return_5d": snapshot.return_5d,
        "return_10d": snapshot.return_10d,
        "return_20d": snapshot.return_20d,
    }


def render_prompt_template(template: str, values: dict[str, Any]) -> str:
    """Render prompt templates that use {{placeholder}} tokens."""
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", str(value))
    return rendered.strip()


def get_prompt_template(config: dict[str, Any], name: str, default: str | None = None) -> str:
    template = config.get("prompts", {}).get(name)
    if isinstance(template, list):
        template = "\n".join(str(line) for line in template)
    if isinstance(template, str) and template.strip():
        return template.strip()
    if default is not None:
        return default.strip()
    raise ConfigError(f"配置文件缺少 prompts.{name}，请参考 config.example.json 补充提示词模板。")


def build_etf_data_block(snapshot: EtfSnapshot, config: dict[str, Any]) -> str:
    template = get_prompt_template(config, "etf_data_block_template")
    return render_prompt_template(
        template,
        {
            "name": snapshot.name,
            "code": snapshot.code,
            "latest_date": snapshot.latest_date,
            "close": fmt_num(snapshot.close),
            "previous_close": fmt_num(snapshot.previous_close),
            "daily_pct": fmt_pct(snapshot.daily_pct),
            "return_5d": fmt_pct(snapshot.return_5d),
            "return_10d": fmt_pct(snapshot.return_10d),
            "return_20d": fmt_pct(snapshot.return_20d),
            "period_high": fmt_num(snapshot.period_high),
            "period_low": fmt_num(snapshot.period_low),
            "volume": fmt_num(snapshot.volume, 0),
            "turnover": fmt_num(snapshot.turnover, 0),
            "daily_table": snapshot.table_text,
        },
    )


def build_daily_prompt(snapshots: list[EtfSnapshot], config: dict[str, Any]) -> str:
    etf_blocks = "\n\n".join(build_etf_data_block(snapshot, config) for snapshot in snapshots)
    template = get_prompt_template(config, "daily_prompt_template")
    return render_prompt_template(template, {"etf_blocks": etf_blocks})


def deepseek_endpoint(base_url: str) -> str:
    cleaned = base_url.rstrip("/")
    if cleaned.endswith("/chat/completions"):
        return cleaned
    return f"{cleaned}/chat/completions"


def strip_markdown_fences(text: str) -> str:
    stripped = text.strip()
    stripped = re.sub(r"^```(?:html)?\s*", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def call_deepseek(prompt: str, config: dict[str, Any], purpose: str) -> str:
    deepseek_conf = config["deepseek"]
    url = deepseek_endpoint(deepseek_conf.get("base_url", "https://api.deepseek.com"))
    timeout = int(deepseek_conf.get("timeout_seconds", 60))
    max_retries = int(deepseek_conf.get("max_retries", 3))
    retry_base_seconds = int(deepseek_conf.get("retry_base_seconds", 5))

    headers = {
        "Authorization": f"Bearer {deepseek_conf['api_key']}",
        "Content-Type": "application/json",
    }
    system_prompt = get_prompt_template(config, "system_prompt", DEFAULT_SYSTEM_PROMPT)
    payload = {
        "model": deepseek_conf.get("model", "deepseek-chat"),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": float(deepseek_conf.get("temperature", 0.2)),
        "stream": False,
    }

    last_error: str | None = None
    for attempt in range(1, max_retries + 1):
        try:
            logging.info("调用 DeepSeek 生成%s（第 %s/%s 次）", purpose, attempt, max_retries)
            response = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if response.status_code in {429, 500, 502, 503, 504} and attempt < max_retries:
                last_error = f"HTTP {response.status_code}: {response.text[:500]}"
                wait_seconds = retry_base_seconds * attempt
                logging.warning("DeepSeek 临时错误：%s；%s 秒后重试", last_error, wait_seconds)
                time.sleep(wait_seconds)
                continue
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            return strip_markdown_fences(content)
        except Exception as exc:
            last_error = str(exc)
            if attempt < max_retries:
                wait_seconds = retry_base_seconds * attempt
                logging.warning("DeepSeek 调用异常：%s；%s 秒后重试", last_error, wait_seconds)
                time.sleep(wait_seconds)
            else:
                logging.exception("DeepSeek 生成%s失败", purpose)

    return f"<div style='color:#b00020;'><strong>DeepSeek 生成{html.escape(purpose)}失败：</strong>{html.escape(last_error or '未知错误')}</div>"


def load_history(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"run_count": 0, "runs": [], "prompt_suggestions": []}
    with path.open("r", encoding="utf-8") as file_obj:
        history = json.load(file_obj)
    history.setdefault("run_count", 0)
    history.setdefault("runs", [])
    history.setdefault("prompt_suggestions", [])
    return history


def save_history(path: Path, history: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".json.tmp")
    with temp_path.open("w", encoding="utf-8") as file_obj:
        json.dump(history, file_obj, ensure_ascii=False, indent=2)
    temp_path.replace(path)


def text_brief_from_html(html_content: str, max_chars: int = 300) -> str:
    text = re.sub(r"<[^>]+>", " ", html_content)
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def calculate_period_returns(runs: list[dict[str, Any]], etfs: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    returns: dict[str, dict[str, Any]] = {}
    if len(runs) < 2:
        return returns

    first_run = runs[0]
    last_run = runs[-1]
    for etf in etfs:
        code = etf["code"]
        name = etf.get("name", code)
        first_close = to_float(first_run.get("etfs", {}).get(code, {}).get("close"))
        last_close = to_float(last_run.get("etfs", {}).get(code, {}).get("close"))
        returns[code] = {
            "name": name,
            "start_close": first_close,
            "end_close": last_close,
            "period_return_pct": pct_change(last_close, first_close),
        }
    return returns


def build_summary_prompt(
    recent_runs: list[dict[str, Any]],
    period_returns: dict[str, dict[str, Any]],
    current_prompt: str,
    config: dict[str, Any],
) -> str:
    compact_runs = json.dumps(recent_runs, ensure_ascii=False, indent=2)
    compact_returns = json.dumps(period_returns, ensure_ascii=False, indent=2)
    template = get_prompt_template(config, "summary_prompt_template")
    return render_prompt_template(
        template,
        {
            "recent_runs": compact_runs,
            "period_returns": compact_returns,
            "current_daily_prompt": current_prompt,
        },
    )


def build_email_body(daily_html: str, summary_html: str | None, run_count: int) -> str:
    now_text = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    summary_section = summary_html or ""
    return f"""
<html>
<body style="font-family: Arial, 'Microsoft YaHei', sans-serif; line-height: 1.65; color: #222;">
  <h1 style="color:#0b5394; border-bottom:2px solid #0b5394; padding-bottom:8px;">ETF 量化日报</h1>
  <p style="color:#666;">运行时间：{html.escape(now_text)}；累计运行次数：{run_count}</p>
  <section>{daily_html}</section>
  {f'<hr style="margin:24px 0; border:0; border-top:1px solid #ddd;"><section>{summary_section}</section>' if summary_section else ''}
  <hr style="margin:24px 0; border:0; border-top:1px solid #ddd;">
  <p style="font-size:12px; color:#888;">本邮件由定时程序自动生成，分析结果由 DeepSeek 基于行情数据生成，仅供研究参考，不构成投资建议。</p>
</body>
</html>
""".strip()


def email_recipients(receiver_config: str | list[str]) -> list[str]:
    if isinstance(receiver_config, list):
        return receiver_config
    return [item.strip() for item in str(receiver_config).split(",") if item.strip()]


def send_email(subject: str, html_body: str, config: dict[str, Any]) -> None:
    email_conf = config["email"]
    sender = email_conf["sender"]
    recipients = email_recipients(email_conf["receiver"])
    if not recipients:
        raise ConfigError("email.receiver 至少需要配置一个收件人")

    message = MIMEText(html_body, "html", "utf-8")
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = Header(subject, "utf-8")

    logging.info("发送邮件到：%s", ", ".join(recipients))
    with smtplib.SMTP_SSL(email_conf.get("smtp_server", "smtp.gmail.com"), int(email_conf.get("smtp_port", 465))) as smtp_obj:
        smtp_obj.login(sender, email_conf["password"])
        smtp_obj.sendmail(sender, recipients, message.as_string())
    logging.info("邮件发送成功")


def collect_snapshots(config: dict[str, Any]) -> list[EtfSnapshot]:
    days = int(config.get("market_data", {}).get("daily_days", 20))
    inter_etf_cooldown = float(config.get("market_data", {}).get("inter_etf_cooldown_seconds", 3.0))
    snapshots: list[EtfSnapshot] = []
    failed_codes: list[str] = []
    for idx, etf in enumerate(config.get("etfs", [])):
        code = str(etf["code"])
        name = etf.get("name", code)
        try:
            df = fetch_quote_history(code, days)
            snapshot = build_snapshot(code, name, df)
            snapshots.append(snapshot)
            logging.info("已获取 %s（%s）：最新收盘 %s，日涨跌 %s", name, code, fmt_num(snapshot.close), fmt_pct(snapshot.daily_pct))
        except Exception as exc:
            logging.warning("获取 %s（%s）行情失败：%s；跳过该 ETF 继续执行", name, code, exc)
            failed_codes.append(code)
        # Insert cooldown between ETF requests to avoid triggering rate limits
        if idx < len(config.get("etfs", [])) - 1 and inter_etf_cooldown > 0:
            time.sleep(inter_etf_cooldown)

    if not snapshots:
        raise ConfigError("config.json 中 etfs 全部获取失败或列表为空")
    if failed_codes:
        logging.warning("以下 ETF 获取失败已跳过：%s", ", ".join(failed_codes))
    return snapshots


def append_current_run(
    history: dict[str, Any],
    snapshots: list[EtfSnapshot],
    analysis_html: str,
    config: dict[str, Any],
) -> int:
    history["run_count"] = int(history.get("run_count", 0)) + 1
    run_id = history["run_count"]
    history["runs"].append(
        {
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "etfs": {snapshot.code: snapshot_to_history(snapshot) for snapshot in snapshots},
            "analysis_brief": text_brief_from_html(analysis_html),
        }
    )

    max_history_runs = int(config.get("report", {}).get("max_history_runs", 120))
    if max_history_runs > 0:
        history["runs"] = history["runs"][-max_history_runs:]
    return run_id


def maybe_generate_summary(
    history: dict[str, Any],
    config: dict[str, Any],
    daily_prompt: str,
) -> str | None:
    report_conf = config.get("report", {})
    summary_every_runs = int(report_conf.get("summary_every_runs", 5))
    run_count = int(history.get("run_count", 0))
    if summary_every_runs <= 0 or run_count % summary_every_runs != 0:
        return None

    recent_runs = history.get("runs", [])[-summary_every_runs:]
    if len(recent_runs) < summary_every_runs:
        logging.warning("历史记录不足 %s 次，跳过阶段总结", summary_every_runs)
        return None

    period_returns = calculate_period_returns(recent_runs, config.get("etfs", []))
    summary_prompt = build_summary_prompt(recent_runs, period_returns, daily_prompt, config)
    summary_html = call_deepseek(summary_prompt, config, f"最近 {summary_every_runs} 次运行收益复盘")
    history["prompt_suggestions"].append(
        {
            "run_count": run_count,
            "timestamp": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "period_returns": period_returns,
            "summary_brief": text_brief_from_html(summary_html, max_chars=500),
        }
    )
    return summary_html


def run(config_path: Path, skip_email: bool = False) -> None:
    setup_logging()
    logging.info("ETF 日报任务启动")
    config = load_config(config_path)

    with SingleRunLock(LOCK_PATH):
        history = load_history(HISTORY_PATH)
        snapshots = collect_snapshots(config)
        daily_prompt = build_daily_prompt(snapshots, config)
        daily_html = call_deepseek(daily_prompt, config, "ETF 日报")
        run_count = append_current_run(history, snapshots, daily_html, config)
        summary_html = maybe_generate_summary(history, config, daily_prompt)
        save_history(HISTORY_PATH, history)

        subject_date = datetime.now().astimezone().strftime("%Y-%m-%d")
        subject = f"【ETF量化日报】科创50ETF & 中证500ETF {subject_date}"
        html_body = build_email_body(daily_html, summary_html, run_count)

        if skip_email:
            output_path = LOG_DIR / f"preview_run_{run_count}.html"
            output_path.write_text(html_body, encoding="utf-8")
            logging.info("已跳过邮件发送，预览 HTML 保存到：%s", output_path)
        else:
            send_email(subject, html_body, config)

    logging.info("ETF 日报任务完成")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成 ETF DeepSeek 分析日报并通过 Gmail 发送。")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="配置文件路径，默认读取当前目录 config.json")
    parser.add_argument("--skip-email", action="store_true", help="不发送邮件，将邮件 HTML 保存到 logs/preview_run_*.html")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        run(Path(args.config).resolve(), skip_email=args.skip_email)
        return 0
    except ConfigError as exc:
        setup_logging()
        logging.error("配置错误：%s", exc)
        return 2
    except LockError as exc:
        setup_logging()
        logging.error("任务锁定：%s", exc)
        return 3
    except Exception:
        setup_logging()
        logging.exception("ETF 日报任务失败")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
