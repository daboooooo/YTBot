"""
Regression tests for Telegram long-polling startup parameters.

python-telegram-bot 22.0 removed ``read_timeout``/``write_timeout``/
``connect_timeout``/``pool_timeout`` from ``Updater.start_polling``; the
timeouts must now be configured on the ``getUpdates`` request object.
These tests fail fast if the polling parameters and the installed library
drift apart again.
"""

import inspect
import logging

from telegram.error import NetworkError
from telegram.ext import Application, Updater
from telegram.request import HTTPXRequest

from ytbot.services.telegram_service import (
    POLLING_PARAMS,
    POLLING_REQUEST_PARAMS,
    _handle_polling_error,
)


class TestPollingParamsCompatibility:

    def test_start_polling_accepts_configured_params(self):
        """Every key in POLLING_PARAMS must be a real start_polling arg"""
        accepted = set(inspect.signature(Updater.start_polling).parameters)
        unsupported = set(POLLING_PARAMS) - accepted
        assert not unsupported, (
            f"Updater.start_polling() no longer accepts: {sorted(unsupported)}"
        )

    def test_removed_timeout_kwargs_not_passed_to_start_polling(self):
        """Timeouts must go through the request object, not start_polling"""
        removed_in_v22 = {
            "read_timeout",
            "write_timeout",
            "connect_timeout",
            "pool_timeout",
        }
        assert not (removed_in_v22 & set(POLLING_PARAMS))

    def test_read_timeout_exceeds_long_poll_timeout(self):
        """read_timeout > timeout avoids false timeouts on a slow proxy"""
        assert (
            POLLING_REQUEST_PARAMS["read_timeout"] > POLLING_PARAMS["timeout"]
        )


class TestGetUpdatesRequestWiring:

    def test_http_request_accepts_timeout_params(self):
        request = HTTPXRequest(**POLLING_REQUEST_PARAMS)
        assert request.read_timeout == POLLING_REQUEST_PARAMS["read_timeout"]

    def test_builder_applies_request_to_get_updates(self):
        """The custom request must be the one used for getUpdates"""
        request = HTTPXRequest(**POLLING_REQUEST_PARAMS)
        app = (
            Application.builder()
            .token("123456:FAKE-TOKEN-for-build-test")
            .get_updates_request(request)
            .build()
        )
        get_updates_request, _ = app.bot._request
        assert get_updates_request is request
        assert get_updates_request.read_timeout == 60

    def test_build_does_not_require_network(self):
        """Building the application with the custom request must not block"""
        app = (
            Application.builder()
            .token("123456:FAKE-TOKEN-for-build-test")
            .get_updates_request(HTTPXRequest(**POLLING_REQUEST_PARAMS))
            .build()
        )
        assert app.updater is not None


class TestPollingErrorCallback:
    """自定义 error_callback 只降噪日志，不改变轮询重试行为。"""

    def test_error_callback_wired_into_polling_params(self):
        """POLLING_PARAMS 必须携带自定义回调，且 start_polling 支持该参数"""
        assert POLLING_PARAMS["error_callback"] is _handle_polling_error
        accepted = set(inspect.signature(Updater.start_polling).parameters)
        assert "error_callback" in accepted

    def test_error_callback_logs_single_warning_line(self, caplog):
        """网络错误只产生一行 WARNING，不再输出整段 traceback"""
        exc = NetworkError("")  # PTB 会把 httpx.ConnectError 包装成空消息
        exc.__cause__ = OSError("tunnel failed")

        with caplog.at_level(
            logging.WARNING, logger="ytbot.services.telegram_service"
        ):
            _handle_polling_error(exc)

        records = [
            r for r in caplog.records
            if r.name == "ytbot.services.telegram_service"
        ]
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING
        assert records[0].exc_info is None
        assert "NetworkError" in records[0].getMessage()
        assert "OSError" in records[0].getMessage()
