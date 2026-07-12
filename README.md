# EvidenceRadar

每日文獻雷達，分成四個互不搶配額的頂層類別：

1. Clinical Medicine
2. Sport Science
3. Sport Nutrition & Fitness
4. LLM & Social Impact

底層仍保留 `sport_nutrition` 與 `fitness_health` 子 stream，但輸出時合併為同一頂層類別。

## 每日輸出契約

- 格式：Markdown (`.md`)
- 輸出目錄：`daily/`
- 檔名：`YYYYMMDD HHMM.Rader.md`
- 時區：`Asia/Tokyo`
- 每類 Featured：目標 5–8 篇；不足時不硬湊
- 每類 Candidate Pool：最多 30 篇，包含 Featured
- 四類總上限：120 篇

範例：

```text
daily/20260712 2224.Rader.md
```

## 配額模型

```text
Clinical Medicine             ≤30
Sport Science                 ≤30
Sport Nutrition & Fitness     ≤30
LLM & Social Impact           ≤30
```

每類各自排序、各自截斷。即使 Sport Science 當日有大量高分研究，也不能占用 Clinical Medicine、LLM 或其他類別的名額。

## 資料流

```text
PubMed + OpenAlex
→ OpenAlex 失敗時以 Crossref 補位
→ metadata 正規化
→ DOI / PMID / OpenAlex ID / title 去重
→ publication type 與研究設計判定
→ 標題優先的領域相關性 gate
→ evidence / relevance / interest / practical 分數
→ 四類獨立 Candidate Pool（每類 ≤30）
→ 四類獨立 Featured（每類 ≤8）
→ Europe PMC 補 OA、PMCID 與 DOI
→ Markdown
```

目前屬於 `AUTO-TRIAGE` 發現層，不是最終證據審核。正式引用前仍須完成全文、校正／撤稿、方法、引用與斷言核對。

## 臨床醫學來源

Clinical Medicine 會同時監測：

- JAMA Network Open
- eClinicalMedicine
- BMC Medicine
- BMJ Open / BMJ Medicine
- Communications Medicine
- PLOS Medicine
- Lancet Regional Health 系列
- 主要綜合醫學期刊中的 RCT、Meta、SR 與 guideline

OA-first，但不是 OA-only。

## 品質防線

`src/quality.py`、`src/clinical.py` 與 `src/categories.py` 會：

1. 阻止 protocol、letter、editorial、correction 冒充 RCT 或高階證據。
2. 讓 scoping review、narrative review、preclinical evidence 保留自己的等級，不升格成 SR／RCT。
3. 以標題為主判斷是否真正屬於臨床、運動科學、運動營養／體適能或 LLM 社會研究。
4. 阻止只在摘要順帶提到 exercise、protein、recovery、biomechanics 的跨領域文章混入。
5. 將動物研究標成 `Preclinical/U`，不得繼承 human RCT／longitudinal tier。
6. 確保四個頂層類別各自保有最多 30 個候選名額。

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

- [`config/output.yml`](config/output.yml)：四類輸出契約
- [`config/streams.yml`](config/streams.yml)：臨床與各子 stream 查詢式
- [`config/scoring.yml`](config/scoring.yml)：每類 Candidate／Featured 門檻與分數權重
- [`src/quality.py`](src/quality.py)：研究設計、相關性、排除與 fallback 規則
- [`src/clinical.py`](src/clinical.py)：Clinical Medicine relevance gate
- [`src/categories.py`](src/categories.py)：四類獨立配額、Featured 與 Markdown renderer

## 選文原則

1. 高證據等級優先：Meta、SR、RCT、guideline、consensus、大型 cohort、高品質 longitudinal／field experiment。
2. 允許納入證據未成熟、但機轉、方法或社會影響值得追蹤的研究。
3. 證據強度與有趣程度分開評分，避免把吸睛題目誤當強證據。
4. OA-first，但不是 OA-only。
