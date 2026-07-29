"""
ScholarForge RAG 模块 — 语义文献检索
轻量方案：TF-IDF 余弦相似度（零依赖，学术文本效果好）
预留接口：未来可切换 sentence-transformers / DashScope embedding

集成点：
- LiteratureAgent: 用语义相似度检索，替代纯关键词匹配
- WritingAgent: per-section 检索最相关文献

设计：
- PaperIndex: 内存索引，构建 paper 向量库
- PaperRetriever: 对外检索接口
"""
import logging
import math
import re
from typing import Any

logger = logging.getLogger("scholarforge.rag")


# ═══════════════════════════════════════════════════════════════
# 1. 中文分词（轻量，零依赖）
# ═══════════════════════════════════════════════════════════════

# 常见中文学术停用词
_CN_STOPWORDS = set("""
的 了 在 是 我 有 和 就 不 人 都 一 一个 上 也 很 到 说 要 去 你
会 着 没有 看 好 自己 这 他 她 它 们 那 些 所 为 所以 因为 但是
然而 如果 虽然 可以 能够 已经 还 又 再 才 之 与 及 或 从 对 以
将 被 把 让 向 关于 其 中 等 等等 等等 进而 此外 另外 以及 并
并且 而且 不仅 同时 通过 经过 根据 按照 基于 针对 对于 由于
随着 方面 领域 研究 方法 本文 我们 他们 因此 提出 利用 采用
进行 分析 结果 表明 显示 发现 具有 存在 需要 可能 作用 影响
相关 重要 主要 关键 不同 其中 当前 目前 未来 发展 包括
""".replace("\n", " ").split())


def _tokenize(text: str) -> list[str]:
    """中英文混合分词
    中文: 2-gram 切分（类似 Bigram 分词，无需 jieba）
    英文: 按空格/标点切 + 小写
    """
    tokens = []
    
    # 分离中英文
    parts = re.split(r'([a-zA-Z]+)', text)
    for part in parts:
        if not part:
            continue
        if re.match(r'^[a-zA-Z]+$', part):
            # 英文：小写 + 过滤短词
            word = part.lower()
            if len(word) > 1:
                tokens.append(word)
        else:
            # 中文：2-gram 切分
            chars = [c for c in part if '\u4e00' <= c <= '\u9fff']
            # unigram
            tokens.extend(chars)
            # bigram
            for i in range(len(chars) - 1):
                tokens.append(chars[i] + chars[i + 1])
    
    # 去停用词 + 长度过滤
    return [t for t in tokens if t not in _CN_STOPWORDS and len(t) >= 1]


# ═══════════════════════════════════════════════════════════════
# 2. TF-IDF 向量化
# ═══════════════════════════════════════════════════════════════

class TfidfVectorizer:
    """轻量 TF-IDF，零依赖"""
    
    def __init__(self, max_features: int = 5000):
        self.max_features = max_features
        self.vocabulary: dict[str, int] = {}  # term → index
        self.idf: dict[str, float] = {}
        self._fitted = False
    
    def fit(self, documents: list[str]):
        """构建词表 + 计算 IDF"""
        # 统计文档频率
        doc_count = len(documents)
        term_doc_count: dict[str, int] = {}
        
        for doc in documents:
            tokens = _tokenize(doc)
            seen = set(tokens)
            for t in seen:
                term_doc_count[t] = term_doc_count.get(t, 0) + 1
        
        # 保留高频词（按文档频率排序，取 top max_features）
        sorted_terms = sorted(term_doc_count.items(), key=lambda x: -x[1])
        sorted_terms = sorted_terms[:self.max_features]
        
        self.vocabulary = {t: i for i, (t, _) in enumerate(sorted_terms)}
        
        # 计算 IDF: log((doc_count + 1) / (term_doc_count + 1)) + 1 (平滑)
        for t in self.vocabulary:
            df = term_doc_count.get(t, 0)
            self.idf[t] = math.log((doc_count + 1) / (df + 1)) + 1
        
        self._fitted = True
        logger.info(f"[ScholarForge.RAG] TF-IDF fitted: {len(self.vocabulary)} terms from {doc_count} docs")
    
    def transform(self, documents: list[str]) -> list[dict[int, float]]:
        """TF-IDF 向量化（稀疏表示：index → weight）"""
        if not self._fitted:
            raise RuntimeError("TF-IDF not fitted yet")
        
        vectors = []
        for doc in documents:
            tokens = _tokenize(doc)
            # 统计 term frequency
            tf: dict[str, int] = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1
            
            # TF-IDF
            vec: dict[int, float] = {}
            for t, count in tf.items():
                if t in self.vocabulary:
                    idx = self.vocabulary[t]
                    vec[idx] = (1 + math.log(count)) * self.idf[t]  # sublinear TF scaling
            
            # L2 归一化
            norm = math.sqrt(sum(v * v for v in vec.values()))
            if norm > 0:
                vec = {k: v / norm for k, v in vec.items()}
            
            vectors.append(vec)
        
        return vectors
    
    def transform_one(self, document: str) -> dict[int, float]:
        return self.transform([document])[0]
    
    def fit_transform(self, documents: list[str]) -> list[dict[int, float]]:
        self.fit(documents)
        return self.transform(documents)


def _cosine_similarity(a: dict[int, float], b: dict[int, float]) -> float:
    """稀疏向量余弦相似度"""
    # L2 归一化后 dot product 即为 cosine
    score = 0.0
    for idx, val_a in a.items():
        if idx in b:
            score += val_a * b[idx]
    return score


# ═══════════════════════════════════════════════════════════════
# 3. PaperIndex — 文献索引
# ═══════════════════════════════════════════════════════════════

class PaperIndex:
    """
    文献语义索引
    构建 paper 向量库 + 提供相似度检索
    """
    
    def __init__(self):
        self.vectorizer = TfidfVectorizer(max_features=3000)
        self._papers: list[Any] = []
        self._vectors: list[dict[int, float]] = []
        self._indexed = False
    
    def index(self, papers: list):
        """构建索引
        papers: PaperCard 列表（带 title、abstract）
        """
        if not papers:
            return
        
        self._papers = papers
        
        # 构造索引文本：title + abstract 前300字
        texts = []
        for p in papers:
            title = p.title if hasattr(p, 'title') else ""
            abstract = (p.abstract or "")[:300] if hasattr(p, 'abstract') else ""
            keywords = (p.keywords or "") if hasattr(p, 'keywords') else ""
            texts.append(f"{title} {abstract} {keywords}")
        
        self._vectors = self.vectorizer.fit_transform(texts)
        self._indexed = True
        logger.info(f"[ScholarForge.RAG] Indexed {len(papers)} papers")
    
    def search(self, query: str, top_k: int = 10, min_score: float = 0.05) -> list[tuple[Any, float]]:
        """语义检索 — 返回 (paper, score) 列表按分数降序"""
        if not self._indexed:
            return []
        
        q_vec = self.vectorizer.transform_one(query)
        
        scored = []
        for i, vec in enumerate(self._vectors):
            score = _cosine_similarity(q_vec, vec)
            if score >= min_score:
                scored.append((self._papers[i], score))
        
        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]
    
    def search_multi_query(self, queries: list[str], top_k: int = 10, min_score: float = 0.05) -> list[tuple[Any, float]]:
        """多查询融合检索（最大分数聚合）"""
        if not self._indexed:
            return []
        
        # 各 query 分别检索，取最高分数
        all_hits: dict[int, tuple[Any, float]] = {}  # idx → (paper, max_score)
        
        for query in queries:
            q_vec = self.vectorizer.transform_one(query)
            for i, vec in enumerate(self._vectors):
                score = _cosine_similarity(q_vec, vec)
                if score >= min_score:
                    if i not in all_hits or score > all_hits[i][1]:
                        all_hits[i] = (self._papers[i], score)
        
        scored = list(all_hits.values())
        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]


# ═══════════════════════════════════════════════════════════════
# 4. PaperRetriever — 统一检索接口
# ═══════════════════════════════════════════════════════════════

class PaperRetriever:
    """
    统一文献检索器
    先关键词过滤 → 语义相似度重排 → 返回 top_k
    
    集成方式：
        retriever = PaperRetriever()
        retriever.load_papers(ctx.papers)
        results = retriever.retrieve("医学图像分割 U-Net 架构", top_k=5)
    """
    
    def __init__(self):
        self._index = PaperIndex()
        self._papers: list[Any] = []
        self._kw_index: dict[str, set[int]] = {}  # keyword → set of paper indices
    
    def load_papers(self, papers: list):
        """加载文献池并构建索引"""
        self._papers = papers
        self._index.index(papers)
        
        # 构建关键词倒排索引
        self._kw_index = {}
        for i, p in enumerate(papers):
            title = (p.title if hasattr(p, 'title') else "").lower()
            abstract = ((p.abstract or "")[:200] if hasattr(p, 'abstract') else "").lower()
            full_text = f"{title} {abstract}"
            tokens = _tokenize(full_text)
            for t in tokens:
                if t not in self._kw_index:
                    self._kw_index[t] = set()
                self._kw_index[t].add(i)
    
    def retrieve(self, query: str, top_k: int = 10, candidates: int = 30) -> list[tuple[Any, float]]:
        """
        两阶段检索：
        1. 关键词召回（倒排索引）→ up to candidates
        2. 语义重排（TF-IDF cosine）→ top_k
        """
        if not self._papers:
            return []
        
        # Step 1: 关键词召回
        query_tokens = _tokenize(query)
        
        if query_tokens and self._kw_index:
            # 投票：每个 token 匹配到的 paper 得 1 分
            vote: dict[int, int] = {}
            for t in query_tokens:
                for idx in self._kw_index.get(t, set()):
                    vote[idx] = vote.get(idx, 0) + 1
            
            if vote:
                # 取 top candidates 个候选
                sorted_candidates = sorted(vote.items(), key=lambda x: -x[1])[:candidates]
                candidate_papers = [self._papers[idx] for idx, _ in sorted_candidates]
            else:
                candidate_papers = self._papers[:candidates]
        else:
            candidate_papers = self._papers[:candidates]
        
        # Step 2: TF-IDF 语义重排
        # 临时索引候选 paper 做精确匹配
        temp_index = PaperIndex()
        temp_index.index(candidate_papers)
        return temp_index.search(query, top_k=top_k, min_score=0.01)
    
    def retrieve_for_writing(self, section_title: str, section_text: str, top_k: int = 5) -> list[tuple[Any, float]]:
        """写作专用检索：章节标题 + 内容联合检索"""
        queries = [section_title]
        if section_text:
            queries.append(section_text[:500])
        return self._index.search_multi_query(queries, top_k=top_k)
    
    def get_reranked(self, query: str, papers: list, top_k: int = 5) -> list[tuple[Any, float]]:
        """对已有 paper 列表做语义重排（不重建整个索引）"""
        if not papers:
            return []
        temp_index = PaperIndex()
        temp_index.index(papers)
        return temp_index.search(query, top_k=top_k)


import hashlib
from functools import lru_cache


def _papers_cache_key(papers: list) -> str:
    """生成文献列表的稳定缓存键"""
    parts = []
    for p in papers:
        pid = p.get("id", "") if isinstance(p, dict) else getattr(p, "id", "")
        title = p.get("title", "") if isinstance(p, dict) else getattr(p, "title", "")
        parts.append(f"{pid}:{title}")
    return hashlib.md5("|".join(parts).encode()).hexdigest()


@lru_cache(maxsize=8)
def _cached_tfidf_search(cache_key: str, papers_tuple: tuple, query: str, top_k: int) -> tuple:
    """带 LRU 缓存的 TF-IDF 语义搜索（papers 转 tuple 使其 hashable）"""
    papers = list(papers_tuple)
    if not papers or not query.strip():
        return tuple((p, 0.0) for p in papers[:top_k])

    docs = []
    for p in papers:
        title = p.get("title", "") if isinstance(p, dict) else getattr(p, "title", "")
        abstract = p.get("abstract", "") if isinstance(p, dict) else getattr(p, "abstract", "")
        authors = p.get("authors", []) if isinstance(p, dict) else getattr(p, "authors", [])
        if isinstance(authors, list):
            authors_str = " ".join(str(a) for a in authors)
        else:
            authors_str = str(authors)
        docs.append(f"{title} {authors_str} {abstract}")

    vec = TfidfVectorizer(max_features=2000)
    vec.fit(docs)
    doc_vectors = vec.transform(docs)
    query_vec = vec.transform_one(query)

    scores = []
    for i, dv in enumerate(doc_vectors):
        score = _cosine_similarity(query_vec, dv)
        scores.append((i, score))

    scores.sort(key=lambda x: -x[1])
    result = []
    for i, score in scores[:top_k]:
        result.append((papers[i], score))
    return tuple(result)


def semantic_search_literature(
    papers: list,
    query: str,
    top_k: int = 20,
) -> list[tuple[Any, float]]:
    """项目级语义搜索 — 对已入库的文献做 TF-IDF 语义检索（带 LRU 缓存）

    Args:
        papers: list of dicts (database rows) with title/abstract/authors
        query: 搜索查询
        top_k: 返回前 N 条
    Returns:
        list of (paper_dict, score) tuples
    """
    if not papers or not query.strip():
        return [(p, 0.0) for p in papers[:top_k]]

    # 尝试从缓存命中（相同文献集 + 不同查询可复用 fit）
    cache_key = _papers_cache_key(papers)
    # dict 不 hashable，转 tuple of frozen items
    try:
        papers_tuple = tuple(
            tuple(sorted(p.items())) if isinstance(p, dict) else p
            for p in papers
        )
        cached = _cached_tfidf_search(cache_key, papers_tuple, query, top_k)
        return list(cached)
    except TypeError:
        # 不可 hash 的对象直接走原逻辑
        pass

    # 构建 TF-IDF
    docs = []
    for p in papers:
        title = p.get("title", "") if isinstance(p, dict) else getattr(p, "title", "")
        abstract = p.get("abstract", "") if isinstance(p, dict) else getattr(p, "abstract", "")
        authors = p.get("authors", []) if isinstance(p, dict) else getattr(p, "authors", [])
        if isinstance(authors, list):
            authors_str = " ".join(str(a) for a in authors)
        else:
            authors_str = str(authors)
        docs.append(f"{title} {authors_str} {abstract}")

    vec = TfidfVectorizer(max_features=2000)
    vec.fit(docs)
    doc_vectors = vec.transform(docs)
    query_vec = vec.transform_one(query)

    scores = []
    for i, dv in enumerate(doc_vectors):
        score = _cosine_similarity(query_vec, dv)
        scores.append((i, score))

    scores.sort(key=lambda x: -x[1])
    result = []
    for i, score in scores[:top_k]:
        result.append((papers[i], score))
    return result
