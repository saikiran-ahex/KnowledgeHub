import json
import logging
from typing import Any, Literal, TypedDict

from langchain_core.documents import Document
from langgraph.graph import END, START, StateGraph

from app import database
from app.services.rag_service import RagService

logger = logging.getLogger(__name__)


QueryCategory = Literal["conversational", "out_of_scope", "document_query"]


class QueryGraphState(TypedDict, total=False):
    question: str
    history: list[dict]
    filters: dict
    owner_ids: list[str]
    top_k: int
    category: QueryCategory
    fallback_flag: bool
    direct_answer: str
    hyde_answer: str
    search_queries: list[str]
    routed_doc_ids: list[str]
    candidates: list[Document]
    selected_docs: list[Document]
    answer: str
    sources: list[dict]
    evaluation_scores: dict[str, float | None]
    review_flag: bool
    review_reason: str | None
    retry_count: int


class QueryGraphService:
    def __init__(self, rag_service: RagService):
        self.rag = rag_service
        self.graph = self._build_graph()

    def run(
        self,
        question: str,
        *,
        history: list[dict] | None = None,
        filters: dict | None = None,
        owner_ids: list[str] | None = None,
        top_k: int | None = None,
    ) -> dict[str, Any]:
        state: QueryGraphState = {
            "question": question,
            "history": history or [],
            "filters": dict(filters or {}),
            "owner_ids": owner_ids or [],
            "top_k": top_k or self.rag.settings.retrieval_top_k,
            "retry_count": 0,
            "review_flag": False,
        }
        return self.graph.invoke(state)

    def _build_graph(self):
        graph = StateGraph(QueryGraphState)
        graph.add_node("classify", self._classify_query)
        graph.add_node("direct_response", self._direct_response)
        graph.add_node("hyde", self._hyde)
        graph.add_node("route_documents", self._route_documents)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("rerank", self._rerank)
        graph.add_node("generate", self._generate_answer)
        graph.add_node("self_eval", self._self_evaluate)
        graph.add_node("judge", self._judge)

        graph.add_edge(START, "classify")
        graph.add_conditional_edges(
            "classify",
            self._route_after_classify,
            {
                "direct_response": "direct_response",
                "hyde": "hyde",
            },
        )
        graph.add_edge("direct_response", END)
        graph.add_edge("hyde", "route_documents")
        graph.add_edge("route_documents", "retrieve")
        graph.add_edge("retrieve", "rerank")
        graph.add_edge("rerank", "generate")
        graph.add_edge("generate", "self_eval")
        graph.add_conditional_edges(
            "self_eval",
            self._route_after_self_eval,
            {
                "judge": "judge",
                "retrieve": "retrieve",
            },
        )
        graph.add_edge("judge", END)
        return graph.compile()

    def _route_after_classify(self, state: QueryGraphState) -> str:
        if state.get("category") in {"conversational", "out_of_scope"}:
            return "direct_response"
        return "hyde"

    def _route_after_self_eval(self, state: QueryGraphState) -> str:
        if (
            state.get("review_flag")
            and state.get("retry_count", 0) < 1
            and state.get("candidates")
        ):
            state["retry_count"] = state.get("retry_count", 0) + 1
            return "retrieve"
        return "judge"

    def _classify_query(self, state: QueryGraphState) -> QueryGraphState:
        docs = database.list_documents()
        doc_metadata = [(row.get("domain"), row.get("description")) for row in docs]
        
        system_prompt = (
            "You are a query classifier. Classify the user message into exactly one of the following "
            "categories. Return only JSON with a single field called category. "
            "If the question is conversational (hi, hello, thanks, thank you, bye, etc...), choose conversational. "
            "If the question is about things outside the scope of the documents, choose out_of_scope. "
            "If the question is about the documents, choose document_query. "
            f"The categories are conversational, out_of_scope, document_query.{(doc_metadata)}\n\n"
        )
        response = self.rag._invoke_chat(f"{system_prompt}\n\nUser message:\n{state['question']}")
        category: QueryCategory = "document_query"
        try:
            payload = json.loads(str(response.content).strip())
            parsed = str(payload.get("category") or "").strip()
            if parsed in {"conversational", "out_of_scope", "document_query"}:
                category = parsed  # type: ignore[assignment]
        except Exception:
            lowered = state["question"].strip().lower()
            if lowered in {"hi", "hello", "thanks", "thank you", "bye"}:
                category = "conversational"
        state["category"] = category
        state["fallback_flag"] = category == "out_of_scope"
        logger.info('Query classified question="%s" category=%s', state['question'][:80], category)
        return state

    def _direct_response(self, state: QueryGraphState) -> QueryGraphState:
        category = state.get("category")
        question = state["question"]
        history = state.get("history") or []
        history_text = '\n'.join(
            f"{m.get('role', 'user').capitalize()}: {m.get('content', '')}"
            for m in history[-12:]
            if m.get('role') in ('user', 'assistant', 'system')
        )
        context_prefix = f"Conversation history:\n{history_text}\n\n" if history_text else ""
        if category == "conversational":
            response = self.rag._invoke_chat(f"{context_prefix}Reply warmly and briefly to the user.\n\nUser message:\n{question}")
            state["answer"] = str(response.content).strip()
        else:
            state["answer"] = "I'm sorry, I couldn't find any relevant information in the uploaded documents to answer your question. Please try rephrasing or ask about a different topic."
        state["sources"] = []
        state["evaluation_scores"] = {}
        return state

    def _hyde(self, state: QueryGraphState) -> QueryGraphState:
        response = self.rag._invoke_chat(
            "You are a helpful assistant. Generate a hypothetical ideal answer to the question as if it "
            "came from a highly relevant technical document. Write two to three sentences directly.\n\n"
            f"Question:\n{state['question']}"
        )
        hyde_answer = str(response.content).strip() or state["question"]
        search_queries = [state["question"], hyde_answer]
        state["hyde_answer"] = hyde_answer
        state["search_queries"] = search_queries
        return state

    def _route_documents(self, state: QueryGraphState) -> QueryGraphState:
        documents = database.list_documents()
        if not documents:
            logger.info('Document routing skipped: no documents in library')
            state["routed_doc_ids"] = []
            return state

        docs_payload = [
            {
                "doc_id": row.get("doc_id"),
                "domain": row.get("domain"),
                "description": row.get("description"),
            }
            for row in documents
        ]
        response = self.rag._invoke_chat(
            "You are a document router. Given the user question and available documents, return JSON "
            "with relevant_doc_ids as an array of doc IDs likely to contain the answer. If all may be "
            "relevant return all IDs. If none seem relevant return an empty array.\n\n"
            + json.dumps(
                {
                    "question": state["question"],
                    "documents": docs_payload,
                }
            )
        )
        try:
            payload = json.loads(str(response.content).strip())
            doc_ids = [str(item) for item in payload.get("relevant_doc_ids", []) if item]
        except Exception:
            doc_ids = []
        logger.info('Document routing completed total_docs=%s routed_doc_ids=%s', len(documents), doc_ids)
        state["routed_doc_ids"] = doc_ids
        return state

    def _retrieve(self, state: QueryGraphState) -> QueryGraphState:
        filters = dict(state.get("filters") or {})
        routed_doc_ids = state.get("routed_doc_ids") or []
        if len(routed_doc_ids) == 1:
            filters["doc_id"] = routed_doc_ids[0]
        queries = list(dict.fromkeys(state.get("search_queries") or [state["question"]]))
        logger.info('Retrieve started queries=%s filters=%s owner_ids=%s', queries, filters, state.get("owner_ids"))
        candidates = self.rag._retrieve_candidates(
            queries,
            state["top_k"],
            filters=filters,
            owner_ids=state.get("owner_ids") or None,
        )
        logger.info('Retrieve completed candidates=%s', len(candidates))
        state["filters"] = filters
        state["candidates"] = candidates
        return state

    def _rerank(self, state: QueryGraphState) -> QueryGraphState:
        candidates = state.get("candidates") or []
        if not candidates:
            state["selected_docs"] = []
            return state
        selected_docs, fallback_used = self.rag._rerank_documents(candidates, state["question"])
        state["selected_docs"] = selected_docs
        if fallback_used:
            state["review_flag"] = True
            state["review_reason"] = "Reranker fallback used"
        return state

    def _generate_answer(self, state: QueryGraphState) -> QueryGraphState:
        selected_docs = state.get("selected_docs") or []
        answer, sources = self.rag._answer_from_documents(
            state["question"],
            selected_docs,
            history=state.get("history") or [],
        )
        state["answer"] = answer
        state["sources"] = sources
        return state

    def _self_evaluate(self, state: QueryGraphState) -> QueryGraphState:
        selected_docs = state.get("selected_docs") or []
        context = "\n\n".join(
            f"Source: {doc.metadata.get('source', 'unknown')}\nContent: {doc.page_content}"
            for doc in selected_docs
        )
        response = self.rag._invoke_chat(
            "You are a strict answer quality evaluator. Return only JSON with faithfulness, relevance, "
            "completeness, overall, and issues. All score fields must be floats between 0 and 1.\n\n"
            + json.dumps(
                {
                    "question": state["question"],
                    "context": context,
                    "answer": state.get("answer", ""),
                }
            )
        )
        scores = {
            "faithfulness": None,
            "relevance": None,
            "completeness": None,
            "overall": None,
        }
        try:
            payload = json.loads(str(response.content).strip())
            for key in scores:
                value = payload.get(key)
                scores[key] = float(value) if value is not None else None
            issues = payload.get("issues") or []
        except Exception:
            issues = []
        state["evaluation_scores"] = scores
        overall = scores.get("overall")
        if not (state.get("selected_docs") or []):
            state["review_flag"] = False
        elif overall is not None and overall < 0.7:
            state["review_flag"] = True
            state["review_reason"] = "; ".join(str(item) for item in issues[:3]) or "Low self-evaluation score"
        return state

    def _judge(self, state: QueryGraphState) -> QueryGraphState:
        selected_docs = state.get("selected_docs") or []
        context = "\n\n".join(
            f"Source: {doc.metadata.get('source', 'unknown')}\nContent: {doc.page_content}"
            for doc in selected_docs
        )
        response = self.rag._invoke_chat(
            "You are an independent quality judge evaluating a question answering system. Return only "
            "JSON with score between 0 and 1 and verdict of pass or fail.\n\n"
            + json.dumps(
                {
                    "question": state["question"],
                    "context": context,
                    "answer": state.get("answer", ""),
                }
            )
        )
        judge_score = None
        verdict = "pass"
        try:
            payload = json.loads(str(response.content).strip())
            raw_score = payload.get("score")
            judge_score = float(raw_score) if raw_score is not None else None
            verdict = str(payload.get("verdict") or "pass").lower()
        except Exception:
            pass

        evaluation_scores = dict(state.get("evaluation_scores") or {})
        evaluation_scores["judge_score"] = judge_score
        state["evaluation_scores"] = evaluation_scores
        if verdict == "fail":
            state["review_flag"] = True
            if not state.get("review_reason"):
                state["review_reason"] = "Judge marked answer as fail"
        return state
