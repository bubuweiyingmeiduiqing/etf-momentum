"""健康检查与进程管理"""
import os
import json
import time
import signal
import socket
import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)


class HealthChecker:
    """系统健康检查器，提供组件级别的健康状态。"""

    def __init__(self):
        self._checks = {}
        self._lock = threading.Lock()
        self._start_time = datetime.now()

    def register(self, name: str, check_fn):
        """注册一个健康检查函数，check_fn 应返回 (ok: bool, detail: str)。"""
        with self._lock:
            self._checks[name] = check_fn

    def check_all(self) -> dict:
        """执行所有健康检查，返回状态字典。"""
        results = {}
        all_healthy = True
        with self._lock:
            for name, fn in self._checks.items():
                try:
                    ok, detail = fn()
                    results[name] = {"healthy": ok, "detail": detail}
                    if not ok:
                        all_healthy = False
                except Exception as e:
                    results[name] = {"healthy": False, "detail": str(e)}
                    all_healthy = False

        uptime_seconds = (datetime.now() - self._start_time).total_seconds()
        return {
            "status": "healthy" if all_healthy else "degraded",
            "uptime_seconds": uptime_seconds,
            "timestamp": datetime.now().isoformat(),
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "components": results,
        }

    def to_json(self) -> str:
        return json.dumps(self.check_all(), ensure_ascii=False, indent=2)


class PIDFile:
    """PID 文件管理，防止重复启动。"""

    def __init__(self, path: str):
        self.path = path
        self._owned = False

    def acquire(self) -> bool:
        """获取 PID 文件锁。若已有进程运行则返回 False。"""
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as f:
                    old_pid = int(f.read().strip())
                # 检查旧进程是否仍在运行
                os.kill(old_pid, 0)
                logger.error("已有进程运行中 (PID=%d)，退出", old_pid)
                return False
            except (OSError, ValueError, ProcessLookupError):
                # 旧 PID 已失效，覆盖
                logger.warning("清理失效的 PID 文件 (PID=%s)", old_pid)
        with open(self.path, "w") as f:
            f.write(str(os.getpid()))
        self._owned = True
        return True

    def release(self):
        """释放 PID 文件。"""
        if self._owned and os.path.exists(self.path):
            try:
                os.remove(self.path)
            except OSError:
                pass


class GracefulShutdown:
    """优雅关闭管理器，注册清理回调并在收到信号时执行。"""

    def __init__(self):
        self._callbacks = []
        self._shutting_down = False
        self._lock = threading.Lock()

    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down

    def register(self, callback, name: str = ""):
        """注册一个清理回调。"""
        with self._lock:
            self._callbacks.append((name, callback))

    def setup_signals(self):
        """注册 SIGTERM / SIGINT 信号处理器。"""
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _handle_signal(self, signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info("收到 %s 信号，开始优雅关闭...", sig_name)
        self.shutdown()

    def shutdown(self):
        """执行所有清理回调。"""
        with self._lock:
            if self._shutting_down:
                return
            self._shutting_down = True

        logger.info("执行 %d 个清理回调...", len(self._callbacks))
        for name, callback in reversed(self._callbacks):
            try:
                label = name or callback.__name__
                logger.info("  清理: %s", label)
                callback()
            except Exception as e:
                logger.error("  清理 %s 失败: %s", name, e)
        logger.info("优雅关闭完成")
