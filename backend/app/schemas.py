from pydantic import BaseModel, Field


class ChatTurn(BaseModel):
    role: str = Field(pattern='^(user|assistant)$')
    content: str = Field(min_length=1)


class StoredChatTurn(ChatTurn):
    id: int
    sources: list[dict] = []
    created_at: str
    image_base64: str | None = None


class RetrievalFilters(BaseModel):
    owner_id: str | None = None
    tenant_id: str | None = None
    file_type: str | None = None
    source: str | None = None
    doc_id: str | None = None
    tags: list[str] | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=2)
    top_k: int | None = None
    history: list[ChatTurn] = []
    filters: RetrievalFilters | None = None


class AskResponse(BaseModel):
    answer: str
    sources: list[dict]


class AskWithFileResponse(BaseModel):
    answer: str
    sources: list[dict]


class ChatResponse(BaseModel):
    answer: str
    sources: list[dict]
    conversation_id: str


class UploadResponse(BaseModel):
    filename: str
    chunks_indexed: int
    doc_id: str | None = None


class HealthResponse(BaseModel):
    status: str


class ImageModelOption(BaseModel):
    value: str
    label: str


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3)
    password: str = Field(min_length=6)


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = 'bearer'
    user_id: int
    username: str
    is_admin: bool = False


class FileRecord(BaseModel):
    id: int
    doc_id: str
    filename: str
    file_type: str
    chunks_indexed: int
    uploaded_at: str
    download_url: str | None = None
    file_size: int | None = None


class DeleteFileResponse(BaseModel):
    success: bool
    message: str


class CleanupVectorsResponse(BaseModel):
    success: bool
    message: str
    text_doc_ids_removed: list[str] = []
    image_doc_ids_removed: list[str] = []


class RunEvaluationRequest(BaseModel):
    dataset_path: str | None = None
    top_k: int | None = None
    use_rerank: bool = True


class ImportChatsToEvaluationRequest(BaseModel):
    dataset_path: str | None = None
    limit: int = 100


class ImportChatsToEvaluationResponse(BaseModel):
    success: bool
    dataset_path: str
    imported: int
    total_pairs_seen: int
    skipped_existing: int
    skipped_invalid: int


class RunEvaluationResponse(BaseModel):
    success: bool
    dataset_path: str
    output_path: str | None = None
    samples: int
    total_rows: int
    max_rows: int
    truncated: bool = False
    created_at: str | None = None
    use_rerank: bool = True
    summary: dict[str, float | None]
    results: list[dict] = []


class ConversationRecord(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[StoredChatTurn] = []


class CreateConversationResponse(BaseModel):
    conversation: ConversationRecord


class DeleteConversationResponse(BaseModel):
    success: bool
    message: str


class AdminSettingsResponse(BaseModel):
    chatModel: str = 'gpt-4o-mini'
    imageModel: str = 'gpt-4o-mini'
