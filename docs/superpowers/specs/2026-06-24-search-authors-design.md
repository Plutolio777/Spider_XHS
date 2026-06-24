# 关键词搜索笔记 → 获取作者信息 设计文档

## 概述

构建 `application` 包下的第一个应用层小脚本：通过给定关键词搜索小红书笔记，提取发布这些笔记的完整作者信息，支持多层搜索策略、实时保存/断点恢复、风控规避。

## 文件结构

```
application/
├── __init__.py
└── search_authors/
    ├── __init__.py
    ├── main.py                 # 入口：命令行参数 + 启动搜索
    ├── core.py                 # 核心逻辑：搜索循环、策略调度、去重
    ├── data/                   # 运行态数据（实时保存，用于断点恢复）
    │   ├── checkpoint.json     # 当前位置：搜到哪个关键词/排序/页了
    │   ├── processed_notes.json  # 已处理过的 note_id 集合
    │   └── authors.jsonl       # 已收集的作者（每行一个 JSON 对象，追加写入）
    └── output/                 # 最终导出
        └── search_authors_2026-06-24.xlsx
```

## 核心流程

```
启动 → 检查 data/ 目录 → 恢复/新建
  → 进入搜索循环：
    外层：排序轮转 [综合, 最新, 最多点赞, 最多评论, 最多收藏]
    中层：关键词轮转（遍历用户给定的所有关键词）
    内层：翻页搜索（page=1,2,3... 直到该关键词+该排序无新结果）
  → 每页结果处理：
    过滤 model_type="note" 的条目
    提取 user_id
    ┌─ 已处理过此 note_id？→ 跳过
    └─ 新 note_id：
       ├─ 作者已收集？→ 跳过
       └─ 新作者？
          ├─ get_note_info() → 确认 2026 年发布
          │   ├─ 否 → 标记已处理，跳过
          │   └─ 是 2026 年 → get_user_info() → 记录
          │         → authors.jsonl 追加一行
          │         → processed_notes.json 追加
          │         → 终端打印进度
          └─ 达到 1000 个作者？→ 输出 Excel → 结束
  → 所有组合耗尽但不足 1000？
    进入备用策略 A（细分笔记类型）
    进入备用策略 B（放宽到 2025~2026 年）
    → 仍不足？→ 如实报告
```

## 搜索策略

### 主策略：排序轮转 × 关键词轮转

| 参数 | 值 |
|---|---|
| `sort_type_choice` | 依次轮转 0→1→2→3→4（综合→最新→最多点赞→最多评论→最多收藏） |
| `note_type` | 0（不限） |
| `note_time` | 0（不限，客户端过滤 2026 年） |
| 翻页 | 每页 20 条，持续翻页直到该关键词+该排序不再出新作者 |

### 备用策略 A：细分笔记类型（主策略耗尽后启用）

| 参数 | 值 |
|---|---|
| `note_type` | 依次切换 1（视频笔记）→ 2（图文笔记） |
| 排序 | 重新轮转 0→1→2→3→4 |
| 关键词 | 重新遍历所有关键词 |

### 备用策略 B：放宽年份（前两层耗尽后启用）

| 参数 | 值 |
|---|---|
| 时间过滤 | 放宽到 2025~2026 年 |
| 排序 | 重新轮转 0→1→2→3→4 |
| 笔记类型 | 重新轮转 0→1→2 |
| 关键词 | 重新遍历所有关键词 |

## 数据模型

```python
# authors.jsonl 中每行是一个 JSON 对象
{
    # 作者信息（来自 get_user_info）
    "user_id": "5ff30a3b0000000001004820",
    "nickname": "Riooooooo",
    "avatar": "https://...",
    "red_id": "1103730034",
    "gender": 0,                    # 0男 1女（注：data_util.py 中 0→男, 1→女）
    "ip_location": "广东",
    "desc": "用户简介文本",
    "follows": "37",
    "fans": "2000",
    "interaction": "12992",
    "user_tags": ["28岁", "广东广州", "诺丁汉大学"],

    # 首次匹配的笔记信息（来自 get_note_info）
    "match_note_title": "下班后用AI做游戏Day40",
    "match_note_desc": "笔记正文内容（完整文本）",
    "match_note_tags": ["游戏开发", "AI", "独立游戏"],  # tag_list 只保留 name
    "match_note_id": "6990a8bf000000000d009fe5",
    "match_note_url": "https://www.xiaohongshu.com/explore/6990a8bf...",

    # 搜索上下文
    "match_keyword": "独立游戏",
    "match_sort_type": "综合排序",

    # 时间戳
    "collected_at": 1771088063000   # 采集时间
}
```

## 断点恢复

| 文件 | 格式 | 更新时机 | 用途 |
|---|---|---|---|
| `checkpoint.json` | JSON | 每完成一页/每次切换参数 | 记录当前搜索位置（关键词索引、排序索引、页码） |
| `processed_notes.json` | JSON 数组 | 每次处理完一条笔记 | 记录已处理的 note_id，避免重复处理 |
| `authors.jsonl` | JSONL（每行追加） | 每收集到一位新作者 | 已收集的作者数据，断点恢复时从文件尾逐行读取 |

### 恢复逻辑

```
启动时检查 data/search_authors/ 目录是否存在
├─ 不存在 → 新建，从头开始
└─ 存在
   ├─ 读取 checkpoint.json → 恢复搜索位置
   ├─ 读取 processed_notes.json → 恢复已处理 set
   ├─ 逐行读取 authors.jsonl → 恢复已收集作者列表
   └─ 继续搜索循环（跳过已处理的笔记）
```

## Excel 输出

文件路径：`output/search_authors_YYYY-MM-DD.xlsx`

| 列名 | 数据源 |
|---|---|
| 用户ID | user_id |
| 昵称 | nickname |
| 小红书号 | red_id |
| 性别 | gender（data_util中0→男/1→女） |
| IP属地 | ip_location |
| 简介 | desc |
| 关注数 | follows |
| 粉丝数 | fans |
| 获赞收藏数 | interaction |
| 用户标签 | user_tags（逗号拼接） |
| 匹配笔记标题 | match_note_title |
| 匹配笔记正文 | match_note_desc |
| 匹配笔记标签 | match_note_tags（逗号拼接） |
| 匹配笔记ID | match_note_id |
| 匹配笔记链接 | match_note_url |
| 触发关键词 | match_keyword |
| 触发排序 | match_sort_type |
| 采集时间 | collected_at |

## 风控策略

### 请求间隔

| 操作 | 间隔 |
|---|---|
| 翻页搜索 | 随机 3~5 秒 |
| get_note_info | 随机 2~4 秒 |
| get_user_info | 随机 2~4 秒 |
| 换关键词/排序 | 随机 5~8 秒 |
| 每收集 50 个作者 | 额外休息 10~15 秒 |

### 失败重试

```
请求失败
├─ 网络/超时错误 → 等待 5s → 重试，最多 3 次
└─ 返回 success=false → 等待 30s → 重试 1 次
   └─ 仍失败 → 跳过并记日志
```

### 优雅退出

捕获 `KeyboardInterrupt`：
- 保存当前 checkpoint
- 从 authors.jsonl 生成 Excel
- 打印汇总（已收集 N 个作者，进度 M%）
- 下次启动自动恢复

## 使用方式

```bash
# 从零开始搜索
python -m application.search_authors.main --keywords 独立游戏,游戏开发 --target 1000

# 断点恢复（自动检测 data/ 目录）
python -m application.search_authors.main --keywords 独立游戏,游戏开发 --target 1000 --resume

# 指定排序轮转顺序
python -m application.search_authors.main --keywords 独立游戏 --target 1000 --sort-order 0,1,4
```

## 依赖

- 项目已有依赖：`requests`, `loguru`, `openpyxl`, `python-dotenv`
- 新增依赖：无（全部复用项目现有 API）
