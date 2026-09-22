"""ArguAna ranking experiments: brute-force baseline + bge-reranker on top-30.

Reuses the chroma temp dir built by the earlier session (vectors already
embedded) — a fresh process only re-ranks, no re-encoding.

Findings so far (from the interactive session):
- HNSW == brute-force cosine (0.4290 vs 0.4288) — Chroma layer is innocent.
- BGE query prompt (mteb leaderboard口径): 0.4344 — NOT the cause of the
  0.429 vs official-0.603 gap.
- Remaining gap is embedding/data revision口径 (HF vs BEIR zip) — flagged
  for the comparison-protocol, not a system bug.
This script measures the system lever: reranker recovery.
"""

import glob
import json
import sys
import time
from pathlib import Path

import torch

from agentnexus.core.config import get_settings
from agentnexus.eval.benchmarks import metrics
from agentnexus.eval.benchmarks.beir import BEIRDatasetSpec, load_beir_dataset
from agentnexus.storage.chroma import get_collection, reset_storage_client

TOP_N = 30
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 0  # 0 = all queries
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("arguana_rerank_result.json")

# reuse latest arguana-fix temp chroma (vectors already embedded)
candidates = sorted(glob.glob(str(Path.home() / "AppData/Local/Temp/arguana-fix-*")), key=Path)
if not candidates:
    raise SystemExit("no arguana-fix temp dir found")
settings = get_settings()
settings.chroma_persist_dir = candidates[-1]
reset_storage_client()

data = load_beir_dataset(BEIRDatasetSpec(name="arguana", display="ArguAna"), offline=True)
queries_eval = data.queries[:LIMIT] if LIMIT else data.queries
qtexts = [q.text for q in queries_eval]
qids = [q.query_id for q in queries_eval]
qrels_eval = {qid: rel for qid, rel in data.qrels.items() if qid in set(qids)}
all_ids = [d.doc_id for d in data.docs]

col = get_collection(namespace="arguana-fix")
res = col.get(ids=all_ids, include=["embeddings"])
doc_emb = torch.tensor(res["embeddings"], dtype=torch.float32)

from agentnexus.rag import embeddings as emb_svc

settings.embedding_model = "BAAI/bge-small-en-v1.5"
emb_svc.reset_embedding_model()
model = emb_svc.get_embedding_model()
qt = model.encode(qtexts, normalize_embeddings=True)
query_emb = torch.tensor(qt if hasattr(qt, "tolist") else qt, dtype=torch.float32)
sims = query_emb @ doc_emb.T
brute_order = sims.argsort(dim=1, descending=True)
id_arr = all_ids
brute_ranked = [[id_arr[i] for i in row[:100].tolist()] for row in brute_order]
m_brute, _ = metrics.score_rankings(dict(zip(qids, brute_ranked)), qrels_eval, k=10, recall_ks=(100,))
print(f"brute-force: ndcg@10={m_brute['ndcg@10']:.4f}", flush=True)

from sentence_transformers import CrossEncoder

reranker = CrossEncoder("BAAI/bge-reranker-v2-m3", device="cpu")
reranked = []
t0 = time.perf_counter()
BATCH_Q = 64
for start in range(0, len(qtexts), BATCH_Q):
    batch_pairs = []
    owners = []
    for qi in range(start, min(start + BATCH_Q, len(qtexts))):
        for di in brute_order[qi, :TOP_N].tolist():
            batch_pairs.append((qtexts[qi], data.docs[int(di)].indexed_text))
            owners.append((qi, int(di)))
    scores = reranker.predict(batch_pairs, batch_size=64, show_progress_bar=False)
    per_query = {}
    for (qi, di), s in zip(owners, scores):
        per_query.setdefault(qi, []).append((di, float(s)))
    for qi in range(start, min(start + BATCH_Q, len(qtexts))):
        top = sorted(per_query[qi], key=lambda x: x[1], reverse=True)[:10]
        reranked.append([id_arr[di] for di, _ in top])
    done = min(start + BATCH_Q, len(qtexts))
    if done % 256 == 0 or done == len(qtexts):
        print(f"reranked {done}/{len(qtexts)} ({time.perf_counter()-t0:.0f}s)", flush=True)

m_rr, _ = metrics.score_rankings(dict(zip(qids, reranked)), qrels_eval, k=10, recall_ks=(100,))
print(f"dense top-{TOP_N} + reranker: ndcg@10={m_rr['ndcg@10']:.4f} map={m_rr['map']:.4f}", flush=True)

OUT.write_text(json.dumps({
    "brute_force": m_brute,
    f"dense_top{TOP_N}_reranked": m_rr,
    "official_mteb_reference": {"ndcg_at_10": 0.60348, "map_at_100": 0.52998,
                                 "note": "leaderboard口径;embedding/数据revision差异未对齐"},
}, indent=2), encoding="utf-8")
print(f"result -> {OUT}", flush=True)
