"""
SDK 日志配置工具。

提供标准化的日志初始化，与 ZapryAgent 配合使用。
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logging(
    level: int = logging.INFO,
    log_file: str = "",
    debug: bool = False,
) -> logging.Logger:
    """
    初始化统一的日志配置。

    Args:
        level: 默认日志级别。
        log_file: 日志文件路径（为空则仅输出到终端）。
        debug: 是否开启 DEBUG 模式。

    Returns:
        根 Logger 实例。
    """
    if debug:
        level = logging.DEBUG

    log_format = "%(asctime)s | %(name)s | %(levelname)s | %(message)s"

    logging.basicConfig(level=level, format=log_format, force=True)

    # 降低第三方库日志级别
    for name in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)

    # 文件输出
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        fh = RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(logging.Formatter(log_format))
        fh.setLevel(logging.INFO)
        logging.getLogger().addHandler(fh)

    return logging.getLogger("zapry_agents_sdk")
