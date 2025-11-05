import logging
import logging.handlers
import os
import sys
from config import CONFIG


def setup_logger(name='ytbot'):
    """
    设置日志系统

    Args:
        name: 日志器名称

    Returns:
        logging.Logger: 配置好的日志器
    """
    # 创建日志器
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, CONFIG['log']['level']))

    # 防止添加重复的处理器
    if logger.handlers:
        return logger

    # 创建格式化器
    formatter = logging.Formatter(CONFIG['log']['format'])

    # 创建控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, CONFIG['log']['level']))
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 创建文件处理器（带轮转）
    try:
        # 确保日志目录存在
        log_dir = os.path.dirname(CONFIG['log']['file'])
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)

        # 添加文件处理器，支持日志轮转
        file_handler = logging.handlers.RotatingFileHandler(
            filename=CONFIG['log']['file'],
            maxBytes=CONFIG['log']['max_bytes'],
            backupCount=CONFIG['log']['backup_count'],
            encoding='utf-8'
        )
        file_handler.setLevel(getattr(logging, CONFIG['log']['level']))
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        # 如果无法创建文件处理器，记录错误到控制台
        console_handler.setFormatter(
            logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        logger.warning(f"无法设置文件日志: {str(e)}")

    return logger


# 获取指定名称的日志器
def get_logger(name=None):
    """获取指定名称的日志器"""
    return logging.getLogger(name) if name else logger


# 设置全局异常处理器
def setup_exception_handler():
    """设置全局异常处理器"""
    def handle_exception(exc_type, exc_value, exc_traceback):
        """处理未捕获的异常"""
        if issubclass(exc_type, KeyboardInterrupt):
            # 不处理键盘中断
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        logger = get_logger()
        logger.error("未捕获的异常", exc_info=(exc_type, exc_value, exc_traceback))

    sys.excepthook = handle_exception


# 初始化默认logger
logger = setup_logger()
# 设置全局异常处理器
setup_exception_handler()
