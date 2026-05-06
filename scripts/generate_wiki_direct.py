#!/usr/bin/env python3
"""
CLI script to generate wiki for a local repository using MiniMax API directly.
Bypasses RAG/embedding step, reads files directly and passes to LLM.

Usage: python generate_wiki_direct.py /path/to/repo [output_dir]
"""

import asyncio
import json
import sys
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from openai import OpenAI

# MiniMax config
MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "sk-cp-askvMC9n1ol7TULF70014hpP4Zzh-EQgXXiC3IeoF7aovuvJP3a5pFgoC-bfTluyn6KvD_wF-rgLBA68LcjAmM5j8Xl-IqUH90aGq46bFEtxgAKbWcB_l60")
MODEL = "MiniMax-M2.7"

client = OpenAI(
    api_key=MINIMAX_API_KEY,
    base_url=MINIMAX_BASE_URL,
)


def llm_call(prompt: str, label: str = "") -> str:
    """Call MiniMax LLM and return the response."""
    print(f"  [LLM] {label}...", end="", flush=True)
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            top_p=0.8,
            max_tokens=8000,
        )
        content = response.choices[0].message.content or ""
        print(f" done ({len(content)} chars)")
        return content
    except Exception as e:
        print(f" ERROR: {e}")
        return f"*Error: {e}*"


def get_file_tree(repo_path: str) -> tuple[str, str]:
    """Walk repo and return (file_tree_str, readme_content)."""
    file_tree_lines = []
    readme_content = ""

    excluded_dirs = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.idea', '.vs', 'vendor'}
    excluded_exts = {'.lock', '.sum', '.mod.sum', '.pb', '.pb.go'}
    excluded_files = {'go.sum', 'yarn.lock', 'package-lock.json', '.DS_Store'}

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in excluded_dirs and not d.startswith('.')]
        for file in sorted(files):
            if file.startswith('.') or file in excluded_files:
                continue
            _, ext = os.path.splitext(file)
            if ext in excluded_exts:
                continue
            rel_dir = os.path.relpath(root, repo_path)
            rel_file = os.path.join(rel_dir, file) if rel_dir != '.' else file
            file_tree_lines.append(rel_file)
            if file.lower() == 'readme.md' and not readme_content:
                try:
                    with open(os.path.join(root, file), 'r', encoding='utf-8', errors='replace') as f:
                        readme_content = f.read()
                except Exception:
                    pass

    return '\n'.join(sorted(file_tree_lines)), readme_content


def read_file(repo_path: str, file_path: str, max_chars: int = 12000) -> str:
    """Read a file, truncate if too large."""
    full = os.path.join(repo_path, file_path)
    if not os.path.exists(full):
        return f"[File not found: {file_path}]"
    try:
        with open(full, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        if len(content) > max_chars:
            content = content[:max_chars] + "\n... [文件过长，已截断]"
        return content
    except Exception as e:
        return f"[无法读取文件: {e}]"


def parse_wiki_structure(xml_text: str) -> dict | None:
    """Parse the XML wiki structure returned by LLM."""
    xml_text = re.sub(r"```(?:xml)?\s*", "", xml_text)
    xml_text = re.sub(r"```\s*$", "", xml_text).strip()

    match = re.search(r"<wiki_structure>.*?</wiki_structure>", xml_text, re.DOTALL)
    if match:
        xml_text = match.group(0)
    else:
        return None

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"Warning: XML parse error: {e}")
        print(f"Response snippet: {xml_text[:300]}")
        return None

    title = root.findtext("title", "Wiki")
    description = root.findtext("description", "")

    pages = []
    for page_el in root.findall(".//pages/page"):
        page_id = page_el.get("id", f"page-{len(pages)+1}")
        page_title = page_el.findtext("title", f"Page {len(pages)+1}")
        page_desc = page_el.findtext("description", "")
        importance = page_el.findtext("importance", "medium")

        file_paths = [fp.text.strip() for fp in page_el.findall(".//relevant_files/file_path") if fp.text]
        related_pages = [r.text.strip() for r in page_el.findall(".//related_pages/related") if r.text]

        pages.append({
            "id": page_id,
            "title": page_title,
            "description": page_desc,
            "importance": importance,
            "filePaths": file_paths,
            "relatedPages": related_pages,
        })

    return {"title": title, "description": description, "pages": pages}


def build_page_prompt(page: dict, repo_path: str, repo_name: str) -> str:
    """Build a page generation prompt with actual file contents."""
    file_sections = []
    for fp in page["filePaths"][:8]:
        content = read_file(repo_path, fp)
        file_sections.append(f"### 📄 {fp}\n```\n{content}\n```")

    files_text = "\n\n".join(file_sections) if file_sections else "（无具体文件）"

    return f"""你是一位技术文档专家。请根据以下源代码文件，为 "{repo_name}" 项目生成一个 Wiki 页面，标题为「{page['title']}」。

## 页面说明
{page['description']}

## 源代码文件
{files_text}

## 输出要求
1. 使用 Markdown 格式，标题层级从 ## 开始
2. 内容必须完全基于上方源代码，不要臆测
3. 在适当位置用 Mermaid 图（flowchart、classDiagram、sequenceDiagram 等）可视化架构/流程
4. 关键代码片段用代码块展示（注明语言）
5. 引用来源格式：`Sources: [文件名:行号]()`
6. 结构：概述 → 详细说明 → 架构图/流程图 → 代码示例 → 小结
7. **用中文输出**，专业术语可保留英文
8. 长度：800~2000字

请直接输出 Markdown 内容，不需要任何前言。"""


def generate_wiki(repo_path: str, output_dir: str):
    """Main wiki generation flow."""
    repo_path = os.path.abspath(repo_path)
    repo_name = os.path.basename(repo_path)

    print(f"\n{'='*60}")
    print(f"生成 Wiki: {repo_path}")
    print(f"模型: {MODEL} @ {MINIMAX_BASE_URL}")
    print(f"输出: {output_dir}")
    print(f"{'='*60}\n")

    # Step 1: Get file tree
    print("Step 1: 扫描项目结构...")
    file_tree, readme = get_file_tree(repo_path)
    print(f"  文件数: {len(file_tree.splitlines())}")
    print(f"  README: {len(readme)} 字符\n")

    # Step 2: Generate wiki structure
    structure_prompt = f"""你是一位技术文档专家，请分析以下项目并为其设计 Wiki 目录结构。

## 项目文件树
<file_tree>
{file_tree}
</file_tree>

## README
<readme>
{readme[:3000]}
</readme>

## 任务
为这个项目设计 6-8 个 Wiki 页面，覆盖：架构、核心模块、API/接口、数据模型、部署配置等关键方面。

## 输出格式
**严格按照以下 XML 格式输出，不要添加任何其他内容，不要用代码块包裹：**

<wiki_structure>
  <title>项目名称 Wiki</title>
  <description>项目简短描述</description>
  <pages>
    <page id="page-1">
      <title>页面标题</title>
      <description>此页面涵盖的内容描述</description>
      <importance>high</importance>
      <relevant_files>
        <file_path>相关文件路径（相对于项目根目录）</file_path>
      </relevant_files>
      <related_pages>
        <related>page-2</related>
      </related_pages>
    </page>
  </pages>
</wiki_structure>

直接输出 XML，从 <wiki_structure> 开始。"""

    print("Step 2: 生成 Wiki 结构...")
    structure_xml = llm_call(structure_prompt, "wiki structure")

    wiki = parse_wiki_structure(structure_xml)
    if not wiki:
        print("ERROR: 无法解析 wiki 结构，原始响应：")
        print(structure_xml[:1000])
        return

    print(f"\nWiki 标题: {wiki['title']}")
    print(f"页面数: {len(wiki['pages'])}")
    for p in wiki['pages']:
        print(f"  [{p['importance']}] {p['title']} ({len(p['filePaths'])} 个文件)")

    # Step 3: Generate each page
    print(f"\nStep 3: 生成 {len(wiki['pages'])} 个页面...")

    for i, page in enumerate(wiki['pages'], 1):
        print(f"\n[{i}/{len(wiki['pages'])}] 正在生成: {page['title']}")
        prompt = build_page_prompt(page, repo_path, repo_name)
        content = llm_call(prompt, page['title'])
        page['content'] = content

    # Step 4: Write output
    os.makedirs(output_dir, exist_ok=True)

    # Combined wiki file
    combined_path = os.path.join(output_dir, "WIKI.md")
    with open(combined_path, 'w', encoding='utf-8') as f:
        f.write(f"# {wiki['title']}\n\n")
        f.write(f"> {wiki['description']}\n\n")
        f.write(f"> 由 deepwiki-open 使用 {MODEL} 自动生成\n\n")
        f.write("---\n\n")

        # Table of contents
        f.write("## 目录\n\n")
        for page in wiki['pages']:
            f.write(f"- [{page['title']}](#{page['id']})\n")
        f.write("\n---\n\n")

        for page in wiki['pages']:
            pid = page['id']
            ptitle = page['title']
            f.write(f'<a id="{pid}"></a>\n\n')
            f.write(f"## {ptitle}\n\n")
            f.write(page.get('content', '*未能生成内容*'))
            f.write("\n\n---\n\n")

    # Individual page files
    for page in wiki['pages']:
        safe_name = re.sub(r'[^\w\-\u4e00-\u9fff]', '_', page['title'])
        page_path = os.path.join(output_dir, f"{page['id']}_{safe_name}.md")
        with open(page_path, 'w', encoding='utf-8') as f:
            f.write(f"# {page['title']}\n\n")
            f.write(page.get('content', '*未能生成内容*'))

    print(f"\n{'='*60}")
    print(f"✅ Wiki 生成完成！")
    print(f"📄 合并文件: {combined_path}")
    print(f"📂 目录: {output_dir}")
    print(f"{'='*60}\n")

    # Print summary
    print("## 生成摘要\n")
    print(f"**项目**: {wiki['title']}")
    print(f"**描述**: {wiki['description']}")
    print(f"\n**页面列表**:")
    for page in wiki['pages']:
        print(f"- {page['title']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python generate_wiki_direct.py <repo_path> [output_dir]")
        sys.exit(1)

    repo_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else f"/tmp/wiki_{os.path.basename(repo_path)}"

    generate_wiki(repo_path, output_dir)
