# EvidenceRadar 封存 GitHub Actions 部署參考

> **main 已停用。** Radar 的正式用家執行由 ChatGPT Work 完成。原 Stage A／B
> workflow 保存在 `legacy/github-actions/`，只供歷史稽核與 regression；GitHub
> 現在負責 source、驗證、release package 儲存與可選 publication hosting。不得用
> 這些封存 workflow 代替用家要求的「執行 Radar」。

EvidenceRadar 現在只有一條正式 execution lane：ChatGPT Work，完整流程按
[`docs/WORK_SETUP.md`](WORK_SETUP.md) 執行。GitHub Actions 只做 source validation、
Work Pack／Runtime 發布與可選的 Pages publication。舊 GitHub discovery 及
translation handoff 僅保存在 `legacy/github-actions/`，不會在 `main` 啟動。

## 建立自己的 repository

1. 在 GitHub 開啟本 repo，選 **Use this template** 建立獨立 repository；若要
   保留 upstream 關係，也可以 fork。不要直接在 upstream 的 default branch
   放自己的 state 或 secrets。
2. 在自己的 repository 開啟 **Settings → Actions → General**。只有建立 release
   或 Pages publication 的 repository 才需要允許 Actions；使用者執行 Radar
   本身不需要 GitHub Actions 權限。
3. 確認 default branch 已包含目前的 schema/config、Work Pack builder 與
   validator。發布 `EvidenceRadar-WorkPack-current.zip` 及其 SHA-256 sidecar；
   使用者只需讓 GPT 下載並驗證這個 package 一次。
4. 在 **Settings → Pages → Build and deployment** 把 Source 設成
   **GitHub Actions**。`pages.yml` 只會發布通過四件套、provenance、候選
   ledger/HTML 數量與 producer-version 檢查的 current bundle。

   Pages 未啟用、Source 不是 **GitHub Actions** 或 API 無法讀取 Pages
   設定時，publication 會在部署前 fail closed；這時只能取得 Actions
   artifact，不能宣稱已有公開 HTML URL。可在 repository checkout 中先做
   唯讀 preflight：

   ```sh
   gh api "repos/OWNER/REPOSITORY/pages" --jq '.build_type'
   ```

   輸出必須是 `workflow`，且之後要等待 `pages.yml` 的 deployment job 成功。

每個 template/fork 都是自己的狀態邊界。在 **Settings → Secrets and variables
→ Actions** 建立：

| 名稱 | 類型 | 用途／缺少時的行為 |
|---|---|---|
| `OPENALEX_API_KEY` | Secret | OpenAlex discovery 必要；缺少時該來源 fail closed 並記錄 gap |
| `OPENREVIEW_TOKEN` | Secret | 選用；公開 OpenReview search 被限制時可改用 authenticated access |
| `NCBI_EMAIL` | Variable | NCBI 建議的 E-utilities client identification |
| `NCBI_API_KEY` | Secret | 選用；PubMed 從每秒 3 次提高到預設每秒 10 次 |

不要把 token 寫入 YAML、config、report 或 Work Pack。Workflow 只以 environment
reference 讀取這些值，不輸出值本身。

Provider 基線以 [NCBI E-utilities usage guidelines](https://www.ncbi.nlm.nih.gov/books/NBK25497/)
與 [OpenAlex authentication/rate-limit documentation](https://developers.openalex.org/api-reference/rate-limits/check-rate-limit-status)
為準；上游規則改變時，先更新 config、tests 與這份文件。

## 封存的排程與 handoff（非現行流程）

舊 `legacy/github-actions/daily-radar.yml` 曾每日於 `06:17 Asia/Tokyo` 執行，
並提供以下 publisher 網路存取預算 override。該檔不在 `.github/workflows/`，
因此 GitHub 不會排程、手動 dispatch 或因 push 啟動它；以下只供歷史稽核：

若只交付已審閱的 queue／workflow control-plane 修正，而且已有仍在 retention 內的
immutable request 等待處理，merge commit 可帶精確標記
`[skip-evidenceradar-stage-a]`；它只略過該次 push-triggered discovery，不影響每日
schedule 或 workflow_dispatch。一般 producer／schema／config 更新不得使用此標記。

| input | 預設 | 意義 |
| --- | ---: | --- |
| `publisher_target_min` | 10 | 本輪成功存取目標下限 |
| `publisher_hard_max` | 15 | 本輪網路探測硬上限；runner 拒絕任何大於 15 的值 |

每輪以 10 筆成功的 publisher/source-page audit records 為目標，publisher-page
candidate attempts 的硬上限為 15；這不是補足數量的承諾。來源被擋、來源不足或
不合格時，runner 保留 gap/警告並低於 target 結束；禁止用未驗證項目 padding。
這個 10–15 預算不限制候選顯示：HTML 按類別顯示本輪所有去重候選，Run JSON
保存相同的完整候選 ledger。候選的 routing score 只影響排序，`LOWER_PRIORITY`、
`FAILED` 或 `NOT_ATTEMPTED` 都不代表候選沒有研究價值。

Stage A 不產生報告；它上傳 SHA-bound TranslationRequest 並保持 State 不變。完成
checkpointed ChatGPT Work handoff 後，Stage B 才會產生單一 self-contained HTML，包含本機可用的搜尋、類別／triage／source 篩選、
分類收合與逐 item 稽核詳情。每筆內容簡述固定使用繁體中文，並標記為 AI 輔助翻譯、
來源繁中摘要、metadata template 或題名層級 fallback；全部都只是導航資訊，不是已核實結論。

翻譯不使用 repository model secret。Request／Response contract 與操作指令見
[`docs/RUNTIME_RELEASE.md`](RUNTIME_RELEASE.md)。

Workflow 宣告 `contents: read` 與 `issues: write`。Stage A 讀取 canonical State，
但不寫 repository contents；研究內容只存在 Actions upload 中的
TranslationRequest。Issue 僅保存 artifact ID/name、request SHA、producer commit、
base State SHA、run ID 與 candidate count，禁止保存 excerpt 或 frozen resume
context。這避免 response 尚不存在時誤寫 State 或公開半成品。

Stage B 驗證成功後才會產生四個 canonical artifact：

```text
artifacts/current/EvidenceRadar_Report.html
artifacts/current/EvidenceRadar_Evidence.json
artifacts/current/EvidenceRadar_Run.json
state/current/EvidenceRadar_State.json
```

`EvidenceRadar_State.json` 是 canonical cross-run state，保存所有
去重候選的 first/last seen 與通知歷史。Local Runtime 可把四檔保存到
`runs/<run_id>/` 作 immutable run record。不要手動刪除 state 來繞過
dedupe 或 publisher hard max；先檢查 run log、source access 與 schema validation。

新 run 同時標記 `SEMANTIC_CONTRACT_V3`。`EvidenceRadar_Run.json` 的每個 query、
access 與 CHECK 都有 executor retrieval receipt；State 持久保存 stable source／claim
registry、append-only access observations、gap backlog 與跨 run relations；Evidence
保存 citation bindings、claim origin、structured effect estimates、conflict groups 與
model inferences。GitHub lane 不會因這些欄位出現就提升 claim：它仍留下空 claims
ledger。完整規格見
[`docs/SEMANTIC_CONTRACT_V3.md`](SEMANTIC_CONTRACT_V3.md)。

V3 HTML 由 Run + Evidence canonical render，Run 保存 report SHA-256；Pages 前的
delivery validator 會重新渲染並要求 byte-identical。這可阻擋手寫到 HTML、但沒有
claim/source/locator binding 的額外結論或數字。

Stage B 可在交付前建立唯一的
`EvidenceRadar-WorkRun-<run_id>.zip`、包內 `manifest.json` 與外部
`.zip.sha256`。manifest 保留四個 canonical artifact 的 SHA-256 與 byte size，
因此 branch conflict 時仍可從該次 Actions artifact 取回完整 run；附件名稱不
會和上一輪的 `EvidenceRadar_Report.html` 等裸檔碰撞。

## 可直接點閱的 HTML 與 links.json

GitHub 的 blob/raw 頁面不是 HTML 預覽；ChatGPT Work 的本機路徑也不是公開網址。
`.github/workflows/pages.yml` 會在 current bundle 通過 delivery validator 後，把
報告部署為 GitHub Pages。一般 template/fork 的固定網址為：

```text
https://OWNER.github.io/REPOSITORY/
```

同一網站的 `links.json` 提供可機器讀取的連結：

- `report_url`：最新可直接閱讀的 HTML；
- `latest.run_json`／`latest.evidence_json`／`latest.state_json`：最新 JSON；
- `immutable_run.report_html`：該 run 不變的 HTML 路徑。
- `immutable_archive.index_json`：本次完整部署所包含的核准 run inventory。

Pages artifact 每次 deployment 都會完整取代前一版，不能依賴上一版 `_site/runs`
仍留在伺服器。核准 inventory 位於 `runs/pages-history.json`；每筆只能指向
`runs/<run_id>/` 的 canonical 四檔，或帶 `.sha256` 的 canonical WorkRun ZIP，並須
記錄四檔 size／SHA-256、run ID 與 recorded producer commit。建站工具會拒絕 symlink、
path／casefold collision、缺檔、額外檔案、hash 漂移、ZIP size／compression bomb、
不存在的 producer、未通過 current delivery contract（只允許交由 recorded producer
重驗的 renderer byte drift），以及未通過該 producer delivery validator 的 bundle。
任一 manifest 核准項目失敗時整個 build 失敗，不會用少一筆歷史的站點覆蓋線上版本。

未列入 manifest 的舊 `runs/` 內容視為 quarantine，不會發布；`public/reports/` 中只有
HTML 的 gzip 沒有 State、Evidence、Run 或 artifact hash binding，也絕不能直接解壓
overlay。每次 publication PR 除更新 current 四檔外，必須把相同 bytes 保存為新的
run directory／WorkRun archive 並 append `runs/pages-history.json`。manifest 中的
current run 與 `artifacts/current` 若同 ID 但 bytes 不同，建站會 fail closed。

`pages.yml` 只在明確 baseline 到待部署 revision 間確實改動 `artifacts/current` 或
canonical State bytes 時，額外要求 current bundle 由目前 checkout producer 產生。
只 append archive inventory 的 rebuild 會略過
這個 current-producer equality gate，避免
舊 canonical report 因 renderer 演進而無法重新部署；建站工具仍只容許已知的 canonical
renderer byte drift，current run 必須與 manifest inventory byte-identical，且仍須通過
其 `protocol_commit` 的 exact recorded-producer validator。其他 validation error、缺少
producer commit 或任何 inventory drift 一律 fail closed。

Append-only 比對使用 push 的 `github.event.before`，不使用只能看見最後一個 commit
的 `HEAD^`；builder 另會逐一核對 manifest 的 first-parent mainline 歷史。因此一次
push 包含多個 commits、先前部署失敗後 main 已前進，或刻意選擇過舊 baseline，都不能
移除或改寫任何既存 entry。Pages 不提供可指定任意 revision 的手動部署入口；失敗的
部署應重跑原本綁定 exact main revision 的 workflow。

Pages workflow 的 deployment 成功前，不應把推算網址宣稱為已可用。Work 若需要
公開連結，先交付實際 HTML 檔，再經審閱的 GitHub publication 更新 bundle；部署
完成後讀取 `links.json` 回傳網址。

封存的 hosted workflow 只執行 Stage A，產生並上傳
`EvidenceRadar_TranslationRequest.json`，狀態為 `TRANSLATION_REQUIRED`。GitHub
hosted runner 不呼叫模型、不 fallback 到 Copilot 或 OpenAI API；它建立
`evidenceradar-handoff` queue issue 後停止。

受限的 ChatGPT Work 排程按
[`templates/work-stage-b-automation.md`](../templates/work-stage-b-automation.md)
執行。它以每批最多 24 筆、每輪最多 8 個已驗證 batch 處理 request；plan SHA、
checkpoint SHA、batch ID 與 candidate ID parity 都是 fail-closed。Checkpoint 只留在
`automation/evidenceradar-translation-<request-sha>` branch，完成後 branch diff
收斂成 `.github/evidenceradar-translation-submission.json` 一檔並經 PR validation。

歷史上 Submission 合併後，`legacy/github-actions/translation-stage-b.yml` 下載原始 Actions artifact，核對 queue
issue 與 full request SHA，checkout request 記錄的 exact producer commit，並以目前
canonical State resume Stage B；它不重跑 discovery。Workflow 會從 exact producer
的 CLI capability 分流：支援 `--profile` 的 modern producer 必須收到 request 綁定的
`profile_id`，且 State、Evidence、Run 三份輸出的 profile 必須一致；不支援該參數的
legacy producer 只接受沒有 profile binding 的舊 request，也不會被傳入 `--profile`。
State 若已改變、artifact 過期、ID 不完整或翻譯違反數字／縮寫／結果宣稱規則，Stage B
在產物上傳前停止。

Stage B 通過三層 validator 後只上傳 immutable publication candidate，並把 issue
改標 `evidenceradar-ready-to-publish`；workflow 本身沒有 `contents: write`、不 push
也不直接改 State。Scheduled Work 再核對 package checksum／manifest，以恰好九個
canonical/current/immutable-run paths 建 publication PR。Public release validation
通過並合併後，`pages.yml` 才部署 HTML；只有 `links.json.run_id` readback 相符才關閉
queue issue。

每輪 Run 的 `source_coverage` 與 Evidence 的 `coverage` 都會逐一列出每個
configured source 的 CHECK summary。欄位 `requested`、`checked`、`searched`、
`unavailable` 與 `all_configured_sources_checked` 描述覆蓋集合；`checks` 內每筆
摘要則包含 `source_id`、`stage`、`status`、`checked_at`、`result_count` 與
`summary`。`checked` 只代表 CHECK 記錄存在，不代表成功；狀態可以是
`SUCCESS`、`NO_RESULTS`、`FAILED` 或 `NOT_ATTEMPTED`。`publisher` 與
`formal_proceedings_or_publisher` 屬於 `bounded_verification` stage，即使
10–15 publisher budget 沒有候選，仍要留下 check summary，避免把未嘗試誤報成
完整 source coverage。

## ChatGPT Work 匯入邊界

ChatGPT Work 的完整 evidence-review lane 仍必須明確帶入 State，按
[`EVIDENCE_RADAR_PROTOCOL.md`](../EVIDENCE_RADAR_PROTOCOL.md) 重新搜尋與讀取目前
來源；GitHub 的 report、state 或 log 不能直接當成目前來源證據。Scheduled Work
control plane 是另一個更窄的角色：它只能翻譯 frozen request 的 navigation text、
checkpoint 與搬運已經由 exact producer 驗證的 bundle，不能新增 claim、事件、
source observation 或研究結論。

若不想先下載 Work Pack，Work 也可從這個 public repository 直接讀取 `main` 的
固定 commit、在 Work VM 執行，並把輸出留在 Work VM。依
[`docs/WORK_SETUP.md`](WORK_SETUP.md) 的 repository-first mode 產生唯一
run-id ZIP、manifest 和 SHA-256，再以附件方式交付；這仍然是 Work local output，
不是 GitHub writeback。

Work 必須先執行 `tools/render_report_from_artifacts.py`，再執行四件套 validator 與
packager；不能手寫最終 HTML。Work 的 search/fetch/follow-up receipts 必須來自
實際執行工具，且 promoted claim 需要同 run 的合格 access observation 與 citation
binding。

反向地，Work 產生的四個 artifact 只有在使用者明確審閱、帶入、使用
`tools/merge_radar_state.py` 與 canonical State 作 deterministic union，並通過
schema/狀態檢查後，才可作為 GitHub lane 的輸入候選；不要把 Work project secrets、MCP、
瀏覽器 session 或未審閱的 `State` 放進公開 repository。兩條 lane 的
`execution_lane` 與 provenance 應保持可見，避免把自動 source audit 誤標成
ChatGPT Work 的完整 claim review。

## 維護與停用提示

GitHub 對公開 repository 的排程 workflow 在連續 60 天沒有 repository 活動時
可能自動停用。若每日報告停止，先在 **Actions** 檢查 workflow 是否 disabled、
default branch 是否仍包含檔案、token/permissions 是否被政策收回，再手動啟用並
執行一次。Actions 停用不會刪除既有 `runs/`、state 或 release artifact；它只表示
新的 GitHub lane 執行尚未發生。
