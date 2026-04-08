import argparse
import json
import math
import os
from pathlib import Path
from statistics import mean

from datasets import Dataset
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app import database
from app.config import get_settings
from app.services.rag_service import RagService


DEFAULT_DATASET_PATH = Path("data/eval/sample_ragas_eval.jsonl")
DEFAULT_OUTPUT_PATH = Path("data/eval/latest_ragas_report.json")
os.environ.setdefault("GIT_PYTHON_REFRESH", "quiet")


class NoTemperatureLangchainLLMWrapper:
    def __init__(self, langchain_llm):
        from ragas.llms import LangchainLLMWrapper

        self._wrapped = LangchainLLMWrapper(langchain_llm)
        self.langchain_llm = self._wrapped.langchain_llm
        self.run_config = getattr(self._wrapped, "run_config", None)

    def __getattr__(self, name):
        return getattr(self._wrapped, name)

    def _normalize_generations(self, result):
        generations = [[generation[0] for generation in result.generations]]
        result.generations = generations
        return result

    def generate_text(
        self,
        prompt,
        n: int = 1,
        temperature=None,
        stop=None,
        callbacks=None,
    ):
        try:
            return self.langchain_llm.generate_prompt(
                prompts=[prompt],
                n=n,
                stop=stop,
                callbacks=callbacks,
            )
        except TypeError:
            result = self.langchain_llm.generate_prompt(
                prompts=[prompt] * n,
                stop=stop,
                callbacks=callbacks,
            )
            return self._normalize_generations(result)

    async def agenerate_text(
        self,
        prompt,
        n: int = 1,
        temperature=None,
        stop=None,
        callbacks=None,
    ):
        try:
            return await self.langchain_llm.agenerate_prompt(
                prompts=[prompt],
                n=n,
                stop=stop,
                callbacks=callbacks,
            )
        except TypeError:
            result = await self.langchain_llm.agenerate_prompt(
                prompts=[prompt] * n,
                stop=stop,
                callbacks=callbacks,
            )
            return self._normalize_generations(result)


def load_dataset(path: Path, *, max_rows: int | None = None) -> tuple[list[dict], int]:
    parsed_rows: list[dict] = []
    total_rows = 0
    for idx, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        stripped_line = line.strip()
        if not stripped_line or stripped_line.startswith("//") or stripped_line.startswith("#"):
            continue
        total_rows += 1
        item = json.loads(stripped_line)
        question = str(item.get("question", "")).strip()
        ground_truth = str(item.get("ground_truth", "")).strip()
        if not question or not ground_truth:
            raise ValueError(f"Dataset row {idx} must contain non-empty question and ground_truth")
        parsed_rows.append(
            {
                "id": item.get("id", idx),
                "question": question,
                "ground_truth": ground_truth,
                "filters": item.get("filters") or {},
                "source": item.get("source"),
                "doc_id": item.get("doc_id"),
            }
        )
    rows = parsed_rows[-max_rows:] if max_rows is not None else parsed_rows
    return rows, total_rows


def collect_prediction(
    rag: RagService,
    question: str,
    top_k: int | None,
    filters: dict | None = None,
    *,
    use_rerank: bool = True,
) -> dict:
    queries = [question]
    queries.extend([q for q in rag._expand_query(question) if q.lower() != question.lower()])
    effective_filters = dict(filters or {})
    admin_owner_ids = list(dict.fromkeys([str(user_id) for user_id in database.get_admin_user_ids()] + ["admin"]))
    fallback_without_source = False

    retrieve_k = top_k or rag.settings.retrieval_top_k
    candidates = rag._retrieve_candidates(queries, retrieve_k, filters=effective_filters, owner_ids=admin_owner_ids)
    if not candidates and effective_filters.get("source"):
        fallback_filters = dict(effective_filters)
        fallback_filters.pop("source", None)
        fallback_candidates = rag._retrieve_candidates(
            queries,
            retrieve_k,
            filters=fallback_filters,
            owner_ids=admin_owner_ids,
        )
        if fallback_candidates:
            candidates = fallback_candidates
            effective_filters = fallback_filters
            fallback_without_source = True
    if use_rerank and rag.reranker is not None:
        try:
            selected_docs, rerank_fallback_used = rag._rerank_documents(candidates, question)
        except Exception:
            selected_docs = candidates[: rag.settings.rerank_top_k]
            rerank_fallback_used = True
    else:
        selected_docs = candidates[: rag.settings.rerank_top_k]
        rerank_fallback_used = False

    answer = rag._answer_from_documents_for_eval(question, selected_docs)
    contexts = [doc.page_content for doc in selected_docs]
    return {
        "answer": answer,
        "contexts": contexts,
        "expanded_queries": queries,
        "filters": effective_filters,
        "candidate_count": len(candidates),
        "fallback_without_source": fallback_without_source,
        "rerank_fallback_used": rerank_fallback_used,
        "selected_sources": [doc.metadata.get("source") for doc in selected_docs],
        "selected_doc_ids": [doc.metadata.get("doc_id") for doc in selected_docs],
        "selected_headings": [doc.metadata.get("heading") for doc in selected_docs],
    }


def _evaluate_with_internal_judges(rag: RagService, *, question: str, contexts: list[str], answer: str) -> dict:
    context = "\n\n".join(contexts)

    self_eval_response = rag._invoke_chat(
        "You are a strict answer quality evaluator. Return only JSON with faithfulness, relevance, "
        "completeness, overall, and issues. All score fields must be floats between 0 and 1.\n\n"
        + json.dumps(
            {
                "question": question,
                "context": context,
                "answer": answer,
            }
        )
    )

    self_scores = {
        "self_faithfulness": None,
        "self_relevance": None,
        "self_completeness": None,
        "self_overall": None,
    }
    self_issues: list[str] = []
    try:
        payload = json.loads(str(self_eval_response.content).strip())
        for source_key, target_key in (
            ("faithfulness", "self_faithfulness"),
            ("relevance", "self_relevance"),
            ("completeness", "self_completeness"),
            ("overall", "self_overall"),
        ):
            value = payload.get(source_key)
            self_scores[target_key] = float(value) if value is not None else None
        self_issues = [str(item) for item in (payload.get("issues") or [])]
    except Exception:
        pass

    judge_response = rag._invoke_chat(
        "You are an independent quality judge evaluating a question answering system. Return only "
        "JSON with score between 0 and 1 and verdict of pass or fail.\n\n"
        + json.dumps(
            {
                "question": question,
                "context": context,
                "answer": answer,
            }
        )
    )
    judge_score = None
    judge_verdict = "pass"
    try:
        payload = json.loads(str(judge_response.content).strip())
        raw_score = payload.get("score")
        judge_score = float(raw_score) if raw_score is not None else None
        judge_verdict = str(payload.get("verdict") or "pass").lower()
    except Exception:
        pass

    return {
        **self_scores,
        "self_issues": self_issues,
        "judge_score": judge_score,
        "judge_verdict": judge_verdict,
    }


def build_ragas_dataset(
    rows: list[dict],
    rag: RagService,
    top_k: int | None,
    *,
    use_rerank: bool = True,
) -> tuple[Dataset, list[dict]]:
    ragas_records: list[dict] = []
    diagnostics_records: list[dict] = []
    for row in rows:
        filters = dict(row.get("filters") or {})
        if row.get("source"):
            filters["source"] = row["source"]
        if row.get("doc_id"):
            filters["doc_id"] = row["doc_id"]
        prediction = collect_prediction(rag, row["question"], top_k, filters=filters, use_rerank=use_rerank)
        internal_eval = _evaluate_with_internal_judges(
            rag,
            question=row["question"],
            contexts=prediction["contexts"],
            answer=prediction["answer"],
        )
        ragas_records.append(
            {
                "question": row["question"],
                "ground_truth": row["ground_truth"],
                "answer": prediction["answer"],
                "contexts": prediction["contexts"],
            }
        )
        diagnostics_records.append(
            {
                "id": row["id"],
                "expected_source": row.get("source"),
                "expected_doc_id": row.get("doc_id"),
                "question": row["question"],
                "ground_truth": row["ground_truth"],
                "filters": prediction["filters"],
                "expanded_queries": prediction["expanded_queries"],
                "candidate_count": prediction["candidate_count"],
                "fallback_without_source": prediction["fallback_without_source"],
                "rerank_used": use_rerank,
                "rerank_fallback_used": prediction["rerank_fallback_used"],
                "selected_sources": prediction["selected_sources"],
                "selected_doc_ids": prediction["selected_doc_ids"],
                "selected_headings": prediction["selected_headings"],
                "retrieved_contexts": prediction["contexts"],
                "response": prediction["answer"],
                "self_faithfulness": internal_eval["self_faithfulness"],
                "self_relevance": internal_eval["self_relevance"],
                "self_completeness": internal_eval["self_completeness"],
                "self_overall": internal_eval["self_overall"],
                "self_issues": internal_eval["self_issues"],
                "judge_score": internal_eval["judge_score"],
                "judge_verdict": internal_eval["judge_verdict"],
            }
        )
    return Dataset.from_list(ragas_records), diagnostics_records


def _clean_float(value):
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _clean_records(records: list[dict]) -> list[dict]:
    cleaned: list[dict] = []
    for row in records:
        cleaned.append({key: _clean_float(value) for key, value in row.items()})
    return cleaned


def run_ragas_evaluation(
    dataset_path: Path | str = DEFAULT_DATASET_PATH,
    *,
    output_path: Path | str | None = DEFAULT_OUTPUT_PATH,
    top_k: int | None = None,
    use_rerank: bool = True,
) -> dict:
    settings = get_settings()
    rag = RagService()
    dataset_file = Path(dataset_path)
    rows, total_rows = load_dataset(dataset_file, max_rows=settings.evaluation_max_rows)
    dataset, diagnostics_records = build_ragas_dataset(rows, rag, top_k, use_rerank=use_rerank)
    from ragas import evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness

    llm = NoTemperatureLangchainLLMWrapper(
        ChatOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model=settings.evaluation_model,
            temperature=1,
        )
    )
    embeddings = LangchainEmbeddingsWrapper(
        OpenAIEmbeddings(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model=settings.embedding_model,
        )
    )

    result = evaluate(
        dataset,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=llm,
        embeddings=embeddings,
    )
    metric_records = _clean_records(result.to_pandas().to_dict(orient="records"))
    records: list[dict] = []
    for diagnostics, metrics in zip(diagnostics_records, metric_records):
        merged = dict(diagnostics)
        metric_row = dict(metrics)
        merged["user_input"] = str(metric_row.pop("user_input", diagnostics["question"]))
        merged["retrieved_contexts"] = metric_row.pop("retrieved_contexts", diagnostics["retrieved_contexts"])
        merged["response"] = metric_row.pop("response", diagnostics["response"])
        merged["reference"] = metric_row.pop("reference", diagnostics["ground_truth"])
        for key, value in metric_row.items():
            merged[key] = value
        records.append(merged)

    metric_names = (
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
        "self_faithfulness",
        "self_relevance",
        "self_completeness",
        "self_overall",
        "judge_score",
    )
    summary = {
        name: mean([float(row[name]) for row in records if row.get(name) is not None]) if any(row.get(name) is not None for row in records) else None
        for name in metric_names
    }

    resolved_output_path = Path(output_path) if output_path is not None else None
    report = {
        "dataset_path": str(dataset_file),
        "output_path": str(resolved_output_path) if resolved_output_path is not None else None,
        "samples": len(records),
        "total_rows": total_rows,
        "max_rows": settings.evaluation_max_rows,
        "truncated": total_rows > len(records),
        "use_rerank": use_rerank,
        "summary": summary,
        "results": records,
    }
    if resolved_output_path is not None:
        resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_output_path.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    return report


def load_saved_ragas_report(path: Path | str = DEFAULT_OUTPUT_PATH) -> dict | None:
    report_path = Path(path)
    if not report_path.exists():
        return None
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return {
            "dataset_path": str(DEFAULT_DATASET_PATH),
            "output_path": str(report_path),
            "samples": len(payload),
            "total_rows": len(payload),
            "max_rows": get_settings().evaluation_max_rows,
            "truncated": False,
            "use_rerank": True,
            "summary": {},
            "results": payload,
        }
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate KnowledgeHub with RAGAS.")
    parser.add_argument("--dataset", required=True, help="Path to a JSONL dataset with question and ground_truth.")
    parser.add_argument("--output", default="ragas_report.json", help="Path to write evaluation results as JSON.")
    parser.add_argument("--top-k", type=int, default=None, help="Optional retrieval top_k override.")
    parser.add_argument("--no-rerank", action="store_true", help="Disable rerank during evaluation.")
    args = parser.parse_args()

    report = run_ragas_evaluation(args.dataset, output_path=args.output, top_k=args.top_k, use_rerank=not args.no_rerank)
    print(json.dumps(report["summary"], indent=2))
    if report["output_path"]:
        print(f"Report written to: {report['output_path']}")


if __name__ == "__main__":
    main()
