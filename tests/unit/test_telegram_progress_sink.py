"""
Unit tests for TelegramProgressSink upload-progress coalescing.

回归覆盖：
1. Nextcloud 阻塞式目录上传期间堆积的多次回调只能产生一条（最新）进度编辑，
   不再出现上传结束瞬间连发 7 条过期编辑；
2. flush 可重复调用，不再触发
   ValueError: list.remove(x): x not in list；
3. flush 完成后 on_success 的最终消息不会被过期进度编辑覆盖。
"""

import asyncio
from unittest.mock import AsyncMock

from ytbot.handlers.telegram_handler import TelegramProgressSink


class FakeTelegramService:
    """记录编辑文案；进度编辑可被 gate 阻塞以模拟进行中的请求。"""

    def __init__(self, gate=None):
        self.edits = []
        self._gate = gate

    async def edit_message(self, chat_id, message_id, text, **kwargs):
        if self._gate is not None and text.startswith("☁️"):
            await self._gate.wait()
        self.edits.append(text)
        return {"message_id": message_id}


def make_sink(service):
    return TelegramProgressSink(
        telegram_service=service,
        chat_id=660685834,
        message_id=749,
    )


def progress_edits(service):
    return [t for t in service.edits if t.startswith("☁️")]


class TestUploadProgressCoalescing:
    async def test_burst_callbacks_coalesce_to_latest_edit(self):
        gate = asyncio.Event()
        service = FakeTelegramService(gate=gate)
        sink = make_sink(service)
        callback = await sink.on_upload_progress()

        # 第一条回调发起编辑并阻塞在请求中（模拟进行中的 API 调用）
        callback(1, 7)
        await asyncio.sleep(0)

        # 编辑进行期间又爆发 6 次回调（旧实现会再排队 6 条编辑）
        for current in range(2, 8):
            callback(current, 7)

        gate.set()
        # 让 worker 完成进行中的编辑并消费爆发期间合并出的最新文案
        await asyncio.sleep(0)
        await sink._flush_upload_progress_tasks()

        # 进行中的一条无法撤回，其余 6 条合并成最后一条（100%）
        edits = progress_edits(service)
        assert len(edits) == 2
        assert "14.3%" in edits[0]
        assert "100.0%" in edits[-1]

    async def test_unstarted_edit_dropped_by_flush(self):
        # worker 从未获得 CPU 时，flush 直接丢弃排队文案，不产生任何编辑
        service = FakeTelegramService()
        sink = make_sink(service)
        callback = await sink.on_upload_progress()
        for current in range(1, 8):
            callback(current, 7)

        await sink._flush_upload_progress_tasks()

        assert progress_edits(service) == []

    async def test_flush_is_idempotent_without_value_error(self):
        service = FakeTelegramService()
        sink = make_sink(service)
        callback = await sink.on_upload_progress()
        callback(5, 10)
        await asyncio.sleep(0)  # 让 worker 完成唯一一条编辑

        # 旧实现清空任务列表后 done_callback remove 会抛 ValueError
        await sink._flush_upload_progress_tasks()
        await sink._flush_upload_progress_tasks()
        await sink._flush_upload_progress_tasks()

        assert len(progress_edits(service)) == 1

    async def test_success_waits_for_inflight_edit_and_drops_pending(self):
        gate = asyncio.Event()
        service = FakeTelegramService(gate=gate)
        sink = make_sink(service)
        sink._send_success = AsyncMock()
        callback = await sink.on_upload_progress()
        callback(3, 10)

        flush_task = asyncio.ensure_future(
            sink._flush_upload_progress_tasks()
        )
        await asyncio.sleep(0)  # 让 worker 进入 gate、flush 进入等待
        assert not flush_task.done()

        success_task = asyncio.ensure_future(
            sink.on_success(
                {
                    "storage_type": "nextcloud",
                    "file_url": "http://example.test/x",
                },
                "text",
                is_twitter=True,
                content_info={"success": True},
            )
        )
        await asyncio.sleep(0)
        # 进行中的进度编辑结束前，不得发送成功消息
        sink._send_success.assert_not_called()

        gate.set()
        await flush_task
        await success_task

        sink._send_success.assert_awaited_once()
        # flush 丢弃排队文案：除进行中的一条外没有额外进度编辑
        assert len(progress_edits(service)) == 1
