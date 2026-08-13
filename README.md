# EvidenceRadar

> **狀態：可使用，仍在持續開發與收緊驗證契約。**
>
> EvidenceRadar 是一套可公開自行部署、可稽核、也可以依個人需求改造的近期研究雷達。它把「發現新文獻」與「證據已核實」分開，使用事件窗、publication identity、來源覆蓋、claim ledger、可攜 State、executor receipts 與 fail-closed validation，減少重複通知、來源誤判與過度宣稱。

## 建議使用方式：長期使用請先複製到自己的 GitHub

如果只是想試用 EvidenceRadar，可以直接使用這個 repository 的 latest verified Work Pack。

但如果打算長期使用，**建議先 Fork／Duplicate 到自己的 GitHub repository**，再讓 Codex 或 ChatGPT Work 按你的閱讀需求修改。這不是執行 Radar 的技術必要條件，而是比較合理的個人化方式：每個人想追的研究來源、主題、篩選門檻、報告長度、語言與翻譯方式都不同。

這個 upstream repository 比較適合作為可稽核的 reference implementation；你的 fork 才應該保存你的個人來源、profile、routing、limits 與輸出偏好。

### 最適合交給 Codex／ChatGPT Work 修改的內容

目前主要 control plane 是 [`config/radar_master.json`](config/radar_master.json)。它對以下項目具有權威性：

- sources 與 source groups
- taxonomy 與 routing categories
- stream routing
- profiles
- discovery／selection／verification limits

`owner_daily` 是目前預設與 production profile，但不是所有用家都應照抄的唯一設定。

輸出、語言、翻譯與 rendering 行為則同時檢查：

- [`config/output.yml`](config/output.yml)
- [`EVIDENCE_RADAR_PROTOCOL.md`](EVIDENCE_RADAR_PROTOCOL.md)
- [`docs/SEMANTIC_CONTRACT_V3.md`](docs/SEMANTIC_CONTRACT_V3.md)
- renderer、schema 與 validator

[`config/streams.yml`](config/streams.yml)、[`config/scoring.yml`](config/scoring.yml) 與部分舊設定仍保留作 compatibility／legacy input；新增個人設定時不要把它們誤當成主要 control plane。

你可以直接要求 Codex 或 ChatGPT Work：

- 增加／移除期刊、publisher、conference、RSS、索引或 OA 來源。
- 改成只追臨床醫學、運動科學、營養、LLM、Human–AI，或建立自己的分類。
- 調整 relevance threshold、候選池、Featured 數量與 final digest 長度。
- 建立新的個人 profile，而不是沿用 `owner_daily`。
- 修改輸出語言、繁中翻譯方式、摘要長度或雙語輸出。
- 新增 source adapter、publication identity 或 source verification 規則。
- 修改 HTML 搜尋、filter、分類或呈現方式，但保留 evidence／claim provenance。
- 加強測試與 validator，而不是為了讓流程通過而刪掉 fail-closed protection。

### 可以直接丟給 Codex／ChatGPT Work 的自訂要求

```text
Read this EvidenceRadar repository before changing anything.

First read:
- README.md
- EVIDENCE_RADAR_PROTOCOL.md
- docs/WORK_SETUP.md
- docs/SEMANTIC_CONTRACT_V3.md
- config/radar_master.json
- config/output.yml

Treat config/radar_master.json as authoritative for sources, taxonomy,
routing, profiles and limits.

Customize this fork for my own research needs.

My requirements:
- Research topics:
- Sources / journals / conferences to add:
- Sources to remove:
- Preferred profile:
- Candidate / digest limits:
- Output language:
- Translation requirements:
- Other requirements:

Preserve the distinction between discovery, source access and verified claims.
Do not weaken provenance, executor receipts, publication identity,
claim/source binding, State deduplication, canonical rendering or
fail-closed validation merely to make the run pass.

Update configuration, code, tests and documentation where required.
Run the relevant validators after the changes.

Do not modify a downloaded verified Work Pack in place.
Modify this repository source and rebuild a new Work Pack from the fork.
```

**不要直接修改已下載並驗證的 Work Pack。** Work Pack 是 read-only 的 portable policy／artifact contract；需要改來源、需求或翻譯功能時，應修改自己的 repository source，再重新 build／release。

## 最快的試用方式

如果不需要個人化，向 ChatGPT Work 說：

```text
執行 EvidenceRadar。
使用這個 repository 的 latest verified Work Pack，
完成搜尋、核實、翻譯、State 更新、canonical HTML rendering
與完整 validation，直到交付四個最終檔案。
```

一次完整 Work run 最後應交付：

```text
EvidenceRadar_Report.html
EvidenceRadar_State.json
EvidenceRadar_Evidence.json
EvidenceRadar_Run.json
```

ChatGPT Work 會下載一次正式 Work Pack、checksum 與簽署 provenance；驗證後在同一輪完成搜尋、來源讀取、核實、繁中翻譯、去重、State 處理、canonical HTML render 與四件套 validation。

GitHub 在這條一般用家路徑主要負責原始碼、版本化設定與 immutable Work Pack 儲存。下載完成後不需要啟動 Actions、不需要建立 issue／PR，也不需要等待 Stage A／Stage B。

Repository 亦提供 [` .agents/skills/evidence-radar/SKILL.md`](.agents/skills/evidence-radar/SKILL.md)（路徑實際為 `.agents/skills/evidence-radar/SKILL.md`），供支援 agent skills 的環境理解 EvidenceRadar 的 end-to-end 執行契約。

## Work 執行模型：一次下載，一輪完成

ChatGPT Work 從 GitHub latest Release 下載三個固定名稱：

```text
EvidenceRadar-WorkPack-current.zip
EvidenceRadar-WorkPack-current.zip.sha256
EvidenceRadar-WorkPack-current.sigstore.json
```

Work Pack releases 為 immutable package。驗證簽署 provenance、checksum 與 manifest 後，後續 Work run 不再把 GitHub 當 execution coordinator。

Work Pack 包含 protocol、設定、schema、template、基準 State、V3 canonical renderer、State merge、四件套 validator、run-id delivery packager 與 requirements。維護者或從 source 使用的人可重現建置：

```sh
python3 tools/build_work_pack.py --output-dir dist
```

完整步驟見 [`docs/WORK_SETUP.md`](docs/WORK_SETUP.md)。

目前 ChatGPT Work lane 是 user-launched terminal flow。Repository 仍保留維護者回歸與歷史部署所需的 legacy／GitHub producer 材料，但它們不是一般用家執行 Radar 的必要條件。

## Radar 現在刻意拆開的幾件事

EvidenceRadar 的核心不是「搜尋到很多論文」，而是避免 LLM 把不同證據狀態偷偷當成同一件事。

### Discovery 不等於 verification

搜尋結果、RSS、metadata 或 abstract 可以讓一篇研究成為 candidate，但不能自動變成已核實 scientific claim。

### Search 不等於 fetch

「搜尋到了這個 URL」和「實際打開並讀取這個來源」是兩個不同事件。V3 要求每個實際 query／fetch／claim verification 都留下 executor receipt，並與 query、source access、source CHECK、candidate IDs 與 pagination 對帳。

### OA 不等於本輪取得全文

`oa_status: YES` 可以同時搭配 blocked／unprobed access。DOI、PubMed、OpenAlex 或 abstract landing page 的 HTTP 200 不會被當成全文可讀；PMCID、arXiv 或其他直接全文來源則保留其直接 URL 與實際 access observation。

### Source identity 不等於 access observation

來源使用 stable source identity；每次實際存取結果則另外保存 append-only observation。換句話說，「這是哪個來源」和「這一輪有沒有成功讀到它」不會混成同一欄。

### 模型推論不等於來源證據

模型推論只能進 inference ledger。Claim 必須綁 source、locator、origin 與 access depth；numeric claim 另外保存 population、exposure、comparator、outcome、timeframe、effect measure、analysis set 與 uncertainty。

### HTML 不是另一份人工改寫的答案

最終 HTML 必須由 canonical JSON artifacts 投影生成：

```sh
python3 tools/render_report_from_artifacts.py --bundle "$WORK_RUN_DIR"
```

候選中文簡述是 navigation text；實質 claim 以 claim ID 綁 Evidence。手動在 HTML 加入結論或數字會破壞 canonical byte parity，應先修改 artifacts 再重新 render。

## Semantic Contract V3

[`docs/SEMANTIC_CONTRACT_V3.md`](docs/SEMANTIC_CONTRACT_V3.md) 把以下概念分開：

- discovery
- content fetch
- claim verification
- source identity / source observation
- citation binding
- model inference
- numeric premise
- conflict
- gap-triggered follow-up

舊 V2 bundle 仍保留相容驗證；新 V3 bundle 啟用額外 fail-closed 規則。任何「模型說自己查過」都不能代替 executor receipt。

## 來源與 profile

目前的 authoritative source/profile selection 位於 [`config/radar_master.json`](config/radar_master.json)。現行配置包含多種可組合來源與 profile；例如 biomedical、LLM／AI、Nature／OA、Lancet 與 curated Elsevier families，並可將不同來源組合到不同 reader profile。

目前預設 `owner_daily` 聚焦：

- Clinical Medicine
- Sport Science
- Sport Nutrition & Fitness
- LLM Research
- Human–AI Research

其他 profile 可只追單一領域，或加入 general research categories。個人 fork 最好建立自己的 profile，而不是把個人偏好硬塞進 upstream 的 `owner_daily`。

每個 enabled stream 所列的 distinct source，每輪都應在 Run 與 Evidence 的 source coverage CHECK 中留下狀態：

| 狀態 | 意義 |
|---|---|
| `SUCCESS` | 已完成來源操作且取得一筆以上結果 |
| `NO_RESULTS` | 已查詢來源，但結果為零 |
| `FAILED` | 已嘗試但 provider/access 操作失敗 |
| `NOT_ATTEMPTED` | 已設定但本輪沒有發出請求 |

`checked` 代表有 CHECK 記錄，**不代表成功**；`searched` 表示實際發出請求，`unavailable` 表示失敗或未嘗試。

## 四個 artifact 與 State

每個完整 Work run 必須交付：

| Artifact | 用途 |
|---|---|
| `EvidenceRadar_Report.html` | 主要可閱讀報告 |
| `EvidenceRadar_State.json` | 跨輪 identity、seen history、已通知事件與 aliases |
| `EvidenceRadar_Evidence.json` | claim、numeric premise、citation binding、locator、source 與 conflict |
| `EvidenceRadar_Run.json` | 完整候選 ledger、時窗、queries、source CHECK、retrieval receipts、provenance、warnings 與統計 |

新 State／Run 顯式記錄 `execution_lane`、`protocol_commit`、`base_state_sha256`、`parent_run_ids`。

State advancement 只可在 artifact validation 通過後發生。若維護者需要把歷史 GitHub State 與 Work State 匯合，使用 deterministic union，而不是以較新的檔案直接覆蓋另一份歷史：

```sh
python3 tools/merge_radar_state.py \
  state/current/EvidenceRadar_State.json \
  /path/to/work/EvidenceRadar_State.json \
  --execution-lane chatgpt_work \
  --protocol-commit COMMIT \
  --output /path/to/merged/EvidenceRadar_State.json
```

## 四件套 validation

Schema validation 必要但不充分。完整 delivery 需把四個 artifacts 視為同一 bundle 驗證：

```sh
python3 tools/validate_delivery_bundle.py \
  --root "$WORK_PACK_DIR" \
  --bundle "$WORK_RUN_DIR" \
  --expected-lane chatgpt_work \
  --manifest "$WORK_PACK_DIR/manifest.json" \
  --require-semantic-contract-v3
```

Validator 會檢查 Run／State provenance、source coverage、完整候選 ledger、HTML item parity、claim/source binding、OA／access 一致性與 canonical rendering。只有具可稽核直接全文 probe 的來源才能支持相應 full-text-backed claim。

## GitHub Pages 與公開報告

公開 fork 可以選擇使用 GitHub Pages 發佈**已驗證**的 current HTML 與 JSON artifacts。Pages 是 publication boundary，不是 ChatGPT Work 執行 Radar 的必要部分。

歷史公開 run 以 manifest、run ID、producer commit、byte size 與 SHA-256 綁定；新的 canonical publication 必須保存完整四件套並通過 publication preflight，避免下一次 Pages replacement 讓既有 immutable URL 漂移或消失。

GitHub blob/raw URL 不等於可閱讀 HTML preview；如果要提供公開閱讀入口，應以 Pages deployment 後的網址為準。

## 封存 maintainer／GitHub producer

Repository 內仍保留維護者回歸與舊部署參考材料。現行一般用家 lane 不依賴 GitHub Actions；`github_actions` producer／Stage A／Stage B 等歷史 transport 不應取代 ChatGPT Work 的 terminal four-file delivery。

封存 producer 的 publisher probing 使用 bounded verification budget，並保留 access gap；publisher 頁面成功打開只代表 access observation，不代表 scientific claim 已被核實。

相關歷史與 migration 說明見：

- [`LEGACY_RUNTIME.md`](LEGACY_RUNTIME.md)
- [`README_MIGRATION.md`](README_MIGRATION.md)
- [`docs/MIGRATION_DUAL_LANE_1.0.md`](docs/MIGRATION_DUAL_LANE_1.0.md)

## 研究契約

預設 owner-focused taxonomy 包含 Clinical Medicine、Sport Science、Sport Nutrition & Fitness、LLM Research 與 Human–AI Research。LLM 使用 L1–L9；Human–AI 使用 H1–H2。Venue 只記錄 publication identity，不作 taxonomy。

合格事件包括 version of record first online、首次正式索引、正式 proceedings 釋出、OA/AAM 首次可用、embargo 解除、preprint 升級 peer-reviewed version，以及正式版本完成核實。搜尋引擎 freshness、卷期回填或 metadata 更新不能單獨作為新事件。

Identity 去重順序固定為：

```text
DOI → PMID → PMCID → arXiv ID → Anthology ID → OpenAlex ID → normalized title
```

Claim support 只可使用 `SUPPORTED`、`PARTIAL`、`CONFLICT`、`UNVERIFIED`；Run status 只可使用 `COMPLETE`、`PARTIAL_SOURCE_COVERAGE`、`SOURCE_ACCESS_GAP`、`STATE_HISTORY_INCOMPLETE`、`NO_QUALIFYING_ITEMS`。

詳細規則見 [`EVIDENCE_RADAR_PROTOCOL.md`](EVIDENCE_RADAR_PROTOCOL.md)。

## Repository 導航

- [`.agents/skills/evidence-radar/SKILL.md`](.agents/skills/evidence-radar/SKILL.md)：agent end-to-end execution contract
- [`config/radar_master.json`](config/radar_master.json)：authoritative sources／taxonomy／routing／profiles／limits
- [`config/output.yml`](config/output.yml)：輸出、翻譯與 rendering 設定
- [`schemas/`](schemas/)：State、Evidence、Run schema
- [`examples/`](examples/)：結構範例，不是目前研究證據
- [`tools/build_work_pack.py`](tools/build_work_pack.py)：可重現 Work Pack builder
- [`tools/run_work_radar.py`](tools/run_work_radar.py)：ChatGPT Work terminal executor
- [`tools/merge_radar_state.py`](tools/merge_radar_state.py)：deterministic State union
- [`tools/package_work_delivery.py`](tools/package_work_delivery.py)：驗證後建立 run-id delivery pack
- [`tools/render_report_from_artifacts.py`](tools/render_report_from_artifacts.py)：V3 JSON canonical render HTML
- [`tools/validate_delivery_bundle.py`](tools/validate_delivery_bundle.py)：四件套與 HTML／provenance 一致性 gate
- [`tools/build_pages_site.py`](tools/build_pages_site.py)：產生 Pages 站點與 `links.json`
- [`templates/gpt-work-instructions.md`](templates/gpt-work-instructions.md)：Work 指令
- [`docs/WORK_SETUP.md`](docs/WORK_SETUP.md)：Work Pack 安裝與執行
- [`docs/SEMANTIC_CONTRACT_V3.md`](docs/SEMANTIC_CONTRACT_V3.md)：retrieval／source／claim／gap／numeric V3 契約

## Runtime and maintainer tooling

Codex **不是** Radar runtime，也不是 downstream 用家的必要依賴；但它很適合用來維護自己的 fork、修改來源／profile／翻譯需求、跑 tests、準備 release 與做 repository audit。

MCP 與 external server 亦不是 `chatgpt_work` lane 的必要條件。一般 Work run 由 ChatGPT Work 做 live web search／direct source read，最後交付 validated HTML + three JSON。

除非使用者另行要求，EvidenceRadar 不啟動 TA／TP03 或圖像生成。

## Open-source maintenance

- Primary maintainer: `hoiyu915-droid`
- Maintained line: `main`
- Governance: [`GOVERNANCE.md`](GOVERNANCE.md)
- Contributions: [`CONTRIBUTING.md`](CONTRIBUTING.md)
- Security reports: [`SECURITY.md`](SECURITY.md)
- Roadmap: [`ROADMAP.md`](ROADMAP.md)
- Public-release audit: [`PUBLIC_RELEASE_AUDIT.md`](PUBLIC_RELEASE_AUDIT.md)

## License

Executable workflow material, schemas, validators, source code, tests, templates and legacy runtime code are licensed under Apache-2.0. Original documentation, report prose/layout and original compilation or arrangement of structured records are licensed under CC BY 4.0.

Article titles, authors, identifiers, publisher metadata, quoted excerpts, linked pages and trademarks are not relicensed. See [`LICENSE`](LICENSE), [`LICENSE-CONTENT.md`](LICENSE-CONTENT.md) and [`NOTICE.md`](NOTICE.md).

## Citation

Use [`CITATION.cff`](CITATION.cff) and cite the exact commit or release used.
