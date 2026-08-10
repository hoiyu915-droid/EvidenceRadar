# EvidenceRadar

EvidenceRadar 是一套可公開自行部署、可稽核的近期研究雷達。它把「發現新文獻」
與「證據已核實」分開，使用事件窗、publication identity、來源覆蓋、claim ledger
與可攜 State 防止重複通知和過度宣稱。

新的 `SEMANTIC_CONTRACT_V3` 再把 discovery、content fetch、claim verification、
source identity/access observation、citation binding、model inference、數字前提與
衝突拆開。完整規格見
[`docs/SEMANTIC_CONTRACT_V3.md`](docs/SEMANTIC_CONTRACT_V3.md)；舊 V2 bundle
仍可驗證，新 V3 bundle 則啟用額外 fail-closed 規則。

GitHub Actions and ChatGPT Work are the two supported EvidenceRadar execution lanes.
兩條 lane 共用同一份 protocol、config、schema 與四個 artifact 契約，但驗證能力
保持可見差異：

| Lane | 適合用途 | 啟動方式 | 驗證邊界 |
|---|---|---|---|
| GitHub Actions | 每日 discovery/source audit 與翻譯 request | 排程或手動 Run workflow | Stage A 上傳 request 並建立 metadata-only Work queue；Stage B 只接受 SHA-bound response |
| ChatGPT Work | 研究者主動執行完整 evidence review | 讀取固定 commit 的公開 repository，或上傳 released Work Pack | 即時搜尋、直接來源閱讀、claim／數字／衝突核實 |

## 自行部署：不必共用維護者的設定

### 路徑 A：GitHub 每日自動執行

1. 按 **Use this template** 建立自己的 repository；需要保留 upstream 關係時
   也可以 fork。
2. 在自己的 repository 啟用 Actions；Stage A workflow 讀取 contents，並以
   `issues: write` 建立不含研究內容的 Work queue metadata。
3. 到 **Actions → EvidenceRadar daily (GitHub Actions lane) → Run workflow**
   先跑一次。之後 workflow 每日依 `Asia/Tokyo` 排程執行。
4. 把免費的 `OPENALEX_API_KEY` 設為 repository Secret，供 OpenAlex discovery
   使用。`NCBI_EMAIL`、`NCBI_API_KEY` 為 PubMed 建議／提額設定。缺少某來源的
   必要 key 時，runner 會 fail closed 記錄該來源 gap，而不是填入假結果。
5. Workflow 正常結束為 `TRANSLATION_REQUIRED`，上傳
   `EvidenceRadar_TranslationRequest.json` 並建立 `evidenceradar-handoff` issue。
   受限的 ChatGPT Work 排程會分批翻譯、逐批驗證與 checkpoint，完成後經 PR
   提交 SHA-bound response；Actions 再以 request 的 exact producer commit 執行
   Stage B。此流程不需 repository model API key 或 Copilot。

使用者的設定與 canonical State 留在自己的 repository；request 與 Stage B
候選包保留為限時 Actions artifacts。Response 與 canonical publication 都只經過
受審閱 PR 寫入自己的 repository，不會寫回 upstream。完整設定見
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
第二次起另外帶入最新 `EvidenceRadar_State.json`。Work Pack 包含 protocol、設定、
schema、template、範例、migration、manifest、V3 canonical renderer、目前 GitHub
runner、State merge、四件套 validation、run-id delivery packager 與 requirements；
不含 credentials、歷史 State、每日報告、CI 或 legacy crawler。

Web scheduled task 不保留本機 checkout 或 project folder；OpenAI 的現行說明是它
可使用 connected tools、plugins 與 skills，但 durable input 必須留在可存取的
project、upload 或 connected service：
[Scheduled tasks](https://learn.chatgpt.com/docs/automations)。因此 EvidenceRadar
仍由 GitHub Actions 執行 Stage A；Work 只從 immutable Actions artifact 讀取 frozen
request，把 checkpoint 留在專用 branch，最後以 PR 交回 response。這個 control
plane 不會把 scheduled translation 誤標成 `chatgpt_work` evidence-review lane。

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
每個 item 都必須有結構化 `title_zh_tw`。Stage A 的 request 以 canonical SHA-256
綁定候選與凍結 resume context；Stage B 只接受同 SHA、exact ID parity 的普通 ChatGPT
response。任何英文題名未成功翻譯、數字／年份／縮寫遺失，或回傳「題名所示／相關議題」
等模板 filler 時，本輪 fail closed，不發佈 HTML。來源有
摘要時可在中文題名後追加一至兩句研究目的／對象／方法簡述；沒有摘要時就只顯示忠實
中文題名，不用空話補字數。這些 navigation text 不會被當成全文 claim 驗證。

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

V3 的每項 query／fetch／claim verification 都要有 executor receipt，逐一對帳
query、source access、CHECK、candidate IDs 與 pagination；「模型說查過」不算。
來源以 canonical URL 重用 stable source ID，實際存取另存 append-only observation。
Follow-up 必須由既有 gap 觸發並受 attempt/cooldown 上限控制。Claim 必須綁
source、locator、origin 與 access depth；`MODEL_INFERENCE` 只進 inference ledger。
數字則保存 population、exposure、comparator、outcome、timeframe、effect measure、
analysis set 與不確定性，衝突保留為結構資料。

ChatGPT Work 先完成三份 JSON，再執行：

```sh
python3 tools/render_report_from_artifacts.py --bundle "$WORK_RUN_DIR"
```

最終 HTML 只能是這個 canonical projection。候選中文簡述是 navigation text；實質
claim 以 claim ID 綁 Evidence。手動加進 HTML 的結論或數字會被 byte-parity gate
拒絕。

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
- [`tools/render_report_from_artifacts.py`](tools/render_report_from_artifacts.py)：由 V3 JSON canonical render HTML
- [`tools/validate_delivery_bundle.py`](tools/validate_delivery_bundle.py)：四件套與 HTML 一致性／producer 閘門
- [`tools/build_pages_site.py`](tools/build_pages_site.py)：產生 Pages 站點與 `links.json`
- [`templates/gpt-work-instructions.md`](templates/gpt-work-instructions.md)：Work 指令
- [`docs/MIGRATION_DUAL_LANE_1.0.md`](docs/MIGRATION_DUAL_LANE_1.0.md)：相容性與 rollback
- [`docs/SEMANTIC_CONTRACT_V3.md`](docs/SEMANTIC_CONTRACT_V3.md)：retrieval／source／claim／gap／numeric V3 契約
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
