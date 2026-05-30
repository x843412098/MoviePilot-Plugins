import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any, Dict, List, Optional, Tuple

import pytz
from apscheduler.schedulers.background import BackgroundScheduler

from app.core.config import settings
from app.helper.downloader import DownloaderHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType


class CloudArchive(_PluginBase):
    """夸克网盘归档插件"""

    plugin_name = "夸克网盘归档"
    plugin_desc = (
        "定期将下载目录中超过指定天数的文件移动到夸克网盘挂载目录，"
        "清理本地文件、硬链接和下载器种子记录，支持手动确认模式。"
    )
    plugin_icon = "cloud_archive.png"
    plugin_version = "1.0.1"
    plugin_author = "Hermes Agent"
    author_url = "https://github.com/NousResearch/hermes-agent"
    plugin_config_prefix = "cloudarchive_"
    plugin_order = 50
    auth_level = 2

    # 私有属性
    _scheduler: Optional[BackgroundScheduler] = None
    _event = Event()

    _enabled = False
    _cron = "0 3 * * *"
    _onlyonce = False
    _notify = True
    _confirm_mode = True
    _days = 30
    _target_path = ""
    _scan_paths = ""
    _delete_qb = True
    _delete_local = True
    _size_threshold_mb = 0

    _pending_files: List[Dict[str, Any]] = []
    _last_scan_time: Optional[str] = None
    _last_transfer_result: Optional[Dict[str, Any]] = None

    # ── 配置表单（v2） ──────────────────────────────────
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
                                "props": {"cols": 12, "md": 6},
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
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "onlyonce",
                                            "label": "立即运行一次",
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
                                        "component": "VTextField",
                                        "props": {
                                            "model": "cron",
                                            "label": "定时表达式",
                                            "placeholder": "0 3 * * *",
                                            "hint": "Cron 表达式，默认每天凌晨3点",
                                            "persistentHint": True,
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
                                            "label": "扫描目录",
                                            "placeholder": "/vol1/1000/Media/源文件/电影\n/vol1/1000/Media/硬链接/电影\n...",
                                            "rows": 3,
                                            "hint": "每行一个目录路径",
                                            "persistentHint": True,
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
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "target_path",
                                            "label": "夸克归档目录",
                                            "placeholder": "/mnt/quark/归档",
                                            "hint": "夸克网盘在 NAS 上的挂载路径",
                                            "persistentHint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "days",
                                            "label": "归档天数",
                                            "type": "number",
                                            "hint": "超过此天数的文件将被移动",
                                            "persistentHint": True,
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
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "confirm_mode",
                                            "label": "手动确认模式",
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
                                            "model": "size_threshold_mb",
                                            "label": "最小文件(MB)",
                                            "type": "number",
                                            "hint": "小于此值的文件跳过，0不限",
                                            "persistentHint": True,
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
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "delete_qb",
                                            "label": "删除下载器种子记录",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "delete_local",
                                            "label": "删除本地文件",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                ],
            }
        ], {
            "enabled": False,
            "onlyonce": False,
            "cron": "0 3 * * *",
            "scan_paths": "",
            "target_path": "",
            "days": 30,
            "confirm_mode": True,
            "notify": True,
            "delete_qb": True,
            "delete_local": True,
            "size_threshold_mb": 0,
        }

    # ── 详情页面 ──────────────────────────────────────────
    def get_page(self) -> Optional[List[dict]]:
        return [
            {
                "component": "VCard",
                "props": {"title": "夸克网盘归档 v1.0.0"},
                "content": [
                    {
                        "component": "VCardText",
                        "props": {
                            "text": "插件已就绪。请在配置页面设置扫描目录和归档目录后启用。"
                        },
                    }
                ],
            }
        ]

    # ── 状态 ──────────────────────────────────────────────
    def get_state(self) -> bool:
        return self._enabled

    # ── 初始化 ────────────────────────────────────────────
    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = config.get("enabled", False)
            self._cron = config.get("cron") or "0 3 * * *"
            self._onlyonce = config.get("onlyonce", False)
            self._notify = config.get("notify", True)
            self._confirm_mode = config.get("confirm_mode", True)
            self._days = int(config.get("days", 30) or 30)
            self._target_path = str(config.get("target_path", "") or "").strip()
            self._scan_paths = str(config.get("scan_paths", "") or "").strip()
            self._delete_qb = config.get("delete_qb", True)
            self._delete_local = config.get("delete_local", True)
            self._size_threshold_mb = float(
                config.get("size_threshold_mb", 0) or 0
            )

        self.stop_service()

        self._pending_files = self.get_data("pending_files") or []
        self._last_scan_time = self.get_data("last_scan_time")
        self._last_transfer_result = self.get_data("last_transfer_result")

        if not self._enabled and not self._onlyonce:
            return

        if not self._target_path:
            logger.warning("[CloudArchive] 未配置夸克归档目录")
            self._enabled = False
            if config:
                config["enabled"] = False
                self.update_config(config=config)
            return

        if not self._scan_paths:
            logger.warning("[CloudArchive] 未配置扫描目录")
            self._enabled = False
            if config:
                config["enabled"] = False
                self.update_config(config=config)
            return

        self._scheduler = BackgroundScheduler(timezone=settings.TZ)

        if self._onlyonce:
            logger.info("[CloudArchive] 立即运行一次")
            self._scheduler.add_job(
                self._scheduled_job,
                "date",
                run_date=datetime.now(tz=pytz.timezone(settings.TZ))
                + timedelta(seconds=3),
            )
            self._onlyonce = False
            if config:
                config["onlyonce"] = False
                self.update_config(config=config)

        if self._cron:
            parts = self._cron.strip().split()
            self._scheduler.add_job(
                self._scheduled_job,
                "cron",
                minute=parts[0] if len(parts) >= 1 else "0",
                hour=parts[1] if len(parts) >= 2 else "3",
            )
            logger.info(f"[CloudArchive] 定时: {self._cron}")

        if self._scheduler.get_jobs():
            self._scheduler.start()

    # ── 定时任务 ──────────────────────────────────────────
    def _scheduled_job(self):
        logger.info("[CloudArchive] 定时任务触发")
        self._do_scan()
        if not self._confirm_mode:
            self._do_transfer()

    # ── 扫描 ──────────────────────────────────────────────
    def _do_scan(self):
        if not self._scan_paths or not self._target_path:
            return

        scan_dirs = [
            p.strip()
            for p in self._scan_paths.replace(",", "\n").split("\n")
            if p.strip()
        ]
        if not scan_dirs:
            return

        cutoff = time.time() - (self._days * 86400)
        pending = []

        for scan_dir in scan_dirs:
            scan_path = Path(scan_dir)
            if not scan_path.exists():
                logger.warning(f"[CloudArchive] 目录不存在: {scan_dir}")
                continue
            for fp in scan_path.rglob("*"):
                if not fp.is_file():
                    continue
                mtime = fp.stat().st_mtime
                if mtime > cutoff:
                    continue
                size_mb = fp.stat().st_size / (1024 * 1024)
                if self._size_threshold_mb > 0 and size_mb < self._size_threshold_mb:
                    continue
                if time.time() - mtime < 3600:
                    continue
                age_days = int((time.time() - mtime) / 86400)
                pending.append(
                    {
                        "name": fp.name,
                        "path": str(fp),
                        "size_mb": round(size_mb, 2),
                        "size_bytes": fp.stat().st_size,
                        "age_days": age_days,
                        "mtime": mtime,
                    }
                )

        seen = set()
        unique = []
        for f in pending:
            key = (f["path"], f["size_bytes"])
            if key not in seen:
                seen.add(key)
                unique.append(f)
        pending = sorted(unique, key=lambda x: x["mtime"])

        self._pending_files = pending
        self._last_scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.save_data("pending_files", pending)
        self.save_data("last_scan_time", self._last_scan_time)

        count = len(pending)
        total_mb = sum(f["size_mb"] for f in pending)
        logger.info(
            f"[CloudArchive] 扫描完成: {count} 个文件, {total_mb:.0f} MB"
        )

    # ── 转移 ──────────────────────────────────────────────
    def _do_transfer(self):
        if not self._pending_files:
            return

        target = Path(self._target_path)
        target.mkdir(parents=True, exist_ok=True)

        success = 0
        failed = 0
        skipped = 0
        details = []

        for item in list(self._pending_files):
            src = Path(item["path"])
            dst = target / item["name"]

            if dst.exists() and dst.stat().st_size == item["size_bytes"]:
                skipped += 1
                self._pending_files.remove(item)
                continue

            try:
                shutil.move(str(src), str(dst))
                if not dst.exists():
                    raise Exception("目标文件不存在")
                if dst.stat().st_size != item["size_bytes"]:
                    raise Exception("文件大小不匹配")
                success += 1
                details.append(f"✅ {item['name']}")
                self._pending_files.remove(item)

                if self._delete_qb:
                    self._remove_qb_torrent(item)

            except Exception as e:
                failed += 1
                details.append(f"❌ {item['name']}: {e}")
                logger.error(f"[CloudArchive] 转移失败: {item['name']}: {e}")

        self._last_transfer_result = {
            "success": success,
            "failed": failed,
            "skipped": skipped,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "detail": "; ".join(details[-10:]),
        }
        self.save_data("pending_files", self._pending_files)
        self.save_data("last_transfer_result", self._last_transfer_result)

        if self._notify:
            self._send_notification(
                "📦 夸克归档完成",
                f"成功:{success} 失败:{failed} 跳过:{skipped}\n"
                + "\n".join(details[-5:]),
            )

        logger.info(
            f"[CloudArchive] 转移完成: 成功{success} 失败{failed} 跳过{skipped}"
        )

    # ── qBittorrent 清理 ─────────────────────────────────
    def _remove_qb_torrent(self, item: Dict[str, Any]):
        try:
            helper = DownloaderHelper()
            services = helper.get_services()
            if not services:
                return
            default_svc = next(
                (s for s in services if s.get("default")), services[0]
            )
            if not default_svc:
                return

            svc_name = default_svc.get("name", "")
            if "qbittorrent" not in default_svc.get("type", "").lower():
                return

            downloader = helper.get_service(name=svc_name)
            if not downloader or not downloader.instance:
                return

            torrents = downloader.instance.get_torrents()
            item_stem = Path(item["name"]).stem
            for t in torrents:
                tname = t.get("name", "")
                if item_stem in tname or tname in item_stem:
                    try:
                        files = downloader.instance.get_torrent_files(
                            t.get("hash")
                        )
                        for f in files:
                            if item["name"] in f.get("name", ""):
                                downloader.instance.delete_torrents(
                                    delete_file=False, ids=[t.get("hash")]
                                )
                                logger.info(
                                    f"[CloudArchive] 删除种子: {tname}"
                                )
                                return
                    except Exception:
                        downloader.instance.delete_torrents(
                            delete_file=False, ids=[t.get("hash")]
                        )
                        logger.info(
                            f"[CloudArchive] 删除种子(名称匹配): {tname}"
                        )
                        return
        except Exception as e:
            logger.error(f"[CloudArchive] 删除种子失败: {e}")

    # ── 通知 ──────────────────────────────────────────────
    def _send_notification(self, title: str, text: str):
        try:
            self.post_message(
                mtype=NotificationType.SiteMessage, title=title, text=text
            )
        except Exception as e:
            logger.warning(f"[CloudArchive] 通知失败: {e}")

    # ── 停止服务 ──────────────────────────────────────────
    def stop_service(self):
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
        self._event.clear()
