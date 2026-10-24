"""kabuステーションAPI 用のロガー設定。

BasicDesign.md / Specifications.md で要求されている loglevel 機能を提供する。
環境変数 `KABU_API_LOG_LEVEL` でレベルを指定可能（既定値: INFO）。
"""

import logging
import os
import sys

DEFAULT_LOG_LEVEL = "INFO"

_LEVEL_MAP = {
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "NOTSET": logging.NOTSET,
}


def _normalize_level(level: str | None) -> int:
    """文字列レベルを logging レベル整数に変換する。"""
    if level is None:
        return logging.INFO
    return _LEVEL_MAP.get(level.upper(), logging.INFO)


def setup_logging(level: str | None = None) -> logging.Logger:
    """kabu_api 用のルートロガーを設定する。

    既に handler が設定されている場合は再設定しない。
    出力先は標準出力、フォーマットはメッセージのみ（print と同じ見た目）。
    """
    logger = logging.getLogger("kabu_api")
    if logger.handlers:
        # 既に設定済みの場合はレベルのみ更新する
        effective_level = _normalize_level(level or os.environ.get("KABU_API_LOG_LEVEL"))
        logger.setLevel(effective_level)
        for handler in logger.handlers:
            handler.setLevel(effective_level)
        return logger

    effective_level = _normalize_level(level or os.environ.get("KABU_API_LOG_LEVEL"))
    logger.setLevel(effective_level)
    logger.propagate = False

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(effective_level)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def get_logger(name: str = "kabu_api") -> logging.Logger:
    """kabu_api 名前空間のロガーを返す。

    まだ setup_logging() が呼ばれていなければ、ここで呼び出す。
    """
    logger = logging.getLogger(name)
    if not logger.handlers and not logging.getLogger("kabu_api").handlers:
        setup_logging()
    return logger
