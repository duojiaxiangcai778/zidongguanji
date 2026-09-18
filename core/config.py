# core/config.py
# 配置读写模块 - 管理 settings.ini 配置文件

import configparser
import os
import sys
import tempfile


class Config:
    """配置读写类，管理 settings.ini 的所有设置项"""

    def __init__(self, config_path=None):
        """
        初始化配置对象
        :param config_path: 配置文件路径，None 则使用默认位置
        """
        if config_path is None:
            # 默认路径：exe 所在目录（打包后）或项目根目录（开发时）
            if getattr(sys, 'frozen', False):
                exe_dir = os.path.dirname(os.path.abspath(sys.executable))
            else:
                exe_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(exe_dir, "settings.ini")
        self.path = config_path
        self.config = configparser.ConfigParser()
        # Bug 14 修复: 脏标志，仅在真正有修改时才写入磁盘
        self._dirty = False
        self._ensure_defaults()
        self.config.read(self.path, encoding='utf-8')

    def _ensure_defaults(self):
        """如果配置文件不存在，创建默认配置"""
        if os.path.exists(self.path):
            return
        try:
            # Bug 7 修复: 处理 os.path.dirname 返回空字符串的情况
            dir_path = os.path.dirname(self.path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
        except OSError:
            pass
        self.config['General'] = {
            'ask_before_execute': 'true',
            'ask_critical_only': 'false',
            'ask_before_close': 'true',
            'force_close': 'false',
            'play_sound_last_10s': 'true',
            'minimize_to_tray': 'true',
            'esc_minimize': 'true',
            'progress_mode': 'percent',
            'start_minimized': 'false',
            'log_level': 'INFO',
        }
        self.config['Window'] = {
            'geometry': '',
        }
        self._write()

    def _write(self):
        """写入配置文件（原子写：先写临时文件，再 os.replace 覆盖）

        Bug P0 修复: 避免写入中途断电/进程被杀导致 settings.ini 半截内容被清空。
        """
        try:
            # Bug 7 修复: 写入前同样检查 dirname
            dir_path = os.path.dirname(self.path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            # 原子写：同目录的临时文件 + replace
            fd, tmp = tempfile.mkstemp(
                prefix=".settings_", suffix=".tmp", dir=dir_path or None
            )
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    self.config.write(f)
                os.replace(tmp, self.path)
            except Exception:
                # 失败时清理临时文件
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            self._dirty = False
        except Exception as e:
            self._dirty = True  # 保留脏标志，下次重试
            print(f"写入配置文件失败: {e}", file=sys.stderr)

    # ---- 读取方法 ----

    def get(self, section, key, default=None):
        """获取字符串类型的配置值"""
        try:
            return self.config.get(section, key)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return default

    def getboolean(self, section, key, default=False):
        """获取布尔类型的配置值"""
        try:
            return self.config.getboolean(section, key)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return default

    def getint(self, section, key, default=0):
        """获取整数类型的配置值"""
        try:
            return self.config.getint(section, key)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return default

    # ---- 写入方法 ----

    def set(self, section, key, value):
        """设置配置项的值（仅修改内存，需调用 save() 持久化）"""
        if section not in self.config:
            self.config[section] = {}
        self.config[section][key] = str(value)
        self._dirty = True

    def setboolean(self, section, key, value):
        """设置布尔配置项的值"""
        self.set(section, key, 'true' if value else 'false')

    def save(self):
        """保存配置到文件（仅当有修改时才写入）"""
        try:
            if self._dirty:
                self._write()
        except Exception as e:
            print(f"保存配置失败: {e}", file=sys.stderr)
