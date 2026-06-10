#!/usr/bin/env python3
"""从 Logseq OG (12316) 迁移页面到 Aria DB (12315)。

用法:
  python3 migrate_og_to_aria.py [--limit N] [--skip-journals] [--dry-run]
"""
import json
import sys
import time
import urllib.request
from typing import Any

OG_URL = "http://127.0.0.1:12316/api"
ARIA_URL = "http://127.0.0.1:12315/api"
TOKEN = "token"
HEADERS = {"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"}


def api_call(url: str, method: str, args: list = None, timeout: int = 15) -> Any:
    payload = json.dumps({"method": method, "args": args or []}).encode()
    req = urllib.request.Request(url, data=payload, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"__error": str(e)}


def get_all_og_pages() -> list:
    return api_call(OG_URL, "logseq.editor.getAllPages")


def get_page_blocks(page_name: str) -> list:
    return api_call(OG_URL, "logseq.editor.getPageBlocksTree", [page_name])


def blocks_to_content(blocks: list, indent: int = 0) -> str:
    """将 block tree 转为 Logseq markdown 格式的文本（每个 block 是一个 list item）。"""
    lines = []
    for b in blocks:
        content = b.get("content", "").strip()
        if not content:
            continue
        # 保持 list item 格式
        prefix = "  " * indent + "- " if indent > 0 else "- "
        # 多行 content 需要缩进对齐
        content_lines = content.split("\n")
        lines.append(prefix + content_lines[0])
        for cl in content_lines[1:]:
            lines.append("  " * indent + "  " + cl)
        # 递归处理子 blocks
        if b.get("children"):
            lines.append(blocks_to_content(b["children"], indent + 1))
    return "\n".join(lines)


def flatten_blocks_for_graphthulhu(blocks: list) -> list:
    """将 block tree 转为 graphthulhu create_page 的 blocks 列表（纯字符串）。
    
    graphthulhu 的 create_page blocks 是顶层平铺的字符串列表。
    对于有子 block 的情况，用 upsert_blocks 更合适，但 create_page 先用简单方式。
    """
    result = []
    for b in blocks:
        content = b.get("content", "").strip()
        if content:
            result.append(content)
        if b.get("children"):
            for child in b["children"]:
                child_content = child.get("content", "").strip()
                if child_content:
                    # 子 block 缩进表示层级
                    result.append("  " + child_content)
                if child.get("children"):
                    for grandchild in child["children"]:
                        gc_content = grandchild.get("content", "").strip()
                        if gc_content:
                            result.append("    " + gc_content)
    return result


def create_page_in_aria(name: str, properties: dict, blocks: list) -> dict:
    """通过 Logseq API 在 Aria 中创建页面并写入 blocks。"""
    # 先创建页面
    result = api_call(ARIA_URL, "logseq.editor.createPage", 
                      [name, properties or {}, {"createFirstBlock": False}],
                      timeout=10)
    if isinstance(result, dict) and "__error" in result:
        return result
    
    # 写入 blocks（逐个追加）
    blocks_written = 0
    for block_content in blocks[:50]:  # 限制每页最多 50 个 block 避免超时
        if not block_content.strip():
            continue
        r = api_call(ARIA_URL, "logseq.editor.appendBlockInPage",
                     [name, block_content], timeout=10)
        if isinstance(r, dict) and "__error" in r:
            break
        blocks_written += 1
        time.sleep(0.1)  # 避免 rate limit
    
    return {"name": name, "blocks_written": blocks_written}


def migrate(limit: int = 10, skip_journals: bool = True, dry_run: bool = False):
    print(f"[migrate] Fetching OG pages...")
    pages = get_all_og_pages()
    if isinstance(pages, dict) and "__error" in pages:
        print(f"[error] Cannot connect to OG: {pages['__error']}")
        return

    # 过滤
    if skip_journals:
        pages = [p for p in pages if not p.get("journal?", False)]

    # 过滤掉明显的垃圾页面（HTML 片段、颜色代码等）
    pages = [p for p in pages if not p.get("originalName", "").startswith(("8B0000", "DDA0DD", "FFCCCC", "E6B422", "87CEEB", "<"))]
    
    print(f"[migrate] {len(pages)} non-journal pages (after filter)")
    
    if limit:
        pages = pages[:limit]
    
    print(f"[migrate] Migrating {len(pages)} pages {'(dry-run)' if dry_run else ''}")
    
    success = 0
    errors = 0
    
    for i, page in enumerate(pages):
        name = page.get("originalName", page.get("name", ""))
        if not name:
            continue
        
        # 读取 block tree
        blocks = get_page_blocks(name)
        if isinstance(blocks, dict) and "__error" in blocks:
            print(f"  [{i+1}] SKIP {name[:40]} (read error)")
            errors += 1
            continue
        
        # 提取 properties
        properties = page.get("properties", {})
        properties["source"] = "logseq-og"
        
        # 转换 blocks
        flat_blocks = flatten_blocks_for_graphthulhu(blocks if isinstance(blocks, list) else [])
        
        if dry_run:
            print(f"  [{i+1}] DRY {name[:40]} ({len(flat_blocks)} blocks)")
            success += 1
            continue
        
        # 写入 Aria
        result = create_page_in_aria(name, properties, flat_blocks)
        if isinstance(result, dict) and "__error" in result:
            print(f"  [{i+1}] ERR {name[:40]}: {result['__error'][:50]}")
            errors += 1
        else:
            blocks_written = result.get("blocks_written", 0)
            print(f"  [{i+1}] OK  {name[:40]} ({blocks_written} blocks)")
            success += 1
        
        time.sleep(0.3)  # 写入间隔
    
    print(f"\n[done] success={success}, errors={errors}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--skip-journals", action="store_true", default=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    migrate(limit=args.limit, skip_journals=args.skip_journals, dry_run=args.dry_run)
