# EvidenceRadar

EvidenceRadar 是一套可公開自行部署、可稽核的近期研究雷達。它把「發現新文獻」
與「證據已核實」分開，使用事件窗、publication identity、來源覆蓋、claim ledger
與可攜 State 防止重複通知和過度宣稱。

GitHub Actions and ChatGPT Work are the two supported EvidenceRadar execution lanes.
兩條 lane 共用同一份 protocol、config、schema 與四個 artifact 契約，但驗證能力
保持可見差異：

| Lane | 適合用途 | 啟動方式 | 驗證邊界 |
|---|---|---|---|
| GitHub Actions | 每日無人值守 discovery/source audit | 排程或手動 Run workflow | metadata、事件窗、來源頁可存取性；不產生未審閱科學結論 |
| ChatGPT Work | 研究者主動執行完整 evidence review | 上傳 Work Pack 並下指令 | 即時搜尋、直接來源閱讀、claim／數字／衝突核實 |

## 自行部署：不必共用維護者的設定

### 路徑 A：GitHub 每日自動執行

1. 按 **Use this template** 建立自己的 repository；需要保留 upstream 關係時
   也可以 fork。
2. 在自己的 repository 啟用 Actions，並允許 workflow 的 `GITHUB_TOKEN`
   寫入 repository contents。
3. 到 **Actions → EvidenceRadar daily (GitHub Actions lane) → Run workflow**
   先跑一次。之後 workflow 每日依 `Asia/Tokyo` 排程執行。
4. 把免費的 `OPENALEX_API_KEY` 設為 repository Secret，供 OpenAlex discovery
   使用。`NCBI_EMAIL`、`NCBI_API_KEY` 為 PubMed 建議／提額設定。缺少某來源的
   必要 key 時，runner 會 fail closed 記錄該來源 gap，而不是填入假結果。

使用者的設定、State 與產出只留在自己的 repository，不需要下載另一份部署
程式，也不會把資料寫回 upstream。完整設定見
[`docs/GITHUB_DEPLOYMENT.md`](docs/GITHUB_DEPLOYMENT.md)。

### 路徑 B：ChatGPT Work 使用者啟動

一般使用者只要下載 GitHub Release 內成對的：

```text
EvidenceRadar-WorkPack-v<VERSION>.zip
EvidenceRadar-WorkPack-v<VERSION>.zip.sha256
```

驗證 checksum、解壓並上傳到自己的 ChatGPT Work project。首次可沒有 State；
第二次起另外帶入最新 `EvidenceRadar_State.json`。Work Pack 只有 protocol、設定、
schema、template、範例、migration、manifest 與兩個 dependency-free 的 State／
validation tools，不含 credentials、歷史 State、每日報告或 Python crawler。

這條 lane 不宣稱能把含 Work Pack 檔案的 project 直接變成 unattended scheduled
run。OpenAI 目前說明：[在含檔案的 project 建立 Scheduled Task 時，該 task
不能存取 project files](https://help.openai.com/en/articles/10291617-tasks-in-chatgpt)。
因此每日排程由 GitHub Actions lane 負責；Work lane 由使用者開啟 pack 後啟動。

維護者或從 source 使用的人可重現建置：

```sh
python3 tools/build_work_pack.py --output-dir dist
```

完整步驟見 [`docs/WORK_SETUP.md`](docs/WORK_SETUP.md)。因此公開使用有兩種低摩擦
方式：GitHub 使用 template/fork；ChatGPT Work 使用一個有版本與 SHA-256 的可攜
部署包。

## GitHub lane 的 10–15 出版社輸出預算

GitHub runner 每輪以 10 筆可存取 publisher/source-page audit records 為目標，
出版社頁面嘗試數硬上限為 15。每個 resolved domain 最多兩次，request 之間延遲，
遇到 HTTP `401`、`403` 或 `429` 立即停止該網域。

來源不足或被擋時允許少於 10，並保留 warning/gap；禁止補位，也不會超過 15。
這個 budget 與每個 active category 的 Featured 5–8 target 是兩件事。來源頁成功
開啟只代表 access audit，不代表研究 claim 已被核實。

設定位於 [`config/deployment.yml`](config/deployment.yml)，手動 workflow 可在
本輪覆寫 `publisher_target_min` 和 `publisher_hard_max`，但 runner 仍強制
`0 ≤ target ≤ hard max`。

## 四個 artifact 與狀態同步

每條 lane 都必須產生：

| Artifact | 用途 |
|---|---|
| `EvidenceRadar_Report.html` | 主要可閱讀報告 |
| `EvidenceRadar_State.json` | 跨輪 identity、已通知事件與 aliases |
| `EvidenceRadar_Evidence.json` | claim、數字、locator 與來源 |
| `EvidenceRadar_Run.json` | 時窗、查詢、來源覆蓋、provenance、警告與統計 |

新 State／Run 顯式記錄 `execution_lane`、`protocol_commit`、
`base_state_sha256`、`parent_run_ids`。GitHub canonical State 位於
`state/current/EvidenceRadar_State.json`；目前產出位於 `artifacts/current/`；
每次完整快照位於 `runs/<run_id>/`。

Work 與 GitHub 從不同 State 分支執行時，使用 deterministic union，不能用較新的
檔案直接覆蓋另一條 lane：

```sh
python3 tools/merge_radar_state.py \
  state/current/EvidenceRadar_State.json \
  /path/to/work/EvidenceRadar_State.json \
  --execution-lane chatgpt_work \
  --protocol-commit COMMIT \
  --output /path/to/merged/EvidenceRadar_State.json
```

## 研究契約

五個獨立類別為 Clinical Medicine、Sport Science、Sport Nutrition & Fitness、
LLM Research 與 Human–AI Research。LLM 使用 L1–L9；Human–AI 使用 H1–H2。
Venue 只記錄 publication identity，不作 taxonomy。

合格事件包括 version of record first online、首次正式索引、正式 proceedings
釋出、OA/AAM 首次可用、embargo 解除、preprint 升級 peer-reviewed version，
以及正式版本完成核實。搜尋引擎 freshness、卷期回填或 metadata 更新不能單獨
作為新事件。

Identity 去重順序固定為：

```text
DOI → PMID → PMCID → arXiv ID → Anthology ID → OpenAlex ID → normalized title
```

Claim support 只可使用 `SUPPORTED`、`PARTIAL`、`CONFLICT`、`UNVERIFIED`；
Run status 只可使用 `COMPLETE`、`PARTIAL_SOURCE_COVERAGE`、
`SOURCE_ACCESS_GAP`、`STATE_HISTORY_INCOMPLETE`、`NO_QUALIFYING_ITEMS`。
詳細規則見 [`EVIDENCE_RADAR_PROTOCOL.md`](EVIDENCE_RADAR_PROTOCOL.md)。

## Repository 導航

- [`config/`](config/)：來源、分類、輸出與部署設定
- [`schemas/`](schemas/)：State、Evidence、Run schema
- [`examples/`](examples/)：最小結構範例，不是目前研究證據
- [`tools/run_github_radar.py`](tools/run_github_radar.py)：新的 GitHub lane runner
- [`tools/merge_radar_state.py`](tools/merge_radar_state.py)：雙 lane State union
- [`tools/build_work_pack.py`](tools/build_work_pack.py)：可重現 Work Pack builder
- [`templates/gpt-work-instructions.md`](templates/gpt-work-instructions.md)：Work 指令
- [`docs/MIGRATION_DUAL_LANE_1.0.md`](docs/MIGRATION_DUAL_LANE_1.0.md)：相容性與 rollback
- [`LEGACY_RUNTIME.md`](LEGACY_RUNTIME.md)：2026-08-08 前 runtime provenance

## Runtime and maintainer tooling

Codex is **not** part of the radar runtime and is not required by downstream
users. It may be used separately for repository maintenance, tests, release
preparation and public-source auditing. MCP and an external server are not
required by either supported lane.

`daily/`、舊 `state/` 與 `legacy/python-runtime/` 是 2026-08-08 前的歷史快照；
新 GitHub runner 不會呼叫 archived crawler。除非使用者另行要求，EvidenceRadar
也不啟動 TA／TP03 或圖像生成。

## Open-source maintenance

- Primary maintainer: `hoiyu915-droid`
- Maintained line: `main`
- Governance: [`GOVERNANCE.md`](GOVERNANCE.md)
- Contributions: [`CONTRIBUTING.md`](CONTRIBUTING.md)
- Security reports: [`SECURITY.md`](SECURITY.md)
- Roadmap: [`ROADMAP.md`](ROADMAP.md)
- Public-release audit: [`PUBLIC_RELEASE_AUDIT.md`](PUBLIC_RELEASE_AUDIT.md)

## License

Executable workflow material, schemas, validators, source code, tests,
templates and legacy runtime code are licensed under Apache-2.0. Original
documentation, report prose/layout and original compilation or arrangement of
structured records are licensed under CC BY 4.0.

Article titles, authors, identifiers, publisher metadata, quoted excerpts,
linked pages and trademarks are not relicensed. See [`LICENSE`](LICENSE),
[`LICENSE-CONTENT.md`](LICENSE-CONTENT.md) and [`NOTICE.md`](NOTICE.md).

## Citation

Use [`CITATION.cff`](CITATION.cff) and cite the exact commit or release used.
