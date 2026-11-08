"""Application entry point."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import APP_NAME
from .redact import RedactingFilter


def _data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _setup_logging() -> Path:
    log_path = _data_dir() / "naretta.log"
    handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    handlers: list[logging.Handler] = [handler]
    if sys.stderr is not None:  # None in a windowed PyInstaller build
        console = logging.StreamHandler()
        console.addFilter(RedactingFilter())
        handlers.append(console)
    level = logging.DEBUG if os.environ.get("NARETTA_DEBUG") else logging.INFO
    logging.basicConfig(level=level, handlers=handlers)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    return log_path


def _load_env_file() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    exe_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
    for candidate in (exe_dir / ".env", Path.cwd() / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)


def _self_test() -> int:
    """Checks that the audio libraries load (useful for a packaged build). Results go to the log."""
    log = logging.getLogger("naretta.selftest")
    ok = True
    try:
        from .audio import VoiceProcessor, list_devices

        log.info("Microphones: %s", list_devices("input"))
        log.info("Speakers: %s", list_devices("output"))
    except Exception as exc:
        log.error("Audio devices: %s", exc)
        ok = False
    try:
        import numpy as np

        processor = VoiceProcessor()
        processor.process_far(np.zeros(VoiceProcessor.FRAME * 4, dtype=np.int16))
        out = processor.process_near(np.zeros(VoiceProcessor.FRAME * 4, dtype=np.int16))
        log.info("Echo cancellation: OK (%d samples processed)", out.size)
    except Exception as exc:
        log.error("Echo cancellation: %s", exc)
        ok = False
    log.info("Self-test %s", "passed" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    _load_env_file()
    log_path = _setup_logging()
    logging.getLogger(__name__).info("Starting %s; log file %s", APP_NAME, log_path)
    if "--self-test" in sys.argv:
        return _self_test()

    from PySide6.QtWidgets import QApplication

    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    window = MainWindow()
    window.show()
    return app.exec()
