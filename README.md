# EvidenceRadar

EvidenceRadar 是一套在 **ChatGPT Work** 內執行的每日研究工作流。它以即時網路搜尋、直接來源讀取、來源連結與可攜狀態檔完成發現、去重、事件核實、證據治理與報告交付。

本 repository 只保存工作流協議、搜尋政策、評分規則、輸出 schema、模板與歷史快照。它不執行每日掃描，也不承擔持久服務責任。

產品能力基線採用 OpenAI 對 ChatGPT Work 的公開定位：以目標、檔案與上下文進行研究、綜合與交付物製作。參考：[OpenAI Developers](https://developers.openai.com/) 與 [ChatGPT research use cases](https://learn.chatgpt.com/use-cases?team=research)。

## 執行邊界

正式執行只有一條路徑：

```text
使用者在 ChatGPT Work 啟動 EvidenceRadar
→ 讀取本 repo 的 protocol/config/template 與上輪 State artifact
→ 即時 web search + 直接開啟權威來源頁
→ 建立候選、事件、claim 與來源覆蓋紀錄
→ 產生 HTML 報告及三個 JSON artifacts
→ 使用者在下一輪重新提供最新 State artifact
```

以下項目均不屬於正式 runtime：

- MCP
- 自架或託管 server
- Codex
- GitHub Actions
- repository 自動 commit／push
- repo 內 Python crawler

`daily/` 與 `state/` 是 2026-08-08 前的歷史快照，只讀保存。GPT Work 新產生的 artifact 不會自動寫回 repository。

## 啟動方式

在同一個 ChatGPT Work project 中提供：

1. [`EVIDENCE_RADAR_PROTOCOL.md`](EVIDENCE_RADAR_PROTOCOL.md)
2. `config/`、`docs/research_taxonomy.md` 與 `templates/gpt-work-instructions.md`
3. 上一輪 `EvidenceRadar_State.json`；首次執行可省略

然後輸入：

```text
依 EvidenceRadar protocol 執行 daily 模式。使用目前時間往回 72 小時，搜尋五個類別，重新讀取實際來源，產生完整 HTML、State、Evidence 與 Run artifacts。
```

若缺少上輪 State，該輪必須標記 `STATE_HISTORY_INCOMPLETE`，不得宣稱已完成跨輪去重。

## 五個獨立類別

1. Clinical Medicine
2. Sport Science
3. Sport Nutrition & Fitness
4. LLM Research
5. Human–AI Research

LLM Research 使用 L1–L9；Human–AI Research 使用 H1–H2。Venue 只記錄 publication identity，不作 taxonomy。完整定義見 [`docs/research_taxonomy.md`](docs/research_taxonomy.md)。

## 合格事件

只有下列事件可以通過最近 72 小時事件窗：

1. version of record 首次 online
2. 首次正式索引
3. 正式 proceedings 釋出
4. OA 全文首次可用
5. author accepted manuscript 首次可用
6. embargo 解除
7. preprint 升級 peer-reviewed version
8. 正式版本完成核實

搜尋結果日期、搜尋引擎 freshness、卷期回填或 metadata 更新不能單獨作為新事件。

## 兩階段研究

### Discovery

- 逐類執行即時搜尋
- 記錄實際查詢、來源 URL、搜尋時間與存取結果
- 以 DOI → PMID → PMCID → arXiv ID → Anthology ID → OpenAlex ID → normalized title 去重
- 建立 Candidate Pool；不足時不補位

### Evidence verification

- 只對高排名候選直接讀取摘要、全文或出版者頁面
- 核實事件、版本、全文狀態、研究設計、主要結果、限制、校正與撤稿
- 所有可見數字保存正負號、單位、方向、比較組與原文 locator
- claim support 只能是 `SUPPORTED`、`PARTIAL`、`CONFLICT`、`UNVERIFIED`

## 每輪交付

| Artifact | 用途 |
|---|---|
| `EvidenceRadar_Report.html` | 主要可閱讀報告 |
| `EvidenceRadar_State.json` | 跨輪 identity、已通知事件與 alias |
| `EvidenceRadar_Evidence.json` | claim、數字、locator 與來源 |
| `EvidenceRadar_Run.json` | 時間窗、查詢、來源覆蓋、警告與統計 |

Run 狀態只可使用：

- `COMPLETE`
- `PARTIAL_SOURCE_COVERAGE`
- `SOURCE_ACCESS_GAP`
- `STATE_HISTORY_INCOMPLETE`
- `NO_QUALIFYING_ITEMS`

來源不完整時不得更新為 `COMPLETE`，也不得把「搜尋不到」寫成「沒有新研究」。

## Repository 導航

- [`EVIDENCE_RADAR_PROTOCOL.md`](EVIDENCE_RADAR_PROTOCOL.md)：完整執行與停止條件
- [`templates/gpt-work-instructions.md`](templates/gpt-work-instructions.md)：可直接放入 ChatGPT Work project 的指令
- [`templates/daily-radar.md`](templates/daily-radar.md)：報告內容模板
- [`config/streams.yml`](config/streams.yml)：搜尋目標與導航查詢
- [`config/scoring.yml`](config/scoring.yml)：排序與 evidence-governance 門檻
- [`config/output.yml`](config/output.yml)：artifact 契約
- `schemas/`：三個 JSON artifact schema
- `examples/`：最小有效 artifact 範例
- `tools/validate_gpt_work_artifacts.py`：標準函式庫結構驗證
- [`LEGACY_RUNTIME.md`](LEGACY_RUNTIME.md)：2026-08-08 前 Python runtime 說明

## 下游邊界

EvidenceRadar 的終點是核實過的研究封包。除非使用者另行要求，不啟動 TA／TP03 或圖像生成。醫療決策、跨來源數字比較、來源衝突與正式交付必須保留 governed 狀態，不得降級成未核實的 Quick 內容。

## Why EvidenceRadar matters

Research monitoring is not merely a search problem. A reusable radar must distinguish discovery dates from publication events, preserve publication identity across versions, expose source gaps, prevent repeated notifications, and keep every reported claim tied to readable evidence. EvidenceRadar makes those decisions explicit through versioned protocols, schemas, validators, governed status values, and portable state.

## Runtime and maintainer tooling

ChatGPT Work is the formal EvidenceRadar execution environment described above. Codex is **not** part of the radar runtime and is not required by downstream users.

Codex may be used separately as maintainer tooling for repository inspection, issue and pull-request work, schema and validator changes, regression repair, documentation, release preparation, and public-source auditing. The primary maintainer reviews every change and remains responsible for evidence policy, licensing, security, and merge decisions.

## Open-source maintenance

- Primary maintainer: `hoiyu915-droid`
- Maintained line: `main`
- Governance: [`GOVERNANCE.md`](GOVERNANCE.md)
- Contributions: [`CONTRIBUTING.md`](CONTRIBUTING.md)
- Security reports: [`SECURITY.md`](SECURITY.md)
- Roadmap: [`ROADMAP.md`](ROADMAP.md)
- Public-release audit: [`PUBLIC_RELEASE_AUDIT.md`](PUBLIC_RELEASE_AUDIT.md)
- Maintenance CI: [`.github/workflows/public-release.yml`](.github/workflows/public-release.yml) validates repository hygiene, examples, and tests; it is not the radar runtime.

## Adoption and impact

EvidenceRadar is intended for researchers, clinicians, evidence communicators, and tool builders who need auditable recent-literature triage rather than an unqualified list of search results. No unverified stars, downloads, users, or deployment figures are claimed. Public integrations, citations, issues, and downstream repositories may be recorded as verifiable evidence of impact.

## License

Executable workflow material, schemas, validators, source code, tests, templates, and legacy runtime code are licensed under Apache-2.0. Original documentation, original report prose and layout, and original compilation or arrangement of structured records are licensed under CC BY 4.0.

Article titles, authors, identifiers, publisher metadata, quoted excerpts, linked pages, and trademarks are not relicensed. See [`LICENSE`](LICENSE), [`LICENSE-CONTENT.md`](LICENSE-CONTENT.md), and [`NOTICE.md`](NOTICE.md).

## Citation

Use [`CITATION.cff`](CITATION.cff) and cite the exact commit or release used.
