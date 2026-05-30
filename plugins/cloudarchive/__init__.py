from typing import Any, Dict, List, Optional, Tuple

from app.plugins import _PluginBase


class CloudArchive(_PluginBase):
    """夸克网盘归档（最小稳定版）"""

    plugin_name = "夸克网盘归档"
    plugin_desc = "稳定基线版：先确保插件详情页与配置页可正常显示。"
    plugin_icon = "cloud_archive.png"
    plugin_version = "1.0.2"
    plugin_author = "Hermes Agent"
    author_url = "https://github.com/x843412098/MoviePilot-Plugins"
    plugin_config_prefix = "cloudarchive_"
    plugin_order = 50
    auth_level = 2

    _enabled = False
    _days = 30
    _target_path = ""
    _scan_paths = ""
    _notify = True

    def init_plugin(self, config: dict = None):
        config = config or {}
        self._enabled = bool(config.get("enabled", False))
        self._days = int(config.get("days", 30) or 30)
        self._target_path = str(config.get("target_path", "") or "").strip()
        self._scan_paths = str(config.get("scan_paths", "") or "").strip()
        self._notify = bool(config.get("notify", True))

    def get_state(self) -> bool:
        return self._enabled

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用插件",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "notify",
                                            "label": "发送通知",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "days",
                                            "label": "归档天数",
                                            "type": "number",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "scan_paths",
                                            "label": "扫描目录（每行一个）",
                                            "rows": 3,
                                            "placeholder": "/vol1/1000/Media/源文件/电影\n/vol1/1000/Media/源文件/电视剧",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "target_path",
                                            "label": "夸克归档目录",
                                            "placeholder": "/path/to/quick",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                ],
            }
        ], {
            "enabled": False,
            "notify": True,
            "days": 30,
            "scan_paths": "",
            "target_path": "",
        }

    def get_page(self) -> Optional[List[dict]]:
        return [
            {
                "component": "VCard",
                "props": {"title": "夸克网盘归档 v1.0.2"},
                "content": [
                    {
                        "component": "VCardText",
                        "props": {
                            "text": "✅ 插件详情页正常\n这是稳定基线版。下一步我会在此基础上逐步恢复：扫描 -> 转移 -> 校验 -> 清理 -> 通知。"
                        },
                    }
                ],
            }
        ]

    def get_service(self) -> List[Dict[str, Any]]:
        return []

    def get_command(self) -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def stop_service(self):
        return
