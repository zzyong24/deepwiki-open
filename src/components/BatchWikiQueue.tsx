'use client';

/**
 * BatchWikiQueue – 批量 Wiki 生成队列面板（后端驱动版）
 *
 * 任务在后端 asyncio Task 中运行，前端仅负责：
 *   - 提交任务 POST /batch/jobs
 *   - 轮询进度 GET  /batch/jobs（每 3 秒）
 *   - 删除任务 DELETE /batch/jobs/{id}
 *
 * 刷新/跳转页面不影响任务执行。
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  FaFolder, FaGithub, FaPlus, FaTrash, FaPlay,
  FaCheck, FaExclamationTriangle, FaSpinner, FaExternalLinkAlt, FaTimes
} from 'react-icons/fa';

// ─── Types ────────────────────────────────────────────────────────────────────

interface BatchJob {
  id: string;
  input: string;
  owner: string;
  repo: string;
  type: string;
  status: 'running' | 'done' | 'error' | 'idle';
  progress: string;
  pages_done: number;
  pages_total: number;
  error?: string;
  wiki_url?: string;
  created_at: string;
}

interface BatchWikiQueueProps {
  provider: string;
  model: string;
  language: string;
  isComprehensiveView: boolean;
}

const STORAGE_KEY = 'deepwiki_batch_job_ids'; // 只存 id 列表，状态从后端拿

// ─── Input Parser (同 page.tsx) ───────────────────────────────────────────────

function parseInput(input: string) {
  const windowsPath = /^[a-zA-Z]:\\/.test(input);
  const unixPath = input.startsWith('/');

  if (windowsPath || unixPath) {
    const parts = input.replace(/\\/g, '/').split('/').filter(Boolean);
    return { owner: 'local', repo: parts[parts.length - 1] || 'local-repo', type: 'local', localPath: input };
  }

  const urlMatch = input.match(/(?:https?:\/\/)?([^/]+)\/([^/]+)\/([^/?#]+)/);
  if (urlMatch) {
    const domain = urlMatch[1].toLowerCase();
    const owner = urlMatch[2];
    const repo = urlMatch[3].replace(/\.git$/, '');
    const type = domain.includes('gitlab') ? 'gitlab' : domain.includes('bitbucket') ? 'bitbucket' : 'github';
    return { owner, repo, type, repoUrl: input.startsWith('http') ? input : `https://${input}` };
  }

  const shorthand = input.match(/^([^/\s]+)\/([^/\s]+)$/);
  if (shorthand) {
    return { owner: shorthand[1], repo: shorthand[2], type: 'github', repoUrl: `https://github.com/${shorthand[1]}/${shorthand[2]}` };
  }

  return null;
}

// ─── Main Component ───────────────────────────────────────────────────────────

export default function BatchWikiQueue({ provider, model, language, isComprehensiveView }: BatchWikiQueueProps) {
  const [jobs, setJobs] = useState<BatchJob[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [inputError, setInputError] = useState<string | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Fetch all jobs from backend ────────────────────────────────
  const fetchJobs = useCallback(async () => {
    try {
      const resp = await fetch('/batch/jobs');
      if (!resp.ok) return;
      const data: BatchJob[] = await resp.json();
      setJobs(data);
    } catch { /* network error — ignore */ }
  }, []);

  // ── Start polling on mount, stop on unmount ────────────────────
  useEffect(() => {
    fetchJobs();
    pollTimer.current = setInterval(fetchJobs, 3000);
    return () => { if (pollTimer.current) clearInterval(pollTimer.current); };
  }, [fetchJobs]);

  // ── Submit a new job ───────────────────────────────────────────
  const addJob = async () => {
    const raw = inputValue.trim();
    if (!raw) return;

    const parsed = parseInput(raw);
    if (!parsed) {
      setInputError('无法识别的格式，请输入有效的路径或仓库 URL');
      return;
    }

    if (jobs.some(j => j.input === raw && j.status === 'running')) {
      setInputError('该项目正在生成中');
      return;
    }

    try {
      const body = {
        input: raw,
        owner: parsed.owner,
        repo: parsed.repo,
        type: parsed.type,
        local_path: (parsed as { localPath?: string }).localPath ?? null,
        repo_url: (parsed as { repoUrl?: string }).repoUrl ?? null,
        provider,
        model,
        language,
        comprehensive: isComprehensiveView,
      };

      const resp = await fetch('/batch/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      if (!resp.ok) {
        const err = await resp.text();
        setInputError(`提交失败: ${err}`);
        return;
      }

      const newJob: BatchJob = await resp.json();
      setJobs(prev => [newJob, ...prev]);
      setInputValue('');
      setInputError(null);
    } catch (e) {
      setInputError(`网络错误: ${e}`);
    }
  };

  // ── Delete a job ───────────────────────────────────────────────
  const deleteJob = async (id: string) => {
    await fetch(`/batch/jobs/${id}`, { method: 'DELETE' });
    setJobs(prev => prev.filter(j => j.id !== id));
  };

  // ── Clear done/error jobs ──────────────────────────────────────
  const clearFinished = async () => {
    await fetch('/batch/jobs', { method: 'DELETE' });
    await fetchJobs();
  };

  // ── Retry a failed job ─────────────────────────────────────────
  const retryJob = async (job: BatchJob) => {
    await fetch(`/batch/jobs/${job.id}`, { method: 'DELETE' });
    setInputValue(job.input);
  };

  const runningCount = jobs.filter(j => j.status === 'running').length;
  const doneCount = jobs.filter(j => j.status === 'done' || j.status === 'error').length;

  return (
    <div className="w-full max-w-2xl">
      {/* Input row */}
      <div className="flex gap-2 mb-2">
        <div className="relative flex-1">
          <div className="absolute inset-y-0 left-3 flex items-center pointer-events-none">
            {inputValue.startsWith('/') || /^[a-zA-Z]:\\/.test(inputValue)
              ? <FaFolder className="text-[var(--accent-primary)]" />
              : <FaGithub className="text-[var(--muted)]" />
            }
          </div>
          <input
            type="text"
            value={inputValue}
            onChange={e => { setInputValue(e.target.value); setInputError(null); }}
            onKeyDown={e => e.key === 'Enter' && addJob()}
            placeholder="本地路径 /path/to/folder 或 GitHub URL / owner/repo"
            className="input-japanese block w-full pl-10 pr-3 py-2 border-[var(--border-color)] rounded-lg bg-transparent text-[var(--foreground)] focus:outline-none focus:border-[var(--accent-primary)] text-sm"
          />
        </div>
        <button
          type="button"
          onClick={addJob}
          className="btn-japanese px-4 py-2 rounded-lg flex items-center gap-1.5 text-sm"
        >
          <FaPlus className="text-xs" /> 添加并生成
        </button>
      </div>

      {inputError && <p className="text-[var(--highlight)] text-xs mb-2">{inputError}</p>}

      {/* Job list */}
      {jobs.length > 0 && (
        <div className="bg-[var(--card-bg)] border border-[var(--border-color)] rounded-lg overflow-hidden mb-3">
          {jobs.map((job, idx) => (
            <div
              key={job.id}
              className={`flex items-center gap-3 px-4 py-3 ${idx < jobs.length - 1 ? 'border-b border-[var(--border-color)]' : ''}`}
            >
              {/* Status icon */}
              <div className="flex-shrink-0 w-5 flex justify-center">
                {job.status === 'done' && <FaCheck className="text-green-500 text-sm" />}
                {job.status === 'error' && <FaExclamationTriangle className="text-[var(--highlight)] text-sm" />}
                {job.status === 'running' && <FaSpinner className="text-[var(--accent-primary)] text-sm animate-spin" />}
              </div>

              {/* Info */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5 mb-0.5">
                  {job.type === 'local'
                    ? <FaFolder className="text-[var(--accent-primary)] text-xs flex-shrink-0" />
                    : <FaGithub className="text-[var(--muted)] text-xs flex-shrink-0" />
                  }
                  <span className="text-sm font-medium text-[var(--foreground)] truncate">
                    {job.owner}/{job.repo}
                  </span>
                </div>

                {job.status === 'running' && (
                  <div>
                    <p className="text-xs text-[var(--muted)] truncate">{job.progress}</p>
                    {job.pages_total > 0 && (
                      <div className="mt-1 h-1 bg-[var(--border-color)] rounded-full overflow-hidden w-full max-w-[200px]">
                        <div
                          className="h-full bg-[var(--accent-primary)] rounded-full transition-all duration-500"
                          style={{ width: `${(job.pages_done / job.pages_total) * 100}%` }}
                        />
                      </div>
                    )}
                  </div>
                )}

                {job.status === 'done' && (
                  <a
                    href={job.wiki_url!}
                    className="text-xs text-[var(--accent-primary)] hover:underline flex items-center gap-1"
                  >
                    查看 Wiki <FaExternalLinkAlt className="text-[10px]" />
                  </a>
                )}

                {job.status === 'error' && (
                  <div className="flex items-center gap-2">
                    <p className="text-xs text-[var(--highlight)] truncate flex-1" title={job.error}>{job.error}</p>
                    <button
                      type="button"
                      onClick={() => retryJob(job)}
                      className="text-xs text-[var(--accent-primary)] hover:underline flex-shrink-0"
                    >
                      重试
                    </button>
                  </div>
                )}
              </div>

              {/* Page counter */}
              {job.status === 'running' && job.pages_total > 0 && (
                <span className="flex-shrink-0 text-xs text-[var(--muted)]">
                  {job.pages_done}/{job.pages_total}
                </span>
              )}

              {/* Delete */}
              {job.status !== 'running' && (
                <button
                  type="button"
                  onClick={() => deleteJob(job.id)}
                  className="flex-shrink-0 text-[var(--muted)] hover:text-[var(--highlight)] transition-colors p-1"
                >
                  <FaTimes className="text-xs" />
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Footer bar */}
      <div className="flex items-center gap-3 text-xs text-[var(--muted)]">
        {runningCount > 0 && (
          <span className="flex items-center gap-1">
            <FaSpinner className="animate-spin" />
            {runningCount} 个任务在后台运行中，跳转页面不影响
          </span>
        )}
        {runningCount === 0 && jobs.length === 0 && (
          <span>支持本地路径、GitHub/GitLab URL、owner/repo，添加后立即在后台生成</span>
        )}
        {doneCount > 0 && (
          <button
            type="button"
            onClick={clearFinished}
            className="ml-auto flex items-center gap-1 hover:text-[var(--foreground)] transition-colors"
          >
            <FaTrash className="text-[10px]" /> 清除已完成
          </button>
        )}
      </div>
    </div>
  );
}
