# KnowledgeHub

KnowledgeHub is a multimodal RAG application with:

- FastAPI backend
- React frontend
- PostgreSQL for app data
- Qdrant for retrieval data
- JWT authentication
- Admin-managed shared document library
- Persistent per-user conversations

## Current Flow

### Roles

- Regular users can register, log in, chat, attach ad-hoc files to a single message, and access only their own conversation history.
- Admin users can do everything regular users can, plus open `/admin` to upload, list, delete, and sync shared library files.

### Shared Library Model

- Indexed knowledge-base files are admin-only.
- Files uploaded from the admin panel are stored in PostgreSQL and indexed into Qdrant with `owner_id='admin'`.
- `/ask` and `/chat` retrieve only from that shared admin-owned corpus.
- Regular users do not have personal indexed file libraries.

### Ad-hoc Files

- `/chat` and `/ask-with-file` accept temporary file attachments.
- Those files are used only for that request.
- They are not persisted in PostgreSQL as library files.
- They are not added to the shared Qdrant corpus.
- Image attachments can optionally use a selectable `image_model`.

### Conversations

- Conversations are stored in PostgreSQL.
- They are scoped by `user_id`.
- Refreshing the browser or logging in again restores prior chats for that user.

### Admin Bootstrap

- Admin access is not created by username convention anymore.
- On backend startup, if `ADMIN_PASSWORD` is set, the app creates or updates the configured admin user from:
  - `ADMIN_USERNAME`
  - `ADMIN_PASSWORD`
- Registration always creates a non-admin user.

## Architecture

### Backend

- FastAPI app: [backend/app/main.py](/c:/Ahex/KnowledgeHub/backend/app/main.py)
- Config: [backend/app/config.py](/c:/Ahex/KnowledgeHub/backend/app/config.py)
- Database layer: [backend/app/database.py](/c:/Ahex/KnowledgeHub/backend/app/database.py)
- RAG service: [backend/app/services/rag_service.py](/c:/Ahex/KnowledgeHub/backend/app/services/rag_service.py)

### Frontend

- User chat app: [frontend/src/App.jsx](/c:/Ahex/KnowledgeHub/frontend/src/App.jsx)
- Admin panel: [frontend/src/Admin.jsx](/c:/Ahex/KnowledgeHub/frontend/src/Admin.jsx)

### Storage

- PostgreSQL stores:
  - users
  - files
  - conversations
  - conversation messages
- Qdrant stores:
  - indexed text retrieval points
  - indexed image retrieval points
- Local disk stores:
  - uploaded files under `backend/data/uploads/`
  - Qdrant data under `backend/data/qdrant/`

## Features

- Username/password authentication
- JWT-based API auth
- Admin panel at `/admin`
- Admin-only shared file uploads
- Duplicate upload prevention by `content_hash`
- Multi-file upload from admin UI
- Shared retrieval across all users
- Persistent conversations
- Ad-hoc file chat
- Ad-hoc image model selection
- Source attribution in answers
- Library sync for stale/orphaned Qdrant data

## API

### Auth

- `POST /register`
- `POST /login`

### Health

- `GET /health`

### Image Models

- `GET /image-models`

Returns the backend allowlist used by the frontend image-model dropdown.

### Retrieval / Chat

- `POST /ask`
- `POST /ask-with-file`
- `POST /chat`

Notes:

- `/ask` uses the shared admin-indexed corpus.
- `/chat` uses the shared admin-indexed corpus plus an optional ad-hoc attached file for that request.
- `/ask-with-file` is a direct ad-hoc retrieval route and does not require login.

### Conversations

- `GET /conversations`
- `POST /conversations`
- `DELETE /conversations/{conversation_id}`

### Admin File Management

- `POST /upload`
- `GET /files`
- `POST /files/cleanup-vectors`
- `DELETE /files/{file_id}`

These routes require an admin JWT.

## Database Schema

### `users`

- `id`
- `username`
- `password_hash`
- `is_admin`
- `created_at`

### `files`

- `id`
- `user_id`
- `doc_id`
- `filename`
- `file_path`
- `file_type`
- `content_hash`
- `chunks_indexed`
- `uploaded_at`

Unique index:

- `(user_id, content_hash)` where `content_hash IS NOT NULL`

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
- `created_at`

## Retrieval Metadata

Indexed payloads may include:

- `doc_id`
- `chunk_id`
- `source`
- `uploaded_at`
- `file_type`
- `content_hash`
- `tags`
- `owner_id`
- `tenant_id`
- `page_no`

Text retrieval payloads are stored with metadata nested under `metadata.*`.
Image retrieval payloads use top-level payload keys.

## Supported File Types

- Text/docs: `.txt`, `.md`, `.pdf`, `.doc`, `.docx`, `.csv`
- Images: `.png`, `.jpg`, `.jpeg`, `.webp`

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
```

Optional model configuration:

```env
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5-mini-2025-08-07
EMBEDDING_MODEL=text-embedding-3-large
OPENROUTER_API_KEY=...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
COHERE_API_KEY=...
COHERE_RERANK_MODEL=rerank-v3.5
CLIP_MODEL_NAME=clip-ViT-B-32
```

## Run

Start everything:

```bash
docker compose up --build
```

Services:

- Frontend: `http://localhost:8502`
- Admin: `http://localhost:8502/admin`
- Backend API/docs: `http://localhost:8001/docs`
- PostgreSQL: `localhost:5432`

Current Docker setup:

- `postgres` uses Docker volume `postgres_data`
- `qdrant` stores data in `backend/data/qdrant`
- `backend` bind-mounts `backend/data` and `backend/app`
- `frontend` runs Vite in a Node container on port `8502`

## Persistence

Persisted state lives in:

- PostgreSQL volume: `postgres_data`
- Uploaded files: `backend/data/uploads/`
- Qdrant storage: `backend/data/qdrant/`

`docker compose up --build` rebuilds containers but does not delete those persisted stores.

## Reset Options

### Full Reset

Removes users, conversations, uploaded files, and Qdrant data:

```powershell
docker compose down
Remove-Item -Recurse -Force backend\data\uploads
Remove-Item -Recurse -Force backend\data\qdrant
docker volume rm knowledgehub_postgres_data
docker compose up --build
```

### Keep Users And Chats, Clear Library Data

Removes uploaded files, Qdrant data, and file records only:

```powershell
docker compose down
Remove-Item -Recurse -Force backend\data\uploads\*
Remove-Item -Recurse -Force backend\data\qdrant\*
docker compose up -d postgres
docker compose exec postgres psql -U knowledgehub -d knowledgehub -c "TRUNCATE TABLE files RESTART IDENTITY;"
docker compose up --build
```

## Migration

### SQLite to PostgreSQL

If you have legacy SQLite data in `backend/data/app.db`, use:

```bash
docker exec -it rag_backend python -m app.migrate_sqlite_to_postgres
```

This migrates:

- users
- files
- conversations
- conversation messages

### Add `is_admin` to Existing PostgreSQL DB

If needed:

```bash
docker exec -it rag_backend python -m app.migrate_add_admin
```

Note: current startup already runs `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for `is_admin` and `content_hash`, so this helper is mainly for older/manual environments.

## Notes

- Frontend requests use `/api` as the backend base path.
- The frontend image-model dropdown is populated from `GET /image-models`.
- The backend is the source of truth for allowed ad-hoc image models.
- Duplicate prevention is by content hash, not filename.
- `Sync Library` removes stale Qdrant entries whose `doc_id` no longer exists in PostgreSQL.

## Evaluation

For a RAGAS-based evaluation workflow, see [EVALUATION.md].
