from functools import lru_cache
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import psycopg
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Depends, Header
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.logging_config import setup_logging
from app.schemas import (
    AskRequest, AskResponse, AskWithFileResponse, ChatResponse, HealthResponse, UploadResponse,
    RegisterRequest, LoginRequest, AuthResponse, FileRecord, DeleteFileResponse,
    ConversationRecord, CreateConversationResponse, DeleteConversationResponse, CleanupVectorsResponse,
    ImageModelOption,
)
from app.services.document_loader import SUPPORTED_EXTENSIONS
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


@lru_cache(maxsize=1)
def get_rag_service():
    from app.services.rag_service import RagService

    logger.info('Initializing RagService')
    return RagService()


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
    is_admin = payload.get('is_admin', False)
    if not user_id or not username:
        raise HTTPException(status_code=401, detail='Invalid token payload')
    return {'user_id': user_id, 'username': username, 'is_admin': is_admin}


def get_admin_user(current_user: dict = Depends(get_current_user)):
    if not current_user.get('is_admin'):
        raise HTTPException(status_code=403, detail='Admin access required')
    return current_user


@app.post('/register', response_model=AuthResponse)
def register(req: RegisterRequest) -> AuthResponse:
    existing = database.get_user_by_username(req.username)
    if existing:
        raise HTTPException(status_code=400, detail='Username already exists')
    password_hash = auth.hash_password(req.password)
    user_id = database.create_user(req.username, password_hash, False)
    is_admin = False
    token = auth.create_access_token({'user_id': user_id, 'username': req.username, 'is_admin': is_admin})
    logger.info('User registered user_id=%s username=%s is_admin=%s', user_id, req.username, is_admin)
    return AuthResponse(access_token=token, user_id=user_id, username=req.username, is_admin=is_admin)


@app.post('/login', response_model=AuthResponse)
def login(req: LoginRequest) -> AuthResponse:
    user = database.get_user_by_username(req.username)
    if not user or not auth.verify_password(req.password, user['password_hash']):
        raise HTTPException(status_code=401, detail='Invalid credentials')
    is_admin = user.get('is_admin', False)
    token = auth.create_access_token({'user_id': user['id'], 'username': user['username'], 'is_admin': is_admin})
    logger.info('User logged in user_id=%s username=%s is_admin=%s', user['id'], user['username'], is_admin)
    return AuthResponse(access_token=token, user_id=user['id'], username=user['username'], is_admin=is_admin)


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
    existing_file = database.get_user_file_by_content_hash(current_user['user_id'], content_hash)
    if existing_file:
        logger.info(
            'Upload rejected: duplicate content filename=%s existing_doc_id=%s user_id=%s',
            file.filename,
            existing_file['doc_id'],
            current_user['user_id'],
        )
        raise HTTPException(
            status_code=409,
            detail=f"You already uploaded this file as '{existing_file['filename']}'. Delete it first if you want to re-upload.",
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
        'owner_id': 'admin',
        'tenant_id': None,
        'tags': [],
        'uploaded_at': datetime.now(timezone.utc).isoformat(),
        'file_type': ext.lstrip('.'),
        'content_hash': content_hash,
        'page_no': None,
    }
    indexed = get_rag_service().ingest_file(output_path, metadata=metadata, raw_bytes=payload)
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
    except psycopg.IntegrityError:
        get_rag_service().delete_by_doc_id(resolved_doc_id)
        if output_path.exists():
            output_path.unlink()
        existing_file = database.get_user_file_by_content_hash(current_user['user_id'], content_hash)
        raise HTTPException(
            status_code=409,
            detail=f"You already uploaded this file as '{existing_file['filename'] if existing_file else file.filename}'. Delete it first if you want to re-upload.",
        )
    elapsed_ms = int((perf_counter() - start) * 1000)
    logger.info('Upload indexed filename=%s chunks=%s elapsed_ms=%s', file.filename, indexed, elapsed_ms)
    return UploadResponse(filename=file.filename, chunks_indexed=indexed, doc_id=resolved_doc_id)


@app.post('/ask', response_model=AskResponse)
def ask(req: AskRequest, current_user: dict = Depends(get_current_user)) -> AskResponse:
    start = perf_counter()
    logger.info('Ask requested question_len=%s top_k=%s history_turns=%s user_id=%s', len(req.question), req.top_k, len(req.history), current_user['user_id'])
    filters = req.filters.model_dump(exclude_none=True) if req.filters else {}
    answer, sources = get_rag_service().ask(
        req.question,
        top_k=req.top_k,
        history=req.history,
        filters=filters,
        owner_ids=['admin'],
    )
    elapsed_ms = int((perf_counter() - start) * 1000)
    logger.info('Ask completed sources=%s elapsed_ms=%s', len(sources), elapsed_ms)
    return AskResponse(answer=answer, sources=sources)


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
    if file is not None and file.filename:
        ext = Path(file.filename).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f'Unsupported file type. Supported: {sorted(SUPPORTED_EXTENSIONS)}',
            )
        if ext in {'.png', '.jpg', '.jpeg', '.webp'} and image_model and image_model not in ALLOWED_ADHOC_IMAGE_MODELS:
            raise HTTPException(status_code=400, detail=f'Unsupported image model. Allowed: {sorted(ALLOWED_ADHOC_IMAGE_MODELS)}')
        
        print("image_model: ", image_model)
        payload = await file.read()
        max_bytes = settings.max_upload_size_mb * 1024 * 1024
        if len(payload) > max_bytes:
            raise HTTPException(status_code=413, detail='File too large.')
        file_path = settings.upload_dir / f'chat_{file.filename}'
        file_path.write_bytes(payload)
        logger.info('Chat received ad-hoc file path=%s bytes=%s', file_path, len(payload))

    if conversation_id:
        conversation = database.get_conversation(conversation_id, current_user['user_id'])
        if not conversation:
            raise HTTPException(status_code=404, detail='Conversation not found')
    else:
        conversation = database.create_conversation(current_user['user_id'])
        conversation_id = conversation['id']

    answer, sources = get_rag_service().chat(
        question=question,
        image_model=image_model,
        top_k=top_k,
        history=history,
        file_path=file_path,
        filters=filters,
        owner_ids=['admin'],
    )

    user_message_content = f'{question} (file: {file.filename})' if file is not None and file.filename else question
    message_count = database.count_conversation_messages(conversation_id)
    if message_count == 0:
        database.update_conversation_title(conversation_id, current_user['user_id'], question[:30])
    database.append_conversation_message(conversation_id, 'user', user_message_content, [])
    database.append_conversation_message(conversation_id, 'assistant', answer, sources)

    elapsed_ms = int((perf_counter() - start) * 1000)
    logger.info('Chat completed sources=%s elapsed_ms=%s', len(sources), elapsed_ms)
    return ChatResponse(answer=answer, sources=sources, conversation_id=conversation_id)


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
def get_files(current_user: dict = Depends(get_admin_user)) -> list[FileRecord]:
    files = database.get_all_admin_files()
    records = []
    for f in files:
        p = Path(f['file_path'])
        records.append(FileRecord(**f, file_size=p.stat().st_size if p.exists() else None))
    return records


@app.post('/files/cleanup-vectors', response_model=CleanupVectorsResponse)
def cleanup_file_vectors(current_user: dict = Depends(get_admin_user)) -> CleanupVectorsResponse:
    files = database.get_all_admin_files()
    valid_doc_ids = {str(file['doc_id']) for file in files}
    result = get_rag_service().cleanup_user_vectors('admin', valid_doc_ids)
    return CleanupVectorsResponse(**result)


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
