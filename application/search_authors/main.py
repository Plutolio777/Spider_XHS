# encoding: utf-8
"""
Search Authors CLI 入口

用法:
    python -m application.search_authors.main --keywords 独立游戏,游戏开发 --target 1000
    python -m application.search_authors.main --keywords 独立游戏 --target 1000 --resume
    python -m application.search_authors.main --keywords 榴莲 --target 500 --sort-order 0,1,4
"""
import argparse
import sys

from loguru import logger

from .core import SearchAuthorsCollector


def parse_args():
    parser = argparse.ArgumentParser(
        description="搜索小红书笔记 → 获取作者信息",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --keywords 独立游戏,游戏开发 --target 1000
  %(prog)s --keywords 榴莲 --target 500 --sort-order 0,1,4 --resume
        """,
    )
    parser.add_argument(
        "--keywords", "-k",
        required=True,
        help="搜索关键词，多个用逗号分隔，如: 独立游戏,游戏开发",
    )
    parser.add_argument(
        "--target", "-t",
        type=int,
        default=1000,
        help="目标作者数量 (默认: 1000)",
    )
    parser.add_argument(
        "--sort-order", "-s",
        help="排序轮转顺序，逗号分隔，0综合 1最新 2最多点赞 3最多评论 4最多收藏 (默认: 0,1,2,3,4)",
        default="0,1,2,3,4",
    )
    parser.add_argument(
        "--resume", "-r",
        action="store_true",
        help="从上次断点恢复",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # 配置 loguru
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:7}</level> | {message}",
        level="INFO",
    )

    keywords = [kw.strip() for kw in args.keywords.split(",") if kw.strip()]
    if not keywords:
        logger.error("未提供有效关键词")
        sys.exit(1)

    sort_values = []
    for s in args.sort_order.split(","):
        s = s.strip()
        if not s:
            continue
        try:
            sort_values.append(int(s))
        except ValueError:
            logger.error(f"无效排序值: '{s}'，排序值必须是数字，0~4")
            sys.exit(1)
    sort_order = sort_values

    # 验证排序参数
    for s in sort_order:
        if s not in [0, 1, 2, 3, 4]:
            logger.error(f"无效排序值: {s}，有效值为 0~4")
            sys.exit(1)

    if args.target <= 0:
        logger.error("目标数量必须大于 0")
        sys.exit(1)

    collector = SearchAuthorsCollector(
        keywords=keywords,
        target=args.target,
        sort_order=sort_order,
        resume=args.resume,
    )
    try:
        collector.run()
    except Exception as e:
        logger.exception(f"运行异常: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
