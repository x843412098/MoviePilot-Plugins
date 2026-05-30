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
    plugin_name = "夸克网盘归档"
    plugin_desc = "仅扫描硬链接视频，支持按剧集目录勾选后归档。"
    plugin_icon = "cloud_archive.png"
    plugin_version = "1.3.0"
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
    _hardlink_paths = ""
    _source_paths = ""
    _delete_qb = True
    _delete_local = True
    _size_threshold_mb = 0
    _selected_mode = True
    _run_selected_once = False
    _series_group_mode = True
    _selected_paths: List[str] = []
    _selected_groups: List[str] = []

    _pending_files: List[Dict[str, Any]] = []
    _pending_groups: List[Dict[str, Any]] = []
    _last_scan_time: Optional[str] = None
    _last_transfer_result: Optional[Dict[str, Any]] = None

    VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".m4v", ".ts", ".m2ts", ".mpg", ".mpeg", ".iso"}

    def _split_lines(self, raw: str) -> List[str]:
        return [x.strip() for x in str(raw or "").replace(",", "\n").split("\n") if x.strip()]

    def _pending_select_items(self) -> List[Dict[str, str]]:
        return [{"title": f"{x.get('name','')} | {x.get('age_days', 0)}天 | {x.get('size_mb',0)}MB", "value": x.get("path", "")} for x in self._pending_files[:800]]

    def _group_select_items(self) -> List[Dict[str, str]]:
        items = []
        for g in self._pending_groups[:800]:
            items.append({"title": f"{g['group']} | {g['count']}个视频 | {g['size_mb']:.0f}MB", "value": g["group"]})
        return items

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [{"component": "VForm", "content": [
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}}]},
                {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VSwitch", "props": {"model": "confirm_mode", "label": "手动确认(只扫描)"}}]},
                {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VSwitch", "props": {"model": "notify", "label": "发送通知"}}]},
                {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VSwitch", "props": {"model": "onlyonce", "label": "立即扫描一次"}}]},
            ]},
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [{"component": "VTextField", "props": {"model": "cron", "label": "定时表达式"}}]},
                {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VTextField", "props": {"model": "days", "label": "归档天数", "type": "number"}}]},
                {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VTextField", "props": {"model": "size_threshold_mb", "label": "最小文件(MB)", "type": "number"}}]},
            ]},
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VTextarea", "props": {"model": "hardlink_paths", "label": "硬链接目录（每行一个，仅扫描这里）", "rows": 3}}]},
            ]},
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VTextarea", "props": {"model": "source_paths", "label": "源文件目录（每行一个，与硬链接目录按顺序对应）", "rows": 3}}]},
            ]},
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12, "md": 8}, "content": [{"component": "VTextField", "props": {"model": "target_path", "label": "夸克归档目录"}}]},
                {"component": "VCol", "props": {"cols": 12, "md": 2}, "content": [{"component": "VSwitch", "props": {"model": "delete_local", "label": "删本地"}}]},
                {"component": "VCol", "props": {"cols": 12, "md": 2}, "content": [{"component": "VSwitch", "props": {"model": "delete_qb", "label": "删QB记录"}}]},
            ]},
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VSwitch", "props": {"model": "selected_mode", "label": "仅归档勾选项"}}]},
                {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VSwitch", "props": {"model": "series_group_mode", "label": "剧集按目录勾选"}}]},
                {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VSwitch", "props": {"model": "run_selected_once", "label": "执行勾选项一次"}}]},
            ]},
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VSelect", "props": {"model": "selected_groups", "label": "剧集目录勾选（多选）", "items": self._group_select_items(), "multiple": True, "chips": True}}]},
            ]},
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VSelect", "props": {"model": "selected_paths", "label": "文件勾选（多选）", "items": self._pending_select_items(), "multiple": True, "chips": True, "hint": "可与目录勾选配合使用", "persistentHint": True}}]},
            ]},
        ]}], {
            "enabled": False, "confirm_mode": True, "notify": True, "onlyonce": False,
            "cron": "0 3 * * *", "days": 30, "size_threshold_mb": 0,
            "hardlink_paths": "", "source_paths": "", "target_path": "",
            "delete_local": True, "delete_qb": True,
            "selected_mode": True, "series_group_mode": True, "run_selected_once": False,
            "selected_paths": [], "selected_groups": [],
        }

    def get_page(self) -> Optional[List[dict]]:
        total_mb = sum(x.get("size_mb", 0) for x in self._pending_files)
        last = self._last_transfer_result or {}
        return [{"component": "VCard", "props": {"title": "夸克网盘归档 v1.3.0"}, "content": [{"component": "VCardText", "props": {"text":
            f"待归档: {len(self._pending_files)} 个视频（{total_mb:.0f}MB / {total_mb/1024:.2f}GB）\n"
            f"目录候选: {len(self._pending_groups)} 组\n"
            f"上次扫描: {self._last_scan_time or '从未'}\n"
            f"上次结果: 成功{last.get('success',0)} 失败{last.get('failed',0)} 跳过{last.get('skipped',0)}"
        }}]}]

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
        self._hardlink_paths = str(config.get("hardlink_paths", "") or "").strip()
        self._source_paths = str(config.get("source_paths", "") or "").strip()
        self._delete_qb = config.get("delete_qb", True)
        self._delete_local = config.get("delete_local", True)
        self._size_threshold_mb = float(config.get("size_threshold_mb", 0) or 0)
        self._selected_mode = config.get("selected_mode", True)
        self._series_group_mode = config.get("series_group_mode", True)
        self._run_selected_once = config.get("run_selected_once", False)
        self._selected_paths = config.get("selected_paths", []) or []
        self._selected_groups = config.get("selected_groups", []) or []

        self._pending_files = self.get_data("pending_files") or []
        self._pending_groups = self.get_data("pending_groups") or []
        self._last_scan_time = self.get_data("last_scan_time")
        self._last_transfer_result = self.get_data("last_transfer_result")

        self.stop_service()
        if not self._enabled and not self._onlyonce and not self._run_selected_once:
            return
        if not self._target_path or not self._hardlink_paths:
            logger.warning("[CloudArchive] 未配置硬链接目录或归档目录")
            return

        self._scheduler = BackgroundScheduler(timezone=settings.TZ)
        if self._onlyonce:
            self._scheduler.add_job(self._do_scan_only, "date", run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3))
            config["onlyonce"] = False
            self.update_config(config=config)
        if self._run_selected_once:
            self._scheduler.add_job(self._run_selected_now, "date", run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=5))
            config["run_selected_once"] = False
            self.update_config(config=config)

        if self._enabled and self._cron:
            p = self._cron.strip().split()
            if len(p) == 5:
                self._scheduler.add_job(self._scheduled_job, "cron", minute=p[0], hour=p[1], day=p[2], month=p[3], day_of_week=p[4])
            else:
                self._scheduler.add_job(self._scheduled_job, "cron", minute="0", hour="3")
        if self._scheduler.get_jobs():
            self._scheduler.start()

    def _do_scan_only(self):
        self._do_scan()
        if self._notify:
            total_mb = sum(x.get("size_mb", 0) for x in self._pending_files)
            self._send_notification("📦 扫描完成", f"候选文件: {len(self._pending_files)} 个，约 {total_mb:.0f}MB（约 {total_mb/1024:.2f}GB）。")

    def _scheduled_job(self):
        self._do_scan()
        if self._confirm_mode:
            if self._notify and self._pending_files:
                total_mb = sum(x.get("size_mb", 0) for x in self._pending_files)
                self._send_notification("📦 发现可归档文件", f"共 {len(self._pending_files)} 个，约 {total_mb:.0f}MB（约 {total_mb/1024:.2f}GB）。")
            return
        self._do_transfer(selected_only=self._selected_mode)

    def _run_selected_now(self):
        self._do_scan()
        self._do_transfer(selected_only=self._selected_mode)

    def _do_scan(self):
        roots = [Path(x) for x in self._split_lines(self._hardlink_paths)]
        cutoff = time.time() - self._days * 86400
        pending = []
        groups: Dict[str, Dict[str, Any]] = {}

        for root in roots:
            if not root.exists():
                continue
            for fp in root.rglob("*"):
                if not fp.is_file() or fp.suffix.lower() not in self.VIDEO_EXTS:
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

                rel = fp.relative_to(root)
                group = rel.parts[0] if len(rel.parts) > 1 else fp.name
                pending.append({
                    "name": fp.name,
                    "path": str(fp),
                    "size_bytes": st.st_size,
                    "size_mb": round(size_mb, 2),
                    "age_days": int((time.time() - st.st_mtime) / 86400),
                    "mtime": st.st_mtime,
                    "hard_root": str(root),
                    "group": f"{root.name}/{group}",
                    "rel_path": str(rel),
                })

        pending.sort(key=lambda x: x["mtime"])
        for p in pending:
            g = p["group"]
            if g not in groups:
                groups[g] = {"group": g, "count": 0, "size_mb": 0.0}
            groups[g]["count"] += 1
            groups[g]["size_mb"] += p["size_mb"]

        valid_paths = {x["path"] for x in pending}
        valid_groups = set(groups.keys())
        self._selected_paths = [p for p in self._selected_paths if p in valid_paths]
        self._selected_groups = [g for g in self._selected_groups if g in valid_groups]

        self._pending_files = pending
        self._pending_groups = sorted(groups.values(), key=lambda x: x["group"])
        self._last_scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.save_data("pending_files", self._pending_files)
        self.save_data("pending_groups", self._pending_groups)
        self.save_data("last_scan_time", self._last_scan_time)

    def _pick_candidates(self, selected_only: bool) -> List[Dict[str, Any]]:
        if not selected_only:
            return list(self._pending_files)
        s_paths = set(self._selected_paths or [])
        s_groups = set(self._selected_groups or []) if self._series_group_mode else set()
        return [x for x in self._pending_files if x["path"] in s_paths or x["group"] in s_groups]

    def _map_source_path(self, item: Dict[str, Any]) -> Tuple[Optional[Path], Optional[Path]]:
        src_roots = self._split_lines(self._source_paths)
        hard_roots = self._split_lines(self._hardlink_paths)
        hard_root = item.get("hard_root")
        try:
            idx = hard_roots.index(hard_root)
        except Exception:
            return None, None
        if idx >= len(src_roots):
            return None, None
        src_root = Path(src_roots[idx])
        return src_root, src_root / item.get("rel_path", "")

    def _cleanup_empty_parents(self, start: Path, stop_root: Path):
        cur = start
        while True:
            if not cur.exists() or cur == stop_root:
                break
            try:
                cur.rmdir()
            except Exception:
                break
            cur = cur.parent

    def _do_transfer(self, selected_only: bool = True):
        candidates = self._pick_candidates(selected_only)
        if not candidates:
            return
        target = Path(self._target_path)
        target.mkdir(parents=True, exist_ok=True)

        success = failed = skipped = 0
        for item in list(candidates):
            src = Path(item["path"])
            if not src.exists():
                skipped += 1
                continue
            dst = target / item["name"]
            try:
                shutil.move(str(src), str(dst))
                if not dst.exists() or dst.stat().st_size != item["size_bytes"]:
                    raise RuntimeError("目标校验失败")

                if self._delete_local:
                    # 清理源文件
                    src_root, source_file = self._map_source_path(item)
                    if source_file and source_file.exists() and source_file.is_file():
                        source_file.unlink(missing_ok=True)
                        if src_root:
                            self._cleanup_empty_parents(source_file.parent, src_root)
                    # 清理硬链接空目录
                    hard_root = Path(item.get("hard_root", "/"))
                    self._cleanup_empty_parents(src.parent, hard_root)

                if self._delete_qb:
                    self._remove_qb_torrent(item)

                if item in self._pending_files:
                    self._pending_files.remove(item)
                if item["path"] in self._selected_paths:
                    self._selected_paths.remove(item["path"])
                success += 1
            except Exception as e:
                logger.error(f"[CloudArchive] 归档失败 {item['path']}: {e}")
                failed += 1

        self._last_transfer_result = {"success": success, "failed": failed, "skipped": skipped, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        self.save_data("pending_files", self._pending_files)
        self.save_data("last_transfer_result", self._last_transfer_result)
        if self._notify:
            self._send_notification("📦 夸克归档完成", f"成功: {success}\n失败: {failed}\n跳过: {skipped}")

    def _remove_qb_torrent(self, item: Dict[str, Any]):
        try:
            helper = DownloaderHelper()
            services = helper.get_services() or []
            if not services:
                return
            svc = next((s for s in services if s.get("default")), services[0])
            if "qb" not in str(svc.get("type", "")).lower():
                return
            dl = helper.get_service(name=svc.get("name", ""))
            if not dl or not dl.instance:
                return
            stem = Path(item["name"]).stem
            for t in dl.instance.get_torrents() or []:
                tname = str(t.get("name", ""))
                if stem and (stem in tname or tname in stem):
                    th = t.get("hash")
                    if th:
                        dl.instance.delete_torrents(delete_file=False, ids=[th])
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
