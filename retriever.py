"""
retriever.py  —  서정대 보도자료 통합 검색 모듈

적용 기법 (우선순위 순):
  1. 메타데이터 필터링     카테고리·분량유형으로 검색 범위 제한
  2. 하이브리드 검색       BM25(키워드) + 벡터(의미) → RRF 통합
  3. Reranking            cross-encoder로 후보군 재점수
  4. MMR                  다양성 확보 (특집 작성 시 유용)

의존 패키지:
    pip install rank-bm25 sentence-transformers
    (나머지는 build_db.py와 공유)

사용 예:
    from retriever import SeojeongRetriever
    r = SeojeongRetriever()
    docs = r.search("총장배 용접대회", category="글로벌 교육 무대", k=3)
"""

import re
import os
from typing import Optional

# ── 선택 패키지 (없어도 graceful fallback) ─────────────────
try:
    from rank_bm25 import BM25Okapi
    HAS_BM25 = True
except ImportError:
    HAS_BM25 = False
    print("⚠ rank-bm25 미설치 → 벡터 검색만 사용  (pip install rank-bm25)")

try:
    from sentence_transformers import CrossEncoder
    HAS_RERANKER = True
except ImportError:
    HAS_RERANKER = False
    print("⚠ CrossEncoder 미설치 → Reranking 비활성  (pip install sentence-transformers)")

# ── LangChain imports (버전 호환) ──────────────────────────
try:
    from langchain_core.documents import Document
except ImportError:
    try:
        from langchain.schema import Document  # type: ignore
    except ImportError:
        from langchain.docstore.document import Document  # type: ignore

try:
    from langchain_chroma import Chroma
except ImportError:
    from langchain_community.vectorstores import Chroma  # type: ignore

try:
    from langchain_huggingface import HuggingFaceEmbeddings
except ImportError:
    from langchain_community.embeddings import HuggingFaceEmbeddings  # type: ignore


# ══════════════════════════════════════════════════════════
# 설정 (build_db.py 와 동일하게 맞출 것)
# ══════════════════════════════════════════════════════════
DB_PATH        = "seojeong_db_v2"
EMBED_MODEL    = "BAAI/bge-m3"
RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"   # 다국어 cross-encoder
RRF_K          = 60   # RRF 상수 (클수록 순위 차이가 완만해짐)


# ══════════════════════════════════════════════════════════
# 유틸
# ══════════════════════════════════════════════════════════
def _get_device() -> str:
    try:
        import torch
        if torch.cuda.is_available():   return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


def _tokenize(text: str) -> list[str]:
    """한국어 BM25용 간이 토크나이저 (공백·특수문자 분리)."""
    tokens = re.split(r'[\s\.,·\-\[\]()\{\}"\']+', text)
    return [t for t in tokens if len(t) > 1]


def _rrf(rank_lists: list[list[int]], k: int = RRF_K) -> list[float]:
    """
    Reciprocal Rank Fusion: 여러 랭킹 목록을 하나의 점수로 통합.
    rank_lists[i][j] = i번째 방법에서 j번 문서의 순위 (0-based)
    """
    n_docs = max(len(rl) for rl in rank_lists)
    scores = [0.0] * n_docs
    for rl in rank_lists:
        for rank, doc_idx in enumerate(rl):
            scores[doc_idx] += 1.0 / (k + rank)
    return scores


# ══════════════════════════════════════════════════════════
# SeojeongRetriever
# ══════════════════════════════════════════════════════════
class SeojeongRetriever:
    """
    서정대 보도자료 DB에 특화된 통합 검색기.

    초기화(load)는 한 번만 수행하고, Streamlit의 @st.cache_resource
    또는 모듈 레벨 싱글톤으로 재사용하도록 설계됨.
    """

    def __init__(
        self,
        db_path: str = DB_PATH,
        embed_model: str = EMBED_MODEL,
        reranker_model: str = RERANKER_MODEL,
    ):
        device = _get_device()
        print(f"[Retriever] 초기화 중 (device={device})")

        # ── 1. Chroma 벡터 DB 로드 ─────────────────────────
        embeddings = HuggingFaceEmbeddings(
            model_name=embed_model,
            model_kwargs={"device": device},
            encode_kwargs={"normalize_embeddings": True},
        )
        self._db = Chroma(
            persist_directory=db_path,
            embedding_function=embeddings,
        )

        # ── 2. 전체 문서 가져와서 BM25 인덱스 구성 ─────────
        if HAS_BM25:
            raw = self._db.get()            # {'ids':[], 'documents':[], 'metadatas':[]}
            self._all_texts     = raw["documents"]
            self._all_metadatas = raw["metadatas"]
            self._all_ids       = raw["ids"]
            tokenized = [_tokenize(t) for t in self._all_texts]
            self._bm25 = BM25Okapi(tokenized)
            print(f"[Retriever] BM25 인덱스 구성 완료: {len(self._all_texts)}건")
        else:
            self._all_texts = self._all_metadatas = self._all_ids = []
            self._bm25 = None

        # ── 3. Cross-encoder Reranker 로드 ─────────────────
        if HAS_RERANKER:
            try:
                self._reranker = CrossEncoder(reranker_model)
                print(f"[Retriever] Reranker 로드 완료: {reranker_model}")
            except Exception as e:
                print(f"[Retriever] Reranker 로드 실패 ({e}) → Reranking 비활성")
                self._reranker = None
        else:
            self._reranker = None

        print("[Retriever] 준비 완료 ✅")


    # ──────────────────────────────────────────────────────
    def search(
        self,
        query: str,
        category: Optional[str] = None,
        length_type: Optional[str] = None,
        k: int = 3,
        use_mmr: bool = False,
    ) -> list[Document]:
        """
        최종 검색 인터페이스.

        Args:
            query       : 검색 쿼리
            category    : 카테고리 필터 (예: "글로벌 교육 무대")
            length_type : 분량 필터 (예: "특집")
            k           : 최종 반환 건수
            use_mmr     : True면 다양성 우선 (특집 작성 시 권장)

        Returns:
            List[Document] — 최상위 k건
        """
        # 메타데이터 필터 구성
        filter_dict: Optional[dict] = None
        if category and length_type:
            filter_dict = {"$and": [{"category": category}, {"length_type": length_type}]}
        elif category:
            filter_dict = {"category": category}
        elif length_type:
            filter_dict = {"length_type": length_type}

        # ── 후보 풀 수 (Reranker가 있으면 넉넉히, 없으면 최소) ─
        fetch_k = max(k * 5, 15)

        # ── Step 1 : 벡터 검색 ─────────────────────────────
        if use_mmr:
            vec_docs = self._mmr_search(query, filter_dict, fetch_k)
        else:
            vec_docs = self._vector_search(query, filter_dict, fetch_k)

        # ── Step 2 : BM25 검색 ─────────────────────────────
        bm25_docs = self._bm25_search(query, category, length_type, fetch_k) if self._bm25 else []

        # ── Step 3 : RRF 통합 ──────────────────────────────
        if bm25_docs:
            merged = self._rrf_merge(vec_docs, bm25_docs, top_n=fetch_k)
        else:
            merged = vec_docs[:fetch_k]

        # ── Step 4 : Reranking ─────────────────────────────
        if self._reranker and len(merged) > k:
            merged = self._rerank(query, merged, top_n=k)
        else:
            merged = merged[:k]

        return merged


    # ──────────────────────────────────────────────────────
    # 내부 메서드
    # ──────────────────────────────────────────────────────
    def _vector_search(self, query, filter_dict, k):
        try:
            return self._db.similarity_search(
                query, k=k, filter=filter_dict
            )
        except Exception:
            # 필터 결과 없음 등 예외 → 필터 없이 재시도
            return self._db.similarity_search(query, k=k)


    def _mmr_search(self, query, filter_dict, k):
        try:
            return self._db.max_marginal_relevance_search(
                query,
                k=k,
                fetch_k=k * 3,
                lambda_mult=0.7,   # 관련성 70% + 다양성 30%
                filter=filter_dict,
            )
        except Exception:
            return self._db.max_marginal_relevance_search(
                query, k=k, fetch_k=k * 3, lambda_mult=0.7
            )


    def _bm25_search(self, query, category, length_type, k) -> list[Document]:
        """BM25 검색 후 메타데이터 필터 후처리."""
        q_tokens  = _tokenize(query)
        scores    = self._bm25.get_scores(q_tokens)

        # 인덱스-점수 쌍 정렬 (높은 순)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)

        results = []
        for idx, score in ranked:
            if score <= 0:
                continue
            meta = self._all_metadatas[idx] or {}
            # 후처리 필터
            if category and meta.get("category") != category:
                continue
            if length_type and meta.get("length_type") != length_type:
                continue
            results.append(
                Document(
                    page_content=self._all_texts[idx],
                    metadata={**meta, "_bm25_score": round(score, 4)},
                )
            )
            if len(results) >= k:
                break
        return results


    def _rrf_merge(self, vec_docs, bm25_docs, top_n) -> list[Document]:
        """두 리스트를 RRF로 합산해서 순위 통합."""
        # 문서 고유 ID → Document 매핑
        doc_map: dict[str, Document] = {}
        vec_ids, bm25_ids = [], []

        for doc in vec_docs:
            uid = doc.metadata.get("id") or doc.page_content[:40]
            doc_map[uid] = doc
            vec_ids.append(uid)

        for doc in bm25_docs:
            uid = doc.metadata.get("id") or doc.page_content[:40]
            doc_map[uid] = doc
            bm25_ids.append(uid)

        all_ids = list(dict.fromkeys(vec_ids + bm25_ids))   # 순서 유지 중복제거
        id_to_idx = {uid: i for i, uid in enumerate(all_ids)}

        # 각 방법의 rank 순서 (인덱스 리스트)
        vec_rank  = [id_to_idx[uid] for uid in vec_ids]
        bm25_rank = [id_to_idx[uid] for uid in bm25_ids]

        scores = _rrf([vec_rank, bm25_rank])
        sorted_ids = sorted(all_ids, key=lambda uid: scores[id_to_idx[uid]], reverse=True)
        return [doc_map[uid] for uid in sorted_ids[:top_n]]


    def _rerank(self, query, docs, top_n) -> list[Document]:
        """Cross-encoder로 query-document 쌍을 재점수 매김."""
        pairs = [[query, doc.page_content] for doc in docs]
        scores = self._reranker.predict(pairs)
        ranked = sorted(zip(docs, scores), key=lambda x: x[1], reverse=True)
        return [doc for doc, _ in ranked[:top_n]]


# ══════════════════════════════════════════════════════════
# 독립 실행 테스트
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    r = SeojeongRetriever()
    print("\n" + "="*50)
    print("테스트 검색")
    print("="*50)

    tests = [
        ("외국인 유학생 용접 기능대회",   "글로벌 교육 무대", None),
        ("국가시험 100% 합격 입시",      "미래를 여는 입시", "특집"),
        ("베이비부머 행복캠퍼스 교육생",   "평생교육",        None),
        ("HiVE RISE 사업 성과",         "세상의 힘이 되는 성과", None),
    ]

    for query, cat, lt in tests:
        print(f"\n[쿼리] {query}")
        if cat: print(f"[카테고리] {cat}")
        if lt:  print(f"[분량] {lt}")
        docs = r.search(query, category=cat, length_type=lt, k=2)
        for i, d in enumerate(docs, 1):
            title = d.metadata.get("title", "")[:45]
            date  = d.metadata.get("date", "")
            media = d.metadata.get("media", "")
            print(f"  {i}. {title}  ({date}, {media})")
