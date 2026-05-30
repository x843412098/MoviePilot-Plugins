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
    plugin_name = "网盘归档"
    plugin_desc = "按电影/电视剧分开配置，扫描硬链接视频并归档到网盘目录。"
    plugin_icon = "cloud_archive.png"
    plugin_version = "2.1.0"
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

    _movie_hardlink = ""
    _movie_source = ""
    _movie_target = ""

    _tv_hardlink = ""
    _tv_source = ""
    _tv_target = ""

    _delete_qb = True
    _delete_local = True
    _size_threshold_mb = 0
    _selected_mode = True
    _run_selected_once = False
    _series_group_mode = True

    _risk_control_enabled = True
    _max_items_per_run = 1
    _item_interval_sec = 2

    _movie_keyword = ""
    _tv_keyword = ""

    _selected_movie_paths: List[str] = []
    _selected_tv_paths: List[str] = []
    _selected_movie_groups: List[str] = []
    _selected_tv_groups: List[str] = []

    _pending_movie_files: List[Dict[str, Any]] = []
    _pending_tv_files: List[Dict[str, Any]] = []
    _pending_movie_groups: List[Dict[str, Any]] = []
    _pending_tv_groups: List[Dict[str, Any]] = []

    _last_scan_time: Optional[str] = None
    _last_transfer_result: Optional[Dict[str, Any]] = None
    _archive_logs: List[Dict[str, Any]] = []
    _clear_logs_once = False

    VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".m4v", ".ts", ".m2ts", ".mpg", ".mpeg", ".iso"}

    def _to_items(self, rows: List[Dict[str, Any]], keyword: str) -> List[Dict[str, str]]:
        # 实时过滤掉已不存在的文件，避免对话框残留陈旧候选
        live_rows = []
        for x in rows:
            p = x.get("path", "")
            if p and Path(p).exists():
                live_rows.append(x)
        kw = (keyword or "").strip().lower()
        if kw:
            live_rows = [x for x in live_rows if kw in str(x.get("group", "")).lower() or kw in str(x.get("name", "")).lower()]
        return [{"title": f"{x.get('group','')} / {x.get('name','')} | {x.get('size_mb',0):.2f}MB / {x.get('size_mb',0)/1024:.2f}GB | {x.get('age_days', 0)}天", "value": x.get("path", "")} for x in live_rows[:800]]

    def _to_group_items(self, rows: List[Dict[str, Any]], keyword: str) -> List[Dict[str, str]]:
        kw = (keyword or "").strip().lower()
        if kw:
            rows = [g for g in rows if kw in str(g.get("group", "")).lower()]
        return [{"title": f"{g['group']} | {g['count']}个视频 | {g['size_mb']:.0f}MB / {g['size_mb']/1024:.2f}GB", "value": g["group"]} for g in rows[:800]]

    def _prune_missing_candidates(self):
        self._pending_movie_files = [x for x in (self._pending_movie_files or []) if x.get("path") and Path(x.get("path")).exists()]
        self._pending_tv_files = [x for x in (self._pending_tv_files or []) if x.get("path") and Path(x.get("path")).exists()]
        movie_paths = {x.get("path") for x in self._pending_movie_files}
        tv_paths = {x.get("path") for x in self._pending_tv_files}
        self._selected_movie_paths = [p for p in (self._selected_movie_paths or []) if p in movie_paths]
        self._selected_tv_paths = [p for p in (self._selected_tv_paths or []) if p in tv_paths]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        self._prune_missing_candidates()
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

            {"component": "VAlert", "props": {"type": "info", "variant": "tonal", "text": "电影配置"}},
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VTextField", "props": {"model": "movie_hardlink", "label": "电影硬链接目录"}}]},
                {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VTextField", "props": {"model": "movie_source", "label": "电影源文件目录"}}]},
                {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VTextField", "props": {"model": "movie_target", "label": "电影网盘归档目录"}}]},
            ]},

            {"component": "VAlert", "props": {"type": "info", "variant": "tonal", "text": "电视剧配置"}},
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VTextField", "props": {"model": "tv_hardlink", "label": "电视剧硬链接目录"}}]},
                {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VTextField", "props": {"model": "tv_source", "label": "电视剧源文件目录"}}]},
                {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VTextField", "props": {"model": "tv_target", "label": "电视剧网盘归档目录"}}]},
            ]},

            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12, "md": 2}, "content": [{"component": "VSwitch", "props": {"model": "delete_local", "label": "删本地"}}]},
                {"component": "VCol", "props": {"cols": 12, "md": 2}, "content": [{"component": "VSwitch", "props": {"model": "delete_qb", "label": "删QB记录"}}]},
                {"component": "VCol", "props": {"cols": 12, "md": 2}, "content": [{"component": "VSwitch", "props": {"model": "selected_mode", "label": "仅归档勾选项"}}]},
                {"component": "VCol", "props": {"cols": 12, "md": 2}, "content": [{"component": "VSwitch", "props": {"model": "series_group_mode", "label": "剧集按目录勾选"}}]},
                {"component": "VCol", "props": {"cols": 12, "md": 2}, "content": [{"component": "VSwitch", "props": {"model": "run_selected_once", "label": "执行勾选项一次"}}]},
            ]},
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VSwitch", "props": {"model": "risk_control_enabled", "label": "启用风控限流"}}]},
                {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VTextField", "props": {"model": "max_items_per_run", "label": "单次最多处理", "type": "number"}}]},
                {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VTextField", "props": {"model": "item_interval_sec", "label": "每项间隔秒", "type": "number"}}]},
                {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VSwitch", "props": {"model": "clear_logs_once", "label": "清空归档日志(一次)"}}]},
            ]},

            {"component": "VAlert", "props": {"type": "info", "variant": "tonal", "text": "电影候选"}},
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VTextField", "props": {"model": "movie_keyword", "label": "按片名搜索过滤(电影)", "clearable": True}}]},
            ]},
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VSelect", "props": {"model": "selected_movie_groups", "label": "电影目录勾选（多选）", "items": self._to_group_items(self._pending_movie_groups, self._movie_keyword), "multiple": True, "chips": True}}]},
            ]},
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VSelect", "props": {"model": "selected_movie_paths", "label": "电影文件勾选（多选）", "items": self._to_items(self._pending_movie_files, self._movie_keyword), "multiple": True, "chips": True}}]},
            ]},

            {"component": "VAlert", "props": {"type": "info", "variant": "tonal", "text": "电视剧候选"}},
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VTextField", "props": {"model": "tv_keyword", "label": "按剧集名搜索过滤(电视剧)", "clearable": True}}]},
            ]},
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VSelect", "props": {"model": "selected_tv_groups", "label": "电视剧目录勾选（多选）", "items": self._to_group_items(self._pending_tv_groups, self._tv_keyword), "multiple": True, "chips": True}}]},
            ]},
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VSelect", "props": {"model": "selected_tv_paths", "label": "电视剧文件勾选（多选）", "items": self._to_items(self._pending_tv_files, self._tv_keyword), "multiple": True, "chips": True}}]},
            ]},
        ]}], {
            "enabled": False, "confirm_mode": True, "notify": True, "onlyonce": False,
            "cron": "0 3 * * *", "days": 30, "size_threshold_mb": 0,
            "movie_hardlink": "", "movie_source": "", "movie_target": "",
            "tv_hardlink": "", "tv_source": "", "tv_target": "",
            "delete_local": True, "delete_qb": True,
            "selected_mode": True, "series_group_mode": True, "run_selected_once": False,
            "risk_control_enabled": True, "max_items_per_run": 1, "item_interval_sec": 2,
            "clear_logs_once": False,
            "movie_keyword": "", "tv_keyword": "",
            "selected_movie_paths": [], "selected_tv_paths": [],
            "selected_movie_groups": [], "selected_tv_groups": [],
        }

    def get_page(self) -> Optional[List[dict]]:
        movie_mb = sum(x.get("size_mb", 0) for x in self._pending_movie_files)
        tv_mb = sum(x.get("size_mb", 0) for x in self._pending_tv_files)
        total_mb = movie_mb + tv_mb
        last = self._last_transfer_result or {}
        logs = self._archive_logs or []
        latest_logs = logs[-5:]
        lines = []
        for r in reversed(latest_logs):
            lines.append(f"[{r.get('time')}] 成功{r.get('success',0)} 失败{r.get('failed',0)} 跳过{r.get('skipped',0)}")
            for d in (r.get('details') or [])[-3:]:
                lines.append(f"  - {d}")
        detail_text = "\n".join(lines) if lines else "暂无归档日志"
        return [{"component": "VCard", "props": {"title": "网盘归档 v2.1.0"}, "content": [{"component": "VCardText", "props": {"text":
            f"电影候选: {len(self._pending_movie_files)} 个（{movie_mb:.0f}MB / {movie_mb/1024:.2f}GB）\n"
            f"电视剧候选: {len(self._pending_tv_files)} 个（{tv_mb:.0f}MB / {tv_mb/1024:.2f}GB）\n"
            f"总计: {total_mb:.0f}MB / {total_mb/1024:.2f}GB\n"
            f"上次扫描: {self._last_scan_time or '从未'}\n"
            f"上次结果: 成功{last.get('success',0)} 失败{last.get('failed',0)} 跳过{last.get('skipped',0)}\n"
            f"最近归档日志:\n{detail_text}"
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

        self._movie_hardlink = str(config.get("movie_hardlink", "") or "").strip()
        self._movie_source = str(config.get("movie_source", "") or "").strip()
        self._movie_target = str(config.get("movie_target", "") or "").strip()
        self._tv_hardlink = str(config.get("tv_hardlink", "") or "").strip()
        self._tv_source = str(config.get("tv_source", "") or "").strip()
        self._tv_target = str(config.get("tv_target", "") or "").strip()

        self._delete_qb = config.get("delete_qb", True)
        self._delete_local = config.get("delete_local", True)
        self._size_threshold_mb = float(config.get("size_threshold_mb", 0) or 0)
        self._selected_mode = config.get("selected_mode", True)
        self._series_group_mode = config.get("series_group_mode", True)
        self._risk_control_enabled = config.get("risk_control_enabled", True)
        self._max_items_per_run = max(1, int(config.get("max_items_per_run", 1) or 1))
        self._item_interval_sec = max(0, int(config.get("item_interval_sec", 2) or 0))
        self._run_selected_once = config.get("run_selected_once", False)
        self._clear_logs_once = config.get("clear_logs_once", False)

        self._movie_keyword = str(config.get("movie_keyword", "") or "").strip()
        self._tv_keyword = str(config.get("tv_keyword", "") or "").strip()

        self._selected_movie_paths = config.get("selected_movie_paths", []) or []
        self._selected_tv_paths = config.get("selected_tv_paths", []) or []
        self._selected_movie_groups = config.get("selected_movie_groups", []) or []
        self._selected_tv_groups = config.get("selected_tv_groups", []) or []

        self._pending_movie_files = self.get_data("pending_movie_files") or []
        self._pending_tv_files = self.get_data("pending_tv_files") or []
        self._pending_movie_groups = self.get_data("pending_movie_groups") or []
        self._pending_tv_groups = self.get_data("pending_tv_groups") or []
        self._last_scan_time = self.get_data("last_scan_time")
        self._last_transfer_result = self.get_data("last_transfer_result")
        self._archive_logs = self.get_data("archive_logs") or []

        if self._clear_logs_once:
            self._archive_logs = []
            self.save_data("archive_logs", self._archive_logs)
            config["clear_logs_once"] = False
            self.update_config(config=config)

        self.stop_service()
        if not self._enabled and not self._onlyonce and not self._run_selected_once:
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

    def _scan_one_category(self, media_type: str, hard_root_raw: str, src_root_raw: str, cn_label: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        # 优先硬链接目录；若未配置或不存在则回退源目录
        hard_root = Path(hard_root_raw) if hard_root_raw else None
        src_root = Path(src_root_raw) if src_root_raw else None
        root = hard_root if hard_root and hard_root.exists() else src_root
        scan_kind = "hardlink" if root and hard_root and root == hard_root else "source"
        if not root or not root.exists():
            return [], []

        cutoff = time.time() - self._days * 86400
        pending = []
        groups: Dict[str, Dict[str, Any]] = {}

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
            group_part = rel.parts[0] if len(rel.parts) > 1 else fp.name
            group = f"{cn_label}/{group_part}"
            pending.append({
                "media_type": media_type,
                "name": fp.name,
                "path": str(fp),
                "size_bytes": st.st_size,
                "size_mb": round(size_mb, 2),
                "age_days": int((time.time() - st.st_mtime) / 86400),
                "mtime": st.st_mtime,
                "hard_root": str(hard_root) if hard_root else "",
                "source_root": str(src_root) if src_root else "",
                "scan_root": str(root),
                "scan_kind": scan_kind,
                "group": group,
                "rel_path": str(rel),
            })

        pending.sort(key=lambda x: x["mtime"])
        for p in pending:
            g = p["group"]
            if g not in groups:
                groups[g] = {"group": g, "count": 0, "size_mb": 0.0}
            groups[g]["count"] += 1
            groups[g]["size_mb"] += p["size_mb"]

        return pending, sorted(groups.values(), key=lambda x: x["group"])

    def _do_scan(self):
        self._pending_movie_files, self._pending_movie_groups = self._scan_one_category("movie", self._movie_hardlink, self._movie_source, "电影")
        self._pending_tv_files, self._pending_tv_groups = self._scan_one_category("tv", self._tv_hardlink, self._tv_source, "电视剧")

        valid_movie_paths = {x["path"] for x in self._pending_movie_files}
        valid_tv_paths = {x["path"] for x in self._pending_tv_files}
        valid_movie_groups = {x["group"] for x in self._pending_movie_groups}
        valid_tv_groups = {x["group"] for x in self._pending_tv_groups}

        self._selected_movie_paths = [x for x in self._selected_movie_paths if x in valid_movie_paths]
        self._selected_tv_paths = [x for x in self._selected_tv_paths if x in valid_tv_paths]
        self._selected_movie_groups = [x for x in self._selected_movie_groups if x in valid_movie_groups]
        self._selected_tv_groups = [x for x in self._selected_tv_groups if x in valid_tv_groups]

        self._last_scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.save_data("pending_movie_files", self._pending_movie_files)
        self.save_data("pending_tv_files", self._pending_tv_files)
        self.save_data("pending_movie_groups", self._pending_movie_groups)
        self.save_data("pending_tv_groups", self._pending_tv_groups)
        self.save_data("last_scan_time", self._last_scan_time)

    def _do_scan_only(self):
        self._do_scan()
        if self._notify:
            total = self._pending_movie_files + self._pending_tv_files
            total_mb = sum(x.get("size_mb", 0) for x in total)
            self._send_notification("📦 扫描完成", f"候选文件: {len(total)} 个，约 {total_mb:.0f}MB（约 {total_mb/1024:.2f}GB）。")

    def _scheduled_job(self):
        self._do_scan()
        if self._confirm_mode:
            if self._notify:
                total = self._pending_movie_files + self._pending_tv_files
                if total:
                    total_mb = sum(x.get("size_mb", 0) for x in total)
                    self._send_notification("📦 发现可归档文件", f"共 {len(total)} 个，约 {total_mb:.0f}MB（约 {total_mb/1024:.2f}GB）。")
            return
        self._do_transfer(selected_only=self._selected_mode)

    def _run_selected_now(self):
        self._do_scan()
        self._do_transfer(selected_only=self._selected_mode)

    def _pick_candidates(self, selected_only: bool) -> List[Dict[str, Any]]:
        rows = self._pending_movie_files + self._pending_tv_files
        if not selected_only:
            return rows

        smp = set(self._selected_movie_paths or [])
        stp = set(self._selected_tv_paths or [])
        smg = set(self._selected_movie_groups or []) if self._series_group_mode else set()
        stg = set(self._selected_tv_groups or []) if self._series_group_mode else set()

        picked = []
        for x in rows:
            if x["media_type"] == "movie":
                if x["path"] in smp or x["group"] in smg:
                    picked.append(x)
            else:
                if x["path"] in stp or x["group"] in stg:
                    picked.append(x)
        return picked

    def _roots_for_item(self, item: Dict[str, Any]) -> Tuple[Optional[Path], Optional[Path], Optional[Path], Optional[Path]]:
        mt = item.get("media_type")
        if mt == "movie":
            hard = Path(self._movie_hardlink) if self._movie_hardlink else None
            src = Path(self._movie_source) if self._movie_source else None
            dst = Path(self._movie_target) if self._movie_target else None
        else:
            hard = Path(self._tv_hardlink) if self._tv_hardlink else None
            src = Path(self._tv_source) if self._tv_source else None
            dst = Path(self._tv_target) if self._tv_target else None
        scan_root = Path(item.get("scan_root")) if item.get("scan_root") else (hard if hard and hard.exists() else src)
        return hard, src, dst, scan_root

    def _cleanup_empty_parents(self, start: Path, stop_root: Optional[Path]):
        if not stop_root:
            return
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
        if self._risk_control_enabled:
            candidates = candidates[: self._max_items_per_run]
        if not candidates:
            return

        success = failed = skipped = 0
        details = []

        for idx, item in enumerate(candidates, start=1):
            src = Path(item["path"])
            hard_root, source_root, target_root, scan_root = self._roots_for_item(item)

            if not src.exists():
                skipped += 1
                details.append(f"⏭️ {item.get('name')} | 文件已不存在")
                continue
            if not target_root:
                failed += 1
                msg = "未配置目标目录"
                details.append(f"❌ {item.get('name')} | {msg}")
                logger.error(f"[CloudArchive] {msg}: {item}")
                continue

            rel = Path(item.get("rel_path", src.name))
            dst = target_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)

            try:
                shutil.move(str(src), str(dst))
                if not dst.exists() or dst.stat().st_size != item["size_bytes"]:
                    raise RuntimeError("目标校验失败")

                # 删除另一侧对应文件：无论扫描来源是硬链接还是源目录，都尽量清理两侧
                if self._delete_local:
                    if hard_root:
                        hard_file = hard_root / rel
                        if hard_file.exists() and hard_file.is_file():
                            hard_file.unlink(missing_ok=True)
                            self._cleanup_empty_parents(hard_file.parent, hard_root)
                    if source_root:
                        source_file = source_root / rel
                        if source_file.exists() and source_file.is_file():
                            source_file.unlink(missing_ok=True)
                            self._cleanup_empty_parents(source_file.parent, source_root)
                    # 扫描根也做一遍兜底清理
                    if scan_root:
                        self._cleanup_empty_parents(src.parent, scan_root)

                if self._delete_qb:
                    self._remove_qb_torrent(item)

                success += 1
                details.append(
                    f"✅ {item.get('group','')} / {item.get('name')} | {item.get('size_mb',0):.2f}MB/{item.get('size_mb',0)/1024:.2f}GB | {src} -> {dst}"
                )
            except Exception as e:
                failed += 1
                details.append(f"❌ {item.get('group','')} / {item.get('name')} | 错误: {e}")
                logger.error(f"[CloudArchive] 归档失败 {src}: {e}")

            if self._risk_control_enabled and self._item_interval_sec > 0 and idx < len(candidates):
                time.sleep(self._item_interval_sec)

        self._last_transfer_result = {
            "success": success,
            "failed": failed,
            "skipped": skipped,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "detail": "\n".join(details[-30:]),
        }

        # 归档日志沉淀（设置页可看/可清）
        self._archive_logs = (self._archive_logs or []) + [{
            "time": self._last_transfer_result["time"],
            "success": success,
            "failed": failed,
            "skipped": skipped,
            "details": details[-100:],
        }]
        self._archive_logs = self._archive_logs[-200:]

        self._do_scan()  # 刷新列表
        self.save_data("last_transfer_result", self._last_transfer_result)
        self.save_data("archive_logs", self._archive_logs)

        if self._notify:
            self._send_notification(
                "📦 网盘归档完成",
                f"成功: {success} 失败: {failed} 跳过: {skipped}\n" + "\n".join(details[-10:])
            )

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
