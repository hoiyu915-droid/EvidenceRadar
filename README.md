# EvidenceRadar

每日文獻雷達，涵蓋：

- 運動科學
- 運動營養
- 體適能與運動健康
- LLM 伴侶、人機關係與社會影響

## 每日輸出契約

- 格式：Markdown (`.md`)
- 輸出目錄：`daily/`
- 檔名：`YYYYMMDD HHMM.Rader.md`
- 時區：`Asia/Tokyo`
- Featured：目標 5–8 篇；不足時不硬湊
- Candidate Pool：最多 30 篇，包含 Featured

範例：

```text
daily/20260712 2220.Rader.md
```

## 資料流

```text
PubMed + OpenAlex
→ OpenAlex 失敗時以 Crossref 補位
→ metadata 正規化
→ DOI / PMID / OpenAlex ID / title 去重
→ publication type 與研究設計判定
→ 標題優先的領域相關性 gate
→ evidence / relevance / interest / practical 分數
→ Europe PMC 補 OA、PMCID 與 DOI
→ Featured 5–8 + Candidate Pool ≤30
→ Markdown
```

目前屬於 `AUTO-TRIAGE` 發現層，不是最終證據審核。正式引用前仍須完成全文、校正／撤稿、方法、引用與斷言核對。

## 品質防線

`src/quality.py` 會：

1. 阻止 protocol、letter、editorial、correction 冒充 RCT 或高階證據。
2. 讓 scoping review、narrative review、preclinical evidence 保留自己的等級，不升格成 SR／RCT。
3. 以標題為主判斷是否真正屬於運動科學、運動營養、體適能或 LLM 社會研究。
4. 阻止只在摘要順帶提到 exercise、protein、recovery、biomechanics 的跨領域文章混入。
5. 將動物研究標成 `Preclinical/U`，不得繼承 human RCT／longitudinal tier。

## 執行

需要 Python 3.12+：

```bash
python -m pip install -r requirements.txt
python -m pytest -q
python src/run.py
```

可指定檢索結束日期與回看天數：

```bash
python src/run.py --end-date 2026-07-12 --lookback-days 3
```

## GitHub Actions

`.github/workflows/daily-radar.yml`：

- 每天 `06:17 Asia/Tokyo` 執行
- 可由 `workflow_dispatch` 手動執行
- connector 可透過更新 `.manual-run` 觸發
- 自動測試、產生 Markdown、commit 與 push

可選 repository secrets：

| Secret | 用途 |
|---|---|
| `NCBI_API_KEY` | 提高 NCBI E-utilities 支援速率 |
| `NCBI_EMAIL` | NCBI／Crossref polite-pool 聯絡資訊 |
| `OPENALEX_API_KEY` | 提高 OpenAlex API 額度；缺少或限流時會嘗試 Crossref fallback |

## 設定

- [`config/output.yml`](config/output.yml)：輸出契約
- [`config/streams.yml`](config/streams.yml)：四條 stream、查詢式與相關詞
- [`config/scoring.yml`](config/scoring.yml)：Candidate Pool、Featured 門檻與分數權重
- [`src/quality.py`](src/quality.py)：研究設計、相關性、排除與 fallback 規則
- [`templates/daily-radar.md`](templates/daily-radar.md)：Markdown 版型參考

## 選文原則

1. 高證據等級優先：Meta、SR、RCT、guideline、consensus、大型 cohort、高品質 longitudinal／field experiment。
2. 允許納入證據未成熟、但機轉、方法或社會影響值得追蹤的研究。
3. 證據強度與有趣程度分開評分，避免把吸睛題目誤當強證據。
4. OA-first，但不是 OA-only。
