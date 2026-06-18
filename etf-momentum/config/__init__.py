"""配置加载模块"""

import os
import yaml

_config = None


def load_config(config_path: str = None) -> dict:
    """加载 YAML 配置文件，支持环境变量覆盖。"""
    global _config
    if _config is not None:
        return _config

    if config_path is None:
        # 优先使用环境变量指定路径
        config_path = os.environ.get("ETF_CONFIG", "config/config.yaml")

    if not os.path.exists(config_path):
        # 回退到示例配置
        example_path = config_path.replace(".yaml", ".example.yaml")
        if os.path.exists(example_path):
            print(f"[WARN]  配置文件 {config_path} 不存在，使用示例配置 {example_path}")
            config_path = example_path
        else:
            raise FileNotFoundError(f"配置文件未找到: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        _config = yaml.safe_load(f)

    # 环境变量覆盖
    _apply_env_overrides(_config)
    return _config


def _apply_env_overrides(config: dict) -> None:
    """用环境变量覆盖配置中的敏感值。"""
    env_map = {
        "ETF_DB_PATH": ("database", "path"),
        "ETF_TELEGRAM_TOKEN": ("telegram", "bot_token"),
        "ETF_TELEGRAM_CHAT_ID": ("telegram", "chat_id"),
        "ETF_EMAIL_SENDER": ("email", "sender"),
        "ETF_EMAIL_PASSWORD": ("email", "password"),
        "ETF_WEB_SECRET_KEY": ("web", "secret_key"),
    }
    for env_key, (section, key) in env_map.items():
        val = os.environ.get(env_key)
        if val and section in config:
            config[section][key] = val


def get_config() -> dict:
    """获取已加载的配置（需先调用 load_config）。"""
    if _config is None:
        return load_config()
    return _config
