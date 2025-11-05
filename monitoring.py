import asyncio
import time
import os
import socket
import subprocess
from config import CONFIG
from logger import get_logger

logger = get_logger(__name__)

async def network_monitor():
    """
    周期性监控网络连接
    """
    last_connection_status = None
    
    logger.info("网络监控任务已启动")
    
    while True:
        try:
            # 检查网络连接
            connection_status = await check_network_connection()
            
            # 如果连接状态发生变化，记录日志
            if connection_status != last_connection_status:
                if connection_status:
                    logger.info("网络连接已恢复")
                    # 如果之前断开过，尝试刷新DNS缓存
                    if last_connection_status is False:
                        try:
                            logger.info("尝试刷新DNS缓存...")
                            if os.name == 'posix':  # Unix/Linux/Mac
                                if sys.platform.startswith('darwin'):  # macOS
                                    subprocess.run(['dscacheutil', '-flushcache'], capture_output=True)
                                    subprocess.run(['killall', '-HUP', 'mDNSResponder'], capture_output=True)
                                else:  # Linux
                                    subprocess.run(['systemctl', 'restart', 'systemd-resolved'], capture_output=True)
                            elif sys.platform.startswith('win'):
                                os.system('ipconfig /flushdns')
                            logger.info("已尝试刷新DNS缓存")
                        except Exception as e:
                            logger.error(f"执行DNS缓存刷新失败: {str(e)}")
                else:
                    logger.warning("网络连接已断开")
                
                last_connection_status = connection_status
        
        except Exception as e:
            logger.error(f"网络监控过程中出错: {str(e)}")
            import traceback
            logger.debug(traceback.format_exc())
        
        # 每30秒检查一次网络连接
        await asyncio.sleep(30)

async def resource_monitor(user_states=None, nextcloud_cache=None):
    """
    定期监控系统资源使用情况，清理过期的用户状态
    防止内存泄漏和资源耗尽
    
    Args:
        user_states: 用户状态字典（如果提供）
        nextcloud_cache: Nextcloud客户端缓存（如果提供）
    """
    # 导入psutil（仅在需要时）
    try:
        import psutil
    except ImportError:
        logger.warning("psutil库未安装，无法监控系统资源")
        return
    
    # 使用提供的状态字典或默认全局变量
    if user_states is None:
        # 尝试获取全局user_states
        try:
            from main import user_states as global_user_states
            user_states = global_user_states
        except (ImportError, NameError):
            logger.warning("未找到用户状态字典，跳过状态清理")
            user_states = {}
    
    if nextcloud_cache is None:
        # 尝试从nextcloud_client模块获取缓存
        try:
            from nextcloud_client import _nextcloud_client_cache as nextcloud_cache
        except ImportError:
            nextcloud_cache = None
    
    logger.info("资源监控任务已启动")
    
    while True:
        try:
            # 获取当前进程内存使用情况
            process = psutil.Process(os.getpid())
            memory_info = process.memory_info()
            memory_used_mb = memory_info.rss / 1024 / 1024  # 转换为MB
            
            # 记录内存使用情况
            logger.debug("当前内存使用: %.2f MB", memory_used_mb)
            
            # 检查用户状态是否过期
            current_time = time.time()
            expired_users = [user_id for user_id, state_info in user_states.items()
                            if (current_time - state_info.get('timestamp', current_time)) >
                            CONFIG['monitor']['user_state_timeout']]
            
            # 清理过期用户状态
            for user_id in expired_users:
                logger.debug("清理过期用户状态: %s", user_id)
                del user_states[user_id]
            
            # 如果清理后仍有较多过期状态，记录警告
            if len(expired_users) > 10:
                logger.warning("清理了 %d 个过期用户状态", len(expired_users))
            
            # 检查内存使用是否超过阈值
            if memory_used_mb > CONFIG['monitor']['memory_threshold']:
                logger.warning("内存使用警告: %.2f MB 超过阈值 %d MB",
                            memory_used_mb, CONFIG['monitor']['memory_threshold'])
                
                # 执行更激进的清理
                # 1. 清理所有用户状态
                if user_states:
                    logger.info("内存压力大，清理所有 %d 个用户状态", len(user_states))
                    user_states.clear()
                
                # 2. 尝试清理Nextcloud客户端缓存
                if nextcloud_cache and nextcloud_cache.get('client'):
                    logger.info("内存压力大，清理Nextcloud客户端缓存")
                    nextcloud_cache['client'] = None
                
                # 3. 发送警告给管理员
                if CONFIG['telegram']['admin_chat_id']:
                    try:
                        # 尝试导入Bot发送警告
                        from telegram_bot import create_bot
                        bot = create_bot()
                        if bot:
                            await bot.send_message(
                                chat_id=CONFIG['telegram']['admin_chat_id'],
                                text=f"⚠️ YTBot内存警告：\n当前内存使用: {memory_used_mb:.2f} MB\n已执行自动清理以释放内存"
                            )
                    except Exception as e:
                        logger.error(f"发送内存警告失败: {str(e)}")
        
        except psutil.Error as e:
            logger.error(f"获取系统资源信息失败: {str(e)}")
        except Exception as e:
            logger.error(f"资源监控过程中出错: {str(e)}")
            import traceback
            logger.debug(traceback.format_exc())
        
        # 每5分钟执行一次监控
        await asyncio.sleep(300)

async def check_network_connection(timeout=5):
    """
    异步检查网络连接
    
    Args:
        timeout: 超时时间（秒）
        
    Returns:
        bool: 是否连接成功
    """
    try:
        # 使用多个公共DNS服务器测试连接
        test_hosts = ['8.8.8.8', '1.1.1.1', '208.67.222.222']
        
        for host in test_hosts:
            try:
                # 创建socket连接（使用asyncio的执行器在后台线程中运行）
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: _create_socket_connection(host, 53, timeout)
                )
                logger.debug(f"网络连接测试成功: {host}")
                return True
            except (socket.timeout, socket.error):
                continue
        
        logger.warning("所有测试主机连接失败")
        return False
    except Exception as e:
        logger.error(f"网络连接测试失败: {str(e)}")
        return False

def _create_socket_connection(host, port, timeout):
    """
    创建socket连接的同步函数
    """
    with socket.create_connection((host, port), timeout=timeout):
        pass

def setup_signal_handlers():
    """
    设置信号处理，确保程序可以优雅地关闭
    清理资源并保存状态
    """
    import signal
    import sys
    
    # 全局标志，用于防止重复执行关闭流程
    _is_shutting_down = False
    
    def signal_handler(sig, frame):
        nonlocal _is_shutting_down
        
        # 防止重复执行关闭流程
        if _is_shutting_down:
            logger.warning("关闭流程已在进行中，忽略重复的信号 %s", sig)
            return
        
        _is_shutting_down = True
        logger.info("收到信号 %s，准备优雅关闭", sig)
        
        # 记录关闭前的状态
        try:
            global processing_updates
            logger.info(
                "关闭前 - 处理中更新数: %d",
                len(processing_updates)
            )
        except (NameError, AttributeError):
            pass
        
        try:
            global user_states
            logger.info(
                "关闭前 - 活跃用户状态数: %d",
                len(user_states)
            )
        except (NameError, AttributeError):
            pass
        
        # 发送关闭通知给管理员（如果有）
        if CONFIG['telegram']['admin_chat_id']:
            try:
                from telegram_bot import create_bot
                bot = create_bot()
                if bot:
                    # 使用同步方式发送消息
                    try:
                        bot.send_message(
                            chat_id=CONFIG['telegram']['admin_chat_id'],
                            text="🛑 YTBot正在关闭，可能是由于系统重启或更新。\n将在完成当前任务后停止。"
                        )
                        logger.info("关闭通知已发送")
                    except Exception as msg_e:
                        logger.warning("无法发送关闭通知: %s", str(msg_e))
            except Exception as e:
                logger.error("处理关闭通知时出错: %s", str(e))
        
        logger.info("YTBot已开始关闭流程")
        
        # 设置全局变量，通知主循环退出
        try:
            global should_continue
            should_continue = False
        except NameError:
            pass
        
        # 给当前任务一些时间完成
        time.sleep(1)
        
        # 强制退出
        logger.info("强制退出程序")
        sys.exit(0)
    
    # 设置信号处理
    signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler)  # 终止信号

# 导入sys模块，用于系统相关操作
import sys