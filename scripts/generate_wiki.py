#!/usr/bin/env python3
"""
CLI script to generate wiki for a local repository using deepwiki-open backend.
Usage: python generate_wiki.py /path/to/repo [output_dir]
"""

import asyncio
import json
import sys
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
import httpx
import websockets

BACKEND_URL = "http://localhost:8001"
WS_URL = "ws://localhost:8001/ws/chat"
PROVIDER = "minimax"
MODEL = "MiniMax-Text-01"
LANGUAGE = "zh"  # Generate in Chinese


async def ws_call(payload: dict, label: str = "") -> str:
    """Send a WebSocket request and collect the full response."""
    print(f"  [WS] {label}...", flush=True)
    content = ""
    async with websockets.connect(WS_URL, ping_interval=None, close_timeout=120) as ws:
        await ws.send(json.dumps(payload))
        async for message in ws:
            content += message
            # Print progress dots
            if len(content) % 500 < 20:
                print(".", end="", flush=True)
    print(f" done ({len(content)} chars)")
    return content


def get_local_repo_info(repo_path: str) -> dict:
    """Fetch file tree and README from backend."""
    print(f"Fetching repo structure for {repo_path}...")
    resp = httpx.get(f"{BACKEND_URL}/local_repo/structure", params={"path": repo_path}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def parse_wiki_structure(xml_text: str) -> dict:
    """Parse the XML wiki structure returned by LLM."""
    # Clean up: remove markdown code fences if any
    xml_text = re.sub(r"```(?:xml)?\s*", "", xml_text).strip()

    # Extract just the wiki_structure block
    match = re.search(r"<wiki_structure>.*?</wiki_structure>", xml_text, re.DOTALL)
    if match:
        xml_text = match.group(0)

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"Warning: XML parse error: {e}")
        print(f"Raw response snippet:\n{xml_text[:500]}")
        return None

    title = root.findtext("title", "Wiki")
    description = root.findtext("description", "")

    pages = []
    for page_el in root.findall(".//pages/page"):
        page_id = page_el.get("id", f"page-{len(pages)+1}")
        page_title = page_el.findtext("title", f"Page {len(pages)+1}")
        page_desc = page_el.findtext("description", "")
        importance = page_el.findtext("importance", "medium")

        file_paths = [fp.text for fp in page_el.findall(".//relevant_files/file_path") if fp.text]
        related_pages = [r.text for r in page_el.findall(".//related_pages/related") if r.text]

        pages.append({
            "id": page_id,
            "title": page_title,
            "description": page_desc,
            "importance": importance,
            "filePaths": file_paths,
            "relatedPages": related_pages,
        })

    return {
        "title": title,
        "description": description,
        "pages": pages,
    }


def read_file_content(repo_path: str, file_path: str) -> str:
    """Read a file from the local repository."""
    full_path = os.path.join(repo_path, file_path)
    if os.path.exists(full_path):
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception as e:
            return f"[Could not read file: {e}]"
    return f"[File not found: {file_path}]"


def build_page_prompt(page: dict, repo_path: str, repo_name: str) -> str:
    """Build the detailed page generation prompt."""
    # Gather relevant file contents
    file_contents = []
    for fp in page["filePaths"][:10]:  # Limit to 10 files per page
        content = read_file_content(repo_path, fp)
        # Truncate very large files
        if len(content) > 8000:
            content = content[:8000] + "\n... [truncated]"
        file_contents.append(f"### File: {fp}\n```\n{content}\n```")

    files_section = "\n\n".join(file_contents) if file_contents else "[No specific files identified]"

    return f"""Generate a comprehensive wiki page titled "{page['title']}" for the repository "{repo_name}".

[RELEVANT_SOURCE_FILES]
{files_section}

[PAGE_DESCRIPTION]
{page['description']}

Instructions:
1. Write a comprehensive technical wiki page based ONLY on the source files above.
2. Use Markdown formatting with proper headers (##, ###).
3. Include Mermaid diagrams where helpful (flowchart, sequenceDiagram, classDiagram, etc.).
4. Add code snippets with language identifiers where relevant.
5. Cite source files using format: Sources: [filename:lines]()
6. Be thorough — this page should cover "{page['title']}" completely.
7. Structure with: Overview → Details → Diagrams → Code Examples → Summary

IMPORTANT: Generate content in Chinese (中文). Start directly with the content, no preamble.
"""


async def generate_wiki(repo_path: str, output_dir: str):
    """Main wiki generation flow."""
    repo_path = os.path.abspath(repo_path)
    repo_name = os.path.basename(repo_path)

    print(f"\n{'='*60}")
    print(f"Generating wiki for: {repo_path}")
    print(f"Provider: {PROVIDER} / {MODEL}")
    print(f"Output dir: {output_dir}")
    print(f"{'='*60}\n")

    # Step 1: Get file tree and README
    repo_info = get_local_repo_info(repo_path)
    file_tree = repo_info.get("file_tree", "")
    readme = repo_info.get("readme", "")

    print(f"File tree: {len(file_tree.splitlines())} files")
    print(f"README: {len(readme)} chars\n")

    # Step 2: Generate wiki structure
    structure_prompt = f"""Analyze this repository and create a wiki structure for it.

1. The complete file tree of the project:
<file_tree>
{file_tree}
</file_tree>

2. The README file of the project:
<readme>
{readme}
</readme>

I want to create a wiki for this repository. Determine the most logical structure for a wiki based on the repository's content.

IMPORTANT: The wiki content will be generated in Chinese (中文) language.

Return your analysis in the following XML format:

<wiki_structure>
  <title>[Overall title for the wiki]</title>
  <description>[Brief description of the repository]</description>
  <pages>
    <page id="page-1">
      <title>[Page title]</title>
      <description>[Brief description of what this page will cover]</description>
      <importance>high|medium|low</importance>
      <relevant_files>
        <file_path>[Path to a relevant file]</file_path>
      </relevant_files>
      <related_pages>
        <related>page-2</related>
      </related_pages>
    </page>
  </pages>
</wiki_structure>

IMPORTANT FORMATTING INSTRUCTIONS:
- Return ONLY the valid XML structure specified above
- DO NOT wrap the XML in markdown code blocks
- DO NOT include any explanation text before or after the XML
- Start directly with <wiki_structure> and end with </wiki_structure>

Create 6-8 pages that cover the key aspects of this repository."""

    structure_payload = {
        "repo_url": repo_path,
        "type": "local",
        "provider": PROVIDER,
        "model": MODEL,
        "language": LANGUAGE,
        "messages": [{"role": "user", "content": structure_prompt}]
    }

    print("Step 1: Generating wiki structure...")
    structure_xml = await ws_call(structure_payload, "wiki structure")

    # Parse the structure
    wiki = parse_wiki_structure(structure_xml)
    if not wiki:
        print("ERROR: Failed to parse wiki structure. Raw response:")
        print(structure_xml[:2000])
        return

    print(f"\nWiki title: {wiki['title']}")
    print(f"Pages: {len(wiki['pages'])}")
    for p in wiki['pages']:
        print(f"  [{p['importance']}] {p['title']} ({len(p['filePaths'])} files)")

    # Step 3: Generate each page
    print(f"\nStep 2: Generating {len(wiki['pages'])} pages...")

    generated_pages = {}
    for i, page in enumerate(wiki['pages'], 1):
        print(f"\n[{i}/{len(wiki['pages'])}] {page['title']}")

        page_prompt = build_page_prompt(page, repo_path, repo_name)

        page_payload = {
            "repo_url": repo_path,
            "type": "local",
            "provider": PROVIDER,
            "model": MODEL,
            "language": LANGUAGE,
            "messages": [{"role": "user", "content": page_prompt}]
        }

        try:
            content = await ws_call(page_payload, page['title'])
            page['content'] = content
            generated_pages[page['id']] = page
        except Exception as e:
            print(f"  ERROR generating page: {e}")
            page['content'] = f"*Error generating content: {e}*"
            generated_pages[page['id']] = page

    # Step 4: Write output
    os.makedirs(output_dir, exist_ok=True)

    # Write individual pages
    for page in wiki['pages']:
        safe_name = re.sub(r'[^\w\-]', '_', page['title'])
        page_path = os.path.join(output_dir, f"{page['id']}_{safe_name}.md")
        with open(page_path, 'w', encoding='utf-8') as f:
            f.write(f"# {page['title']}\n\n")
            f.write(page.get('content', '*No content generated*'))
        print(f"  Wrote: {page_path}")

    # Write combined wiki
    combined_path = os.path.join(output_dir, "WIKI.md")
    with open(combined_path, 'w', encoding='utf-8') as f:
        f.write(f"# {wiki['title']}\n\n")
        f.write(f"> {wiki['description']}\n\n")
        f.write(f"Generated by deepwiki-open with {PROVIDER}/{MODEL}\n\n")
        f.write("---\n\n")

        # Table of contents
        f.write("## 目录\n\n")
        for page in wiki['pages']:
            f.write(f"- [{page['title']}](#{page['id']})\n")
        f.write("\n---\n\n")

        # All pages
        for page in wiki['pages']:
            pid = page['id']
            ptitle = page['title']
            f.write(f'<a id="{pid}"></a>\n\n')
            f.write(f"# {ptitle}\n\n")
            f.write(page.get('content', '*No content generated*'))
            f.write("\n\n---\n\n")

    print(f"\n{'='*60}")
    print(f"Wiki generated successfully!")
    print(f"Combined wiki: {combined_path}")
    print(f"Individual pages: {output_dir}/")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python generate_wiki.py <repo_path> [output_dir]")
        sys.exit(1)

    repo_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else f"/tmp/wiki_{os.path.basename(repo_path)}"

    asyncio.run(generate_wiki(repo_path, output_dir))
