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
    VIDEO_EXTENSIONS = {
        ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".ts", ".m2ts", ".iso", ".mpg", ".mpeg", ".webm"
    }
    plugin_name = "夸克网盘归档"
    plugin_desc = "旧文件转移到夸克挂载目录，支持先扫描再勾选归档。"
    plugin_icon = "cloud_archive.png"
    plugin_version = "1.2.2"
    plugin_author = "Hermes Agent"
    author_url = "https://github.com/x843412098/MoviePilot-Plugins"
    plugin_config_prefix = "cloudarchive_"
    plugin_order = 50
    auth_level = 2

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
    _hardlink_paths = ""
    _delete_qb = True
    _delete_local = True
    _size_threshold_mb = 0
    _selected_mode = True
    _run_selected_once = False
    _selected_paths: List[str] = []

    _pending_files: List[Dict[str, Any]] = []
    _last_scan_time: Optional[str] = None
    _last_transfer_result: Optional[Dict[str, Any]] = None

    def _pending_select_items(self) -> List[Dict[str, str]]:
        items = []
        for x in self._pending_files[:500]:
            label = f"{x.get('name','')} | {x.get('age_days',0)}天 | {x.get('size_mb',0)}MB"
            items.append({"title": label, "value": x.get("path", "")})
        return items

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VSwitch", "props": {"model": "confirm_mode", "label": "手动确认模式(只扫描)"}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VSwitch", "props": {"model": "notify", "label": "发送通知"}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VSwitch", "props": {"model": "onlyonce", "label": "立即扫描一次"}}]},
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [{"component": "VTextField", "props": {"model": "cron", "label": "定时表达式", "placeholder": "0 3 * * *"}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VTextField", "props": {"model": "days", "label": "归档天数", "type": "number"}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VTextField", "props": {"model": "size_threshold_mb", "label": "最小文件(MB)", "type": "number"}}]},
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VTextarea", "props": {"model": "scan_paths", "label": "扫描目录（每行一个）", "rows": 3}}]},
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VTextarea", "props": {"model": "hardlink_paths", "label": "硬链接目录（每行一个）", "rows": 2}}]},
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {"component": "VCol", "props": {"cols": 12, "md": 8}, "content": [{"component": "VTextField", "props": {"model": "target_path", "label": "夸克归档目录"}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 2}, "content": [{"component": "VSwitch", "props": {"model": "delete_local", "label": "删本地"}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 2}, "content": [{"component": "VSwitch", "props": {"model": "delete_qb", "label": "删QB记录"}}]},
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VSwitch", "props": {"model": "selected_mode", "label": "仅归档勾选项"}}]},
                            {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VSwitch", "props": {"model": "run_selected_once", "label": "执行勾选项一次"}}]},
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
                                        "component": "VSelect",
                                        "props": {
                                            "model": "selected_paths",
                                            "label": "扫描结果勾选（多选）",
                                            "items": self._pending_select_items(),
                                            "multiple": True,
                                            "chips": True,
                                            "hint": "先点‘立即扫描一次’保存后刷新，再在这里勾选，再打开‘执行勾选项一次’保存。",
                                            "persistentHint": True,
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
            "confirm_mode": True,
            "notify": True,
            "onlyonce": False,
            "cron": "0 3 * * *",
            "days": 30,
            "size_threshold_mb": 0,
            "scan_paths": "",
            "hardlink_paths": "",
            "target_path": "",
            "delete_local": True,
            "delete_qb": True,
            "selected_mode": True,
            "run_selected_once": False,
            "selected_paths": [],
        }

    def get_page(self) -> Optional[List[dict]]:
        last = self._last_transfer_result or {}
        status = "已启用" if self._enabled else "未启用"
        msg = (
            f"状态: {status}\n"
            f"手动确认: {'开启' if self._confirm_mode else '关闭'}\n"
            f"待转移: {len(self._pending_files)} 个\n"
            f"已勾选: {len(self._selected_paths)} 个\n"
            f"上次扫描: {self._last_scan_time or '从未'}\n"
            f"上次结果: 成功{last.get('success',0)} 失败{last.get('failed',0)} 跳过{last.get('skipped',0)}"
        )
        return [{"component": "VCard", "props": {"title": "夸克网盘归档 v1.2.0"}, "content": [{"component": "VCardText", "props": {"text": msg}}]}]

    def get_state(self) -> bool:
        return self._enabled

    def init_plugin(self, config: dict = None):
        config = config or {}
        self._enabled = config.get("enabled", False)
        self._cron = config.get("cron") or "0 3 * * *"
        self._onlyonce = config.get("onlyonce", False)
        self._notify = config.get("notify", True)
        self._confirm_mode = config.get("confirm_mode", True)
        self._days = int(config.get("days", 30) or 30)
        self._target_path = str(config.get("target_path", "") or "").strip()
        self._scan_paths = str(config.get("scan_paths", "") or "").strip()
        self._hardlink_paths = str(config.get("hardlink_paths", "") or "").strip()
        self._delete_qb = config.get("delete_qb", True)
        self._delete_local = config.get("delete_local", True)
        self._size_threshold_mb = float(config.get("size_threshold_mb", 0) or 0)
        self._selected_mode = config.get("selected_mode", True)
        self._run_selected_once = config.get("run_selected_once", False)
        self._selected_paths = config.get("selected_paths", []) or []
        if not isinstance(self._selected_paths, list):
            self._selected_paths = []

        self.stop_service()

        self._pending_files = self.get_data("pending_files") or []
        self._last_scan_time = self.get_data("last_scan_time")
        self._last_transfer_result = self.get_data("last_transfer_result")

        if not self._enabled and not self._onlyonce and not self._run_selected_once:
            return
        if not self._target_path or not self._hardlink_paths:
            logger.warning("[CloudArchive] 未配置硬链接目录或归档目录")
            return

        self._scheduler = BackgroundScheduler(timezone=settings.TZ)

        if self._onlyonce:
            self._scheduler.add_job(self._do_scan_only, "date", run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3))
            self._onlyonce = False
            config["onlyonce"] = False
            self.update_config(config=config)

        if self._run_selected_once:
            self._scheduler.add_job(self._run_selected_now, "date", run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=5))
            self._run_selected_once = False
            config["run_selected_once"] = False
            self.update_config(config=config)

        if self._enabled and self._cron:
            parts = self._cron.strip().split()
            if len(parts) == 5:
                self._scheduler.add_job(self._scheduled_job, "cron", minute=parts[0], hour=parts[1], day=parts[2], month=parts[3], day_of_week=parts[4])
            else:
                self._scheduler.add_job(self._scheduled_job, "cron", minute="0", hour="3")

        if self._scheduler.get_jobs():
            self._scheduler.start()

    def _split_lines(self, raw: str) -> List[str]:
        return [x.strip() for x in raw.replace(",", "\n").split("\n") if x.strip()]

    def _is_video_file(self, fp: Path) -> bool:
        return fp.suffix.lower() in self.VIDEO_EXTENSIONS

    def _do_scan_only(self):
        self._do_scan()
        if self._notify:
            total_mb = sum(x.get("size_mb", 0) for x in self._pending_files)
            total_gb = total_mb / 1024
            self._send_notification(
                "📦 扫描完成",
                f"候选文件: {len(self._pending_files)} 个，约 {total_mb:.0f}MB（约 {total_gb:.2f}GB）。请在插件配置里勾选后执行。",
            )

    def _run_selected_now(self):
        self._do_scan()
        self._do_transfer(selected_only=self._selected_mode)

    def _scheduled_job(self):
        self._do_scan()
        if self._confirm_mode:
            if self._notify and self._pending_files:
                total_mb = sum(x.get("size_mb", 0) for x in self._pending_files)
                total_gb = total_mb / 1024
                self._send_notification(
                    "📦 发现可归档文件",
                    f"共 {len(self._pending_files)} 个，约 {total_mb:.0f}MB（约 {total_gb:.2f}GB）。手动确认模式已开启。",
                )
            return
        self._do_transfer(selected_only=self._selected_mode)

    def _do_scan(self):
        # 仅扫描“硬链接目录”下的视频文件
        scan_dirs = self._split_lines(self._hardlink_paths)
        video_exts = {
            ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".m4v", ".ts", ".m2ts", ".mpg", ".mpeg", ".iso"
        }
        cutoff = time.time() - (self._days * 86400)
        pending = []
        for scan_dir in scan_dirs:
            root = Path(scan_dir)
            if not root.exists():
                continue
            for fp in root.rglob("*"):
                if not fp.is_file():
                    continue
                if fp.suffix.lower() not in video_exts:
                    continue
                try:
                    st = fp.stat()
                except Exception:
                    continue
                if st.st_mtime > cutoff:
                    continue
                size_mb = st.st_size / (1024 * 1024)
                if self._size_threshold_mb > 0 and size_mb < self._size_threshold_mb:
                    continue
                pending.append({"name": fp.name, "path": str(fp), "size_bytes": st.st_size, "size_mb": round(size_mb, 2), "age_days": int((time.time() - st.st_mtime) / 86400), "mtime": st.st_mtime})

        seen = set()
        uniq = []
        for p in pending:
            k = (p["path"], p["size_bytes"])
            if k in seen:
                continue
            seen.add(k)
            uniq.append(p)

        self._pending_files = sorted(uniq, key=lambda x: x["mtime"])
        valid_paths = {x["path"] for x in self._pending_files}
        self._selected_paths = [p for p in self._selected_paths if p in valid_paths]
        self._last_scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.save_data("pending_files", self._pending_files)
        self.save_data("last_scan_time", self._last_scan_time)

    def _do_transfer(self, selected_only: bool = True):
        if not self._pending_files:
            return
        if selected_only:
            selected_set = set(self._selected_paths or [])
            candidates = [x for x in self._pending_files if x.get("path") in selected_set]
        else:
            candidates = list(self._pending_files)

        target_root = Path(self._target_path)
        target_root.mkdir(parents=True, exist_ok=True)

        success = failed = skipped = 0
        details = []

        for item in list(candidates):
            src = Path(item["path"])
            if not src.exists():
                skipped += 1
                details.append(f"⏭️ 不存在: {item['name']}")
                if item in self._pending_files:
                    self._pending_files.remove(item)
                continue
            dst = target_root / item["name"]
            if dst.exists() and dst.stat().st_size == item["size_bytes"]:
                skipped += 1
                details.append(f"⏭️ 已存在: {item['name']}")
                if item in self._pending_files:
                    self._pending_files.remove(item)
                continue
            try:
                shutil.move(str(src), str(dst))
                if not dst.exists() or dst.stat().st_size != item["size_bytes"]:
                    raise RuntimeError("目标校验失败")
                if self._delete_local:
                    self._cleanup_hardlinks(item["name"], keep_path=str(dst))
                if self._delete_qb:
                    self._remove_qb_torrent(item)
                success += 1
                details.append(f"✅ {item['name']}")
                if item in self._pending_files:
                    self._pending_files.remove(item)
                if item.get("path") in self._selected_paths:
                    self._selected_paths.remove(item.get("path"))
            except Exception as e:
                failed += 1
                details.append(f"❌ {item['name']}: {e}")
                logger.error(f"[CloudArchive] 转移失败 {item['path']}: {e}")

        self._last_transfer_result = {
            "success": success,
            "failed": failed,
            "skipped": skipped,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "detail": "\n".join(details[-20:]),
        }
        self.save_data("pending_files", self._pending_files)
        self.save_data("last_transfer_result", self._last_transfer_result)

        if self._notify:
            self._send_notification("📦 夸克归档完成", f"成功: {success}\n失败: {failed}\n跳过: {skipped}")

    def _cleanup_hardlinks(self, filename: str, keep_path: str = ""):
        for root_raw in self._split_lines(self._hardlink_paths):
            root = Path(root_raw)
            if not root.exists():
                continue
            for fp in root.rglob(filename):
                try:
                    if keep_path and str(fp) == keep_path:
                        continue
                    if fp.is_file():
                        fp.unlink(missing_ok=True)
                except Exception as e:
                    logger.warning(f"[CloudArchive] 删除硬链接失败 {fp}: {e}")

    def _remove_qb_torrent(self, item: Dict[str, Any]):
        try:
            helper = DownloaderHelper()
            services = helper.get_services() or []
            if not services:
                return
            svc = next((s for s in services if s.get("default")), services[0])
            if "qb" not in str(svc.get("type", "")).lower():
                return
            downloader = helper.get_service(name=svc.get("name", ""))
            if not downloader or not downloader.instance:
                return
            stem = Path(item["name"]).stem
            for t in downloader.instance.get_torrents() or []:
                tname = str(t.get("name", ""))
                if stem and (stem in tname or tname in stem):
                    thash = t.get("hash")
                    if thash:
                        downloader.instance.delete_torrents(delete_file=False, ids=[thash])
                        return
        except Exception as e:
            logger.warning(f"[CloudArchive] 删除QB记录失败: {e}")

    def _send_notification(self, title: str, text: str):
        try:
            self.post_message(mtype=NotificationType.SiteMessage, title=title, text=text)
        except Exception as e:
            logger.warning(f"[CloudArchive] 通知失败: {e}")

    def get_service(self) -> List[Dict[str, Any]]:
        return []

    def get_command(self) -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def stop_service(self):
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
        self._event.clear()
