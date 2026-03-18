# RAGAS Evaluation

This project now uses a RAGAS-based evaluation workflow for retrieval + answer quality.

## What It Evaluates

The evaluation script:

1. loads a labeled JSONL dataset
2. runs each question through the current KnowledgeHub RAG pipeline
3. captures retrieved contexts and generated answers
4. scores them with RAGAS

Current metrics:

- `faithfulness`
- `answer_relevancy`
- `context_precision`
- `context_recall`

Safety limit:

- each run is capped by `EVALUATION_MAX_ROWS` from backend settings
- if the dataset exceeds that limit, only the latest `EVALUATION_MAX_ROWS` rows are evaluated

## Dataset Format

Use one JSON object per line with:

- `question`
- `ground_truth`

Example:

```json
{"id":"alpha-decay-1","question":"What does alpha decay mean?","ground_truth":"Alpha decay is a radioactive decay process in which an unstable nucleus emits an alpha particle, reducing its atomic number by 2 and mass number by 4."}
```

Sample file:

- [sample_ragas_eval.jsonl](/c:/Ahex/KnowledgeHub/backend/data/eval/sample_ragas_eval.jsonl)

## Run

After rebuilding so `ragas` is installed:

```bash
docker compose up --build
docker exec -it rag_backend python -m app.evaluate_ragas --dataset /app/data/eval/sample_ragas_eval.jsonl --output /app/data/eval/ragas_report.json
```

For your own dataset, place it under `backend/data/` so it is visible inside the backend container.

Example:

```bash
docker exec -it rag_backend python -m app.evaluate_ragas --dataset /app/data/my_ragas_eval.jsonl --output /app/data/my_ragas_report.json
```

## Notes

- The script evaluates the same shared admin-indexed corpus used by `/ask` and `/chat`.
- It uses `EVALUATION_MODEL` as the RAGAS judge model and `EMBEDDING_MODEL` for embeddings.
- Recommended default evaluator: `gpt-4.1-nano-2025-04-14`.
- RAGAS evaluation costs API tokens.
- This setup assumes the current `ragas` and `langchain-openai` versions are compatible in your environment.
