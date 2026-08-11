# EvidenceRadar
(目前依然開發及修改中，請複製到個人GitHub ，要求gpt生成你的個人設定)

(目前這repo最大的價值在某些核實方式更技巧例如fetch和search對LLM的分別是甚麼)

(能用但還是需要時間改進，我要等reset⋯⋯ XD)

EvidenceRadar 是一套可公開自行部署、可稽核的近期研究雷達。它把「發現新文獻」
與「證據已核實」分開，使用事件窗、publication identity、來源覆蓋、claim ledger
與可攜 State 防止重複通知和過度宣稱。

## 用家入口：只要執行 Radar

用家向 ChatGPT Work 說「執行文獻雷達」即可。GPT 下載一次正式 Work Pack 與
checksum，之後在同一個 Work 對話內完成搜尋、核實、繁中翻譯、去重、State
合併、HTML render 與四件套驗證，最後直接交付：

```text
EvidenceRadar_Report.html
EvidenceRadar_State.json
EvidenceRadar_Evidence.json
EvidenceRadar_Run.json
```

GitHub 在這條路徑只負責原始碼與版本化 ZIP 儲存。下載完成後不啟動 Actions、
不建立 issue／PR、不等待 Stage A/B，也不要求公開發布才能取得檔案。

新的 `SEMANTIC_CONTRACT_V3` 再把 discovery、content fetch、claim verification、
source identity/access observation、citation binding、model inference、數字前提與
衝突拆開。完整規格見
[`docs/SEMANTIC_CONTRACT_V3.md`](docs/SEMANTIC_CONTRACT_V3.md)；舊 V2 bundle
仍可驗證，新 V3 bundle 則啟用額外 fail-closed 規則。

## 唯一用家執行路徑

GPT 從 GitHub latest Release 下載這兩個固定名稱：

```text
EvidenceRadar-WorkPack-current.zip
EvidenceRadar-WorkPack-current.zip.sha256
```

驗證 checksum 與 manifest 後，後續執行不再讀 GitHub。Work Pack 包含 protocol、
設定、schema、template、基準 State、V3 canonical renderer、State merge、四件套
validator、run-id delivery packager 與 requirements。renderer 所需的 GitHub
projection module 只可 import，CLI 在 pack 內強制停用；不含 Stage B automation、
credentials、每日報告、CI 或 legacy crawler。

維護者或從 source 使用的人可重現建置：

```sh
python3 tools/build_work_pack.py --output-dir dist
```

完整步驟見 [`docs/WORK_SETUP.md`](docs/WORK_SETUP.md)。Repository 內仍保留供
維護者回歸與舊部署相容的 `github_actions` producer，但它不是用家入口，也不會被
Work Pack 呼叫。執行 Radar 不需要 GitHub workflow、issue、PR 或 Stage B。

## 維護者相容 producer 的 10–15 出版社存取預算

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

Pages 每次部署都是完整站點替換。可公開的歷史 run 必須列在
`runs/pages-history.json`，並以 run ID、recorded producer commit 及四檔的 byte
size／SHA-256 綁定。建站時會逐筆通過 current delivery contract；歷史 renderer 的
byte parity 再由 recorded producer validator 核對，之後才從完整 inventory 重建
`runs/<run_id>/` 和 `runs/index.json`。任一核准項目缺檔、hash
漂移、producer 不存在、路徑或 run ID 衝突都會停止部署。未列入 manifest 的 legacy
目錄以及只有 HTML 的 `public/reports/*.gz` 不會被 overlay 到同源網站。新的 canonical
publication 必須在同一個 reviewed change 保存其完整四檔並 append manifest，否則
Pages 會拒絕 current run，避免下一次 replacement 讓 immutable URL 消失。

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
