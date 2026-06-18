#!/usr/bin/env python3
"""Web 服务独立入口 —— python run_web.py"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import load_config
from web.app import app

config = load_config()
web_config = config.get("web", {})
app.run(
    host=web_config.get("host", "0.0.0.0"),
    port=web_config.get("port", 5000),
    debug=web_config.get("debug", False),
)
