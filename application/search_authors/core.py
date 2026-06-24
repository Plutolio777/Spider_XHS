# encoding: utf-8
"""
Search Authors 采集器核心逻辑
"""
import json
import os
import time
import random
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from openpyxl import Workbook

from apis.xhs_pc_apis import XHS_Apis
from xhs_utils.common_util import load_env

# 排序名称映射
SORT_TYPE_NAMES = {0: "综合排序", 1: "最新", 2: "最多点赞", 3: "最多评论", 4: "最多收藏"}

# 笔记类型值
NOTE_TYPE_ALL = 0
NOTE_TYPE_VIDEO = 1
NOTE_TYPE_IMAGE = 2


class SearchAuthorsCollector:
    """关键词搜索笔记 → 获取作者信息 采集器"""

    def __init__(self, keywords, target=1000, sort_order=None, resume=False):
        self.keywords = keywords if isinstance(keywords, list) else [keywords]
        self.target = target
        self.sort_order = sort_order or [0, 1, 2, 3, 4]
        self.resume = resume

        # API
        self.cookies_str = load_env()
        if not self.cookies_str:
            raise ValueError("COOKIES not found in .env")
        self.api = XHS_Apis()

        # 目录
        self.base_dir = Path(__file__).parent
        self.data_dir = self.base_dir / "data"
        self.output_dir = self.base_dir / "output"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 持久化文件路径
        self.checkpoint_path = self.data_dir / "checkpoint.json"
        self.processed_notes_path = self.data_dir / "processed_notes.json"
        self.authors_path = self.data_dir / "authors.jsonl"

        # 运行状态
        self.processed_notes = set()   # 已处理的 note_id
        self.authors = []              # 已收集的作者列表 [{...}]
        self.collected_count = 0       # 已收集作者数
        self.total_notes_processed = 0 # 总处理笔记数
        self.start_time = None

        # checkpoint 当前搜索位置
        self.sort_idx = 0      # 当前排序索引
        self.kw_idx = 0        # 当前关键词索引
        self.page = 1          # 当前页码
        self.current_strategy = "main"  # main / backup_a / backup_b
        self.current_note_type = NOTE_TYPE_ALL
        self.current_year_min = 2026    # 过滤年份下限

        # 控制标记
        self._stop = False
        self._strategy_exhausted = {"main": False, "backup_a": False, "backup_b": False}

        # 注册信号处理
        signal.signal(signal.SIGINT, self._signal_handler)

    # ──────────── 公开入口 ────────────

    def run(self):
        """运行采集器"""
        self.start_time = time.time()
        logger.info(f"=== Search Authors 启动 ===")
        logger.info(f"关键词: {self.keywords}")
        logger.info(f"目标数量: {self.target}")
        logger.info(f"排序轮转: {[SORT_TYPE_NAMES[s] for s in self.sort_order]}")

        # 恢复断点或初始化
        if self.resume and self.checkpoint_path.exists():
            self._load_checkpoint()
            logger.info(f"已从断点恢复: 已收集 {self.collected_count} 个作者")
        else:
            self._init_state()

        # 搜索循环
        self._search_cycle()

        # 输出结果
        self._generate_excel()
        self._print_summary()

    # ──────────── 搜索循环 ────────────

    def _search_cycle(self):
        """主搜索循环：策略 × 排序 × 关键词 × 翻页"""
        strategies = [
            ("main", self._run_main_strategy),
            ("backup_a", self._run_backup_a_strategy),
            ("backup_b", self._run_backup_b_strategy),
        ]

        # 跳过已耗尽的策略
        start_strategy = self.current_strategy
        started = False

        for strategy_name, strategy_func in strategies:
            if not started and strategy_name != start_strategy:
                continue
            started = True

            if self._strategy_exhausted.get(strategy_name):
                logger.info(f"策略 {strategy_name} 已耗尽，跳过")
                continue
            if self.collected_count >= self.target or self._stop:
                break

            logger.info(f"进入策略: {strategy_name}")
            self.current_strategy = strategy_name
            strategy_func()

        if self.collected_count >= self.target:
            logger.info(f"[目标达成] 达到目标数量: {self.target}")
        elif self._stop:
            logger.info("[中断] 用户中断")
        else:
            logger.warning(f"所有策略已耗尽，共收集 {self.collected_count}/{self.target} 个作者")

    def _run_main_strategy(self):
        """主策略：不限笔记类型 × 2026年"""
        self.current_note_type = NOTE_TYPE_ALL
        self.current_year_min = 2026
        self._run_sort_keyword_loop()

    def _run_backup_a_strategy(self):
        """备用A：细分笔记类型 × 2026年"""
        self.current_year_min = 2026
        for note_type in [NOTE_TYPE_VIDEO, NOTE_TYPE_IMAGE]:
            if self.collected_count >= self.target or self._stop:
                break
            if self.resume and note_type < self.current_note_type:
                continue
            self.current_note_type = note_type
            logger.info(f"备用A: 切换笔记类型 {note_type}")
            self._run_sort_keyword_loop()
        self._strategy_exhausted["backup_a"] = True

    def _run_backup_b_strategy(self):
        """备用B：放宽年份到 2025~2026"""
        self.current_year_min = 2025
        for note_type in [NOTE_TYPE_ALL, NOTE_TYPE_VIDEO, NOTE_TYPE_IMAGE]:
            if self.collected_count >= self.target or self._stop:
                break
            if self.resume and note_type < self.current_note_type:
                continue
            self.current_note_type = note_type
            logger.info(f"备用B: 放宽年份至2025, 笔记类型 {note_type}")
            self._run_sort_keyword_loop()

    def _run_sort_keyword_loop(self):
        """排序轮转 × 关键词轮转 × 翻页"""
        for si in range(self.sort_idx, len(self.sort_order)):
            sort_type = self.sort_order[si]
            self.sort_idx = si
            logger.info(f"  排序: {SORT_TYPE_NAMES[sort_type]}")

            for ki in range(self.kw_idx, len(self.keywords)):
                keyword = self.keywords[ki]
                self.kw_idx = ki
                self.page = 1
                logger.info(f"    关键词: {keyword}")

                while True:
                    if self.collected_count >= self.target or self._stop:
                        return

                    logger.info(f"      第 {self.page} 页...")
                    try:
                        success, msg, result = self._search_with_retry(keyword, sort_type)
                    except Exception as e:
                        logger.error(f"      搜索异常: {e}")
                        break

                    if not success:
                        logger.warning(f"      搜索失败: {msg}")
                        self._random_sleep(10, 15)
                        break

                    items = result.get("data", {}).get("items", [])
                    if not items:
                        logger.info(f"      无结果")
                        break

                    self._process_page(items, keyword, sort_type)

                    has_more = result.get("data", {}).get("has_more", False)
                    if not has_more:
                        logger.info(f"      已翻完")
                        break

                    self.page += 1
                    self._save_checkpoint()
                    self._random_sleep(3, 5)  # 翻页间隔

            # 重置关键词索引（下一个排序从第一个关键词开始）
            self.kw_idx = 0

        # 重置排序索引
        self.sort_idx = 0
        self._strategy_exhausted[self.current_strategy] = True

    # ──────────── 处理页面结果 ────────────

    def _process_page(self, items, keyword, sort_type):
        """处理一页搜索结果"""
        for item in items:
            if self.collected_count >= self.target or self._stop:
                return

            if item.get("model_type") != "note":
                continue

            note_id = item.get("id")
            if not note_id:
                continue

            # 跳过已处理过的笔记
            if note_id in self.processed_notes:
                continue

            # 尝试从搜索结果中提取 user_id
            note_card = item.get("note_card", {})
            user_info = note_card.get("user", {})
            user_id = user_info.get("user_id")
            if not user_id:
                continue

            # 标记已处理
            self.processed_notes.add(note_id)
            self.total_notes_processed += 1

            # 检查作者是否已收集
            if self._is_author_collected(user_id):
                continue

            # 处理新笔记（获取详情→检查年份→获取作者）
            self._process_note(note_id, user_id, keyword, sort_type, item)

    def _process_note(self, note_id, user_id, keyword, sort_type, search_item):
        """处理单条笔记：获取详情 → 检查年份 → 获取作者"""
        # 获取笔记详情
        note_card_search = search_item.get("note_card", {})
        xsec_token = search_item.get("xsec_token", "")

        # 先尝试从搜索结果中提取 display_title 作为标题候选
        display_title = note_card_search.get("display_title", "")

        # 获取笔记详情（获取 time、title、desc、tags）
        note_detail = self._fetch_note_detail(note_id, xsec_token)
        if note_detail is None:
            return

        note_card = note_detail.get("note_card", {})

        # 检查年份
        note_time = note_card.get("time", 0)
        if not note_time:
            return

        dt = datetime.fromtimestamp(note_time / 1000)
        if dt.year < self.current_year_min:
            return

        # 获取作者详细信息
        author_data = self._fetch_user_info(user_id)
        if author_data is None:
            return

        # 提取笔记标签（只保留 name）
        tag_list = note_card.get("tag_list", [])
        note_tags = [t.get("name", "") for t in tag_list if t.get("name")]

        # 构造作者记录
        basic = author_data.get("basic_info", {})
        interactions = author_data.get("interactions", [])
        tags_data = author_data.get("tags", [])
        user_tags = [t.get("name", "") for t in tags_data if t.get("name")]

        author_record = {
            "user_id": user_id,
            "nickname": basic.get("nickname", ""),
            "avatar": basic.get("imageb", ""),
            "red_id": basic.get("red_id", ""),
            "gender": basic.get("gender", ""),
            "ip_location": basic.get("ip_location", ""),
            "desc": basic.get("desc", ""),
            "follows": interactions[0].get("count", 0) if len(interactions) > 0 else 0,
            "fans": interactions[1].get("count", 0) if len(interactions) > 1 else 0,
            "interaction": interactions[2].get("count", 0) if len(interactions) > 2 else 0,
            "user_tags": user_tags,
            "match_note_title": note_card.get("title", display_title),
            "match_note_desc": note_card.get("desc", ""),
            "match_note_tags": note_tags,
            "match_note_id": note_id,
            "match_note_url": f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={xsec_token}",
            "match_keyword": keyword,
            "match_sort_type": SORT_TYPE_NAMES.get(sort_type, str(sort_type)),
            "collected_at": int(time.time() * 1000),
        }

        # 实时保存
        self.authors.append(author_record)
        self.collected_count += 1
        self._append_author_line(author_record)
        self._append_processed_note(note_id)

        # 保存 processed_notes
        self._save_processed_notes()

        logger.info(f"  [{self.collected_count}/{self.target}] {basic.get('nickname', '')} "
                     f"(粉丝: {interactions[1].get('count', 0) if len(interactions) > 1 else 0}) "
                     f"← {keyword}")

        # 每收集 50 个作者额外休息
        if self.collected_count % 50 == 0:
            self._random_sleep(10, 15)

    # ──────────── API 调用（带重试） ────────────

    def _search_with_retry(self, keyword, sort_type):
        """搜索笔记（带重试）"""
        return self._call_with_retry(
            lambda: self.api.search_note(
                query=keyword,
                cookies_str=self.cookies_str,
                page=self.page,
                sort_type_choice=sort_type,
                note_type=self.current_note_type,
                note_time=0,
            )
        )

    def _fetch_note_detail(self, note_id, xsec_token):
        """获取笔记详情"""
        url = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={xsec_token}"
        success, msg, data = self._call_with_retry(
            lambda: self.api.get_note_info(url, self.cookies_str)
        )
        if not success:
            logger.warning(f"    获取笔记详情失败: {note_id}, {msg}")
            return None
        items = data.get("data", {}).get("items", [])
        if not items:
            return None
        return items[0]

    def _fetch_user_info(self, user_id):
        """获取用户详细信息"""
        success, msg, data = self._call_with_retry(
            lambda: self.api.get_user_info(user_id, self.cookies_str)
        )
        if not success:
            logger.warning(f"    获取用户信息失败: {user_id}, {msg}")
            return None
        return data.get("data", {})

    def _call_with_retry(self, func, max_retries=3, base_delay=5):
        """带重试和延迟的 API 调用"""
        last_exc = None
        for attempt in range(1, max_retries + 1):
            try:
                self._random_sleep(2, 4)
                result = func()
                if result and len(result) >= 1 and result[0]:
                    return result
                # success=False
                if attempt < max_retries:
                    logger.debug(f"    API 返回失败, {attempt}/{max_retries}, 等待后重试")
                    self._random_sleep(25, 35)
                    continue
                return result
            except Exception as e:
                last_exc = e
                if attempt < max_retries:
                    logger.debug(f"    API 异常: {e}, {attempt}/{max_retries}, 重试")
                    self._random_sleep(base_delay, base_delay + 5)
                    continue
                raise last_exc

    # ──────────── 状态管理 ────────────

    def _init_state(self):
        """初始化新状态"""
        self.processed_notes = set()
        self.authors = []
        self.collected_count = 0
        self.total_notes_processed = 0
        self.sort_idx = 0
        self.kw_idx = 0
        self.page = 1
        self.current_strategy = "main"
        self.current_note_type = NOTE_TYPE_ALL
        self.current_year_min = 2026
        self._strategy_exhausted = {"main": False, "backup_a": False, "backup_b": False}

        # 创建空状态文件
        self._save_processed_notes()
        self._save_checkpoint()
        # authors.jsonl 留空（不创建，由追加写入触发创建）

    def _load_checkpoint(self):
        """从文件恢复状态"""
        # 恢复 checkpoint
        if self.checkpoint_path.exists():
            with open(self.checkpoint_path, "r", encoding="utf-8") as f:
                cp = json.load(f)
            self.sort_idx = cp.get("sort_idx", 0)
            self.kw_idx = cp.get("kw_idx", 0)
            self.page = cp.get("page", 1)
            self.current_strategy = cp.get("current_strategy", "main")
            self.current_note_type = cp.get("current_note_type", NOTE_TYPE_ALL)
            self.current_year_min = cp.get("current_year_min", 2026)
            self.collected_count = cp.get("collected_count", 0)
            self.total_notes_processed = cp.get("total_notes_processed", 0)
            exhausted = cp.get("strategy_exhausted", {})
            for k in ["main", "backup_a", "backup_b"]:
                self._strategy_exhausted[k] = exhausted.get(k, False)

        # 恢复已处理的笔记
        if self.processed_notes_path.exists():
            with open(self.processed_notes_path, "r", encoding="utf-8") as f:
                self.processed_notes = set(json.load(f))

        # 恢复已收集的作者
        if self.authors_path.exists():
            self.authors = []
            with open(self.authors_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self.authors.append(json.loads(line))

    def _save_checkpoint(self):
        """保存断点"""
        cp = {
            "sort_idx": self.sort_idx,
            "kw_idx": self.kw_idx,
            "page": self.page,
            "current_strategy": self.current_strategy,
            "current_note_type": self.current_note_type,
            "current_year_min": self.current_year_min,
            "collected_count": self.collected_count,
            "total_notes_processed": self.total_notes_processed,
            "strategy_exhausted": self._strategy_exhausted,
            "updated_at": int(time.time() * 1000),
        }
        with open(self.checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(cp, f, ensure_ascii=False, indent=2)

    def _save_processed_notes(self):
        """保存已处理笔记列表"""
        with open(self.processed_notes_path, "w", encoding="utf-8") as f:
            json.dump(list(self.processed_notes), f, ensure_ascii=False)

    def _append_author_line(self, record):
        """追加一行作者数据"""
        with open(self.authors_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _append_processed_note(self, note_id):
        """追加已处理笔记（实时增量保存）"""
        # 也追加到文件末尾（方便实时查看进度）
        # processed_notes 由 _save_processed_notes 周期性全量保存

    # ──────────── 辅助方法 ────────────

    def _is_author_collected(self, user_id):
        """检查作者是否已收集"""
        for author in self.authors:
            if author.get("user_id") == user_id:
                return True
        return False

    def _random_sleep(self, min_s, max_s):
        """随机延迟"""
        delay = random.uniform(min_s, max_s)
        time.sleep(delay)

    def _signal_handler(self, signum, frame):
        """信号处理（Ctrl+C）"""
        if self._stop:
            logger.warning("再次按 Ctrl+C 强制退出")
            sys.exit(1)
        logger.info("接收到中断信号，正在保存状态...")
        self._save_checkpoint()
        self._save_processed_notes()
        self._stop = True

    # ──────────── 输出 ────────────

    def _generate_excel(self):
        """生成 Excel 输出"""
        if not self.authors:
            logger.warning("无数据，不生成 Excel")
            return

        today_str = datetime.now().strftime("%Y-%m-%d")
        output_path = self.output_dir / f"search_authors_{today_str}.xlsx"

        wb = Workbook()
        ws = wb.active
        ws.title = "作者列表"

        headers = [
            "用户ID", "昵称", "小红书号", "性别", "IP属地", "简介",
            "关注数", "粉丝数", "获赞收藏数", "用户标签",
            "匹配笔记标题", "匹配笔记正文", "匹配笔记标签",
            "匹配笔记ID", "匹配笔记链接", "触发关键词", "触发排序", "采集时间"
        ]
        ws.append(headers)

        for author in self.authors:
            gender_str = "男" if author.get("gender") == 0 else ("女" if author.get("gender") == 1 else "未知")
            row = [
                author.get("user_id", ""),
                author.get("nickname", ""),
                author.get("red_id", ""),
                gender_str,
                author.get("ip_location", ""),
                author.get("desc", ""),
                author.get("follows", 0),
                author.get("fans", 0),
                author.get("interaction", 0),
                ", ".join(author.get("user_tags", [])),
                author.get("match_note_title", ""),
                author.get("match_note_desc", ""),
                ", ".join(author.get("match_note_tags", [])),
                author.get("match_note_id", ""),
                author.get("match_note_url", ""),
                author.get("match_keyword", ""),
                author.get("match_sort_type", ""),
                author.get("collected_at", ""),
            ]
            ws.append(row)

        wb.save(str(output_path))
        logger.info(f"Excel 已保存: {output_path}")

    def _print_summary(self):
        """打印汇总"""
        elapsed = time.time() - self.start_time
        logger.info("=" * 50)
        logger.info("采集完成汇总")
        logger.info(f"  收集作者: {self.collected_count}/{self.target}")
        logger.info(f"  处理笔记: {self.total_notes_processed}")
        logger.info(f"  耗时: {elapsed:.0f}秒 ({elapsed/60:.1f}分钟)")
        logger.info(f"  数据目录: {self.data_dir}")
        logger.info(f"  输出文件: {self.output_dir}")
        logger.info("=" * 50)
