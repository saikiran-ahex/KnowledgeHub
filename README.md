# KnowledgeHub

KnowledgeHub is a multimodal RAG application with:

- FastAPI backend
- React frontend
- PostgreSQL for app data
- Qdrant for vector retrieval
- LangGraph-backed query orchestration
- OpenAI for chat, embeddings, and vision-based image understanding
- Cohere reranking

## Current Architecture

### Roles

- Regular users can register, log in, chat against the shared library, attach ad-hoc files for a single request, submit feedback, and access only their own conversations.
- Admin users can do everything regular users can, plus open `/admin` to upload files, manage the shared library, run evaluation, and review flagged answers.

### Shared Library Model

- Indexed library files are admin-managed and globally available to all authenticated users.
- Duplicate uploads are blocked by `content_hash` across the shared library.
- Library files are stored in PostgreSQL and indexed into Qdrant.

### Ingestion Pipeline

On upload, the backend currently does the following:

1. Validates file type and size.
2. Profiles the document with the LLM to generate:
   - `domain`
   - `description`
3. Extracts text and chunks it for retrieval.
4. For PDF and DOCX files, extracts embedded images.
5. Describes standalone images and extracted document images with a vision model.
6. Embeds text chunks and image descriptions with `text-embedding-3-large`.
7. Stores chunks in the main Qdrant text collection.
8. Stores file and document metadata in PostgreSQL.

Current supported file types:

- Text/docs: `.txt`, `.md`, `.pdf`, `.doc`, `.docx`, `.csv`
- Images: `.png`, `.jpg`, `.jpeg`, `.webp`

### Query Pipeline

Normal document queries now go through a LangGraph-backed flow:

1. query classification
2. direct response for conversational / out-of-scope
3. HyDE generation
4. document routing using PostgreSQL `documents`
5. retrieval from Qdrant
6. reranking with Cohere
7. answer generation
8. self-evaluation
9. judge scoring

Ad-hoc file chat still uses the existing direct file-assisted path for stability.

### Conversations, Feedback, and Review

- Conversations are stored in PostgreSQL and scoped by `user_id`.
- Assistant messages can store:
  - `ragas_score`
  - `judge_score`
- Users can submit thumbs up / thumbs down feedback.
- Negative feedback or low-quality pipeline results can create items in the human review queue.

## Project Layout

### Backend

- API app: [backend/app/main.py](/c:/Ahex/KnowledgeHub/backend/app/main.py)
- Config: [backend/app/config.py](/c:/Ahex/KnowledgeHub/backend/app/config.py)
- Database layer: [backend/app/database.py](/c:/Ahex/KnowledgeHub/backend/app/database.py)
- RAG service: [backend/app/services/rag_service.py](/c:/Ahex/KnowledgeHub/backend/app/services/rag_service.py)
- Query graph: [backend/app/services/query_graph.py](/c:/Ahex/KnowledgeHub/backend/app/services/query_graph.py)
- Document loading: [backend/app/services/document_loader.py](/c:/Ahex/KnowledgeHub/backend/app/services/document_loader.py)

### Frontend

- User app: [frontend/src/App.jsx](/c:/Ahex/KnowledgeHub/frontend/src/App.jsx)
- Admin panel: [frontend/src/Admin.jsx](/c:/Ahex/KnowledgeHub/frontend/src/Admin.jsx)
- Styles: [frontend/src/styles.css](/c:/Ahex/KnowledgeHub/frontend/src/styles.css)

## Storage

### PostgreSQL

PostgreSQL stores:

- users
- files
- documents
- conversations
- conversation_messages
- feedback
- human_review_queue
- evaluation_runs
- admin_settings

### Qdrant

Qdrant currently stores the main text retrieval collection.

Note:

- the code still keeps a legacy image-collection cleanup path so older image vectors can be cleaned up safely
- new ingestion and retrieval are now text-description based rather than CLIP-based

### Disk

Local disk stores:

- uploaded files under `backend/data/uploads/`
- extracted document images under `backend/data/uploads/_extracted/`
- Qdrant storage under `backend/data/qdrant/`
- evaluation datasets and reports under `backend/data/eval/`

## API

### Auth

- `POST /register`
- `POST /login`

JWT payload includes:

- `user_id`
- `username`
- `role`
- `is_admin`

### Health

- `GET /health`

### Retrieval / Chat

- `GET /image-models`
- `POST /ask`
- `POST /ask-with-file`
- `POST /chat`

Notes:

- `/ask` uses the shared admin-managed library.
- `/chat` uses the shared library and optionally an ad-hoc attached file.
- `/ask-with-file` is the direct ad-hoc route and does not require login.

### Conversations

- `GET /conversations`
- `POST /conversations`
- `DELETE /conversations/{conversation_id}`

### Feedback

- `POST /feedback`

### Admin Library Management

- `POST /upload`
- `GET /files`
- `GET /files/download/{file_id}`
- `DELETE /files/{file_id}`
- `POST /files/cleanup-vectors`

Notes:

- `GET /files` is readable by any authenticated user.
- upload, delete, download, cleanup require admin access.

### Evaluation

- `POST /evaluation/run`
- `GET /evaluation/latest`
- `POST /evaluation/import-chats`

### Review Queue

- `GET /review-queue`
- `POST /review-queue/{queue_id}/reviewed`

## Database Schema

### `users`

- `id`
- `username`
- `password_hash`
- `role`
- `is_admin`
- `created_at`

### `files`

- `id`
- `user_id`
- `uploaded_by`
- `doc_id`
- `filename`
- `file_path`
- `file_type`
- `content_hash`
- `is_global`
- `chunks_indexed`
- `uploaded_at`

Unique constraints/indexes:

- `doc_id` unique
- `content_hash` unique across the shared library when present

### `documents`

- `id`
- `doc_id`
- `domain`
- `description`
- `created_at`

### `conversations`

- `id`
- `user_id`
- `title`
- `created_at`
- `updated_at`

### `conversation_messages`

- `id`
- `conversation_id`
- `role`
- `content`
- `sources_json`
- `ragas_score`
- `judge_score`
- `created_at`
- `image_base64`

### `feedback`

- `id`
- `message_id`
- `chunks_used_json`
- `feedback_result`
- `comment`
- `knowledge_gap_flag`
- `created_at`

### `human_review_queue`

- `id`
- `message_id`
- `reason`
- `reviewed`
- `created_at`

### `evaluation_runs`

- `id`
- `admin_user_id`
- `dataset_path`
- `output_path`
- `samples`
- `total_rows`
- `max_rows`
- `truncated`
- `use_rerank`
- `summary_json`
- `report_json`
- `created_at`

## Configuration

See [.env.example](/c:/Ahex/KnowledgeHub/.env.example).

Important variables:

```env
DATABASE_URL=postgresql://knowledgehub:knowledgehub@postgres:5432/knowledgehub
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-this-admin-password
OPENAI_API_KEY=...
JWT_SECRET_KEY=change-this-secret-key-in-production
QDRANT_URL=http://qdrant:6333
COHERE_API_KEY=...
```

Primary model variables:

```env
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5-mini-2025-08-07
EVALUATION_MODEL=gpt-4.1-nano-2025-04-14
EMBEDDING_MODEL=text-embedding-3-large
```

Optional image-model routing:

```env
OPENROUTER_API_KEY=...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
```

## Run

Start the full stack:

```bash
docker compose up --build
```

Services:

- Frontend: `http://localhost:8502`
- Admin: `http://localhost:8502/admin`
- Backend docs: `http://localhost:8001/docs`
- PostgreSQL: `localhost:5432`

Docker setup:

- `postgres` uses Docker volume `postgres_data`
- `qdrant` stores data in `backend/data/qdrant`
- `backend` bind-mounts `backend/data` and `backend/app`
- `frontend` runs Vite in a Node container on port `8501`, exposed as `8502`

## Persistence

Persisted state lives in:

- PostgreSQL volume: `postgres_data`
- uploaded files: `backend/data/uploads/`
- extracted images: `backend/data/uploads/_extracted/`
- Qdrant storage: `backend/data/qdrant/`
- evaluation files: `backend/data/eval/`

`docker compose up --build` rebuilds containers but does not delete those persisted stores.

## Reset Options

### Full Reset

Removes users, chats, files, extracted images, and retrieval data:

```powershell
docker compose down
Remove-Item -Recurse -Force backend\data\uploads
Remove-Item -Recurse -Force backend\data\qdrant
docker volume rm knowledgehub_postgres_data
docker compose up --build
```

### Keep Users And Chats, Clear Library Data

Removes uploaded files, extracted images, Qdrant data, and file records only:

```powershell
docker compose down
Remove-Item -Recurse -Force backend\data\uploads\*
Remove-Item -Recurse -Force backend\data\qdrant\*
docker compose up -d postgres
docker compose exec postgres psql -U knowledgehub -d knowledgehub -c "TRUNCATE TABLE documents, files RESTART IDENTITY CASCADE;"
docker compose up --build
```

## Notes

- Frontend requests use `/api` as the backend base path.
- The frontend image-model dropdown is populated from `GET /image-models`.
- Duplicate prevention is by content hash, not filename.
- `Sync Library` removes stale vector entries whose `doc_id` no longer exists in PostgreSQL.
- Evaluation results are stored both on disk and in PostgreSQL.

## Evaluation

For the RAGAS-based evaluation workflow, see [EVALUATION.md](/c:/Ahex/KnowledgeHub/EVALUATION.md).
