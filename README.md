# Slay AI

《杀戮尖塔》(Slay the Spire) 实时辅助决策工具。点击桌面悬浮球，自动截图识别当前局面，由多模态大模型 + RAG 给出可执行的操作建议。

不修改游戏、不读写游戏内存，只是一个"旁观你屏幕的助手"：

```text
截图 -> 视觉模型识别画面 -> 后端整理成 GameState
     -> RAG 查询游戏资料 -> 推理模型生成建议 -> 悬浮窗流式展示
```

## 特性

- **零侵入** — 纯屏幕截图 + 模拟按键，不注入进程、不改存档
- **悬浮球交互** — Tkinter 置顶悬浮球，点击即分析，右键退出，位置可拖动并保存
- **流式输出** — SSE 推送 `status` / `token` / `error` / `done`，悬浮窗逐字追加建议
- **固定工作流 + 推理模型** — 高风险动作（截图、滚动、按键）由后端严格控制，推理模型只消费结构化 `GameState`
- **混合 RAG** — `Qwen Embedding + FAISS` 语义召回 + `jieba + BM25` 文本召回 + `qwen-rerank` 重排，把外观描述映射到真实游戏实体（药水 / 遗物 / 怪物）
- **可取消 + 并发锁** — 同时只允许一次分析，关闭悬浮窗即通知后端中止
- **多级降级** — 模型 JSON 解析失败自动修复；rerank 不可用回退混合打分；MySQL 不可用时主流程不崩溃

## 技术栈

| 层 | 组件 |
| --- | --- |
| 后端 | FastAPI / Uvicorn、LangChain、Pydantic |
| 采集 | pyautogui（截图 / 按键 / 滚轮）、Pillow |
| 检索 | FAISS、rank-bm25 + jieba、httpx（rerank） |
| 存储 | MySQL + PyMySQL |
| 前端 | Tkinter、requests、threading |

模型：视觉 `qwen3.7-flash`，推理 `deepseek-v4-flash`，Embedding `qwen3.7-text-embedding`，Rerank `qwen3-rerank`（均可通过环境变量覆盖）。

## 环境要求

```text
Python  >= 3.12
MySQL   >= 5.7 / 8.0
系统     Windows（截图坐标与启动脚本按 Windows 调优）
游戏     《杀戮尖塔》，窗口需保持前台可见
```

## 快速开始

```bash
# 1. 安装依赖（使用 uv）
uv sync

# 2. 配置环境变量，然后填入真实 Key 与 MySQL 连接信息
cp .env.example .env        # Windows: copy .env.example .env

# 3. 启动后端
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# 4. 另开一个终端，启动桌面悬浮球
uv run python frontend/overlay.py
```

Windows 下可一键启动后端 + 前端：

```bat
start_all.bat
start_all.bat stop    :: 停止后端
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok","service":"Slay AI Backend"}
```

> ⚠️ 读取牌库时后端会真实按下 `D`、滚动滚轮、按下 `Esc`。坐标没调好前不要在重要局面里测试。

## 环境变量

配置从根目录 `.env` 读取，完整模板见 [`.env.example`](.env.example)。

| 变量 | 说明 |
| --- | --- |
| `QWEN_API` | Qwen API Key（`QUEN_API` 为兼容拼写，填一个即可） |
| `QWEN_BASE_URL` | Qwen OpenAI 兼容接口地址 |
| `DEEPSEEK_API_KEY` | DeepSeek API Key |
| `QWEN_VISION_MODEL` | 视觉模型，默认 `qwen3.7-flash` |
| `QWEN_EMBEDDING_MODEL` | 向量模型，默认 `qwen3.7-text-embedding` |
| `QWEN_RERANK_MODEL` | 重排模型，默认 `qwen3-rerank` |
| `QWEN_RERANK_URL` | 留空时自动使用 `{QWEN_BASE_URL}/rerank` |
| `DEEPSEEK_BASE_URL` | 默认 `https://api.deepseek.com` |
| `DEEPSEEK_REASONING_MODEL` | 推理模型，默认 `deepseek-v4-flash` |
| `MODEL_TIMEOUT_SECONDS` | 模型调用超时，默认 `60` |
| `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` | MySQL 连接（`MYSQL_*` 命名同样兼容） |
| `HUIJI_COOKIE` | 仅数据爬取脚本需要 |

缺少 `QWEN_API`、`QWEN_BASE_URL`、`DEEPSEEK_API_KEY` 时启动会直接报错。`.env` 已被 `.gitignore` 忽略。

## 数据准备

需要 MySQL 中存在 5 张表：`kapai`（卡牌：`name`/`cost`/`xiaoguo`）、`shijian`（事件：`name`/`celue`）、`yiwu`、`yaoshui`、`guaiwu`（后三张用于 RAG，字段 `id`/`name`/`miaoshu`/`xiaoguo`，`guaiwu` 另有 `celue`/`fenzu`）。

数据可用仓库自带脚本从灰机 wiki 抓取，再构建 FAISS 索引：

```bash
uv run python scripts/crawl_kapai.py      # 其余: crawl_yiwu / crawl_yaoshui / crawl_guaiwu / crawl_shijian
uv run python scripts/build_faiss_indexes.py
uv run python scripts/search_faiss.py     # 验证召回效果
```

索引文件位于 `indexes/faiss/`，更新数据后需重建，并确认 `meta.json` 的 `ids` 与数据库 `id` 对应。

## 项目结构

```text
slay_ai/
├── main.py                      直接启动 FastAPI 的入口
├── start_all.bat                Windows 一键启动后端 + 悬浮球
├── config/regions.json          截图区域与动作参数
├── app/
│   ├── api/routes.py            /health、/assist/stream、/assist/cancel
│   ├── core/                    配置、模型封装、提示词、路径
│   ├── schemas/                 GameState 与视觉模型输出结构
│   ├── vision/                  截图、区域读取、视觉模型调用与解析
│   ├── rag/                     MySQL、FAISS、BM25 + rerank 混合检索
│   ├── tools/game_tools.py      游戏状态采集工具
│   ├── agent/                   各界面固定工作流、推理模型流式输出
│   └── services/                一次求助请求的总编排
├── frontend/overlay.py          桌面悬浮球与悬浮窗
├── indexes/faiss/               RAG 向量索引与 meta
├── img/                         测试截图与实体图片
└── scripts/                     数据爬取、图片描述、索引构建
```

## 支持的界面

`combat` 战斗 · `campfire` 火堆 · `event` 事件 · `card_reward` 选牌奖励 · `boss_relic` Boss 遗物 · `start_event` 开局事件 · `shop` 商店 · `unknown` 未识别

每个界面有固定的采集工作流（`app/agent/workflows.py`），采集结果统一整理为 `GameState`（`app/schemas/game.py`），推理模型只基于该对象给出建议。截图坐标集中在 `config/regions.json`，区域用左上角 + 右下角两点表示。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/health` | 健康检查 |
| `GET` / `POST` | `/assist/stream` | 发起一次辅助分析（SSE） |
| `POST` | `/assist/cancel` | 取消当前分析 |

SSE 事件：`status` 采集进度、`token` 建议片段、`error` 错误、`done` 结束。另有注释心跳 `: ping`，非可见事件，前端不要展示。

## 已知限制

- 截图坐标强依赖分辨率、窗口位置与游戏 UI 缩放，换设备需重新标定
- `start_event` 尚未完整识别涅奥奖励选项
- 战斗中暂未单独识别玩家格挡、buff/debuff、抽牌堆、弃牌堆、消耗堆
- 模型请求发出后，取消无法保证立刻打断远端推理
- RAG 效果依赖 MySQL 数据质量与 FAISS 索引是否同步

## 免责声明

本项目为个人技术学习项目，与 Mega Crit Games 无关。《杀戮尖塔》(Slay the Spire) 及相关素材版权归其各自所有者。
