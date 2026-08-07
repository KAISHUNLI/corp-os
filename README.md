# corp-os

企业智能体：两条线。

1. **上传入库**：准入 → 分类/敏感度 → 权限/审批 → 切片检索  
2. **对话使用**：问知识走权限 RAG；办事以后接 ERP（须审批）

## 从零搭建

→ [docs/from-scratch/README.md](docs/from-scratch/README.md)

```bash
cd deploy && cp .env.example .env && docker compose up -d
PYTHONPATH=src .venv/bin/alembic upgrade head
```

## 启动

```bash
# 后端（先起 Postgres）
source .venv/bin/activate
uvicorn corp_os.app:app --reload --app-dir src --host 127.0.0.1 --port 8001

# 前端
cd web && npm run dev
```

- 前端 http://127.0.0.1:5173  
- API http://127.0.0.1:8001/docs  
- 登录：`alice` / `demo123`（更多账号见 seed）

## 上传线怎么读代码

看 `src/corp_os/services/ingest.py`，四个函数按顺序：

`gate_file` → `classify_content` → `authorize_upload` → `commit_document`  
入口：`ingest_upload`

## Embedding + Milvus（第3/4步）

默认本地 BGE + Milvus。切换模型或迁库后重嵌：

```bash
export HF_ENDPOINT=https://hf-mirror.com   # 国内可选
PYTHONPATH=src .venv/bin/python -m corp_os.scripts.reindex_embeddings
```

- Embedding → [docs/from-scratch/03-embedding.md](docs/from-scratch/03-embedding.md)  
- Milvus → [docs/from-scratch/04-milvus.md](docs/from-scratch/04-milvus.md)  
- LLM 回答 → [docs/from-scratch/05-rag.md](docs/from-scratch/05-rag.md)  
- LangGraph → [docs/from-scratch/06-langgraph.md](docs/from-scratch/06-langgraph.md)  
- ERP 工具 → [docs/from-scratch/07-erp-tools.md](docs/from-scratch/07-erp-tools.md)
