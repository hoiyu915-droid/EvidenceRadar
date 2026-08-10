# EvidenceRadar GitHub Actions 部署

EvidenceRadar 有兩條可選的執行 lane：GitHub Actions 這份文件描述每日自動
執行；ChatGPT Work 則按 [`docs/WORK_SETUP.md`](WORK_SETUP.md) 由使用者手動
攜帶 artifact。兩條 lane 共用 protocol、schema 與設定，但不共用隱含的
credentials，也不把其中一條的執行誤當成另一條的 source verification。

## 建立自己的 repository

1. 在 GitHub 開啟本 repo，選 **Use this template** 建立獨立 repository；若要
   保留 upstream 關係，也可以 fork。不要直接在 upstream 的 default branch
   放自己的 state 或 secrets。
2. 在自己的 repository 開啟 **Settings → Actions → General**，允許 Actions
   執行，將 **Workflow permissions** 設為 **Read and write permissions**，並在
   **Actions** 頁面啟用這個 workflow。排程只會在 default branch 的 workflow
   檔上生效。
3. 確認 default branch 已包含 `requirements.txt`、`tools/run_github_radar.py`
   與目前的 schema/config。先用 **Run workflow** 做一次手動 smoke run，再
   讓每日排程接手。
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

## 排程、手動輸出與權限

`.github/workflows/daily-radar.yml` 每日於 `06:17 Asia/Tokyo` 執行，刻意避開
整點尖峰。GitHub 的 [`schedule` syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule)
使用 IANA timezone。**Run workflow** 可覆寫本輪 publisher 網路存取預算：

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
ordinary ChatGPT handoff 後，local Runtime Stage B 才會產生單一 self-contained HTML，包含本機可用的搜尋、類別／triage／source 篩選、
分類收合與逐 item 稽核詳情。每筆內容簡述固定使用繁體中文，並標記為 AI 輔助翻譯、
來源繁中摘要、metadata template 或題名層級 fallback；全部都只是導航資訊，不是已核實結論。

翻譯不使用 repository model secret。Request／Response contract 與操作指令見
[`docs/RUNTIME_RELEASE.md`](RUNTIME_RELEASE.md)。

Workflow 只宣告 `contents: read`。Stage A 讀取 canonical State 但不寫 repository；
唯一的新產物是 Actions upload 中的 TranslationRequest。這避免 hosted lane 在普通
chatbot response 尚不存在時誤寫 State 或公開半成品。

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

Pages workflow 的 deployment 成功前，不應把推算網址宣稱為已可用。Work 若需要
公開連結，先交付實際 HTML 檔，再經審閱的 GitHub publication 更新 bundle；部署
完成後讀取 `links.json` 回傳網址。

每日 hosted workflow 只執行 Stage A，產生並上傳
`EvidenceRadar_TranslationRequest.json`，狀態為 `TRANSLATION_REQUIRED`。GitHub
hosted runner 不會假裝能呼叫普通 ChatGPT chatbot，也不會 fallback 到 Copilot 或
OpenAI API。使用者完成 chatbot handoff 後，應在 local Runtime 以同一 request 與
response 執行 Stage B；只有 Stage B 四件套全部驗證通過才可進入 publication。

Stage B 產物要公開時，維護者須以一般受審閱的 repository 流程提交四件套與
canonical State，再明確啟動 `public-release.yml` 與 `pages.yml`。Daily Stage A
本身不 push、不 dispatch publication，也不把 TranslationRequest 誤當成 recovery
bundle 或公開報告。

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

ChatGPT Work 不是 GitHub Actions 的背景 worker。要在 Work 續跑時，使用者明確
帶入當次 `EvidenceRadar_State.json`（以及需要的 report/evidence 作為歷史參考），
再按照 [`EVIDENCE_RADAR_PROTOCOL.md`](../EVIDENCE_RADAR_PROTOCOL.md) 重新搜尋與
讀取目前來源。Work 必須重新驗證時窗、事件與 claims；GitHub 的 report、state
或 log 不能直接當成目前來源證據，也不能讓 Work 自動把檔案寫回 repository。

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
