# KnowledgeHub

Multimodal RAG chatbot with user authentication, per-user document isolation, persistent conversations, and Qdrant-backed retrieval.

## Overview

- Backend: FastAPI + LangChain
- Frontend: React
- Vector database: Qdrant
- Database: SQLite
- Text embeddings: OpenAI embeddings
- Image embeddings: CLIP (`clip-ViT-B-32`)
- Optional reranking: Cohere
- Auth: JWT

## Features

- User registration and login
- Per-user file isolation
- Persistent chat conversations stored in SQLite
- Text and image retrieval
- Ad-hoc chat file upload for a single message
- File deletion with vector cleanup
- Orphaned vector cleanup from the UI
- Duplicate upload prevention per user using file content hash

## Current Behavior

### Authentication

- Users register and log in with username and password
- JWT tokens are stored in browser `localStorage`
- All file and conversation operations are scoped to the authenticated user

### Uploads

- Supported files: `.txt`, `.md`, `.pdf`, `.doc`, `.docx`, `.csv`, `.png`, `.jpg`, `.jpeg`, `.webp`
- Uploaded files are stored in `backend/data/uploads/{user_id}/`
- Files are indexed into Qdrant with user ownership metadata
- The same user cannot upload the same file content twice
- Different users can upload the same file independently

### Chat

- `/chat` searches only the current user's indexed data
- Chat conversations are persisted in SQLite and restored on login
- New conversations are created automatically
- Ad-hoc chat file uploads are used only for that request and are not persistently indexed

### My Files

- Shows the authenticated user's tracked uploads
- Deleting a file removes:
  - the SQLite file record
  - the physical uploaded file
  - associated vectors in Qdrant
- `Clean Orphaned Vectors` removes vectors owned by the current user whose `doc_id` no longer exists in the `files` table

## API Endpoints

### Auth

- `POST /register`
- `POST /login`

### Health

- `GET /health`

### Files

- `POST /upload`
- `GET /files`
- `POST /files/cleanup-vectors`
- `DELETE /files/{file_id}`

### Conversations

- `GET /conversations`
- `POST /conversations`
- `DELETE /conversations/{conversation_id}`

### Retrieval / Chat

- `POST /ask`
- `POST /ask-with-file`
- `POST /chat`

All authenticated endpoints require:

```http
Authorization: Bearer <jwt_token>
```

## Stored Metadata

Indexed documents store metadata such as:

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

## Database Schema

### `users`

- `id`
- `username`
- `password_hash`
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

Unique rule:

- `(user_id, content_hash)` must be unique when `content_hash` is present

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

## Run

Start the stack:

```bash
docker compose up --build
```

Open:

- Frontend: `http://localhost:8502`
- Backend docs: `http://localhost:8001/docs`

## Persistence

Persistent application data lives in:

- SQLite DB: `backend/data/app.db`
- Uploaded files: `backend/data/uploads/`
- Qdrant storage: `backend/data/qdrant/`

## Fresh Reset

This removes all users, chats, files, and vectors:

```powershell
docker compose down
Remove-Item -Force backend\data\app.db
Remove-Item -Recurse -Force backend\data\uploads
Remove-Item -Recurse -Force backend\data\qdrant
docker compose up --build
```

## Startup Notes

- Backend initializes SQLite and Qdrant collections on startup
- The first startup after a reset can take longer because collections are recreated
- Wait for backend startup to complete before sending the first chat request

## Environment

Required in `.env`:

```env
OPENAI_API_KEY=...
JWT_SECRET_KEY=change-this-secret-key-in-production
```

Optional:

```env
COHERE_API_KEY=...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5-mini-2025-08-07
EMBEDDING_MODEL=text-embedding-3-large
QDRANT_URL=http://qdrant:6333
```

## Notes

- Frontend requests are sent to backend through `/api`
- Existing stale vectors from older versions can be cleaned from the `My Files` page
- Duplicate prevention is based on file content hash, not filename
