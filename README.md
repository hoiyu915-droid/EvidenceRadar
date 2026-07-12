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
- Featured：5–8 篇
- Candidate Pool：最多 30 篇
- 若合格研究不足，不為湊數降低門檻

範例：

```text
daily/20260712 0630.Rader.md
```

## 選文原則

1. 高證據等級研究優先：Meta、SR、RCT、guideline、consensus、大型 cohort、高品質 longitudinal／field experiment。
2. 允許納入證據尚未成熟、但機轉、方法或社會影響特別值得追蹤的研究。
3. 「證據強度」與「有趣程度」分開評分，避免把吸睛題目誤當強證據。
4. OA-first，但不限定只能收錄 OA 期刊。

完整輸出設定見 [`config/output.yml`](config/output.yml)，日報格式見 [`templates/daily-radar.md`](templates/daily-radar.md)。
