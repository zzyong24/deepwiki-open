"""
api/batch_wiki.py — 后端批量 Wiki 生成队列

任务生命周期：
  idle → running → done | error

每个任务独立运行一个 asyncio Task：
  1. 获取文件树（本地 os.walk / 远程 git clone 已有的 data_pipeline）
  2. LLM 生成 wiki 结构 XML
  3. 逐页 LLM 生成内容
  4. 保存到 wiki_cache

前端只需要：
  POST /batch/jobs          — 提交任务
  GET  /batch/jobs          — 获取所有任务列表（含进度）
  GET  /batch/jobs/{job_id} — 获取单个任务详情
  DELETE /batch/jobs/{job_id} — 删除任务

任务状态持久化到 ~/.adalflow/batch_jobs.json，重启不丢失（但 running 状态重启后变 error）。
"""

import asyncio
import json
import logging
import os
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Optional

from adalflow.core.types import ModelType
from fastapi import APIRouter
from pydantic import BaseModel

from api.config import get_model_config
from api.minimax_client import MinimaxClient
from api.logging_config import setup_logging
from api.websocket_wiki import _scan_local_repo, _build_local_context

setup_logging()
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/batch", tags=["batch"])

# ─── Persistence ─────────────────────────────────────────────────────────────

def _jobs_file() -> str:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_dir = os.path.join(project_root, "output")
    os.makedirs(output_dir, exist_ok=True)
    return os.path.join(output_dir, "batch_jobs.json")


def _load_persisted() -> Dict[str, dict]:
    try:
        with open(_jobs_file(), "r", encoding="utf-8") as f:
            data = json.load(f)
        # Mark any previously-running jobs as error (process restarted)
        for job in data.values():
            if job.get("status") == "running":
                job["status"] = "error"
                job["error"] = "服务重启，任务中断，请重新提交"
        return data
    except Exception:
        return {}


def _save_persisted(jobs: Dict[str, dict]):
    try:
        with open(_jobs_file(), "w", encoding="utf-8") as f:
            json.dump(jobs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Failed to persist batch jobs: {e}")


# In-memory store (source of truth while process is running)
_jobs: Dict[str, dict] = _load_persisted()
_jobs_lock = asyncio.Lock()


# ─── Pydantic Models ─────────────────────────────────────────────────────────

class BatchJobRequest(BaseModel):
    input: str                     # raw user input (path or URL)
    owner: str
    repo: str
    type: str                      # local / github / gitlab / bitbucket
    local_path: Optional[str] = None
    repo_url: Optional[str] = None
    provider: str = "minimax"
    model: str = "MiniMax-M2.7"
    language: str = "zh"
    comprehensive: bool = True


class BatchJobStatus(BaseModel):
    id: str
    input: str
    owner: str
    repo: str
    type: str
    status: str                    # idle / running / done / error
    progress: str
    pages_done: int
    pages_total: int
    error: Optional[str] = None
    wiki_url: Optional[str] = None
    created_at: str
    updated_at: str


# ─── LLM Helper ──────────────────────────────────────────────────────────────

async def _llm_call(prompt: str, provider: str, model: str) -> str:
    """Call LLM and return full response text (non-streaming, collected)."""
    model_config = get_model_config(provider, model)

    if provider == "minimax":
        client = MinimaxClient()
        model_kwargs = {
            "model": model,
            "stream": True,
            "temperature": model_config.get("temperature", 0.7),
        }
        if "top_p" in model_config:
            model_kwargs["top_p"] = model_config["top_p"]

        api_kwargs = client.convert_inputs_to_api_kwargs(
            input=prompt,
            model_kwargs=model_kwargs,
            model_type=ModelType.LLM,
        )
        response = await client.acall(api_kwargs=api_kwargs, model_type=ModelType.LLM)

        parts = []
        in_think = False
        think_buf = ""
        async for chunk in response:
            choices = getattr(chunk, "choices", [])
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue
            text = getattr(delta, "content", None)
            if text is None:
                continue
            # Filter <think> blocks
            if not in_think and not think_buf:
                if "<think>" in text:
                    in_think = True
                    before, _, rest = text.partition("<think>")
                    if before:
                        parts.append(before)
                    think_buf = rest
                    if "</think>" in think_buf:
                        _, _, after = think_buf.partition("</think>")
                        think_buf = ""
                        in_think = False
                        if after:
                            parts.append(after)
                else:
                    parts.append(text)
            elif in_think:
                think_buf += text
                if "</think>" in think_buf:
                    _, _, after = think_buf.partition("</think>")
                    think_buf = ""
                    in_think = False
                    if after:
                        parts.append(after)
            else:
                parts.append(text)

        return "".join(parts).strip()

    # Fallback: openai-compatible
    from api.openai_client import OpenAIClient
    client = OpenAIClient()
    model_kwargs = {
        "model": model,
        "stream": False,
        "temperature": model_config.get("temperature", 0.7),
    }
    api_kwargs = client.convert_inputs_to_api_kwargs(
        input=prompt,
        model_kwargs=model_kwargs,
        model_type=ModelType.LLM,
    )
    response = await client.acall(api_kwargs=api_kwargs, model_type=ModelType.LLM)
    return str(response).strip()


# ─── Wiki Structure Parser ────────────────────────────────────────────────────

def _parse_structure(raw: str) -> Optional[dict]:
    text = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.IGNORECASE).strip()
    text = re.sub(r"```(?:xml)?\s*", "", text)
    text = re.sub(r"```\s*$", "", text).strip()

    m = re.search(r"<wiki_structure>[\s\S]*?</wiki_structure>", text)
    if not m:
        return None

    xml_text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", m.group(0))
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning(f"XML parse error: {e}")
        return None

    title = root.findtext("title") or "Wiki"
    description = root.findtext("description") or ""
    pages = []
    for p in root.findall(".//pages/page"):
        pid = p.get("id") or f"page-{len(pages)+1}"
        ptitle = p.findtext("title") or f"Page {len(pages)+1}"
        importance = p.findtext("importance") or "medium"
        file_paths = [fp.text.strip() for fp in p.findall(".//relevant_files/file_path") if fp.text]
        related = [r.text.strip() for r in p.findall(".//related_pages/related") if r.text]
        pages.append({"id": pid, "title": ptitle, "importance": importance,
                      "filePaths": file_paths, "relatedPages": related, "content": ""})

    if not pages:
        return None

    return {"id": f"wiki-{uuid.uuid4().hex[:8]}", "title": title,
            "description": description, "pages": pages, "sections": [], "rootSections": []}


# ─── Structure Prompt ────────────────────────────────────────────────────────

def _structure_prompt(file_tree: str, readme: str, owner: str, repo: str,
                      language: str, comprehensive: bool) -> str:
    lang_str = "Mandarin Chinese (中文)" if language == "zh" else \
               "Japanese (日本語)" if language == "ja" else "English"
    scope = """Create a structured wiki with:
- Overview, System Architecture, Core Features, Data Management, Backend Systems, Deployment""" \
        if comprehensive else \
        "Create a concise wiki with 3-5 focused pages covering the essential aspects."

    return f"""Analyze this repository {owner}/{repo} and create a wiki structure.

File tree:
<file_tree>
{file_tree[:8000]}
</file_tree>

README:
<readme>
{readme[:2000]}
</readme>

{scope}

IMPORTANT: Generate wiki in {lang_str}.

Return ONLY valid XML, no markdown fences:
<wiki_structure>
  <title>Project Wiki</title>
  <description>Brief description</description>
  <pages>
    <page id="page-1">
      <title>Page Title</title>
      <description>What this page covers</description>
      <importance>high</importance>
      <relevant_files>
        <file_path>path/to/file.py</file_path>
      </relevant_files>
      <related_pages>
        <related>page-2</related>
      </related_pages>
    </page>
  </pages>
</wiki_structure>"""


def _page_prompt(page: dict, owner: str, repo: str, language: str) -> str:
    lang_str = "Mandarin Chinese (中文)" if language == "zh" else \
               "Japanese (日本語)" if language == "ja" else "English"
    files = "\n".join(f"- {f}" for f in page["filePaths"])
    return f"""Generate a technical wiki page titled "{page['title']}" for {owner}/{repo}.

Relevant source files:
{files}

Requirements:
- Markdown format, headings start at ##
- Include Mermaid diagrams where useful
- Ground all claims in source files
- 800–2000 words
- Language: {lang_str}

Start directly with the content (no preamble)."""


# ─── Job Runner ──────────────────────────────────────────────────────────────

async def _update(job_id: str, **kwargs):
    async with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(kwargs)
            _jobs[job_id]["updated_at"] = datetime.now().isoformat()
            _save_persisted(_jobs)


async def _run_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return

    owner = job["owner"]
    repo = job["repo"]
    jtype = job["type"]
    local_path = job.get("local_path")
    repo_url = job.get("repo_url")
    provider = job["provider"]
    model = job["model"]
    language = job["language"]
    comprehensive = job.get("comprehensive", True)

    repo_url_for_api = local_path if jtype == "local" else (repo_url or f"https://github.com/{owner}/{repo}")

    try:
        # ── Step 1: file tree ─────────────────────────────────────
        await _update(job_id, status="running", progress="获取仓库结构...")

        file_tree = ""
        readme = ""

        if jtype == "local" and local_path:
            if not os.path.isdir(local_path):
                raise ValueError(f"本地路径不存在: {local_path}")
            file_list, readme = _scan_local_repo(local_path)
            file_tree = "\n".join(file_list)
        else:
            file_tree = f"{owner}/{repo} (remote repository)"
            # Try GitHub API (unauthenticated, best effort)
            try:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD?recursive=1",
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            blobs = [f["path"] for f in data.get("tree", []) if f.get("type") == "blob"]
                            file_tree = "\n".join(blobs)
                        # 403 rate limit → just continue with minimal tree
            except Exception:
                pass  # proceed with minimal tree

        # ── Step 2: wiki structure ────────────────────────────────
        await _update(job_id, progress="生成 Wiki 结构...")

        structure_prompt = _structure_prompt(file_tree, readme, owner, repo, language, comprehensive)
        structure_raw = await _llm_call(structure_prompt, provider, model)
        wiki = _parse_structure(structure_raw)
        if not wiki:
            raise ValueError("LLM 未返回有效的 Wiki 结构 XML，请重试")

        pages_total = len(wiki["pages"])
        await _update(job_id, pages_total=pages_total,
                      progress=f"生成 {pages_total} 个页面...")

        # ── Step 3: page content ──────────────────────────────────
        for i, page in enumerate(wiki["pages"]):
            await _update(job_id, progress=f"生成页面 {i+1}/{pages_total}: {page['title']}")

            # For local repos, inject actual file content into context
            if jtype == "local" and local_path and page["filePaths"]:
                context = _build_local_context(local_path, page["filePaths"], max_files=6)
                p_prompt = _page_prompt(page, owner, repo, language) + f"\n\nSource file contents:\n{context}"
            else:
                p_prompt = _page_prompt(page, owner, repo, language)

            content = await _llm_call(p_prompt, provider, model)
            wiki["pages"][i]["content"] = content
            await _update(job_id, pages_done=i + 1)

        # ── Step 4: save wiki cache ───────────────────────────────
        await _update(job_id, progress="保存到缓存...")

        generated_pages = {p["id"]: p for p in wiki["pages"]}
        cache_body = {
            "repo": {"owner": owner, "repo": repo, "type": jtype,
                     "localPath": local_path, "repoUrl": repo_url},
            "language": language,
            "comprehensive": comprehensive,
            "wiki_structure": wiki,
            "generated_pages": generated_pages,
            "provider": provider,
            "model": model,
        }
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "http://localhost:8001/api/wiki_cache",
                    json=cache_body,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status not in (200, 201):
                        logger.warning(f"Cache save returned {resp.status}")
        except Exception as e:
            logger.warning(f"Cache save failed (non-fatal): {e}")

        # ── Step 5: build view URL ────────────────────────────────
        from urllib.parse import urlencode, quote
        params: dict = {"type": jtype, "provider": provider, "model": model,
                        "language": language, "comprehensive": str(comprehensive).lower()}
        if jtype == "local" and local_path:
            params["local_path"] = quote(local_path, safe="")
        elif repo_url:
            params["repo_url"] = quote(repo_url, safe="")

        wiki_url = f"/{owner}/{repo}?{urlencode(params)}"
        await _update(job_id, status="done", progress="生成完成 ✓", wiki_url=wiki_url)
        logger.info(f"Batch job {job_id} done: {wiki_url}")

    except asyncio.CancelledError:
        await _update(job_id, status="error", progress="任务已取消", error="任务已取消")
    except Exception as e:
        logger.error(f"Batch job {job_id} failed: {e}")
        await _update(job_id, status="error", progress="生成失败", error=str(e))


# ─── REST Endpoints ──────────────────────────────────────────────────────────

@router.post("/jobs", response_model=BatchJobStatus)
async def create_job(req: BatchJobRequest):
    """Submit a new batch wiki generation job."""
    job_id = uuid.uuid4().hex
    now = datetime.now().isoformat()
    job = {
        "id": job_id,
        "input": req.input,
        "owner": req.owner,
        "repo": req.repo,
        "type": req.type,
        "local_path": req.local_path,
        "repo_url": req.repo_url,
        "provider": req.provider,
        "model": req.model,
        "language": req.language,
        "comprehensive": req.comprehensive,
        "status": "running",
        "progress": "排队中...",
        "pages_done": 0,
        "pages_total": 0,
        "error": None,
        "wiki_url": None,
        "created_at": now,
        "updated_at": now,
    }
    async with _jobs_lock:
        _jobs[job_id] = job
        _save_persisted(_jobs)

    # Fire and forget
    asyncio.create_task(_run_job(job_id))

    return BatchJobStatus(**job)


@router.get("/jobs", response_model=List[BatchJobStatus])
async def list_jobs():
    """List all batch jobs."""
    async with _jobs_lock:
        jobs = list(_jobs.values())
    # Sort newest first
    jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
    return [BatchJobStatus(**j) for j in jobs]


@router.get("/jobs/{job_id}", response_model=BatchJobStatus)
async def get_job(job_id: str):
    """Get a single batch job status."""
    async with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Job not found")
    return BatchJobStatus(**job)


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str):
    """Delete a batch job."""
    async with _jobs_lock:
        _jobs.pop(job_id, None)
        _save_persisted(_jobs)
    return {"ok": True}


@router.delete("/jobs")
async def clear_done_jobs():
    """Delete all completed/error jobs."""
    async with _jobs_lock:
        to_delete = [jid for jid, j in _jobs.items() if j["status"] in ("done", "error")]
        for jid in to_delete:
            del _jobs[jid]
        _save_persisted(_jobs)
    return {"deleted": len(to_delete)}
