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

每個 template/fork 都是自己的狀態邊界。在 **Settings → Secrets and variables
→ Actions** 建立：

| 名稱 | 類型 | 用途／缺少時的行為 |
|---|---|---|
| `OPENALEX_API_KEY` | Secret | OpenAlex discovery 必要；缺少時該來源 fail closed 並記錄 gap |
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
使用 IANA timezone。**Run workflow** 可覆寫本輪 publisher 輸出範圍：

| input | 預設 | 意義 |
| --- | ---: | --- |
| `publisher_target_min` | 10 | 本輪目標下限 |
| `publisher_hard_max` | 15 | 本輪硬上限；runner 拒絕任何大於 15 的值 |

每輪以 10 筆成功的 publisher/source-page audit records 為目標，publisher-page
candidate attempts 的硬上限為 15；這不是補足數量的承諾。來源被擋、來源不足或
不合格時，runner 保留 gap/警告並低於 target 結束；禁止用未驗證項目 padding。

Workflow 宣告 `permissions: contents: write`，只為了將產生的 artifact、state
與 `runs/` 歷史寫回同一個 repository。提交步驟使用 `EvidenceRadar bot` 身份，
精準 stage 這些生成路徑；沒有內容變更時安全退出。啟用 branch protection、
required checks 或限制 Actions token 時，依自己的治理規則調整，並保留可追溯
的 run log 與 artifact upload。

每次 run 會驗證並上傳四個 artifact：

```text
artifacts/current/EvidenceRadar_Report.html
artifacts/current/EvidenceRadar_Evidence.json
artifacts/current/EvidenceRadar_Run.json
state/current/EvidenceRadar_State.json
```

`EvidenceRadar_State.json` 是 GitHub lane 的 canonical cross-run state；runner
也會把四檔保存到 `runs/<run_id>/` 作 immutable run record，並在提交前確認
current State 與 canonical State byte-identical。不要手動刪除 state 來繞過
dedupe 或 publisher hard max；先檢查 run log、source access 與 schema validation。

## ChatGPT Work 匯入邊界

ChatGPT Work 不是 GitHub Actions 的背景 worker。要在 Work 續跑時，使用者明確
帶入當次 `EvidenceRadar_State.json`（以及需要的 report/evidence 作為歷史參考），
再按照 [`EVIDENCE_RADAR_PROTOCOL.md`](../EVIDENCE_RADAR_PROTOCOL.md) 重新搜尋與
讀取目前來源。Work 必須重新驗證時窗、事件與 claims；GitHub 的 report、state
或 log 不能直接當成目前來源證據，也不能讓 Work 自動把檔案寫回 repository。

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
