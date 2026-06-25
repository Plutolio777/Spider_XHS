# encoding: utf-8
"""
Search Authors 默认配置

直接运行 python -m application.search_authors.main 即可使用这些默认值
命令行参数可覆盖这些默认值
"""

# 搜索关键词（逗号分隔）
KEYWORDS = "我在小红书做游戏,独立游戏,独立游戏制作"

# 目标作者数量
TARGET = 1000

# 排序轮转顺序（0综合 1最新 2最多点赞 3最多评论 4最多收藏）
SORT_ORDER = "4,3,2,1,0"

# 是否断点恢复（默认否，首次运行设为 False）
RESUME = True
