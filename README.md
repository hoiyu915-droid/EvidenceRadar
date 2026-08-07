# EvidenceRadar

每日文獻雷達。系統把研究問題、publication identity、全文事件與歷史通知分開處理，並在每次成功執行後把輸出與持久狀態寫回 repository。

## 五個互不搶配額的頂層類別

1. Clinical Medicine
2. Sport Science
3. Sport Nutrition & Fitness
4. LLM Research
5. Human–AI Research

LLM Research 再依正式研究問題分成 L1–L9；Human–AI Research 分成 H1–H2。完整定義見 [`docs/research_taxonomy.md`](docs/research_taxonomy.md)。ACL、ICLR、NeurIPS、ICML、AAMAS、AAAI、IJCAI、TACL、CL、PMLR、JMLR 等只記入 `journal_or_venue`，不作 taxonomy。

## 每日輸出契約

- 格式：Markdown (`.md`)
- 目錄：`daily/`
- 檔名：`YYYYMMDD HHMM.Rader.md`
- 時區：`Asia/Tokyo`
- 每類 Featured：目標 5–8 篇；不足時不硬湊
- 每類 Candidate Pool：最多 30 篇，包含 Featured
- 五類總上限：150 篇
- AI 類先保留跨方向召回，再按分數補位

## 跨輪歷史與去重

`state/literature_registry.json` 是所有「曾經找出」文獻的持久 registry。每輪先做單輪 DOI／PMID／PMCID／OpenAlex／title 去重，再與 registry 比對；歷史中已存在的 work 會更新 `last_seen_at`、`seen_count`、來源、stream 與研究方向，但不再進入當輪 Candidate Pool。例外是可驗證的新事件：preprint 升級正式版本，或同一 work 首次取得 OA 全文；這些仍可重新入選一次，事件會寫入 `notified_events`。

識別優先序：

```text
DOI → PMID → PMCID → arXiv ID → Anthology ID → OpenAlex ID → normalized title
```

首次升級時會匯入：

- `state/readable_fulltext_event_ledger.json` 的既有全文事件
- `daily/*.md` 中已輸出的歷史文獻

每次成功執行另附加 `state/run_history.jsonl`，留下 new works、history duplicates、candidate、featured 與 registry 總量。Registry 只在整輪成功後以原子替換寫入，失敗執行不提交半成品。

## 資料流

```text
PubMed + OpenAlex
→ OpenAlex 失敗時以 Crossref 補位
→ metadata 正規化與研究設計判定
→ 單輪 work-level 去重
→ 歷史 registry 去重（抑制重覆通知）
→ 正式 research-problem taxonomy（L1–L9 / H1–H2）
→ 五類獨立 Candidate Pool
→ AI 類方向保留 + 跨方向價值排序
→ Europe PMC 補 OA、PMCID 與 DOI
→ Markdown
→ literature registry + run history
→ workflow commit / push `daily/` 與 `state/`
```

目前仍屬於 `AUTO-TRIAGE` 發現層。Venue、metadata 日期或搜尋引擎 freshness 不能單獨證明正式全文事件；正式引用前仍須核對全文、版本、校正／撤稿、方法、引用與斷言。

## 執行

需要 Python 3.12+：

```bash
python -m pip install -r requirements.txt
python -m pytest -q
python src/run.py
```

可指定檢索結束日期與回看天數：

```bash
python src/run.py --end-date 2026-08-08 --lookback-days 3
```

## GitHub Actions

`.github/workflows/daily-radar.yml`：

- 每天 `06:17 Asia/Tokyo` 執行
- 可由 `workflow_dispatch` 手動執行
- connector 可透過更新 `.manual-run` 觸發
- 自動測試與執行 radar
- 將 `daily/`、`state/literature_registry.json`、`state/run_history.jsonl` 一起 commit / push

可選 repository secrets：

| Secret | 用途 |
|---|---|
| `NCBI_API_KEY` | 提高 NCBI E-utilities 支援速率 |
| `NCBI_EMAIL` | NCBI／Crossref polite-pool 聯絡資訊 |
| `OPENALEX_API_KEY` | 提高 OpenAlex API 額度；缺少或限流時嘗試 Crossref fallback |

## 設定

- [`config/output.yml`](config/output.yml)：五類輸出契約
- [`config/streams.yml`](config/streams.yml)：臨床、運動、L1–L9、H1–H2 查詢式
- [`config/scoring.yml`](config/scoring.yml)：Candidate／Featured 門檻與方向配額
- [`src/formal_taxonomy.py`](src/formal_taxonomy.py)：正式 AI taxonomy、多標籤與方向平衡
- [`src/history.py`](src/history.py)：跨輪 registry、歷史匯入、抑制重覆與 run ledger
- [`src/quality.py`](src/quality.py)：研究設計、相關性與排除規則
- [`src/categories.py`](src/categories.py)：五類獨立配額與 Markdown renderer
