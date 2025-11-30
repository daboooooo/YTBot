# 导入必要的模块
import os
import sys
import asyncio

# 导入模块化组件
from config import CONFIG, validate_config
from logger import logger, setup_exception_handler
from downloader import check_yt_dlp_version
from monitoring import check_network_connection
from telegram_bot import TelegramHandler, create_bot
from nextcloud_client import get_nextcloud_client
from monitoring import network_monitor, resource_monitor, setup_signal_handlers

# 主事件循环引用
main_event_loop = None

# 用户状态管理字典，用于存储用户的选择状态
# 格式: {user_id: {'state': 'waiting_for_download_type', 'url': 'youtube_url',
#        'timestamp': timestamp}}
user_states = {}

# 设置全局异常处理器
setup_exception_handler()


# 检查必需的配置是否存在
def check_required_config():
    """
    检查必需的配置是否存在并有效

    Returns:
        tuple: (缺失的配置列表, 管理员聊天ID)
    """
    # 使用模块化的配置验证
    missing_configs = validate_config()

    # 获取管理员聊天ID
    admin_chat_id = CONFIG['telegram'].get('admin_chat_id')

    return missing_configs, admin_chat_id


# 初始化全局Bot变量
bot = None


async def main_async():
    """
    异步主函数，启动Bot和所有任务
    """
    tasks = []

    # 创建并发控制信号量
    semaphore = asyncio.Semaphore(CONFIG['app']['max_concurrent_downloads'])

    # 创建处理中更新的集合，跟踪正在处理的更新
    processing_updates = set()

    try:
        # 创建Bot实例
        bot = create_bot(CONFIG['telegram']['token'])
        if not bot:
            logger.error("无法创建Bot实例，程序将退出")
            return False

        # 初始化TelegramHandler
        handler = TelegramHandler(
            bot=bot,
            user_states=user_states,
            semaphore=semaphore,
            processing_updates=processing_updates
        )

        # 初始化Bot
        if not await handler.initialize_bot():
            logger.error("初始化Bot失败，程序将退出")
            return False

        # 启动监控任务
        logger.info("启动监控任务...")
        network_task = asyncio.create_task(network_monitor())
        resource_task = asyncio.create_task(resource_monitor(user_states))
        tasks.extend([network_task, resource_task])

        logger.info("YTBot已成功启动，开始执行轮询...")

        # 直接执行轮询函数，而不是创建单独的任务
        # 这样可以确保轮询函数被执行，并且可以捕获任何异常
        await handler.start_polling()

    except asyncio.CancelledError:
        logger.info("收到取消信号，准备关闭")
    except KeyboardInterrupt:
        logger.info("收到键盘中断，准备关闭")
    except Exception as e:
        logger.critical(f"主函数发生未处理异常: {str(e)}")
        import traceback
        logger.debug(traceback.format_exc())
    finally:
        # 取消所有任务
        for task in tasks:
            if not task.done():
                task.cancel()

        # 等待任务完成取消
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception:
            pass

        logger.info("所有任务已取消，YTBot已关闭")

    return True


def main():
    """
    主函数入口
    """
    logger.info("=== YTBot 启动中 ===")

    # 检查并加载配置
    missing_configs, admin_chat_id = check_required_config()
    if missing_configs:
        logger.error(f"缺少必要的配置: {', '.join(missing_configs)}")
        return 1

    # 打印配置摘要
    logger.info("配置已加载:")
    logger.info("- Telegram Bot: @未知")
    logger.info(f"- Nextcloud: {CONFIG['nextcloud']['url']}")
    logger.info(f"- 最大并发下载数: {CONFIG['app']['max_concurrent_downloads']}")
    logger.info(f"- 管理员通知: {'已启用' if admin_chat_id else '未启用'}")

    # 检查网络连接
    if not asyncio.run(check_network_connection()):
        logger.warning("网络连接检查失败，可能会影响功能")
    else:
        logger.info("网络连接正常")

    # 检查yt-dlp版本
    try:
        check_yt_dlp_version()
    except Exception as e:
        logger.warning(f"检查yt-dlp版本时出错: {str(e)}")

    # 测试Nextcloud连接
    try:
        logger.debug("尝试获取Nextcloud客户端...")
        nextcloud_client = get_nextcloud_client()

        if nextcloud_client is None:
            logger.error("无法获取Nextcloud客户端，返回值为None")
            return 1

        logger.debug("Nextcloud客户端获取成功，检查upload_dir: %s", CONFIG['nextcloud']['upload_dir'])

        logger.debug("Nextcloud客户端获取成功，准备测试连接")

        # 先创建目录（如果不存在）
        try:
            upload_dir = CONFIG['nextcloud']['upload_dir']
            if not upload_dir.startswith('/'):
                upload_dir = '/' + upload_dir

            # 使用webdav3库支持的方法检查目录是否存在
            # 尝试列出目录内容，如果成功则目录存在，失败则不存在
            dir_exists = False
            try:
                # 注意：webdav3的list方法不需要前导斜杠
                path_without_slash = upload_dir.lstrip('/')
                files = nextcloud_client.list(path_without_slash)
                dir_exists = True
                logger.debug(f"目录已存在，包含文件: {len(files)}个")
            except Exception as list_error:
                # 如果列出失败，目录可能不存在
                logger.debug(f"目录可能不存在: {str(list_error)}")

            if not dir_exists:
                logger.info(f"尝试创建Nextcloud上传目录: {upload_dir}")
                # 注意：webdav3的mkdir方法不需要前导斜杠
                nextcloud_client.mkdir(upload_dir.lstrip('/'))
                logger.info("目录创建成功")

            # 验证连接成功
            logger.info("Nextcloud连接测试成功")
        except Exception as dir_error:
            logger.error(f"检查或创建Nextcloud目录时出错: {str(dir_error)}")
            return 1
    except Exception as e:
        logger.error(f"Nextcloud连接测试失败: {str(e)}")
        # 添加更详细的错误信息
        import traceback
        logger.debug(f"详细错误信息: {traceback.format_exc()}")
        return 1

    # 设置信号处理
    setup_signal_handlers()

    # 构建启动通知消息
    start_message = (
        f"- 系统: {os.name}\n"
        f"- Python版本: {sys.version.split()[0]}\n"
        f"- 并发下载限制: {CONFIG['app']['max_concurrent_downloads']}"
    )

    # 如果配置了管理员，发送启动通知
    if admin_chat_id:
        logger.info(f"向管理员 {admin_chat_id} 发送启动通知")
        # 启动异步任务发送通知
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                bot = create_bot(CONFIG['telegram']['token'])
                if bot:
                    loop.run_until_complete(
                        bot.send_message(
                            chat_id=admin_chat_id,
                            text=(f"🚀 YTBot已成功启动！\n\n"
                                  f"📊 系统状态:\n{start_message}\n\n"
                                  f"💡 提示: 发送YouTube链接开始下载音乐或视频")
                        )
                    )
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"发送启动通知失败: {str(e)}")

    # 启动异步主函数
    try:
        asyncio.run(main_async())
        return 0
    except KeyboardInterrupt:
        logger.info("收到键盘中断，程序退出")
        return 0
    except SystemExit:
        logger.info("程序正常退出")
        return 0
    except Exception as e:
        logger.critical(f"程序发生致命错误: {str(e)}")
        import traceback
        logger.debug(traceback.format_exc())
        return 1


if __name__ == '__main__':
    sys.exit(main())
