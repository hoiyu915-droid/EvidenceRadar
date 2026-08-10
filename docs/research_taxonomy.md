# EvidenceRadar Formal Research Taxonomy

本 taxonomy 由 ChatGPT Work 在每輪即時搜尋後套用。它是語意分類政策，不是 Python module、venue 清單或自動程式執行規格；最新性與納入資格仍由 `EVIDENCE_RADAR_PROTOCOL.md` 的 source read、event gate 與 evidence governance 決定。

## Classification rule

分類回答「這篇研究解決甚麼問題」，不回答「它在哪個 venue 發表」或「社群現在用甚麼包裝詞」。一篇研究可同時標上多個 direction，但只有一個 primary category 用於配額。

## LLM Research

| Code | Direction | Scope |
|---|---|---|
| L1 | Model Behavior & Alignment | instruction following、sycophancy、hallucination、uncertainty、calibration、reasoning behavior、safety、robustness、jailbreak、alignment |
| L2 | Context & Inference-Time Computation | context selection/compression、long-context、test-time compute、prompting、in-context learning、reasoning scaffolds、self-reflection、verification |
| L3 | Retrieval & Grounding | dense/sparse/hybrid retrieval、query rewriting、reranking、evidence selection、source authority、citation verification、grounding、RAG safety、graph/temporal/multimodal retrieval |
| L4 | Memory & Personalization | working/episodic/semantic memory、continual memory、consolidation、forgetting、temporal decay、routing、personalization、user model、longitudinal adaptation |
| L5 | Agents & Decision Systems | planning、tool use、action selection、agent loop、reflection、browser/computer-use、autonomous execution、long-horizon tasks、credit assignment、agent safety |
| L6 | Multi-Agent Systems | coordination、communication、role assignment、task decomposition、debate、cooperation/competition、collective failure、multi-agent planning、emergent behavior |
| L7 | Systems, Runtime & Interfaces | serving、inference systems、runtime、orchestration、state machines/DAG、MCP、protocol/interface、sandbox、observability、latency/cost/reliability |
| L8 | Training, Adaptation & Model Architecture | pretraining、post-training、SFT、RL、preference optimization、distillation、continual learning、PEFT、MoE、architecture、data curriculum、synthetic data |
| L9 | Evaluation, Benchmarks & Measurement | benchmark design、contamination、judge reliability、LLM-as-a-judge、evaluation validity、agent/retrieval benchmark、longitudinal evaluation、ecological validity |

## Human–AI Research

| Code | Direction | Scope |
|---|---|---|
| H1 | Human–LLM Interaction / Relationship | attachment、companionship、anthropomorphism、trust、emotional dependence、social connection、well-being、persona relational effects、longitudinal relationship |
| H2 | Human–AI Interaction / HCI | interaction design、decision support、trust、agency、collaboration、appropriation、longitudinal use；非 LLM 系統只能進 H2 |

若研究只能間接推論到 companion／relationship，必須保留「推論」邊界，不得升格為 H1 的直接證據。

## Venue rule

ACL、ICLR、NeurIPS、ICML、AAMAS、AAAI、IJCAI、TACL、CL、PMLR、JMLR 是來源或 venue。Main、Findings、Workshop 是 publication identity。它們不得成為研究方向。

## Navigation aliases

流行詞只可作搜尋導航；正式納入時還原為研究問題：

| Navigation term | Canonical problem |
|---|---|
| Graph Engineering | workflow/state-machine orchestration |
| Loop Engineering | iterative agent control / planning |
| Harness AI | runtime/orchestration/tool execution |
| RAG 2.0 | retrieval planning / reranking / grounding / evidence selection |
| Memory Layers | episodic/semantic/working memory architecture |

## Minimum recall and diversity

Candidate Pool 先為當輪有合格候選的每個方向保留至少一席，再按跨方向價值排序補滿；LLM 單一方向最多六篇，Human–AI 單一方向最多十五篇。Featured 同樣先做方向保留，再補位；LLM 單一方向最多兩篇，Human–AI 單一方向最多四篇。若 L1–L9 同時活躍而 Featured 上限為八篇，依各方向最高分候選排序保留八個方向，未入選方向仍保留在 Candidate Pool 與 coverage 報告中。
