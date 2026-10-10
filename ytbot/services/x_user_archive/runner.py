"""Two-phase orchestration for X user archive tasks (spec 5, 12-15).

Phase 1 enumerates the timeline into a manifest (or resumes a partial
enumeration); phase 2 strictly-sequentially archives every manifest
record via the existing TwitterHandler, then rebuilds the merged book
PDF. The runner never touches Telegram, the process lock,
StorageService or the existing storage rules.

CLI integration is intentionally thin: ``add_archive_arguments`` and
``validate_archive_args`` are shared with cli.py and are unit-tested
without invoking the real parser entry point.
"""

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List

from ...core.enhanced_logger import get_logger
from .archiver import (
    ArchiveOptions,
    ArchivePause,
    PostArchiver,
    is_valid_content_dir,
)
from .manifest import (
    ManifestStore,
    TaskPaths,
    TaskPathsError,
    TaskState,
    TaskStateStore,
    dedupe_merge,
)
from .models import (
    ContentKind,
    DownloadStatus,
    EnumStatus,
    EnumStopReason,
    classify_target,
    parse_iso_utc,
    resolve_time_window,
)
from .pdf_merger import generate_merged_pdf
from .timeline import (
    EnumerateOptions,
    TimelineEnumerationError,
    TimelineEnumerator,
)

logger = get_logger(__name__)

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_PARTIAL = 2


# ---------------------------------------------------------------------------
# argparse additions (kept additive; cli.py just calls these)
# ---------------------------------------------------------------------------

def add_archive_arguments(parser: argparse.ArgumentParser) -> None:
    g = parser
    g.add_argument(
        "--archive-x-user",
        type=str, default=None, metavar="TARGET",
        help=(
            "Archive a user's full history: @handle, handle, profile URL "
            "or one of their status URLs (no Telegram, no process lock)"
        ),
    )
    g.add_argument(
        "--history-years", type=int, default=5,
        help="Lookback window in calendar years (default: 5)",
    )
    g.add_argument(
        "--start-at", type=str, default=None,
        help="Window start (ISO-8601); overrides --history-years",
    )
    g.add_argument(
        "--end-at", type=str, default=None,
        help="Window end (ISO-8601, half-open); default: task start",
    )
    g.add_argument(
        "--archive-dir", type=str, default="./x_archive",
        help="Archive root directory (default: ./x_archive)",
    )
    g.add_argument(
        "--dry-run", action="store_true",
        help="Enumerate and build the manifest only; do not download",
    )
    g.add_argument(
        "--resume", type=str, default=None, metavar="TASK",
        help="Resume a task directory or task id (reuses its window)",
    )
    g.add_argument(
        "--include-replies", action="store_true",
        help="Also scroll /with_replies",
    )
    g.add_argument(
        "--no-reposts", action="store_true",
        help="Exclude reposts (kept by default)",
    )
    g.add_argument(
        "--max-posts", type=int, default=1000,
        help="Safety cap of unique posts (default: 1000; 0 = unlimited)",
    )
    g.add_argument(
        "--scroll-stall", type=int, default=8,
        help="Rounds without new entries before stopping (default: 8)",
    )
    g.add_argument(
        "--request-delay", type=float, default=2.0,
        help="Polite delay between per-post downloads in seconds "
             "(default: 2.0)",
    )
    g.add_argument(
        "--no-merged-pdf", action="store_true",
        help="Do not generate the merged book PDF",
    )
    g.add_argument(
        "--merged-pdf", type=str, default=None, metavar="PATH",
        help="Custom output path for the merged book PDF",
    )


def validate_archive_args(parser: argparse.ArgumentParser, args) -> None:
    """spec 5.2 cross-field validation. Calls parser.error on problems."""
    if not getattr(args, "archive_x_user", None) and \
            not getattr(args, "resume", None):
        return

    if getattr(args, "archive_x_user", None) and getattr(args, "download", None):
        parser.error("--archive-x-user cannot be combined with --download")

    if getattr(args, "resume", None):
        if getattr(args, "start_at", None) or getattr(args, "end_at", None) \
                or args.history_years != 5:
            parser.error(
                "--resume keeps the original fixed time window; do not "
                "pass --start-at/--end-at/--history-years with it"
            )
    else:
        try:
            resolve_time_window(
                datetime.now(timezone.utc),
                history_years=args.history_years,
                start_at=args.start_at,
                end_at=args.end_at,
            )
        except ValueError as exc:
            parser.error(str(exc))

    if args.max_posts < 0:
        parser.error("--max-posts must be a non-negative integer (0 = unlimited)")
    if args.no_merged_pdf and args.merged_pdf:
        parser.error("--no-merged-pdf cannot be combined with --merged-pdf")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class XUserArchiveRunner:
    """Orchestrates one archive task.

    Network/browser collaborators are factory-injected so tests run with
    fakes; production defaults resolve the real Twitter handler through
    DownloadService.platform_manager.
    """

    def __init__(
        self,
        *,
        handler_factory=None,
        enumerator_factory=None,
        archiver_factory=None,
        merge_func=None,
        scrape_status=None,
        sleep_func=None,
        clock=None,
    ):
        self._handler_factory = handler_factory or _default_handler_factory
        self._enumerator_factory = enumerator_factory or _default_enumerator
        self._archiver_factory = archiver_factory or _default_archiver
        self._merge_func = merge_func or generate_merged_pdf
        self._scrape_status = scrape_status or _default_scrape_status
        self._sleep = sleep_func or (lambda d: asyncio.sleep(d))
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # -- public entry point ----------------------------------------------

    def run(self, args) -> int:
        return asyncio.run(self.run_async(args))

    async def run_async(self, args) -> int:
        started = time.monotonic()
        started_at = self._clock()
        try:
            if getattr(args, "resume", None):
                paths, state, records = self._load_resume(args)
                handle = state.author_id
                window_start = parse_iso_utc(state.start_at)
                window_end = parse_iso_utc(state.end_at)
            else:
                handle = await self._resolve_handle(args)
                window_start, window_end = resolve_time_window(
                    self._clock(),
                    history_years=args.history_years,
                    start_at=args.start_at,
                    end_at=args.end_at,
                )
                paths = TaskPaths.create(
                    args.archive_dir, handle, now=self._clock()
                )
                state = TaskState(
                    task_id=paths.task_id,
                    author_id=handle,
                    start_at=window_start.isoformat(),
                    end_at=window_end.isoformat(),
                    options=self._options_snapshot(args),
                )
                records = []
                TaskStateStore(paths).save(state)
                self._echo_safety_banner(
                    handle, window_start, window_end, args
                )
        except TaskPathsError as exc:
            print(f"❌ 无法恢复任务：{exc}")
            return EXIT_FAILURE

        manifest = ManifestStore(paths)
        state_store = TaskStateStore(paths)

        # -- phase 1: enumeration -----------------------------------------
        # Fresh tasks are "unknown"; resume re-scrolls only when the
        # previous enumeration was not complete (spec 13.2.6).
        need_enum = state.enum_status != EnumStatus.COMPLETE

        interrupted = False
        if need_enum:
            handler = self._handler_factory(handle, args)
            try:
                outcome = await self._enumerate(
                    handler, handle, window_start, window_end, args,
                    state, manifest, records,
                )
            except TimelineEnumerationError as exc:
                state.enum_status = EnumStatus.FAILED
                state.enum_stop_reason = exc.reason
                state.last_error = exc.message
                state_store.save(state)
                print(f"❌ 枚举失败（{exc.reason}）：{exc.message}")
                failed_rows, _ = manifest.load()
                return await self._finalize(
                    paths, state, manifest,
                    failed_rows, args, started, started_at,
                    enum_failed=True,
                )
            except (KeyboardInterrupt, asyncio.CancelledError):
                state.enum_status = EnumStatus.PARTIAL
                state.enum_stop_reason = EnumStopReason.INTERRUPTED
                state.download_status = "paused"
                state_store.save(state)
                records, _ = manifest.load()
                canonical, _ = dedupe_merge(records)
                manifest.rewrite(canonical)
                print("\n⚠️ 枚举被中断，已落盘进度；可用 --resume 继续")
                return await self._finalize(
                    paths, state, manifest, canonical, args,
                    started, started_at, interrupted=True,
                )

            # canonicalize everything observed
            all_rows, warnings = manifest.load()
            canonical, diag = dedupe_merge(all_rows)
            manifest.rewrite(canonical)
            records = canonical
            state.enum_status = outcome.enum_status
            state.enum_stop_reason = outcome.stop_reason
            state.unique = len(canonical)
            state.options["articles_channel"] = outcome.articles_channel
            state.options["enum_warnings"] = warnings + diag
            self._print_enum_summary(outcome, canonical, args, paths, state)
            state_store.save(state)

        if args.dry_run:
            print("ℹ️ dry-run：未下载任何正文，也未生成合订大 PDF。")
            return await self._finalize(
                paths, state, manifest, records, args,
                started, started_at, dry_run=True,
            )

        # -- phase 2: per-post downloads ----------------------------------
        handler = self._handler_factory(handle, args)
        archiver = self._archiver_factory(
            handler, paths.content_root, args, self._sleep
        )

        paused_reason = None
        for index, rec in enumerate(records, 1):
            if rec.status == DownloadStatus.PERMANENT_ERROR:
                # spec 13.2.5: skipped by default in v1
                continue
            if rec.status == DownloadStatus.SKIPPED and \
                    rec.archive_path and \
                    is_valid_content_dir(Path(rec.archive_path)):
                continue
            state.download_status = "running"
            print(
                f"[{index}/{len(records)}] {rec.published_at:%Y-%m-%d} "
                f"{rec.post_url}"
            )
            try:
                status = await archiver.archive(rec)
            except ArchivePause as exc:
                paused_reason = exc
                state.download_status = "paused"
                state.last_error = f"{exc.code}: {exc.message}"
                self._refresh_counters(state, records)
                manifest.rewrite(records)
                state_store.save(state)
                break
            except (KeyboardInterrupt, asyncio.CancelledError):
                interrupted = True
                state.download_status = "paused"
                self._refresh_counters(state, records)
                manifest.rewrite(records)
                state_store.save(state)
                break
            self._refresh_counters(state, records)
            manifest.rewrite(records)
            state_store.save(state)
            print(f"   -> {status}")
            if status in (
                DownloadStatus.DOWNLOADED, DownloadStatus.SKIPPED
            ) and index < len(records):
                await archiver.polite_delay()
        else:
            state.download_status = "completed"

        if paused_reason is not None:
            print(
                f"⏸️ 系统性错误，任务暂停：{paused_reason.message}"
                "\n   更新 cookies/稍后用 --resume 继续。"
            )
        if interrupted:
            print("\n⚠️ 下载被中断，进度已保存；可用 --resume 继续。")

        # -- merged book ---------------------------------------------------
        if not args.no_merged_pdf and state.download_status != "paused" \
                and not interrupted:
            await self._build_merged_book(
                handle, paths, state, records, args, window_start,
                window_end,
            )
            state_store.save(state)
        elif args.no_merged_pdf:
            state.merged_pdf["status"] = "disabled"
            state_store.save(state)

        return await self._finalize(
            paths, state, manifest, records, args,
            started, started_at,
            interrupted=interrupted,
            paused=paused_reason is not None,
        )

    # -- phase 1 -----------------------------------------------------------

    async def _resolve_handle(self, args) -> str:
        kind, handle = classify_target(args.archive_x_user)
        if kind != "status_url":
            return handle
        # Verify the status URL is reachable and confirm the author.
        result = await self._scrape_status(args.archive_x_user)
        if not result.get("success"):
            raise TaskPathsError(
                f"无法解析帖子作者：{result.get('error', 'unknown error')}"
            )
        author = str(result.get("author") or "").lstrip("@").lower()
        if author and author != handle:
            logger.warning(
                "status URL author %s differs from resolved %s",
                author, handle,
            )
        return handle

    async def _enumerate(
        self, handler, handle, window_start, window_end, args,
        state: TaskState, manifest: ManifestStore,
        prior_records: List[Any],
    ):
        options = EnumerateOptions(
            target_handle=handle,
            start_at=window_start,
            end_at=window_end,
            include_replies=args.include_replies,
            include_reposts=not args.no_reposts,
            max_posts=args.max_posts,
            scroll_stall=args.scroll_stall,
            request_delay=args.request_delay,
        )
        preload = {r.tweet_id for r in prior_records}
        enumerator = self._enumerator_factory(
            handler.extractor, options, self._sleep, preload
        )
        total_new = 0
        async for batch in enumerator.enumerate():
            manifest.append_batch(batch)
            total_new += len(batch)
            logger.info(
                "enumeration batch: +%s (total accepted %s)",
                len(batch), len(enumerator._accepted_ids),
            )
        outcome = enumerator.outcome
        # outcome counters describe THIS scroll; accumulate for resumes
        state.raw_discovered += outcome.raw_discovered
        state.out_of_scope += outcome.out_of_scope
        state.out_of_range += outcome.out_of_range
        state.invalid += outcome.invalid
        return outcome

    # -- phase 2 -----------------------------------------------------------

    async def _build_merged_book(
        self, handle, paths, state: TaskState, records, args,
        window_start, window_end,
    ):
        if args.merged_pdf:
            output = Path(args.merged_pdf).expanduser()
        else:
            output = paths.default_merged_pdf_path(
                parse_iso_utc(state.start_at),
                parse_iso_utc(state.end_at),
            )
        state.merged_pdf["status"] = "running"
        result = await self._merge_func(
            handle=handle,
            records=records,
            output_path=output,
            window_start=window_start,
            window_end=window_end,
            enum_status=state.enum_status,
            archive_root=str(paths.handle_dir),
        )
        pdf_state = result.to_dict()
        pdf_state["skipped_no_pdf"] = len(pdf_state.pop("missing_pdf", []))
        pdf_state["error"] = pdf_state.get("message")
        pdf_state["size_bytes"] = (
            Path(output).stat().st_size
            if result.status == "success" and output.exists() else 0
        )
        pdf_state["updated_at"] = self._clock().isoformat()
        state.merged_pdf = pdf_state

    # -- finalize / report -------------------------------------------------

    def _refresh_counters(self, state: TaskState, records) -> None:
        state.downloaded = sum(
            1 for r in records if r.status == DownloadStatus.DOWNLOADED
        )
        state.skipped = sum(
            1 for r in records if r.status == DownloadStatus.SKIPPED
        )
        state.retryable_failed = sum(
            1 for r in records
            if r.status == DownloadStatus.RETRYABLE_ERROR
        )
        state.permanent_failed = sum(
            1 for r in records
            if r.status == DownloadStatus.PERMANENT_ERROR
        )
        state.retries = sum(max(0, r.attempts - 1) for r in records)
        state.unique = len(records)

    async def _finalize(
        self, paths, state: TaskState, manifest: ManifestStore, records,
        args, started, started_at, *,
        enum_failed=False, interrupted=False, paused=False,
        dry_run=False,
    ) -> int:
        self._refresh_counters(state, records)
        state_store = TaskStateStore(paths)
        state_store.save(state)

        finished_at = self._clock()
        report = self._build_report(
            paths, state, records, started_at, finished_at,
            time.monotonic() - started, dry_run=dry_run,
        )
        report_path = paths.report_path()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        self._print_terminal_summary(state, report, report_path, dry_run)

        if dry_run:
            return EXIT_SUCCESS
        pdf_status = state.merged_pdf.get("status")
        download_failures = (
            state.retryable_failed + state.permanent_failed
        )
        if enum_failed or download_failures > 0 or paused or interrupted:
            return EXIT_FAILURE
        if pdf_status in ("failed", "skipped"):
            return EXIT_FAILURE
        if state.enum_status == EnumStatus.PARTIAL:
            return EXIT_PARTIAL
        return EXIT_SUCCESS

    def _build_report(
        self, paths, state: TaskState, records, started_at, finished_at,
        elapsed, *, dry_run,
    ) -> dict:
        posts = [r for r in records if r.content_type != ContentKind.ARTICLE]
        articles = [r for r in records if r.content_type == ContentKind.ARTICLE]
        errors = {}
        for r in records:
            if r.error_code:
                errors.setdefault(r.error_code, []).append(
                    {"tweet_id": r.tweet_id, "message": r.error_message}
                )
        limitations = []
        if state.options.get("articles_channel") != "present":
            limitations.append(
                "未发现长文(Articles)独立通道：长文覆盖不保证，"
                "枚举状态不标记 complete"
            )
        if state.enum_status == EnumStatus.PARTIAL:
            limitations.append(
                "时间线枚举为 partial：可能受滚动深度/限流影响，"
                "更早历史未完全覆盖，可 --resume 补枚举"
            )
        return {
            "task_id": state.task_id,
            "author_id": state.author_id,
            "display_name": state.display_name,
            "start_at": state.start_at,
            "end_at": state.end_at,
            "enum_status": state.enum_status,
            "enum_stop_reason": state.enum_stop_reason,
            "discovered": state.raw_discovered,
            "unique": len(records),
            "post_count": len(posts),
            "article_count": len(articles),
            "reply_count": sum(1 for r in records if r.is_reply),
            "repost_count": sum(1 for r in records if r.is_repost),
            "out_of_range": state.out_of_range,
            "invalid": state.invalid,
            "out_of_scope": state.out_of_scope,
            "downloaded": state.downloaded,
            "skipped": state.skipped,
            "retryable_failed": state.retryable_failed,
            "permanent_failed": state.permanent_failed,
            "retries": state.retries,
            "merged_pdf": dict(state.merged_pdf),
            "archive_dir": str(paths.task_dir),
            "dry_run": dry_run,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "elapsed_seconds": round(elapsed, 3),
            "errors": {
                "counts": {k: len(v) for k, v in errors.items()},
                "details": errors,
            },
            "limitations": limitations,
        }

    # -- presentation ------------------------------------------------------

    def _options_snapshot(self, args) -> dict:
        return {
            "include_replies": args.include_replies,
            "include_reposts": not args.no_reposts,
            "max_posts": args.max_posts,
            "scroll_stall": args.scroll_stall,
            "request_delay": args.request_delay,
            "no_merged_pdf": args.no_merged_pdf,
        }

    def _echo_safety_banner(self, handle, start, end, args) -> None:
        print("=" * 64)
        print(f"X 用户归档任务：@{handle}")
        print(f"时间窗口（半开）：{start:%Y-%m-%d %H:%M} ~ "
              f"{end:%Y-%m-%d %H:%M} UTC")
        cap = "不限" if args.max_posts == 0 else str(args.max_posts)
        print(f"数量上限：{cap}    转帖：{'排除' if args.no_reposts else '保留'}"
              f"    回复：{'包含' if args.include_replies else '仅主帖'}")
        if not args.start_at and not args.end_at and args.max_posts == 1000:
            print("提示：当前为默认五年窗口 + 1000 条上限，"
                  "建议先用 --dry-run 预览规模。")
        print("=" * 64)

    def _print_enum_summary(self, outcome, canonical, args, paths, state):
        if canonical:
            earliest = min(r.published_at for r in canonical)
            latest = max(r.published_at for r in canonical)
            months = sorted({
                r.published_at.strftime("%Y/%m") for r in canonical
            })
        else:
            earliest = latest = None
            months = []
        print("-" * 64)
        print(f"枚举结束：{state.enum_status}（{outcome.stop_reason}）")
        print(f"原始观察 {outcome.raw_discovered}，去重后 "
              f"{len(canonical)}；窗口外 {outcome.out_of_range}，"
              f"越界作者 {outcome.out_of_scope}，无效 {outcome.invalid}")
        if canonical:
            print(f"发布跨度：{earliest:%Y-%m-%d} ~ {latest:%Y-%m-%d}")
            print(f"预计落位年月（{len(months)} 个）："
                  f"{', '.join(months[:12])}"
                  f"{' ...' if len(months) > 12 else ''}")
            print("最早 3 条：")
            for r in canonical[:3]:
                print(f"  {r.published_at:%Y-%m-%d} {r.post_url}")
            print("最新 3 条：")
            for r in canonical[-3:]:
                print(f"  {r.published_at:%Y-%m-%d} {r.post_url}")
        print(f"清单：{paths.manifest_path}")
        if outcome.stop_reason == EnumStopReason.MAX_POSTS:
            print("⚠️ 已命中 --max-posts 上限：枚举 partial；"
                  "可缩小时间窗口或显式调高 --max-posts 后 --resume。")

    def _print_terminal_summary(self, state, report, report_path, dry_run):
        mp = state.merged_pdf
        print("=" * 64)
        print("归档任务汇总")
        print(f"  枚举：{state.enum_status}（{state.enum_stop_reason}）")
        print(f"  发现/去重：{state.raw_discovered}/{state.unique}")
        if not dry_run:
            print(f"  已下载：{state.downloaded}，跳过：{state.skipped}，"
                  f"可重试失败：{state.retryable_failed}，"
                  f"永久失败：{state.permanent_failed}，"
                  f"重试次数：{state.retries}")
            if mp.get("status") not in ("not_generated", None):
                print(f"  合订 PDF：{mp.get('status')} "
                      f"收录 {mp.get('included_posts', 0)}/"
                      f"{mp.get('downloadable_posts', 0)} 篇，"
                      f"{mp.get('pages', 0)} 页 -> {mp.get('path')}")
                if mp.get("status") == "skipped":
                    print(f"  ⚠️ {mp.get('message')}")
        print(f"  报告：{report_path}")
        if report["limitations"]:
            for item in report["limitations"]:
                print(f"  限制：{item}")
        print("=" * 64)

    # -- resume ------------------------------------------------------------

    def _load_resume(self, args):
        paths = TaskPaths.resume(args.resume, args.archive_dir)
        state = TaskStateStore(paths).load()
        records, warnings = ManifestStore(paths).load()
        canonical, diag = dedupe_merge(records)
        if warnings or diag:
            logger.warning("resume manifest notes: %s %s", warnings, diag)
        # spec 13.2.3/4: validate on-disk state
        for rec in canonical:
            if rec.status == DownloadStatus.DOWNLOADED:
                if not rec.archive_path or not is_valid_content_dir(
                    Path(rec.archive_path)
                ):
                    rec.status = DownloadStatus.PENDING
                    rec.archive_path = None
        return paths, state, canonical


# ---------------------------------------------------------------------------
# Default production collaborators
# ---------------------------------------------------------------------------

_download_service_singleton = None


def _default_download_service():
    global _download_service_singleton
    if _download_service_singleton is None:
        from ..download_service import DownloadService
        _download_service_singleton = DownloadService()
    return _download_service_singleton


async def _default_scrape_status(url: str):
    service = _default_download_service()
    probe_handler = service.platform_manager.get_handler(url)
    if probe_handler is None:
        return {"success": False, "error": f"unsupported link: {url}"}
    return await probe_handler.extractor.scrape_tweet(url)


def _default_handler_factory(handle, args):
    service = _default_download_service()
    handler = service.platform_manager.get_handler(
        f"https://x.com/{handle}"
    )
    if handler is None:
        raise TaskPathsError(f"未找到 X/Twitter 处理器：@{handle}")
    return handler


def _default_enumerator(extractor, options, sleep_func, preload_ids):
    return TimelineEnumerator(
        extractor, options, sleep_func=sleep_func,
        preload_ids=preload_ids,
    )


def _default_archiver(handler, content_root, args, sleep_func):
    return PostArchiver(
        handler, content_root,
        ArchiveOptions(request_delay=args.request_delay),
        sleep_func=sleep_func,
    )
