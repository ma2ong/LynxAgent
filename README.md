# AlphaAgent

> Find your alpha. A 股量化形态选股 + 多智能体深度分析的轻量 SaaS。

## 这是什么

- **后端**：FastAPI 轻量服务（`app/`），SQLite 存认证与自选股，量化能力来自 `tradingagents/quant`。
- **量化核心**：`tradingagents/`，A 股形态扫描、K 线图服务、智能选股池等。
- **前端**：Vue 3 + Element Plus（`frontend/`）。

## 运行

后端：

```bash
pip install -e .
# 可选：在 .env 设置 JWT_SECRET，以及 MONGO_URI（不设则关闭行业富集，不影响主流程）
uvicorn app.lite_main:app --reload --port 8000
```

前端：

```bash
cd frontend
npm install
npm run dev
```

## 许可

本仓库全部源码均为作者原创。许可条款见 [`LICENSE`](./LICENSE)，版权声明见 [`NOTICE`](./NOTICE)。
