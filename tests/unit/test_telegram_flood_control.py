"""
Unit tests for Telegram flood-control handling and progress throttling.

覆盖方案A（RetryAfter 熔断 + 进度节流 + error_handler 静默）与
方案B（统一长轮询参数 POLLING_PARAMS）的核心逻辑。
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import NetworkError, RetryAfter

from ytbot.services.telegram_service import (
    POLLING_PARAMS,
    TelegramService,
)
from ytbot.handlers.telegram_handler import TelegramHandler

MB = 1024 * 1024


def make_message(message_id=123):
    """构造带 to_dict 的 mock message 对象。"""
    msg = MagicMock()
    msg.message_id = message_id
    msg.to_dict.return_value = {"message_id": message_id}
    return msg


@pytest.fixture
def service():
    """提供绕过 connect() 的 TelegramService 实例（直接注入 mock bot）。"""
    TelegramService._instance = None
    svc = TelegramService()
    svc.bot = MagicMock()
    svc.application = MagicMock()
    svc._connected = True
    svc._flood_until = 0.0
    yield svc
    TelegramService._instance = None


@pytest.fixture
def handler(service):
    return TelegramHandler(
        telegram_service=service,
        storage_service=MagicMock(),
        download_service=MagicMock()
    )


def progress_data(downloaded_mb, total_mb=100.0, speed=0):
    return {
        'status': 'downloading',
        'downloaded_bytes': int(downloaded_mb * MB),
        'total_bytes': int(total_mb * MB),
        'speed': speed,
    }


class TestProgressThrottle:
    """方案A2：_update_download_progress 双阈值节流。"""

    async def test_first_progress_edit_sent(self, handler, service):
        service.bot.edit_message_text = AsyncMock(return_value=make_message(55))

        with patch('ytbot.handlers.telegram_handler.time.monotonic', return_value=100.0):
            await handler._update_download_progress(1, 55, progress_data(50.0))

        service.bot.edit_message_text.assert_called_once()
        assert handler._progress_last_edit[55] == 100.0
        assert handler._progress_last_percent[55] == pytest.approx(50.0)

    async def test_skipped_within_5_seconds(self, handler, service):
        service.bot.edit_message_text = AsyncMock(return_value=make_message(55))

        with patch('ytbot.handlers.telegram_handler.time.monotonic', return_value=100.0):
            await handler._update_download_progress(1, 55, progress_data(50.0))
        with patch('ytbot.handlers.telegram_handler.time.monotonic', return_value=103.0):
            await handler._update_download_progress(1, 55, progress_data(60.0))

        # 5 秒内的第二次调用被节流跳过
        service.bot.edit_message_text.assert_called_once()

    async def test_skipped_when_percent_change_below_2(self, handler, service):
        service.bot.edit_message_text = AsyncMock(return_value=make_message(55))

        with patch('ytbot.handlers.telegram_handler.time.monotonic', return_value=100.0):
            await handler._update_download_progress(1, 55, progress_data(50.0))
        # 间隔已超过 5 秒，但进度仅变化 1%
        with patch('ytbot.handlers.telegram_handler.time.monotonic', return_value=110.0):
            await handler._update_download_progress(1, 55, progress_data(51.0))

        service.bot.edit_message_text.assert_called_once()

    async def test_sent_when_both_thresholds_pass(self, handler, service):
        service.bot.edit_message_text = AsyncMock(return_value=make_message(55))

        with patch('ytbot.handlers.telegram_handler.time.monotonic', return_value=100.0):
            await handler._update_download_progress(1, 55, progress_data(50.0))
        with patch('ytbot.handlers.telegram_handler.time.monotonic', return_value=110.0):
            await handler._update_download_progress(1, 55, progress_data(55.0))

        assert service.bot.edit_message_text.call_count == 2
        assert handler._progress_last_percent[55] == pytest.approx(55.0)

    async def test_discardable_dropped_during_flood(self, handler, service):
        service.bot.edit_message_text = AsyncMock(return_value=make_message(55))
        # 模拟熔断窗口活跃
        service._flood_until = time.monotonic() + 60.0

        with patch('ytbot.handlers.telegram_handler.time.monotonic', return_value=100.0):
            await handler._update_download_progress(1, 55, progress_data(50.0))

        # 熔断窗口内丢弃进度编辑，不产生 API 调用
        service.bot.edit_message_text.assert_not_called()
        # 状态未记录：熔断结束后下一次进度更新可立即发出
        assert 55 not in handler._progress_last_edit

    async def test_non_downloading_status_ignored(self, handler, service):
        service.bot.edit_message_text = AsyncMock(return_value=make_message(55))

        await handler._update_download_progress(
            1, 55, {'status': 'finished'}
        )

        service.bot.edit_message_text.assert_not_called()


class TestFloodCircuitBreaker:
    """方案A1：TelegramService 全局 flood 熔断。"""

    def test_register_flood_sets_window(self, service):
        before = time.monotonic()
        service._register_flood(30)

        assert service._flood_until >= before + 32.0
        assert service._flood_until <= time.monotonic() + 32.0 + 5

    def test_register_flood_keeps_longer_window(self, service):
        service._flood_until = time.monotonic() + 3600
        service._register_flood(5)
        # 已有更长的熔断窗口时不缩短
        assert service._flood_until > time.monotonic() + 3500

    async def test_wait_out_flood_returns_immediately_when_clear(self, service):
        service._flood_until = 0.0
        assert await service._wait_out_flood() is True

    async def test_wait_out_flood_aborts_on_shutdown(self, service):
        service._flood_until = time.monotonic() + 300
        service._shutdown_event.set()

        assert await service._wait_out_flood() is False

    async def test_wait_out_flood_sleeps_until_window_expires(self, service):
        # monotonic 序列：初始 100 → 睡满 3 秒后 103（窗口到期）
        clock = [100.0, 103.0]
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        service._flood_until = 103.0
        with patch(
            'ytbot.services.telegram_service.time.monotonic',
            side_effect=lambda: clock.pop(0) if clock else 103.0
        ), patch(
            'ytbot.services.telegram_service.asyncio.sleep',
            side_effect=fake_sleep
        ):
            assert await service._wait_out_flood() is True

        assert sleeps == [3.0]

    async def test_edit_discardable_dropped_during_flood(self, service):
        service.bot.edit_message_text = AsyncMock(return_value=make_message())
        service._flood_until = time.monotonic() + 60.0

        result = await service.edit_message(1, 55, "text", discardable=True)

        assert result is None
        service.bot.edit_message_text.assert_not_called()

    async def test_edit_nondiscardable_waits_and_retries(self, service):
        message = make_message(55)
        service.bot.edit_message_text = AsyncMock(
            side_effect=[RetryAfter(5), message]
        )
        # 跳过真实等待（等待行为已在上面单独测试）
        service._wait_out_flood = AsyncMock(return_value=True)

        result = await service.edit_message(1, 55, "text")

        assert result == {"message_id": 55}
        assert service.bot.edit_message_text.call_count == 2
        # RetryAfter 已登记熔断窗口
        assert service._flood_until > time.monotonic()
        service._wait_out_flood.assert_awaited()

    async def test_edit_gives_up_after_repeated_flood(self, service):
        service.bot.edit_message_text = AsyncMock(side_effect=RetryAfter(5))
        service._wait_out_flood = AsyncMock(return_value=True)

        result = await service.edit_message(1, 55, "text")

        assert result is None
        assert service.bot.edit_message_text.call_count == 2

    async def test_edit_message_not_modified_still_handled(self, service):
        service.bot.edit_message_text = AsyncMock(
            side_effect=Exception("Message is not modified")
        )

        result = await service.edit_message(1, 55, "text")

        assert result == {"message_id": 55, "unchanged": True}

    async def test_send_message_waits_and_retries_on_flood(self, service):
        message = make_message(77)
        service.bot.send_message = AsyncMock(side_effect=[RetryAfter(5), message])
        service._wait_out_flood = AsyncMock(return_value=True)

        result = await service.send_message(1, "hello")

        assert result == {"message_id": 77}
        assert service.bot.send_message.call_count == 2
        assert service._flood_until > time.monotonic()


class TestHealthCheckFloodAwareness:
    """方案A4：熔断窗口内跳过 get_me 健康检查。"""

    async def test_health_check_skipped_during_flood(self, service):
        service.bot.get_me = AsyncMock()
        service._flood_until = time.monotonic() + 60.0

        assert await service.check_connection_health() is True
        service.bot.get_me.assert_not_called()

    async def test_health_check_performed_without_flood(self, service):
        bot_info = MagicMock()
        service.bot.get_me = AsyncMock(return_value=bot_info)
        service._flood_until = 0.0

        assert await service.check_connection_health() is True
        service.bot.get_me.assert_awaited_once()


class TestErrorHandlerSilence:
    """方案A3：网络/限流错误下 error_handler 不向用户发消息。"""

    def _make_context(self, error):
        ctx = MagicMock()
        ctx.error = error
        return ctx

    async def test_silent_on_retry_after(self, handler, service):
        service.send_message = AsyncMock()

        await handler.error_handler(None, self._make_context(RetryAfter(10)))

        service.send_message.assert_not_called()

    async def test_silent_on_network_error(self, handler, service):
        service.send_message = AsyncMock()

        await handler.error_handler(None, self._make_context(NetworkError("proxy reset")))

        service.send_message.assert_not_called()

    async def test_notifies_on_other_errors(self, handler, service):
        service.send_message = AsyncMock()
        update = MagicMock()
        update.effective_chat.id = 42

        await handler.error_handler(update, self._make_context(ValueError("boom")))

        service.send_message.assert_awaited_once()


class TestPollingParams:
    """方案B：统一长轮询参数。"""

    def test_params_values(self):
        assert POLLING_PARAMS["timeout"] == 50
        # read_timeout 必须大于长轮询挂起时长，避免代理慢导致误判超时
        assert POLLING_PARAMS["read_timeout"] > POLLING_PARAMS["timeout"]
        assert set(POLLING_PARAMS["allowed_updates"]) == {"message", "callback_query"}
        assert POLLING_PARAMS["drop_pending_updates"] is False

    async def test_start_updater_polling_uses_unified_params(self, service):
        service.application.updater.start_polling = AsyncMock()

        await service._start_updater_polling()

        service.application.updater.start_polling.assert_called_once_with(
            **POLLING_PARAMS
        )
