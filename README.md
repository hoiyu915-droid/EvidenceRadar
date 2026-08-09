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
| ChatGPT Work | 研究者主動執行完整 evidence review | 讀取固定 commit 的公開 repository，或上傳 released Work Pack | 即時搜尋、直接來源閱讀、claim／數字／衝突核實 |

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
5. 若要將來源英文摘要翻成繁中，另設選用的
   `EVIDENCERADAR_TRANSLATION_API_KEY` Secret；沒有設定仍會輸出繁中 metadata
   fallback，不會在簡述欄貼回英文。

使用者的設定、State 與產出只留在自己的 repository，不需要下載另一份部署
程式，也不會把資料寫回 upstream。完整設定見
[`docs/GITHUB_DEPLOYMENT.md`](docs/GITHUB_DEPLOYMENT.md)。

### 路徑 B：ChatGPT Work 使用者啟動

一般執行可讓 Work 直接讀取公開 repository 的 `main`，先固定本輪 commit SHA，
再在 **Work VM** 的新 run-id 目錄中執行、驗證與封裝；不需要先下載部署檔。
這些檔案保存在 Work VM，並以唯一名稱的 run-id ZIP 與 checksum 交付，不會自動
寫回 GitHub，也不會因本機路徑而自動取得公開網址。

若 Work VM 無法取得 repository checkout，或需要離線／固定版本輸入，再下載
GitHub Release 內成對的 Work Pack：

```text
EvidenceRadar-WorkPack-v<VERSION>.zip
EvidenceRadar-WorkPack-v<VERSION>.zip.sha256
```

驗證 checksum、解壓並上傳到自己的 ChatGPT Work project。首次可沒有 State；
第二次起另外帶入最新 `EvidenceRadar_State.json`。Work Pack 只有 protocol、設定、
schema、template、範例、migration、manifest 與 dependency-free 的 State merge、
四件套 validation 及 run-id delivery packager，不含 credentials、歷史 State、
每日報告或 Python crawler。

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

## GitHub lane 的 10–15 出版社存取預算

GitHub runner 每輪以 10 筆可存取 publisher/source-page audit records 為目標，
出版社頁面嘗試數硬上限為 15。每個 resolved domain 最多兩次，request 之間延遲，
遇到 HTTP `401`、`403` 或 `429` 立即停止該網域。

來源不足或被擋時允許少於 10，並保留 warning/gap；禁止補位，也不會超過 15。
這個 budget **只限制 publisher 網路探測，不限制候選保存或顯示**。GitHub HTML
依類別先顯示約 5–8 筆 Featured，再提供可搜尋、可展開的完整候選池；
`EvidenceRadar_Run.json` 的 `candidates` ledger 保存相同的完整候選集，包括低優先、
回補索引、更正／撤回、來源受阻與尚未探測者。
來源頁成功開啟只代表 access audit，不代表研究 claim 已被核實。

OA 狀態與本輪全文存取結果是兩個欄位：`oa_status: YES` 可同時搭配
`access_status: BLOCKED`。PMCID 與 arXiv 候選保留直接 HTML/PDF 下載連結；
DOI、PubMed、OpenAlex 或 abstract landing page 的 HTTP 200 不會被誤報為全文可讀。

HTML 提供題名／作者／期刊／簡述搜尋，以及類別、閱讀層級、來源、OA、全文存取
篩選與分類收合。套用篩選時會展開完整池，不會只搜尋 Featured。
每個 item 都有繁體中文內容簡述。設定 `EVIDENCERADAR_TRANSLATION_API_KEY` 時，
GitHub lane 會批次翻譯有限長度的 provider abstract 節錄；沒有憑證、翻譯失敗或沒有
abstract 時，改用繁中 metadata／題名層級 fallback，不在簡述區顯示英文摘要。
這些文字只協助瀏覽，不會被當成全文 claim 驗證。

### 每輪 source coverage CHECK

每個 enabled stream 所列的 distinct source，每輪都要在 Run 的
`source_coverage.checks` 與 Evidence 的 `coverage.checks` 出現一筆 CHECK
摘要，即使沒有結果、沒有該 lane 的 adapter、請求被擋，或 bounded
verification 沒有合資格候選。CHECK 狀態固定為：

目前 discovery adapters 覆蓋 PubMed、Europe PMC、OpenAlex、arXiv、OpenReview、
ACL Anthology 與 PMLR；publisher 與 formal proceedings 走後段 bounded
verification check。

| 狀態 | 意義 |
|---|---|
| `SUCCESS` | 已完成來源操作且取得一筆以上結果 |
| `NO_RESULTS` | 已查詢來源，但結果為零 |
| `FAILED` | 已嘗試但 provider/access 操作失敗 |
| `NOT_ATTEMPTED` | 已設定但本輪沒有發出請求 |

`source_coverage.checked` 是「有 CHECK 記錄」的 source ID 集合，**不等於
成功**；`searched` 表示實際發出請求的來源，`unavailable` 表示失敗或未嘗試，
`all_configured_sources_checked` 只有在每個 requested source 都有 CHECK 時才為
true。`publisher` 與 `formal_proceedings_or_publisher` 都是
`bounded_verification` stage，即使 publisher 10–15 探測預算沒有 eligible item，
也必須寫出 `NO_RESULTS`、`FAILED` 或 `NOT_ATTEMPTED` 的 check summary。

設定位於 [`config/deployment.yml`](config/deployment.yml)，手動 workflow 可在
本輪覆寫 `publisher_target_min` 和 `publisher_hard_max`，但 runner 仍強制
`0 ≤ target ≤ hard max`。

## 四個 artifact 與狀態同步

每條 lane 都必須產生：

| Artifact | 用途 |
|---|---|
| `EvidenceRadar_Report.html` | 主要可閱讀報告 |
| `EvidenceRadar_State.json` | 所有已發現候選的跨輪 identity、seen history、已通知事件與 aliases |
| `EvidenceRadar_Evidence.json` | claim、數字、locator 與來源 |
| `EvidenceRadar_Run.json` | 完整候選 ledger、時窗、查詢、source CHECK 覆蓋、provenance、警告與統計 |

新 State／Run 顯式記錄 `execution_lane`、`protocol_commit`、
`base_state_sha256`、`parent_run_ids`。GitHub canonical State 位於
`state/current/EvidenceRadar_State.json`；目前產出位於 `artifacts/current/`；
每次完整快照位於 `runs/<run_id>/`。

公開 repository 可用 GitHub Pages 把驗證後的 current HTML 變成真正可直接點閱
的網址：`https://OWNER.github.io/REPOSITORY/`。部署同時產生 `links.json`，列出
最新報告、三份 JSON 與 immutable run 的固定連結。GitHub blob/raw 網址與
ChatGPT Work 的本機路徑不算 HTML 交付；Pages deployment 成功後才回報公開連結。

`tools/validate_delivery_bundle.py` 會把四件套視為一個整體，檢查 producer/lane
provenance、canonical State、source coverage、完整候選 ledger、Featured/full-pool
標記、OA／全文存取一致性，以及 HTML 實際顯示的 work IDs。只有具可稽核直接全文
probe 的來源才能支持 `SUPPORTED` claim。這道閘門防止舊 runner、手填狀態或舊
Work Pack 在新版合併後覆寫 current。

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
- [`tools/package_work_delivery.py`](tools/package_work_delivery.py)：驗證後建立唯一 run-id Work ZIP／manifest／checksum
- [`tools/validate_delivery_bundle.py`](tools/validate_delivery_bundle.py)：四件套與 HTML 一致性／producer 閘門
- [`tools/build_pages_site.py`](tools/build_pages_site.py)：產生 Pages 站點與 `links.json`
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
