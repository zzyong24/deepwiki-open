# DeepWiki-Open 使用手册

> 本文基于已集成 MiniMax 的 fork 版本，记录完整的启动、配置、生成 Wiki 流程。

---

## 目录

- [架构概览](#架构概览)
- [启动服务](#启动服务)
- [Web UI 使用（GitHub/GitLab 等远程仓库）](#web-ui-使用)
- [本地仓库 Wiki 生成（scripts/generate_wiki_direct.py）](#本地仓库-wiki-生成)
- [配置说明](#配置说明)
- [MiniMax 接入细节](#minimax-接入细节)
- [常见问题](#常见问题)

---

## 架构概览

```
deepwiki-open/
├── src/                    # Next.js 15 前端（端口 3000）
├── api/                    # FastAPI + ADALFlow 后端（端口 8001）
│   ├── main.py             # 后端入口
│   ├── api.py              # HTTP + WebSocket 路由
│   ├── rag.py              # RAG 检索增强生成
│   ├── websocket_wiki.py   # WebSocket wiki 生成处理器
│   ├── minimax_client.py   # MiniMax 客户端（自定义扩展）
│   └── config/
│       ├── generator.json  # 生成模型配置
│       ├── embedder.json   # Embedding 模型配置
│       └── repo.json       # 仓库过滤规则
├── scripts/generate_wiki_direct.py # 本地仓库 Wiki 生成脚本（绕过 RAG）
├── scripts/generate_wiki.py        # 本地仓库 Wiki 生成脚本（走 WebSocket，需 embedding）
└── .env                    # 环境变量（API Key 等）
```

**两种 Wiki 生成路径：**

| 路径 | 适用场景 | 说明 |
|------|---------|------|
| **Web UI** | GitHub / GitLab / Bitbucket 远程仓库 | 先 clone → 建 embedding 索引 → LLM 生成 |
| **scripts/generate_wiki_direct.py** | 本地仓库（推荐） | 直接读文件 → 调 LLM，跳过 embedding，MiniMax 可用 |

---

## 启动服务

### 方式一：手动启动（当前使用方式）

需要两个终端分别启动前端和后端。

**启动后端（端口 8001）：**

```bash
cd /Users/zyongzhu/workbase/github/moon/deepwiki-open

# 激活 Python 虚拟环境
source .venv/bin/activate

# 启动后端
python -m api.main
```

**启动前端（端口 3001）：**

```bash
cd /Users/zyongzhu/workbase/github/moon/deepwiki-open

# 安装依赖（首次）
npm install

# 启动前端
npm run dev
```

前端起来后访问：**http://localhost:3001**

> ⚠️ 端口从 3000 改为 **3001**，因为 SuperPipeline 占用了 3000。

**验证服务是否在跑：**

```bash
lsof -i :3001 -i :8001 | grep LISTEN
# 看到 node 监听 3001、python3 监听 8001 即正常
```

---

### 方式二：Docker（官方推荐，一键启动）

适合不想管理 Python/Node 环境的场景。**注意：需要在 .env 里配好 API Key。**

```bash
cd /Users/zyongzhu/workbase/github/moon/deepwiki-open

# 确认 .env 已配置（见下方配置说明）
cat .env

# 启动（后台运行加 -d）
docker-compose up
# 或后台运行：
docker-compose up -d
```

Docker 会：
- 同时启动前端（3000）和后端（8001）
- 将 `~/.adalflow` 挂载到容器，持久化缓存（clone 的仓库、embedding 索引、wiki 缓存）

停止：
```bash
docker-compose down
```

---

## Web UI 使用

Web UI 支持**远程仓库**（GitHub / GitLab / Bitbucket）和**本地仓库**。

访问 http://localhost:3001，操作步骤：

1. 在输入框粘贴仓库 URL，例如：
   - `https://github.com/openai/codex`
   - `https://gitlab.com/somegroup/someproject`
2. **本地仓库**：选择「Local」类型，输入绝对路径，例如：
   - `/Users/zyongzhu/workbase/agent/agent-manage-backend`
3. 私有远程仓库：点击「+ Add access tokens」，填入 Personal Access Token
4. 右上角选择模型 Provider（MiniMax）和 Model
5. 点击 **Generate Wiki**，等待生成完成

> ✅ **本地仓库 + MiniMax 已完全支持**：Web UI 会跳过 embedding/RAG 步骤，直接读取本地文件调用 LLM，与 `scripts/generate_wiki_direct.py` 路径完全一致。

> ⚠️ **远程仓库 + MiniMax 仍有限制**：RAG pipeline 依赖 embedding 索引。MiniMax embedding API 字段格式（`texts`）与 OpenAI（`input`）不兼容，embedding 全为空，wiki 生成失败。远程仓库建议用 Google Gemini 或 OpenAI。

---

## 本地仓库 Wiki 生成

用 `scripts/generate_wiki_direct.py`，直接读取本地文件、调用 MiniMax 聊天 API，不走 embedding。

### 基本用法

```bash
cd /Users/zyongzhu/workbase/github/moon/deepwiki-open

# 激活虚拟环境
source .venv/bin/activate

# 生成 wiki，输出到默认路径 /tmp/wiki_<项目名>/
python scripts/generate_wiki_direct.py /path/to/your/repo

# 指定输出目录
python scripts/generate_wiki_direct.py /path/to/your/repo /path/to/output/dir
```

**示例：**

```bash
# 给 agent-manage-backend 生成 wiki
python scripts/generate_wiki_direct.py /Users/zyongzhu/workbase/agent/agent-manage-backend
# 输出到：/tmp/wiki_agent-manage-backend/

# 指定输出目录
python scripts/generate_wiki_direct.py /Users/zyongzhu/workbase/agent/agent-manage-backend ~/Desktop/my-wiki
```

### 输出文件

```
/tmp/wiki_<项目名>/
├── WIKI.md                      # 合并文件（所有页面 + 目录）
├── page-1_项目概览.md
├── page-2_系统架构.md
├── page-3_核心模块详解.md
└── ...（6-8 个页面）
```

### 脚本参数（顶部常量，可直接修改）

```python
# scripts/generate_wiki_direct.py 顶部
MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"  # MiniMax API 地址
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "sk-...")  # 优先读环境变量
MODEL = "MiniMax-M2.7"            # 使用的模型
```

- **生成语言**：默认中文（prompt 里写死了「用中文输出」，改 `build_page_prompt()` 可切换）
- **页面数**：structure prompt 要求生成 6-8 个页面，改 prompt 可调整
- **文件读取上限**：每个文件最多读 12000 字符（`read_file()` 的 `max_chars` 参数）
- **每页引用文件上限**：最多 8 个文件（`build_page_prompt()` 的 `[:8]` 切片）

---

## 配置说明

所有配置分两层：**环境变量（.env）** 和 **JSON 配置文件（api/config/）**。

### .env 文件

路径：`/Users/zyongzhu/workbase/github/moon/deepwiki-open/.env`

```bash
# ─── MiniMax（当前配置）───────────────────────────────────────
MINIMAX_API_KEY=sk-cp-xxxxx              # MiniMax API Key
MINIMAX_BASE_URL=https://api.minimaxi.com/v1  # 注意：是 minimaxi（多一个 i）

# ─── Embedding（复用 MiniMax OpenAI 兼容接口）────────────────
OPENAI_API_KEY=sk-cp-xxxxx              # 同 MiniMax Key
OPENAI_BASE_URL=https://api.minimaxi.com/v1
DEEPWIKI_EMBEDDER_TYPE=openai           # embedding 类型

# ─── 其他可选配置 ─────────────────────────────────────────────
# GOOGLE_API_KEY=xxx                    # 使用 Gemini 时
# OPENROUTER_API_KEY=xxx                # 使用 OpenRouter 时
# OLLAMA_HOST=http://localhost:11434    # 本地 Ollama（默认值）

# ─── 服务配置 ──────────────────────────────────────────────────
# PORT=8001                             # 后端端口（默认 8001）
# SERVER_BASE_URL=http://localhost:8001 # 前端调用后端的地址

# ─── 日志 ──────────────────────────────────────────────────────
# LOG_LEVEL=DEBUG                       # 日志级别（默认 INFO）
# LOG_FILE_PATH=./debug.log             # 日志文件路径

# ─── 鉴权（可选，限制谁能生成）────────────────────────────────
# DEEPWIKI_AUTH_MODE=true
# DEEPWIKI_AUTH_CODE=your_secret_code
```

**切换到 Google Gemini：**
```bash
GOOGLE_API_KEY=your_google_key
DEEPWIKI_EMBEDDER_TYPE=google
```

**切换到 Ollama 本地模型：**
```bash
DEEPWIKI_EMBEDDER_TYPE=ollama
OLLAMA_HOST=http://localhost:11434
```

---

### api/config/generator.json

控制**文本生成**模型列表、默认模型、参数。

路径：`api/config/generator.json`

```json
{
  "default_provider": "minimax",       // Web UI 默认选中的 provider
  "providers": {
    "minimax": {
      "client_class": "MinimaxClient",
      "default_model": "MiniMax-M2.7", // 该 provider 默认模型
      "supportsCustomModel": true,      // 是否允许前端输入自定义模型名
      "models": {
        "MiniMax-M2.7": {
          "temperature": 0.7,
          "top_p": 0.8
        },
        "MiniMax-Text-01": { ... }
      }
    },
    "google": { ... },
    "openai": { ... },
    "ollama": { ... }
  }
}
```

**如何添加新模型：** 在对应 provider 的 `models` 下加一条即可，key 是模型 ID，value 是参数。

---

### api/config/embedder.json

控制 **RAG embedding**（仅 Web UI 路径用到）。

```json
{
  "embedder": {
    "client_class": "OpenAIClient",
    "batch_size": 500,
    "model_kwargs": {
      "model": "text-embedding-3-small",
      "dimensions": 256,
      "encoding_format": "float"
    }
  },
  "retriever": {
    "top_k": 20            // 检索时返回的相关片段数量
  },
  "text_splitter": {
    "split_by": "word",
    "chunk_size": 350,     // 每个文本块的大小（词数）
    "chunk_overlap": 100   // 相邻块重叠词数
  }
}
```

**想用 OpenAI 兼容的 embedding（如 Qwen）：**
将 `api/config/embedder_openai_compatible.json` 的内容复制到 `embedder.json`，并在 `.env` 设置 `OPENAI_API_KEY` 和 `OPENAI_BASE_URL`。

---

### api/config/repo.json

控制**扫描仓库时哪些文件/目录被跳过**。

```json
{
  "file_filters": {
    "excluded_dirs": [".git", "node_modules", "venv", ...],
    "excluded_files": ["yarn.lock", "*.min.js", "*.exe", ...]
  },
  "repository": {
    "max_size_mb": 50000   // 仓库最大体积限制（50GB）
  }
}
```

如果你的项目有特定目录不想被分析（比如 `vendor/`、`testdata/`），在 `excluded_dirs` 里加上就行。

---

## MiniMax 接入细节

### 关键注意点

| 事项 | 说明 |
|------|------|
| **API 地址** | `https://api.minimaxi.com/v1`（有两个 `i`，不是 `api.minimax.chat`） |
| **可用模型** | `MiniMax-M2.7`（推荐）、`abab6.5s-chat`、`abab6.5-chat` |
| **不可用模型** | `MiniMax-Text-01`（报错：token plan not support this model） |
| **Embedding 兼容性** | MiniMax embedding API 用 `texts` 字段，OpenAI 用 `input`，不兼容，导致 RAG pipeline 报错 |
| **Anthropic 兼容接口** | `https://api.minimaxi.com/anthropic`（也可用，但 deepwiki 走 OpenAI 兼容接口） |

### Embedding 兼容性问题（已知限制）

MiniMax 的 `/v1/embeddings` 接口要求请求体是 `{"texts": [...]}` 而不是 OpenAI 标准的 `{"input": [...]}`。deepwiki 的 ADALFlow 用 OpenAI 格式发请求，MiniMax 忽略了 `input` 字段并返回空向量，导致：

```
ValueError: No valid documents with embeddings found (168 documents had empty embeddings)
```

**临时解决方案**：用 `scripts/generate_wiki_direct.py` 直接读文件、调聊天 API，完全绕过 embedding。

**彻底解决方案**（如需 Web UI 支持本地仓库）：在 `api/` 下实现自定义 `MinimaxEmbedderClient`，重写 embedding 请求格式。

---

## 常见问题

**Q: Web UI 能给本地仓库生成 Wiki 吗？**

可以。在 Web UI 输入框选择「Local」类型，输入本地仓库绝对路径（如 `/Users/zyongzhu/workbase/agent/agent-manage-backend`），选择 MiniMax provider，点击 Generate Wiki 即可。后端会跳过 embedding 步骤，直接读取本地文件调用 LLM。

**Q: scripts/generate_wiki_direct.py 生成的内容开头有 `<think>...</think>` 是什么？**

MiniMax-M2.7 是思维链模型，会先输出推理过程再输出正文。可以用 `sed` 过滤：
```bash
sed '/<think>/,/<\/think>/d' WIKI.md > WIKI_clean.md
```

**Q: 想换 Google Gemini 用 Web UI 正常生成怎么办？**

Google 的 embedding API 完全兼容，是目前最稳定的方式：
```bash
# .env
GOOGLE_API_KEY=your_key
DEEPWIKI_EMBEDDER_TYPE=google
```
然后重启后端。

**Q: 重启机器后服务没了怎么恢复？**

手动重新启动：
```bash
# 后端
cd /Users/zyongzhu/workbase/github/moon/deepwiki-open
source .venv/bin/activate && python -m api.main &

# 前端
npm run dev &
```

**Q: Web UI 生成的 wiki 缓存在哪里？**

`~/.adalflow/` 下，包括：
- `~/.adalflow/repos/` — clone 的远程仓库
- `~/.adalflow/databases/` — embedding 索引
- `~/.adalflow/wikicache/` — 生成的 wiki 缓存

清理缓存：`rm -rf ~/.adalflow/`
