import sys
import logging
from PySide6.QtCore import QObject, Signal
import os
import codecs
import io

class GlobalLogger(QObject):
    """全局日志管理器 - 确保 buffer 存在的版本"""
    log_received = Signal(str, str)  # 信号: (消息, 颜色)
    
    def __init__(self):
        super().__init__()
        # 首先确保 stdout 和 stderr 有有效的 buffer
        self._ensure_valid_streams()
        
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr
        
        # 现在安全地创建包装器
        self.utf8_stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "strict")
        self.utf8_stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, "strict")
        
        # 配置日志系统
        self.logger = logging.getLogger()
        self.logger.setLevel(logging.DEBUG)
        
        # 创建自定义处理器
        self.handler = self.UTF8Handler(self)
        self.logger.addHandler(self.handler)
        
        # 重定向
        sys.stdout = self.utf8_stdout
        sys.stderr = self.utf8_stderr
    
    def _ensure_valid_streams(self):
        """确保 sys.stdout 和 sys.stderr 有有效的 buffer 属性"""
        # 为 sys.stdout 确保有效的 buffer
        if not hasattr(sys.stdout, 'buffer') or sys.stdout.buffer is None:
            print("修复 sys.stdout.buffer...")
            sys.stdout = self._create_stream_with_buffer(sys.stdout)
        
        # 为 sys.stderr 确保有效的 buffer
        if not hasattr(sys.stderr, 'buffer') or sys.stderr.buffer is None:
            print("修复 sys.stderr.buffer...")
            sys.stderr = self._create_stream_with_buffer(sys.stderr)
    
    def _create_stream_with_buffer(self, original_stream):
        """创建一个有有效 buffer 的流包装器"""
        class StreamWithBuffer:
            def __init__(self, original):
                self.original = original
                self.buffer = io.BytesIO()  # 创建内存缓冲区
            
            def write(self, text):
                # 同时写入原始流和我们的缓冲区
                if self.original and hasattr(self.original, 'write'):
                    self.original.write(text)
                # 也写入我们的缓冲区
                if isinstance(text, str):
                    self.buffer.write(text.encode('utf-8'))
                else:
                    self.buffer.write(text)
            
            def flush(self):
                if self.original and hasattr(self.original, 'flush'):
                    self.original.flush()
                self.buffer.flush()
            
            def getvalue(self):
                return self.buffer.getvalue().decode('utf-8')
        
        return StreamWithBuffer(original_stream)
    
    class UTF8Handler(logging.Handler):
        """强制使用 UTF-8 编码的日志处理器"""
        def __init__(self, logger_manager):
            super().__init__()
            self.logger_manager = logger_manager
            self.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        
        def emit(self, record):
            """发送日志记录，强制 UTF-8 编码"""
            try:
                message = self.format(record)
                if not isinstance(message, str):
                    message = str(message, "utf-8", "replace")
                
                message = message.encode("utf-8", "replace").decode("utf-8")
                
                color = {
                    logging.DEBUG: "#569CD6",
                    logging.INFO: "#A9DC76",
                    logging.WARNING: "#DCDCAA",
                    logging.ERROR: "#F48771",
                    logging.CRITICAL: "#FF0000"
                }.get(record.levelno, "#D4D4D4")
                
                if not message.endswith('\n'):
                    message += '\n'
                self.logger_manager.log_received.emit(message, color)
            except Exception as e:
                error_msg = f"日志记录错误: {str(e)}".encode("utf-8", "replace").decode("utf-8")
                self.logger_manager.log_received.emit(error_msg + "\n", "#FF0000")
    
    def write(self, text):
        """捕获标准输出和错误输出，强制使用 UTF-8"""
        try:
            if not isinstance(text, str):
                text = str(text, "utf-8", "replace")
            
            text = text.encode("utf-8", "replace").decode("utf-8")
            
            if text.strip():
                from datetime import datetime
                timestamp = datetime.now().strftime("%H:%M:%S")
                
                if sys.stdout is self.utf8_stdout:
                    color = "#A9DC76"
                else:
                    color = "#F48771"
                
                if not text.endswith('\n'):
                    text += '\n'
                self.log_received.emit(f"[{timestamp}] {text}", color)
        except Exception as e:
            error_msg = f"输出重定向错误: {str(e)}".encode("utf-8", "replace").decode("utf-8")
            self.log_received.emit(error_msg + "\n", "#FF0000")
    
    def flush(self):
        """实现 flush 方法"""
        self.utf8_stdout.flush()
        self.utf8_stderr.flush()
    
    # 添加标准 logging 方法
    def debug(self, msg, *args, **kwargs):
        """记录调试信息"""
        self.logger.debug(msg, *args, **kwargs)
    
    def info(self, msg, *args, **kwargs):
        """记录普通信息"""
        self.logger.info(msg, *args, **kwargs)
    
    def warning(self, msg, *args, **kwargs):
        """记录警告信息"""
        self.logger.warning(msg, *args, **kwargs)
    
    def error(self, msg, *args, **kwargs):
        """记录错误信息"""
        self.logger.error(msg, *args, **kwargs)
    
    def critical(self, msg, *args, **kwargs):
        """记录严重错误信息"""
        self.logger.critical(msg, *args, **kwargs)
    
    def exception(self, msg, *args, **kwargs):
        """记录异常信息"""
        self.logger.exception(msg, *args, **kwargs)
    
    class UTF8Handler(logging.Handler):
        """强制使用 UTF-8 编码的日志处理器"""
        def __init__(self, logger_manager):
            super().__init__()
            self.logger_manager = logger_manager
            self.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        
        def emit(self, record):
            """发送日志记录，强制 UTF-8 编码"""
            try:
                # 确保消息是 UTF-8 编码的字符串
                message = self.format(record)
                if not isinstance(message, str):
                    message = str(message, "utf-8", "replace")
                
                # 确保消息是有效的 UTF-8
                message = message.encode("utf-8", "replace").decode("utf-8")
                
                # 根据日志级别确定颜色
                color = {
                    logging.DEBUG: "#569CD6",    # 蓝色 - 调试信息
                    logging.INFO: "#A9DC76",     # 绿色 - 普通信息
                    logging.WARNING: "#DCDCAA",  # 黄色 - 警告
                    logging.ERROR: "#F48771",    # 红色 - 错误
                    logging.CRITICAL: "#FF0000"  # 亮红色 - 严重错误
                }.get(record.levelno, "#D4D4D4")  # 默认白色
                
                # 发送信号（确保添加换行符）
                if not message.endswith('\n'):
                    message += '\n'
                self.logger_manager.log_received.emit(message, color)
            except Exception as e:
                # 错误处理也使用 UTF-8
                error_msg = f"日志记录错误: {str(e)}".encode("utf-8", "replace").decode("utf-8")
                self.logger_manager.log_received.emit(error_msg + "\n", "#FF0000")
    
    def write(self, text):
        """捕获标准输出和错误输出，强制使用 UTF-8"""
        try:
            # 确保文本是 UTF-8 编码的字符串
            if not isinstance(text, str):
                text = str(text, "utf-8", "replace")
            
            # 确保文本是有效的 UTF-8
            text = text.encode("utf-8", "replace").decode("utf-8")
            
            if text.strip():  # 忽略空行
                # 添加时间戳
                from datetime import datetime
                timestamp = datetime.now().strftime("%H:%M:%S")
                
                # 确定输出类型
                if sys.stdout is self.utf8_stdout:
                    color = "#A9DC76"  # 绿色 - 标准输出
                else:
                    color = "#F48771"  # 红色 - 错误输出
                
                # 发送信号（确保添加换行符）
                if not text.endswith('\n'):
                    text += '\n'
                self.log_received.emit(f"[{timestamp}] {text}", color)
        except Exception as e:
            error_msg = f"输出重定向错误: {str(e)}".encode("utf-8", "replace").decode("utf-8")
            self.log_received.emit(error_msg + "\n", "#FF0000")
    
    def flush(self):
        """实现 flush 方法"""
        self.utf8_stdout.flush()
        self.utf8_stderr.flush()
    
    def __del__(self):
        """恢复原始输出"""
        sys.stdout = self.original_stdout
        sys.stderr = self.original_stderr
        self.logger.removeHandler(self.handler)

# 创建全局日志实例
global_logger = GlobalLogger()