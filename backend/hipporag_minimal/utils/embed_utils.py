"""NumPy-only KNN for minimal HippoRAG (no torch dependency)."""
from typing import List

import numpy as np
from tqdm import tqdm


def retrieve_knn(
    query_ids: List[str],
    key_ids: List[str],
    query_vecs,
    key_vecs,
    k=2047,
    query_batch_size=1000,
    key_batch_size=10000,
):
    """
    Retrieve the top-k nearest neighbors for each query id from the key ids.
    Uses NumPy only (no torch).
    """
    if len(key_vecs) == 0:
        return {}

    query_vecs = np.asarray(query_vecs, dtype=np.float32)
    key_vecs = np.asarray(key_vecs, dtype=np.float32)

    # L2-normalize
    qn = np.linalg.norm(query_vecs, axis=1, keepdims=True)
    qn[qn == 0] = 1
    query_vecs = query_vecs / qn
    kn = np.linalg.norm(key_vecs, axis=1, keepdims=True)
    kn[kn == 0] = 1
    key_vecs = key_vecs / kn

    results = {}

    def get_batches(vecs, batch_size):
        for i in range(0, len(vecs), batch_size):
            yield vecs[i : i + batch_size], i

    for query_batch, query_batch_start_idx in tqdm(
        get_batches(vecs=query_vecs, batch_size=query_batch_size),
        total=(len(query_vecs) + query_batch_size - 1) // query_batch_size,
        desc="KNN for Queries",
    ):
        batch_topk_sim_scores = []
        batch_topk_indices = []
        offset_keys = 0

        for key_batch, key_batch_start_idx in get_batches(vecs=key_vecs, batch_size=key_batch_size):
            # (Q, K) similarity
            similarity = np.dot(query_batch, key_batch.T)
            actual_k = min(k, similarity.shape[1])
            if actual_k <= 0:
                offset_keys += key_batch.shape[0]
                continue
            # topk per query row
            topk_idx_relative = np.argsort(-similarity, axis=1)[:, :actual_k]
            topk_scores = np.take_along_axis(similarity, topk_idx_relative, axis=1)
            topk_indices_abs = topk_idx_relative + offset_keys
            batch_topk_sim_scores.append(topk_scores)
            batch_topk_indices.append(topk_indices_abs)
            offset_keys += key_batch.shape[0]

        if not batch_topk_sim_scores:
            continue
        batch_topk_sim_scores = np.concatenate(batch_topk_sim_scores, axis=1)
        batch_topk_indices = np.concatenate(batch_topk_indices, axis=1)
        final_k = min(k, batch_topk_sim_scores.shape[1])
        if final_k <= 0:
            continue
        final_order = np.argsort(-batch_topk_sim_scores, axis=1)[:, :final_k]
        final_topk_indices = np.take_along_axis(batch_topk_indices, final_order, axis=1)
        final_topk_sim_scores = np.take_along_axis(batch_topk_sim_scores, final_order, axis=1)

        for i in range(final_topk_indices.shape[0]):
            query_relative_idx = query_batch_start_idx + i
            query_idx = query_ids[query_relative_idx]
            key_idxs = final_topk_indices[i].tolist()
            scores = final_topk_sim_scores[i].tolist()
            query_to_topk_key_ids = [key_ids[idx] for idx in key_idxs]
            results[query_idx] = (query_to_topk_key_ids, scores)

    return results
