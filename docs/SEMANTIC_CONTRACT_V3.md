# EvidenceRadar Semantic Contract V3

V3 把「檢索到書目」、「實際讀到內容」、「引文精確」與「claim 受到支持」拆成
四個可稽核層次。它不取代 V2 的 OA／全文存取契約；新產物同時保留
`SEMANTIC_CONTRACT_V2` 與 `SEMANTIC_CONTRACT_V3` marker，舊 V2 bundle 仍可由
validator 讀取。

## 1. 不升格原則

下游序列化只能維持或降低證據狀態。只有同一 run 內新增且能對帳的 executor
receipt、source observation、citation binding 與 locator，才可提高 claim status。

```text
discovered URL != opened content
OA YES != full text accessible
metadata != abstract
abstract != full text
topic alignment != claim support
model inference != source extraction
PARTIAL / CONFLICT / UNVERIFIED != SUPPORTED
```

找不到、零結果、被擋、全文缺失、身分衝突、數字衝突與不可解決，都是合法輸出；
它們不得在 Run、State、Evidence 或 HTML 之間被改寫成成功。

## 2. 三階段 retrieval receipt

`EvidenceRadar_Run.json.retrieval_attempts` 保存執行程式實際產生的 receipt：

- `DISCOVERY`：查詢 registry、index、repository 或 proceedings；
- `CONTENT_FETCH`：開啟指定 landing page、abstract、HTML 或 PDF；
- `CLAIM_VERIFY`：為 locator／claim support 執行的內容核對；
- `FOLLOWUP`：由既有 gap 觸發的受控補檢索。

每筆 receipt 必須有穩定 `attempt_id`、backend、時間、狀態、endpoint、request
fingerprint、結果數、結果 ID hash、pagination 與 limit flag。`receipt_origin` 固定為
`EXECUTOR`。`NO_RESULTS` 只能代表實際查詢成功且結果為零；`NOT_ATTEMPTED` 的
requested pages 必須為零；保留了部分結果時使用 `PARTIAL`，不得以 `FAILED` 隱藏。

receipt 是由執行工具寫出的可對帳紀錄，不是外部 provider 的密碼學證明。不得由
模型僅憑敘述補填。Work 必須從實際工具呼叫的 query、URL、時間、狀態與結果建立
receipt；validator 會逐筆對照 `queries`、`source_access`、source CHECK、candidate
ledger 與 result hash。

Provider-specific query translation 要另寫 `search_expansions`，保存原式、實際式、
來源與理由。「本次未檢出」只適用於 receipt 已證明成功的 `NO_RESULTS`，不等於
網路或研究領域不存在資料。

## 3. Stable source registry 與 access observation

`State.source_registry` 是跨 run 的來源身分表；`Evidence.source_registry` 必須與它
JSON-identical。Canonical URL 會移除 fragment／tracking parameter、正規化 host 與
query order，再推導穩定 `source_id`。同一 canonical URL 必須重用同一 ID；同一 ID
不得指向兩個互斥 work identity。

每次檢索結果另寫不可覆蓋的 `source_observations`：

```text
access_depth: NONE | METADATA | LANDING_PAGE | ABSTRACT | FULL_TEXT
access_outcome: ACCESSIBLE | BLOCKED | PAYWALLED | FAILED |
                NOT_ATTEMPTED | NOT_CHECKED
```

知道 PMC／arXiv PDF URL 但未成功打開時，URL 和 OA evidence 仍保留，
`access_depth` 不得升成 `FULL_TEXT`。只有該直接位置的成功 receipt 能建立
`FULL_TEXT + ACCESSIBLE` observation。歷史可讀紀錄不會被較新的 `NOT_CHECKED`
覆蓋。

Source role 與 claim kind 分開治理。Discovery index 可以支撐書目事實；新聞、
部落格或 self statement 可支撐「該來源曾這樣表示」的 attribution；科學結果、
官方數字與政策陳述則必須使用適合該命題的 primary／systematic／official authority。

## 4. Citation binding 與 claim origin

每個 Evidence claim 都必須明列：

- `claim_kind`；
- `claim_origin`；
- `citation_binding_ids`；
- `support_reason`；
- support status 與 source IDs。

`claim_origin` 只有：

```text
FULLTEXT_EXTRACTED
ABSTRACT_EXTRACTED
METADATA_REPORTED
EXTERNAL_REPORT
MODEL_INFERENCE
```

來源支持只可透過 `citation_bindings` 建立。Binding 固定包含 stable source ID、
canonical URL、extraction origin、實際 access depth、exact locator 與 support scope。
Binding 的深度不得超過可找到的 `ACCESSIBLE` source observation。

`MODEL_INFERENCE` 不得出現在 citation binding，也不得冒充 Evidence claim origin；
它只能放在 `Evidence.inferences`，明列 `origin: MODEL_INFERENCE` 與 basis bindings。
一般 `SUPPORTED` scientific／numeric claim 必須使用 `FULLTEXT_EXTRACTED`、
`FULL_TEXT` 與 `EXACT` 或 `QUALIFIED` binding。Metadata origin 只可用於書目事實或
attribution。

## 5. Canonical claim registry 與跨 run 關係

`State.claim_registry` 保存 claim ID、work、kind、origin、claim text SHA-256、狀態、
source／binding IDs、first/last seen run 與最後狀態改變的 run。相同 claim ID 不可
換成另一段文字或另一種命題。

新 claim status 若提升為 `SUPPORTED`，`last_status_change_run` 必須是本 run，且
至少一個合格 binding 必須有本 run 的 accessible source observation。沿用歷史
`SUPPORTED` 可以保留狀態，但不能靠新的 metadata／abstract receipt 重新「證明」。

`State.work_relations` 保存版本、preprint-to-VOR、correction、retraction 與 duplicate
report；`State.claim_relations` 保存 supports、contradicts、supersedes。每條 relation
保留 comparison basis、review status 與 observed run。新版不會覆蓋舊 work／claim，
而是建立關係。

## 6. Gap-driven follow-up

未解決事項持續留在 `State.gaps`，包含類型、scope、首次／最後 run、attempt count、
最大次數、cooldown、receipt IDs 與 resolution criterion。允許的 gap 包括來源不可用、
全文不可讀、identity 未解、claim 未核實與數字衝突。
Configured backend／database 使用 `SOURCE_SYSTEM` scope；已進 stable source registry
的特定來源使用 `SOURCE` scope，兩者不可混用。

補檢索不是固定重搜。`Run.followup_attempts` 只能引用前一 run 已存在的 OPEN gap，
並保留：trigger、scope、parent candidate（WORK scope）、實際 query、backend、時間、
result、executor attempt ID、resolved gap IDs 與 outcome。Receipt 是
`NOT_ATTEMPTED` 時不可算 follow-up。達到 `max_attempts` 後轉為 `UNRESOLVABLE`；
只有成功 receipt 可 `RESOLVED`。每日固定的全來源檢查可以提供 gap resolution
receipt，但只有明確標成 `FOLLOWUP`、且由 gap 排程的額外檢索才會消耗 follow-up
attempt budget 或寫入 `Run.followup_attempts`。

## 7. 結構化數字與衝突

含數字的 claim 必須有 `effect_estimate_ids`。每筆 estimate 保留 value、effect
measure、unit、population、exposure、comparator、outcome、denominator、timeframe、
analysis set、estimator、method、uncertainty，必要時另存 95% CI。OR、HR、RR、平均差
或不同 denominator 不得只因數值接近而合併。

互斥或不可直接比較的結果放入 `conflict_groups`，明列差異維度。未確認 population、
exposure、comparator、outcome、timeframe、effect measure、analysis set 與 method
相容前，不得把衝突磨平成單一結論。`CONFLICT` claim 必須屬於一個 conflict group；
`RESOLVED` conflict 必須保留 resolution 說明。

## 8. Topic alignment 不等於 evidence quality

每個候選以 criterion ID 保存 `topic_alignments`：`DIRECT`、`PARTIAL`、
`MECHANISM_CONFLICT`、`OUT_OF_SCOPE` 或 `UNCERTAIN`，並記錄 basis：`RULE`、
`FULLTEXT`、`ABSTRACT` 或 `MODEL_INFERENCE`。Alignment 只回答「是否符合 Radar
問題」，不得提高 source role、access depth、claim origin 或 support status。

## 9. Canonical 四件套與 HTML

V3 先完成 State、Evidence、Run，再以唯一 renderer 產生 HTML：

```sh
python3 tools/render_report_from_artifacts.py --bundle "$WORK_RUN_DIR"
```

Renderer 同步 current claim registry、claim count、`report_sha256`，並在所有語意
驗證通過後才寫回 State、Run 與 HTML。Report 的候選摘要標成
`navigation_summary`；實質 claim 標成 `substantive_claim` 並帶 claim ID。Validator
會重新渲染並要求 byte-identical，額外手寫到 HTML 的「研究顯示」或數字會 fail
closed。

最後依序執行 per-file schema validator、cross-bundle validator 與唯一 run-id
packager。結構驗證證明 artifacts 內部一致，不取代研究者對來源真實性與 locator 的
人工審查。
