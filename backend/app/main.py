import base64
from functools import lru_cache
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
from time import perf_counter
from uuid import uuid4

import psycopg
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
import mimetypes
from fastapi.responses import StreamingResponse

from app.config import get_settings
from app.logging_config import setup_logging
from app.schemas import (
    AskRequest, AskResponse, AskWithFileResponse, ChatResponse, HealthResponse, UploadResponse,
    RegisterRequest, LoginRequest, AuthResponse, FileRecord, DeleteFileResponse,
    ConversationRecord, CreateConversationResponse, DeleteConversationResponse, CleanupVectorsResponse,
    ImageModelOption, RunEvaluationRequest, RunEvaluationResponse,
    ImportChatsToEvaluationRequest, ImportChatsToEvaluationResponse, AdminSettingsResponse,
    FeedbackRequest, FeedbackResponse, HumanReviewQueueItem, MarkReviewResponse,
)
from app.services.document_loader import SUPPORTED_EXTENSIONS, load_document
from app import database, auth

setup_logging()
logger = logging.getLogger(__name__)
settings = get_settings()
ALLOWED_ADHOC_IMAGE_MODELS = {str(item['value']) for item in settings.adhoc_image_model_options}

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


_TRIVIAL_CHAT_PATTERNS = {
    'hi', 'hello', 'hey', 'ok', 'okay', 'thanks', 'thank you', 'yo', 'sup',
}


def _normalize_eval_text(value: str) -> str:
    return re.sub(r'\s+', ' ', (value or '').strip()).lower()


def _is_valid_eval_chat_pair(question: str, answer: str) -> bool:
    q = _normalize_eval_text(question)
    a = _normalize_eval_text(answer)

    if not q or not a:
        return False
    if q in _TRIVIAL_CHAT_PATTERNS:
        return False
    if q == '[object object]' or a == '[object object]':
        return False
    if len(q) < 8 or len(a) < 16:
        return False
    if '(file:' in q:
        return False
    if q.startswith('error:') or a.startswith('error:'):
        return False
    if 'uploaded image' in q or 'uploaded image' in a:
        return False
    if 'what is this image' in q or 'what is in this image' in q:
        return False
    if 'i can provide a general definition' in a:
        return False
    return True


def _shared_library_owner_ids() -> list[str]:
    admin_ids = [str(user_id) for user_id in database.get_admin_user_ids()]
    # Keep the legacy marker so previously indexed shared files remain retrievable
    # until they are re-uploaded or cleaned up.
    owner_ids = list(dict.fromkeys(admin_ids + ['admin']))
    if not admin_ids:
        logger.warning('No admin users found while resolving shared library owner ids')
    return owner_ids


@lru_cache(maxsize=1)
def get_rag_service():
    from app.services.rag_service import RagService

    logger.info('Initializing RagService')
    return RagService()


@lru_cache(maxsize=1)
def get_query_graph_service():
    from app.services.query_graph import QueryGraphService

    logger.info('Initializing QueryGraphService')
    return QueryGraphService(get_rag_service())


@app.on_event('startup')
def warmup_on_startup() -> None:
    start = perf_counter()
    logger.info('Startup warmup started')
    database.init_db()
    if settings.admin_password:
        admin_hash = auth.hash_password(settings.admin_password)
        admin_user_id = database.upsert_admin_user(settings.admin_username, admin_hash)
        logger.info('Admin user ensured user_id=%s username=%s', admin_user_id, settings.admin_username)
    else:
        logger.warning('ADMIN_PASSWORD is not set; admin bootstrap skipped')
    get_rag_service()
    get_query_graph_service()
    elapsed_ms = int((perf_counter() - start) * 1000)
    logger.info('Startup warmup completed elapsed_ms=%s', elapsed_ms)


@app.get('/health', response_model=HealthResponse)
def health() -> HealthResponse:
    logger.info('Health check requested')
    return HealthResponse(status='ok')


@app.get('/image-models', response_model=list[ImageModelOption])
def get_image_models() -> list[ImageModelOption]:
    return [ImageModelOption(**item) for item in settings.adhoc_image_model_options]


def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Missing or invalid token')
    token = authorization.split(' ')[1]
    payload = auth.decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail='Invalid token')
    user_id = payload.get('user_id')
    username = payload.get('username')
    role = payload.get('role') or ('admin' if payload.get('is_admin', False) else 'user')
    is_admin = role == 'admin' or payload.get('is_admin', False)
    if not user_id or not username:
        raise HTTPException(status_code=401, detail='Invalid token payload')
    return {'user_id': user_id, 'username': username, 'role': role, 'is_admin': is_admin}


def get_admin_user(current_user: dict = Depends(get_current_user)):
    if current_user.get('role') != 'admin' and not current_user.get('is_admin'):
        raise HTTPException(status_code=403, detail='Admin access required')
    return current_user


@app.post('/register', response_model=AuthResponse)
def register(req: RegisterRequest) -> AuthResponse:
    existing = database.get_user_by_username(req.username)
    if existing:
        raise HTTPException(status_code=400, detail='Username already exists')
    password_hash = auth.hash_password(req.password)
    role = 'user'
    is_admin = False
    user_id = database.create_user(req.username, password_hash, is_admin, role=role)
    token = auth.create_access_token({'user_id': user_id, 'username': req.username, 'role': role, 'is_admin': is_admin})
    logger.info('User registered user_id=%s username=%s role=%s is_admin=%s', user_id, req.username, role, is_admin)
    return AuthResponse(access_token=token, user_id=user_id, username=req.username, role=role, is_admin=is_admin)


@app.post('/login', response_model=AuthResponse)
def login(req: LoginRequest) -> AuthResponse:
    user = database.get_user_by_username(req.username)
    if not user or not auth.verify_password(req.password, user['password_hash']):
        raise HTTPException(status_code=401, detail='Invalid credentials')
    role = user.get('role') or ('admin' if user.get('is_admin', False) else 'user')
    is_admin = role == 'admin' or user.get('is_admin', False)
    token = auth.create_access_token({'user_id': user['id'], 'username': user['username'], 'role': role, 'is_admin': is_admin})
    logger.info('User logged in user_id=%s username=%s role=%s is_admin=%s', user['id'], user['username'], role, is_admin)
    return AuthResponse(access_token=token, user_id=user['id'], username=user['username'], role=role, is_admin=is_admin)


@app.post('/upload', response_model=UploadResponse)
async def upload(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_admin_user),
) -> UploadResponse:
    start = perf_counter()
    if not file.filename:
        logger.warning('Upload rejected: missing filename')
        raise HTTPException(status_code=400, detail='Missing filename.')

    ext = Path(file.filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        logger.warning('Upload rejected: unsupported extension filename=%s ext=%s', file.filename, ext)
        raise HTTPException(
            status_code=400,
            detail=f'Unsupported file type. Supported: {sorted(SUPPORTED_EXTENSIONS)}',
        )

    payload = await file.read()
    logger.info('Upload received filename=%s bytes=%s user_id=%s', file.filename, len(payload), current_user['user_id'])
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(payload) > max_bytes:
        logger.warning('Upload rejected: file too large filename=%s bytes=%s max=%s', file.filename, len(payload), max_bytes)
        raise HTTPException(status_code=413, detail='File too large.')

    content_hash = database.sha256_bytes(payload)
    existing_file = database.get_file_by_content_hash(content_hash)
    if existing_file:
        logger.info(
            'Upload rejected: duplicate content filename=%s existing_doc_id=%s current_user_id=%s',
            file.filename,
            existing_file['doc_id'],
            current_user['user_id'],
        )
        raise HTTPException(
            status_code=409,
            detail=f"That file already exists in the shared library as '{existing_file['filename']}'. Delete it first if you want to re-upload.",
        )

    resolved_doc_id = uuid4().hex
    user_upload_dir = settings.upload_dir / str(current_user['user_id'])
    user_upload_dir.mkdir(parents=True, exist_ok=True)
    output_path = user_upload_dir / f"{resolved_doc_id}_{file.filename}"
    output_path.write_bytes(payload)
    logger.info('Upload saved path=%s', output_path)

    metadata = {
        'doc_id': resolved_doc_id,
        'source': file.filename,
        'owner_id': str(current_user['user_id']),
        'tenant_id': None,
        'tags': [],
        'uploaded_at': datetime.now(timezone.utc).isoformat(),
        'file_type': ext.lstrip('.'),
        'content_hash': content_hash,
        'page_no': None,
    }
    rag_service = get_rag_service()
    preloaded_text = None
    if ext not in {'.png', '.jpg', '.jpeg', '.webp'}:
        try:
            preloaded_text = load_document(output_path)
        except Exception as exc:
            logger.warning('Upload pre-load failed filename=%s error=%s', file.filename, exc)
    domain, description = rag_service.profile_document(output_path, preloaded_text=preloaded_text)
    metadata['domain'] = domain
    metadata['description'] = description
    indexed = rag_service.ingest_file(
        output_path,
        metadata=metadata,
        raw_bytes=payload,
        preloaded_text=preloaded_text,
    )
    try:
        database.create_file_record(
            user_id=current_user['user_id'],
            doc_id=resolved_doc_id,
            filename=file.filename,
            file_path=str(output_path),
            file_type=ext.lstrip('.'),
            chunks=indexed,
            content_hash=content_hash,
        )
        database.create_document_record(resolved_doc_id, domain, description or f'Indexed document: {file.filename}')
    except psycopg.IntegrityError:
        rag_service.delete_by_doc_id(resolved_doc_id)
        if output_path.exists():
            output_path.unlink()
        existing_file = database.get_file_by_content_hash(content_hash)
        raise HTTPException(
            status_code=409,
            detail=f"That file already exists in the shared library as '{existing_file['filename'] if existing_file else file.filename}'. Delete it first if you want to re-upload.",
        )
    elapsed_ms = int((perf_counter() - start) * 1000)
    logger.info('Upload indexed filename=%s chunks=%s elapsed_ms=%s', file.filename, indexed, elapsed_ms)
    return UploadResponse(filename=file.filename, chunks_indexed=indexed, doc_id=resolved_doc_id)


@app.post('/ask', response_model=AskResponse)
def ask(req: AskRequest, current_user: dict = Depends(get_current_user)) -> AskResponse:
    start = perf_counter()
    logger.info('Ask requested question_len=%s top_k=%s history_turns=%s user_id=%s', len(req.question), req.top_k, len(req.history), current_user['user_id'])
    filters = req.filters.model_dump(exclude_none=True) if req.filters else {}
    result = get_query_graph_service().run(
        req.question,
        top_k=req.top_k,
        history=req.history,
        filters=filters,
        owner_ids=_shared_library_owner_ids(),
    )
    elapsed_ms = int((perf_counter() - start) * 1000)
    logger.info('Ask completed sources=%s elapsed_ms=%s', len(result.get('sources', [])), elapsed_ms)
    return AskResponse(answer=result.get('answer', ''), sources=result.get('sources', []))


@app.post('/ask-with-file', response_model=AskWithFileResponse)
async def ask_with_file(
    question: str = Form(...),
    file: UploadFile = File(...),
    image_model: str | None = Form(default=None),
    top_k: int | None = Form(default=None),
    history_json: str | None = Form(default=None),
    filters_json: str | None = Form(default=None),
) -> AskWithFileResponse:
    start = perf_counter()
    if not file.filename:
        raise HTTPException(status_code=400, detail='Missing filename.')

    ext = Path(file.filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f'Unsupported file type. Supported: {sorted(SUPPORTED_EXTENSIONS)}',
        )
    if ext in {'.png', '.jpg', '.jpeg', '.webp'} and image_model and image_model not in ALLOWED_ADHOC_IMAGE_MODELS:
        raise HTTPException(status_code=400, detail=f'Unsupported image model. Allowed: {sorted(ALLOWED_ADHOC_IMAGE_MODELS)}')

    payload = await file.read()
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(payload) > max_bytes:
        raise HTTPException(status_code=413, detail='File too large.')

    history = []
    filters = None
    if history_json:
        try:
            history = json.loads(history_json)
        except json.JSONDecodeError:
            history = []
    if filters_json:
        try:
            filters = json.loads(filters_json)
        except json.JSONDecodeError:
            filters = None

    temp_name = f'adhoc_{file.filename}'
    output_path = settings.upload_dir / temp_name
    output_path.write_bytes(payload)
    logger.info('Ask-with-file uploaded temp file path=%s question_len=%s history_turns=%s', output_path, len(question), len(history))

    answer, sources = get_rag_service().ask_with_file(
        question=question,
        file_path=output_path,
        image_model=image_model,
        top_k=top_k,
        history=history,
        filters=filters,
    )
    elapsed_ms = int((perf_counter() - start) * 1000)
    logger.info('Ask-with-file completed sources=%s elapsed_ms=%s', len(sources), elapsed_ms)
    return AskWithFileResponse(answer=answer, sources=sources)


@app.post('/chat', response_model=ChatResponse)
async def chat(
    question: str = Form(...),
    conversation_id: str | None = Form(default=None),
    image_model: str | None = Form(default=None),
    top_k: int | None = Form(default=None),
    history_json: str | None = Form(default=None),
    filters_json: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    current_user: dict = Depends(get_current_user),
) -> ChatResponse:
    start = perf_counter()
    history = []
    filters = {}
    if history_json:
        try:
            history = json.loads(history_json)
        except json.JSONDecodeError:
            history = []
    if filters_json:
        try:
            filters = json.loads(filters_json)
        except json.JSONDecodeError:
            filters = {}

    file_path = None
    image_base64: str | None = None
    if file is not None and file.filename:
        ext = Path(file.filename).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f'Unsupported file type. Supported: {sorted(SUPPORTED_EXTENSIONS)}',
            )
        if ext in {'.png', '.jpg', '.jpeg', '.webp'} and image_model and image_model not in ALLOWED_ADHOC_IMAGE_MODELS:
            raise HTTPException(status_code=400, detail=f'Unsupported image model. Allowed: {sorted(ALLOWED_ADHOC_IMAGE_MODELS)}')

        payload = await file.read()
        max_bytes = settings.max_upload_size_mb * 1024 * 1024
        if len(payload) > max_bytes:
            raise HTTPException(status_code=413, detail='File too large.')
        file_path = settings.upload_dir / f'chat_{file.filename}'
        file_path.write_bytes(payload)
        logger.info('Chat received ad-hoc file path=%s bytes=%s', file_path, len(payload))
        if ext in {'.png', '.jpg', '.jpeg', '.webp'}:
            image_base64 = base64.b64encode(payload).decode('utf-8')

    if conversation_id:
        conversation = database.get_conversation(conversation_id, current_user['user_id'])
        if not conversation:
            raise HTTPException(status_code=404, detail='Conversation not found')
    else:
        conversation = database.create_conversation(current_user['user_id'])
        conversation_id = conversation['id']

    if file_path is not None:
        answer, sources = get_rag_service().chat(
            question=question,
            image_model=image_model,
            top_k=top_k,
            history=history,
            file_path=file_path,
            filters=filters,
            owner_ids=_shared_library_owner_ids(),
        )
        evaluation_scores = {}
        review_flag = False
        review_reason = None
    else:
        result = get_query_graph_service().run(
            question,
            top_k=top_k,
            history=history,
            filters=filters,
            owner_ids=_shared_library_owner_ids(),
        )
        answer = result.get('answer', '')
        sources = result.get('sources', [])
        evaluation_scores = result.get('evaluation_scores', {}) or {}
        review_flag = bool(result.get('review_flag'))
        review_reason = result.get('review_reason')

    user_message_content = question
    message_count = database.count_conversation_messages(conversation_id)
    if message_count == 0:
        database.update_conversation_title(conversation_id, current_user['user_id'], question[:30])
    database.append_conversation_message(conversation_id, 'user', user_message_content, [], image_base64)
    assistant_message_id = database.append_conversation_message(
        conversation_id,
        'assistant',
        answer,
        sources,
        ragas_score=evaluation_scores.get('overall'),
        judge_score=evaluation_scores.get('judge_score'),
    )
    if review_flag:
        database.enqueue_human_review(assistant_message_id, str(review_reason or 'Flagged by query graph evaluation'))

    elapsed_ms = int((perf_counter() - start) * 1000)
    logger.info('Chat completed sources=%s elapsed_ms=%s', len(sources), elapsed_ms)
    return ChatResponse(
        answer=answer,
        sources=sources,
        conversation_id=conversation_id,
        assistant_message_id=assistant_message_id,
        evaluation_scores=evaluation_scores,
    )


@app.get('/conversations', response_model=list[ConversationRecord])
def get_conversations(current_user: dict = Depends(get_current_user)) -> list[ConversationRecord]:
    conversations = database.get_user_conversations(current_user['user_id'])
    return [ConversationRecord(**conversation) for conversation in conversations]


@app.post('/conversations', response_model=CreateConversationResponse)
def create_conversation(current_user: dict = Depends(get_current_user)) -> CreateConversationResponse:
    conversation = database.create_conversation(current_user['user_id'])
    return CreateConversationResponse(conversation=ConversationRecord(**conversation))


@app.delete('/conversations/{conversation_id}', response_model=DeleteConversationResponse)
def delete_conversation(conversation_id: str, current_user: dict = Depends(get_current_user)) -> DeleteConversationResponse:
    conversation = database.delete_conversation(conversation_id, current_user['user_id'])
    if not conversation:
        raise HTTPException(status_code=404, detail='Conversation not found')
    return DeleteConversationResponse(success=True, message='Conversation deleted successfully')

@app.get('/files', response_model=list[FileRecord])
def get_files(current_user: dict = Depends(get_current_user)) -> list[FileRecord]:
    files = database.get_all_admin_files()
    records = []
    for f in files:
        p = Path(f['file_path'])
        records.append(FileRecord(
            **{k: v for k, v in f.items() if k not in {'file_path', 'is_global'}},
            download_url=f'/files/download/{f["id"]}',
            is_global=bool(f.get('is_global', True)),
            file_size=p.stat().st_size if p.exists() else None,
        ))
    return records


@app.get('/files/download/{file_id}')
def download_file(file_id: int, current_user: dict = Depends(get_admin_user)) -> StreamingResponse:
    file_record = database.get_admin_file_by_id(file_id)
    if not file_record:
        raise HTTPException(status_code=404, detail='File not found')
    file_path = Path(file_record['file_path']).resolve()
    upload_dir = settings.upload_dir.resolve()
    if not file_path.is_relative_to(upload_dir):
        raise HTTPException(status_code=400, detail='Invalid file path')
    if not file_path.exists():
        raise HTTPException(status_code=404, detail='File not found on disk')
    mime_type, _ = mimetypes.guess_type(file_record['filename'])
    mime_type = mime_type or 'application/octet-stream'
    filename = file_record['filename']
    return StreamingResponse(
        content=file_path.open('rb'),
        media_type=mime_type,
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@app.post('/files/cleanup-vectors', response_model=CleanupVectorsResponse)
def cleanup_file_vectors(current_user: dict = Depends(get_admin_user)) -> CleanupVectorsResponse:
    files = database.get_all_admin_files()
    valid_doc_ids = {str(file['doc_id']) for file in files}
    text_removed: list[str] = []
    image_removed: list[str] = []
    for admin_owner_id in _shared_library_owner_ids():
        result = get_rag_service().cleanup_user_vectors(admin_owner_id, valid_doc_ids)
        text_removed.extend(result.get('text_doc_ids_removed', []))
        image_removed.extend(result.get('image_doc_ids_removed', []))
    total_removed = len(set(text_removed + image_removed))
    return CleanupVectorsResponse(
        success=True,
        message=f'Removed {total_removed} stale library entries.',
        text_doc_ids_removed=sorted(set(text_removed)),
        image_doc_ids_removed=sorted(set(image_removed)),
    )


@app.post('/evaluation/run', response_model=RunEvaluationResponse)
def run_evaluation(req: RunEvaluationRequest, current_user: dict = Depends(get_admin_user)) -> RunEvaluationResponse:
    base_data_dir = settings.upload_dir.parent.resolve()
    dataset_path = (base_data_dir / 'eval' / 'sample_ragas_eval.jsonl') if not req.dataset_path else Path(req.dataset_path)
    if not dataset_path.is_absolute():
        dataset_path = (base_data_dir / dataset_path).resolve()
    else:
        dataset_path = dataset_path.resolve()

    try:
        dataset_path.relative_to(base_data_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail='dataset_path must be inside backend/data') from exc

    if not dataset_path.exists():
        raise HTTPException(status_code=404, detail=f'Dataset not found: {dataset_path}')

    output_path = (base_data_dir / 'eval' / 'latest_ragas_report.json').resolve()
    try:
        from app.evaluate_ragas import run_ragas_evaluation
        report = run_ragas_evaluation(
            dataset_path,
            output_path=output_path,
            top_k=req.top_k,
            use_rerank=req.use_rerank,
        )
    except Exception as exc:
        logger.exception('Evaluation failed dataset=%s', dataset_path)
        raise HTTPException(status_code=500, detail=f'Evaluation failed: {exc}') from exc

    database.create_evaluation_run(current_user['user_id'], report)
    return RunEvaluationResponse(success=True, **report)


@app.get('/evaluation/latest', response_model=RunEvaluationResponse)
def get_latest_evaluation(current_user: dict = Depends(get_admin_user)) -> RunEvaluationResponse:
    db_run = database.get_latest_evaluation_run(current_user['user_id'])
    if db_run and isinstance(db_run.get('report'), dict):
        report = dict(db_run['report'])
        report['created_at'] = db_run.get('created_at')
        return RunEvaluationResponse(success=True, **report)

    output_path = (settings.upload_dir.parent.resolve() / 'eval' / 'latest_ragas_report.json').resolve()
    try:
        from app.evaluate_ragas import load_saved_ragas_report
        report = load_saved_ragas_report(output_path)
    except Exception as exc:
        logger.exception('Failed to load latest evaluation report')
        raise HTTPException(status_code=500, detail=f'Failed to load latest evaluation report: {exc}') from exc

    if not report:
        raise HTTPException(status_code=404, detail='No saved evaluation report found')
    database.create_evaluation_run(current_user['user_id'], report)
    return RunEvaluationResponse(success=True, **report)


@app.post('/evaluation/import-chats', response_model=ImportChatsToEvaluationResponse)
def import_chats_to_evaluation(
    req: ImportChatsToEvaluationRequest,
    current_user: dict = Depends(get_admin_user),
) -> ImportChatsToEvaluationResponse:
    base_data_dir = settings.upload_dir.parent.resolve()
    dataset_path = (base_data_dir / 'eval' / 'sample_ragas_eval.jsonl') if not req.dataset_path else Path(req.dataset_path)
    if not dataset_path.is_absolute():
        dataset_path = (base_data_dir / dataset_path).resolve()
    else:
        dataset_path = dataset_path.resolve()

    try:
        dataset_path.relative_to(base_data_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail='dataset_path must be inside backend/data') from exc

    limit = max(1, min(req.limit, settings.evaluation_max_rows))
    pairs = database.get_recent_chat_pairs(limit=limit)

    existing_ids: set[str] = set()
    if dataset_path.exists():
        for line in dataset_path.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                if item.get('id'):
                    existing_ids.add(str(item['id']))
            except json.JSONDecodeError:
                continue

    rows_to_add: list[str] = []
    skipped_existing = 0
    skipped_invalid = 0
    seen_questions: set[str] = set()
    for pair in pairs:
        row_id = f"chat-{pair['assistant_message_id']}"
        if row_id in existing_ids:
            skipped_existing += 1
            continue
        normalized_question = _normalize_eval_text(pair['question'])
        if normalized_question in seen_questions:
            skipped_invalid += 1
            continue
        if not _is_valid_eval_chat_pair(pair['question'], pair['answer']):
            skipped_invalid += 1
            continue
        seen_questions.add(normalized_question)
        payload = {
            'id': row_id,
            'question': pair['question'],
            'ground_truth': pair['answer'],
            'source': 'stored_chat',
            'username': pair['username'],
            'conversation_id': pair['conversation_id'],
            'created_at': pair['created_at'],
        }
        rows_to_add.append(json.dumps(payload, ensure_ascii=True))

    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    existing_text = dataset_path.read_text(encoding='utf-8') if dataset_path.exists() else ''
    merged_lines = [line for line in existing_text.splitlines() if line.strip()]
    merged_lines.extend(rows_to_add)
    dataset_path.write_text('\n'.join(merged_lines) + ('\n' if merged_lines else ''), encoding='utf-8')

    return ImportChatsToEvaluationResponse(
        success=True,
        dataset_path=str(dataset_path),
        imported=len(rows_to_add),
        total_pairs_seen=len(pairs),
        skipped_existing=skipped_existing,
        skipped_invalid=skipped_invalid,
    )


@app.get('/admin/settings', response_model=AdminSettingsResponse)
def get_admin_settings(current_user: dict = Depends(get_admin_user)) -> AdminSettingsResponse:
    settings_data = database.get_admin_settings()
    return AdminSettingsResponse(**settings_data)


@app.post('/admin/settings', response_model=AdminSettingsResponse)
def save_admin_settings(req: AdminSettingsResponse, current_user: dict = Depends(get_admin_user)) -> AdminSettingsResponse:
    database.save_admin_settings(req.model_dump())
    return req


@app.post('/feedback', response_model=FeedbackResponse)
def submit_feedback(req: FeedbackRequest, current_user: dict = Depends(get_current_user)) -> FeedbackResponse:
    feedback_id = database.create_feedback(
        message_id=req.message_id,
        feedback_result=req.feedback_result,
        chunks_used=req.chunks_used,
        comment=req.comment,
        knowledge_gap_flag=req.knowledge_gap_flag,
    )
    if not req.feedback_result or req.knowledge_gap_flag:
        database.enqueue_human_review(req.message_id, 'Negative user feedback')
    return FeedbackResponse(success=True, feedback_id=feedback_id)


@app.get('/review-queue', response_model=list[HumanReviewQueueItem])
def get_review_queue(current_user: dict = Depends(get_admin_user)) -> list[HumanReviewQueueItem]:
    items = database.list_human_review_queue()
    return [HumanReviewQueueItem(**item) for item in items]


@app.post('/review-queue/{queue_id}/reviewed', response_model=MarkReviewResponse)
def mark_reviewed(queue_id: int, current_user: dict = Depends(get_admin_user)) -> MarkReviewResponse:
    database.mark_human_reviewed(queue_id)
    return MarkReviewResponse(success=True)


@app.delete('/files/{file_id}', response_model=DeleteFileResponse)
def delete_file(file_id: int, current_user: dict = Depends(get_admin_user)) -> DeleteFileResponse:
    file_record = database.delete_admin_file_record(file_id)
    if not file_record:
        raise HTTPException(status_code=404, detail='File not found')
    
    # Delete from vector store
    get_rag_service().delete_by_doc_id(file_record['doc_id'])
    
    # Delete physical file
    try:
        file_path = Path(file_record['file_path'])
        if file_path.exists():
            file_path.unlink()
            logger.info('Physical file deleted path=%s', file_path)
    except Exception as exc:
        logger.warning('Failed to delete physical file path=%s error=%s', file_record['file_path'], exc)
    
    logger.info('File deleted file_id=%s doc_id=%s user_id=%s', file_id, file_record['doc_id'], current_user['user_id'])
    return DeleteFileResponse(success=True, message='File removed from the shared library successfully')
