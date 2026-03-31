import json
import logging
import base64
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from langchain_cohere import CohereRerank
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from openai import BadRequestError
from openai import OpenAI
from PIL import Image
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from app.config import get_settings
from app.services.chunker import chunk_text
from app.services.document_loader import IMAGE_EXTENSIONS, extract_document_images, load_document

logger = logging.getLogger(__name__)


class RagService:
    _DOC_PREFIX_RE = re.compile(r'^[0-9a-f]{32}_(.+)$')
    _TRAILING_SOURCES_RE = re.compile(r'\n*Sources used:\s*(?:\n\s*-\s*.+)+\s*$', re.IGNORECASE)

    def __init__(self) -> None:
        init_start = perf_counter()
        settings = get_settings()
        self.settings = settings
        logger.info(
            'RagService init started model=%s embedding=%s text_collection=%s',
            settings.openai_model,
            settings.embedding_model,
            settings.qdrant_collection,
        )

        self.chat_llm = self._build_chat_llm(use_default_temperature=False)
        self.chat_llm_default_temp = self._build_chat_llm(use_default_temperature=True)
        self.openai_client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
        self.embedding_model = OpenAIEmbeddings(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model=settings.embedding_model,
        )

        self.qdrant_client = QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
        )
        logger.info('Qdrant client initialized url=%s', settings.qdrant_url)
        self._ensure_collections()

        self.vector_store = QdrantVectorStore(
            client=self.qdrant_client,
            collection_name=settings.qdrant_collection,
            embedding=self.embedding_model,
        )

        self.reranker = None
        if settings.cohere_api_key:
            self.reranker = CohereRerank(
                cohere_api_key=settings.cohere_api_key,
                model=settings.cohere_rerank_model,
                top_n=settings.rerank_top_k,
            )
            logger.info('Cohere reranker enabled model=%s top_n=%s', settings.cohere_rerank_model, settings.rerank_top_k)
        else:
            logger.info('Cohere reranker disabled: missing COHERE_API_KEY')
        logger.info('RagService init completed elapsed_ms=%s', int((perf_counter() - init_start) * 1000))

    def _build_chat_llm(self, use_default_temperature: bool) -> ChatOpenAI:
        model_name = self.settings.openai_model.lower().strip()
        kwargs = {
            'api_key': self.settings.openai_api_key,
            'base_url': self.settings.openai_base_url,
            'model': self.settings.openai_model,
        }
        if not use_default_temperature:
            # GPT-5 family models reject custom temperature values.
            if model_name.startswith('gpt-5'):
                logger.info('Skipping custom temperature for GPT-5 model=%s', self.settings.openai_model)
            else:
                kwargs['temperature'] = self.settings.temperature
        return ChatOpenAI(**kwargs)

    def _invoke_chat(self, payload):
        try:
            return self.chat_llm.invoke(payload)
        except BadRequestError as exc:
            msg = str(exc)
            if 'temperature' in msg and 'Only the default (1) value is supported' in msg:
                logger.warning('Provider rejected custom temperature; retrying with model default temperature')
                return self.chat_llm_default_temp.invoke(payload)
            raise

    def _ensure_collections(self) -> None:
        text_exists = self.qdrant_client.collection_exists(self.settings.qdrant_collection)
        if text_exists:
            logger.info('Qdrant text collection exists name=%s', self.settings.qdrant_collection)
        else:
            logger.info('Qdrant text collection missing, creating name=%s', self.settings.qdrant_collection)
            text_dim = len(self.embedding_model.embed_query('dimension probe'))
            self.qdrant_client.create_collection(
                collection_name=self.settings.qdrant_collection,
                vectors_config=qmodels.VectorParams(size=text_dim, distance=qmodels.Distance.COSINE),
            )
            logger.info('Qdrant text collection created name=%s dim=%s', self.settings.qdrant_collection, text_dim)

    def _expand_query(self, question: str) -> list[str]:
        logger.info('Query expansion started')
        normalized_question = question.strip()
        if len(normalized_question) < 12:
            logger.info('Query expansion skipped: question too short')
            return []
        prompt = (
            f'Generate {self.settings.query_expansion_count} alternate phrasings for this query. '
            'Return strictly JSON array of strings only. No markdown.'
        )
        response = self._invoke_chat(f'{prompt}\n\nQuery: {normalized_question}')
        content = str(response.content).strip()

        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                seen = {normalized_question.lower()}
                variations = []
                for item in parsed:
                    text = str(item).strip()
                    if not text or text.lower() in seen:
                        continue
                    seen.add(text.lower())
                    variations.append(text)
                selected = variations[: self.settings.query_expansion_count]
                logger.info('Query expansion completed variations=%s', len(selected))
                return selected
        except json.JSONDecodeError:
            logger.warning('Query expansion parse failed; using original query only')

        return []

    @staticmethod
    def _dedupe_docs(documents: list[Document]) -> list[Document]:
        deduped: list[Document] = []
        seen = set()
        for doc in documents:
            key = (doc.metadata.get('source', ''), doc.page_content)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(doc)
        return deduped

    @staticmethod
    def _source_name(file_path: Path, metadata: dict | None = None) -> str:
        metadata = metadata or {}
        raw = str(metadata.get('source') or file_path.name)
        return RagService._normalize_source_name(raw)

    @classmethod
    def _normalize_source_name(cls, source_name: str) -> str:
        normalized = source_name.removeprefix('chat_').removeprefix('adhoc_')
        match = cls._DOC_PREFIX_RE.match(normalized)
        normalized = match.group(1) if match else normalized
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        return normalized

    @classmethod
    def _source_matches(cls, actual_source: str, expected_source: str) -> bool:
        return cls._normalize_source_name(actual_source).lower() == cls._normalize_source_name(expected_source).lower()

    @classmethod
    def _strip_trailing_sources_block(cls, text: str) -> str:
        return cls._TRAILING_SOURCES_RE.sub('', text).strip()

    @staticmethod
    def _build_qdrant_filter(filters: dict | None, *, metadata_prefix: str = '', owner_ids: list[str] | None = None) -> qmodels.Filter | None:
        if not filters and not owner_ids:
            return None
        filters = filters or {}
        must: list[qmodels.FieldCondition] = []
        should: list[qmodels.FieldCondition] = []
        owner_id = filters.get('owner_id')
        tenant_id = filters.get('tenant_id')
        file_type = filters.get('file_type')
        source = filters.get('source')
        doc_id = filters.get('doc_id')
        tags = filters.get('tags')

        def field(name: str) -> str:
            return f'{metadata_prefix}{name}' if metadata_prefix else name

        if owner_ids:
            for oid in owner_ids:
                should.append(qmodels.FieldCondition(key=field('owner_id'), match=qmodels.MatchValue(value=str(oid))))
        elif owner_id:
            must.append(qmodels.FieldCondition(key=field('owner_id'), match=qmodels.MatchValue(value=str(owner_id))))
        if tenant_id:
            must.append(qmodels.FieldCondition(key=field('tenant_id'), match=qmodels.MatchValue(value=str(tenant_id))))
        if file_type:
            must.append(qmodels.FieldCondition(key=field('file_type'), match=qmodels.MatchValue(value=str(file_type))))
        if source:
            must.append(qmodels.FieldCondition(key=field('source'), match=qmodels.MatchValue(value=str(source))))
        if doc_id:
            must.append(qmodels.FieldCondition(key=field('doc_id'), match=qmodels.MatchValue(value=str(doc_id))))
        if tags:
            for tag in tags:
                must.append(qmodels.FieldCondition(key=field('tags'), match=qmodels.MatchValue(value=str(tag))))

        if not must and not should:
            return None
        return qmodels.Filter(must=must, should=should if should else None)

    @staticmethod
    def _payload_metadata(payload: dict | None) -> dict:
        payload = payload or {}
        nested = payload.get('metadata')
        return nested if isinstance(nested, dict) else payload

    @classmethod
    def _payload_value(cls, payload: dict | None, key: str):
        payload = payload or {}
        if key in payload:
            return payload.get(key)
        return cls._payload_metadata(payload).get(key)

    @staticmethod
    def _auto_tags(text: str, source_name: str, max_tags: int = 8) -> list[str]:
        tokens = []
        for raw in (source_name.replace('.', ' ') + ' ' + text[:3000]).lower().split():
            cleaned = ''.join(ch for ch in raw if ch.isalnum() or ch in {'_', '-'})
            if len(cleaned) < 3:
                continue
            if cleaned in {
                'the', 'and', 'for', 'with', 'that', 'this', 'from', 'into', 'your', 'have',
                'are', 'was', 'were', 'not', 'you', 'has', 'had', 'will', 'shall', 'can', 'its',
                'pdf', 'doc', 'docx', 'txt', 'csv', 'png', 'jpg', 'jpeg', 'webp'
            }:
                continue
            tokens.append(cleaned)

        seen = set()
        tags: list[str] = []
        for token in tokens:
            if token in seen:
                continue
            seen.add(token)
            tags.append(token)
            if len(tags) >= max_tags:
                break
        return tags

    @staticmethod
    def _guess_heading(text: str) -> str | None:
        for line in (text or '').splitlines():
            cleaned = line.strip()
            if 4 <= len(cleaned) <= 120:
                return cleaned
        return None

    def profile_document(self, file_path: Path, preloaded_text: str | None = None) -> tuple[str | None, str | None]:
        ext = file_path.suffix.lower()
        source_name = file_path.name.removeprefix('chat_').removeprefix('adhoc_')
        if ext in IMAGE_EXTENSIONS:
            return 'image', f'Uploaded image asset: {source_name}'

        try:
            text = preloaded_text if preloaded_text is not None else load_document(file_path)
        except Exception as exc:
            logger.warning('Document profiling failed during load file=%s error=%s', file_path.name, exc)
            return None, None

        excerpt = ' '.join((text or '').split())[:6000]
        if not excerpt:
            return None, f'Indexed document: {source_name}'

        prompt = (
            'Read this document excerpt and return strictly JSON with two fields: '
            '"domain" as a short phrase for the document domain, and "description" as one sentence '
            'summarizing the document. Do not include markdown.'
        )
        try:
            response = self._invoke_chat(f'{prompt}\n\nExcerpt:\n{excerpt}')
            payload = json.loads(str(response.content).strip())
            domain = str(payload.get('domain') or '').strip() or None
            description = str(payload.get('description') or '').strip() or None
            if domain or description:
                logger.info('Document profiling completed file=%s domain=%s', file_path.name, domain)
                return domain, description
        except Exception as exc:
            logger.warning('Document profiling LLM parse failed file=%s error=%s', file_path.name, exc)

        return None, f'Indexed document: {source_name}'

    def _finalize_candidates(self, candidates: list[Document], limit: int | None = None) -> list[Document]:
        deduped = self._dedupe_docs(candidates)
        deduped.sort(key=lambda doc: float(doc.metadata.get('score', 0.0)), reverse=True)
        per_source: dict[str, int] = {}
        selected: list[Document] = []
        for doc in deduped:
            score = float(doc.metadata.get('score', 0.0))
            if score < self.settings.retrieval_min_score:
                continue
            source = str(doc.metadata.get('source', 'unknown'))
            current = per_source.get(source, 0)
            if current >= self.settings.retrieval_max_per_source:
                continue
            per_source[source] = current + 1
            selected.append(doc)
            if limit is not None and len(selected) >= limit:
                break
        return selected

    def _rerank_documents(self, candidates: list[Document], question: str) -> tuple[list[Document], bool]:
        if self.reranker is None:
            return candidates[: self.settings.rerank_top_k], False
        try:
            reranked = self.reranker.compress_documents(candidates, query=question)
            return reranked[: self.settings.rerank_top_k], False
        except Exception as exc:
            logger.warning('Rerank failed; falling back to top retrieved docs error=%s', exc)
            return candidates[: self.settings.rerank_top_k], True

    def _ingest_image(self, file_path: Path, metadata: dict) -> int:
        source_name = self._source_name(file_path, metadata)
        logger.info('Image ingest started source=%s', source_name)
        description = self._describe_document_image(file_path) or f'Image file: {source_name}'

        text_doc = Document(
            page_content=description,
            metadata={
                'source': source_name,
                'normalized_source': self._normalize_source_name(source_name),
                'type': 'image',
                'chunk_type': 'image',
                'chunk_id': f"{metadata.get('doc_id', file_path.stem)}:image:1",
                'heading': self._guess_heading(description) or source_name,
                'chunk_size_chars': len(description),
                'image_url': str(file_path),
                **metadata,
            },
        )
        self.vector_store.add_documents([text_doc])
        logger.info('Image ingest completed source=%s', source_name)
        return 1

    def _describe_document_image(self, image_path: Path) -> str:
        mime = 'image/png'
        try:
            with Image.open(image_path) as image:
                normalized = BytesIO()
                image.convert('RGB').save(normalized, format='PNG')
                encoded = base64.b64encode(normalized.getvalue()).decode('utf-8')
        except Exception as exc:
            logger.warning('Document image normalization failed image=%s error=%s', image_path.name, exc)
            return ''
        image_url = f'data:{mime};base64,{encoded}'
        prompt = (
            'This image is extracted from a technical document. Please provide a detailed description of '
            'everything visible in this image including any text, labels, diagrams, arrows, measurements, '
            'port names, UI elements, steps, or technical specifications. Be as specific as possible so that '
            'someone who cannot see the image can fully understand its contents.'
        )
        try:
            response = self.openai_client.chat.completions.create(
                model=self.settings.openai_model,
                messages=[
                    {'role': 'system', 'content': 'You describe technical document images in detail for retrieval indexing.'},
                    {
                        'role': 'user',
                        'content': [
                            {'type': 'text', 'text': prompt},
                            {'type': 'image_url', 'image_url': {'url': image_url}},
                        ],
                    },
                ],
            )
            return (response.choices[0].message.content or '').strip()
        except Exception as exc:
            logger.warning('Document image description failed image=%s error=%s', image_path.name, exc)
            return ''

    def ingest_file(
        self,
        file_path: Path,
        metadata: dict | None = None,
        raw_bytes: bytes | None = None,
        preloaded_text: str | None = None,
    ) -> int:
        ingest_start = perf_counter()
        metadata = dict(metadata or {})
        source_name = self._source_name(file_path, metadata)
        logger.info('Ingest started source=%s', source_name)
        if raw_bytes:
            metadata['content_hash'] = hashlib.sha256(raw_bytes).hexdigest()

        if file_path.suffix.lower() in IMAGE_EXTENSIONS:
            if not metadata.get('tags'):
                metadata['tags'] = self._auto_tags('', source_name)
            indexed = self._ingest_image(file_path, metadata)
            logger.info(
                'Ingest completed source=%s chunks=%s elapsed_ms=%s',
                source_name,
                indexed,
                int((perf_counter() - ingest_start) * 1000),
            )
            return indexed

        text = preloaded_text if preloaded_text is not None else load_document(file_path)
        chunks = chunk_text(
            text,
            chunk_size=self.settings.chunk_size,
            overlap=self.settings.chunk_overlap,
        )
        if not metadata.get('tags'):
            metadata['tags'] = self._auto_tags(text, source_name)

        docs = []
        for idx, chunk in enumerate(chunks):
            heading = self._guess_heading(chunk)
            chunk_meta = {
                'source': source_name,
                'normalized_source': self._normalize_source_name(source_name),
                'type': 'text',
                'chunk_type': 'text',
                'chunk_id': f"{metadata.get('doc_id', file_path.stem)}:{idx}",
                'heading': heading,
                'chunk_size_chars': len(chunk),
                'image_url': None,
                **metadata,
            }
            docs.append(Document(page_content=chunk, metadata=chunk_meta))

        extracted_dir = self.settings.upload_dir / '_extracted' / str(metadata.get('doc_id', file_path.stem))
        extracted_images = extract_document_images(
            file_path,
            extracted_dir,
            min_dimension=self.settings.document_image_min_dimension,
            min_bytes=self.settings.document_image_min_bytes,
        )

        def _describe_extracted_image(item: tuple[int, dict]) -> Document | None:
            image_index, image_info = item
            image_path = Path(image_info['path'])
            description = self._describe_document_image(image_path)
            if not description:
                return None
            image_meta = {
                'source': source_name,
                'normalized_source': self._normalize_source_name(source_name),
                'type': 'image',
                'chunk_type': 'image',
                'chunk_id': f"{metadata.get('doc_id', file_path.stem)}:image:{image_index}",
                'heading': self._guess_heading(description) or image_path.name,
                'chunk_size_chars': len(description),
                'page_no': image_info.get('page_no'),
                'image_url': str(image_path),
                **metadata,
            }
            return Document(page_content=description, metadata=image_meta)

        if extracted_images:
            max_workers = max(1, min(self.settings.document_image_description_workers, len(extracted_images)))
            logger.info(
                'Image description started source=%s images=%s workers=%s',
                source_name,
                len(extracted_images),
                max_workers,
            )
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = [ex.submit(_describe_extracted_image, item) for item in enumerate(extracted_images, start=1)]
                for future in as_completed(futures):
                    doc = future.result()
                    if doc is not None:
                        docs.append(doc)

        if not docs:
            logger.info('Ingest completed source=%s chunks=0 elapsed_ms=%s', source_name, int((perf_counter() - ingest_start) * 1000))
            return 0
        logger.info('Vector store add started source=%s docs=%s', source_name, len(docs))
        self.vector_store.add_documents(docs)
        logger.info('Ingest completed source=%s chunks=%s elapsed_ms=%s', source_name, len(docs), int((perf_counter() - ingest_start) * 1000))
        return len(docs)

    def _retrieve_candidates(self, queries: list[str], retrieve_k: int, filters: dict | None = None, owner_ids: list[str] | None = None) -> list[Document]:
        filters = dict(filters or {})
        requested_source = str(filters.pop('source', '') or '').strip()
        qdrant_filter = self._build_qdrant_filter(filters, metadata_prefix='metadata.', owner_ids=owner_ids)
        text_candidates: list[Document] = []

        embedding_start = perf_counter()
        query_vectors = self.embedding_model.embed_documents(queries)
        logger.info(
            'Text query embeddings completed queries=%s elapsed_ms=%s',
            len(queries),
            int((perf_counter() - embedding_start) * 1000),
        )

        def _search_text(query: str, query_vector: list[float]) -> list[Document]:
            logger.info('Text retrieval started query="%s"', query[:120])
            result = self.qdrant_client.query_points(
                collection_name=self.settings.qdrant_collection,
                query=query_vector,
                limit=retrieve_k,
                with_payload=True,
                query_filter=qdrant_filter,
            )
            found: list[Document] = []
            for point in result.points:
                payload = point.payload or {}
                metadata = self._payload_metadata(payload)
                source = self._normalize_source_name(str(self._payload_value(payload, 'source') or 'unknown'))
                if requested_source and not self._source_matches(source, requested_source):
                    continue
                found.append(
                    Document(
                        page_content=str(payload.get('page_content', payload.get('text', metadata.get('text', '')))),
                        metadata={
                            'source': source,
                            'type': self._payload_value(payload, 'type'),
                            'score': float(point.score or 0.0),
                            'doc_id': self._payload_value(payload, 'doc_id'),
                            'chunk_id': self._payload_value(payload, 'chunk_id'),
                            'owner_id': self._payload_value(payload, 'owner_id'),
                            'tenant_id': self._payload_value(payload, 'tenant_id'),
                            'uploaded_at': self._payload_value(payload, 'uploaded_at'),
                            'file_type': self._payload_value(payload, 'file_type'),
                            'page_no': self._payload_value(payload, 'page_no'),
                            'content_hash': self._payload_value(payload, 'content_hash'),
                            'tags': self._payload_value(payload, 'tags'),
                            'heading': self._payload_value(payload, 'heading'),
                            'chunk_size_chars': self._payload_value(payload, 'chunk_size_chars'),
                        },
                    )
                )
            return found

        max_workers = min(len(queries), 4) or 1
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = [ex.submit(_search_text, query, vec) for query, vec in zip(queries, query_vectors)]
            for f in as_completed(futures):
                text_candidates.extend(f.result())

        candidates = self._finalize_candidates(text_candidates, limit=retrieve_k)
        logger.info(
            'Retrieval completed text_docs=%s total=%s source_filter=%s',
            len(text_candidates),
            len(candidates),
            requested_source or None,
        )
        return candidates

    def _answer_from_documents(self, question: str, selected_docs: list[Document], history: list[dict] | None = None) -> tuple[str, list[dict]]:
        history = history or []
        history_text = '\n'.join([f"{item.get('role', 'user')}: {item.get('content', '')}" for item in history[-8:]])
        context_text = '\n\n'.join(
            [
                f"Source: {doc.metadata.get('source', 'unknown')}\nType: {doc.metadata.get('type', 'text')}\nContent: {doc.page_content}"
                for doc in selected_docs
            ]
        )

        system_msg = (
            'You are a helpful assistant with access to a document knowledge base. '
            'If user greets you, greet them back. '
            'Use the provided context to answer the question accurately and directly. '
            'Focus on what was asked without adding unnecessary information. '
            'If context is provided, base your answer on it. '
            'If no context is provided, answer conversationally from your general knowledge or the conversation history.'
        )
        answer_prompt = ChatPromptTemplate.from_messages(
            [
                ('system', system_msg),
                (
                    'human',
                    'Conversation history:\n{history}\n\nQuestion:\n{question}\n\nContext:\n{context}\n\nAnswer directly. Do not add a sources section.',
                ),
            ]
        )
        answer = self._invoke_chat(answer_prompt.format_messages(question=question, history=history_text, context=context_text))
        logger.info('Answer generation completed')

        source_scores: dict[str, dict] = {}
        total = max(len(selected_docs), 1)
        for rank, doc in enumerate(selected_docs, start=1):
            source = self._normalize_source_name(str(doc.metadata.get('source', 'unknown')))
            score = float(total - rank + 1) / float(total)
            existing = source_scores.get(source)
            if existing is None or score > float(existing.get('score', 0.0)):
                source_scores[source] = {
                    'source': source,
                    'score': score,
                    'doc_id': doc.metadata.get('doc_id'),
                    'chunk_id': doc.metadata.get('chunk_id'),
                    'owner_id': doc.metadata.get('owner_id'),
                    'tenant_id': doc.metadata.get('tenant_id'),
                    'uploaded_at': doc.metadata.get('uploaded_at'),
                    'file_type': doc.metadata.get('file_type'),
                    'page_no': doc.metadata.get('page_no'),
                    'content_hash': doc.metadata.get('content_hash'),
                    'tags': doc.metadata.get('tags'),
                    'type': doc.metadata.get('type'),
                    'heading': doc.metadata.get('heading'),
                }

        sources = list(source_scores.values())
        return self._strip_trailing_sources_block(str(answer.content)), sources

    def _answer_from_documents_for_eval(self, question: str, selected_docs: list[Document]) -> str:
        context_text = '\n\n'.join(
            [
                f"Source: {doc.metadata.get('source', 'unknown')}\nContent: {doc.page_content}"
                for doc in selected_docs
            ]
        )

        system_msg = (
            'You are a precise multimodal RAG assistant that answers questions based strictly on the provided context. '
            'Provide accurate, complete, and relevant answers. '
            'Use all relevant information from the context to answer thoroughly. '
        )
        answer_prompt = ChatPromptTemplate.from_messages(
            [
                ('system', system_msg),
                ('human', 'Question: {question}\n\nContext:\n{context}\n\nProvide a complete and accurate answer based on the context.'),
            ]
        )
        answer = self._invoke_chat(answer_prompt.format_messages(question=question, context=context_text))
        return self._strip_trailing_sources_block(str(answer.content))

    def _analyze_image_with_llm(self, image_path: Path, question: str, image_model: str | None = None) -> str:
        mime = 'image/png'
        suffix = image_path.suffix.lower().lstrip('.')
        if suffix in {'jpg', 'jpeg'}:
            mime = 'image/jpeg'
        elif suffix == 'webp':
            mime = 'image/webp'

        encoded = base64.b64encode(image_path.read_bytes()).decode('utf-8')
        image_url = f'data:{mime};base64,{encoded}'
        if image_model=='nemotron-nano':
            _client=OpenAI(api_key=self.settings.openrouter_api_key, base_url=self.settings.openrouter_base_url)
            _model=self.settings.openrouter_model
            logger.info('Using OpenRouter for image analysis model=%s', _model)
        else:
            _client=self.openai_client
            _model=image_model or self.settings.openai_model
            logger.info('Using OpenAI for image analysis model=%s', _model)
        
        logger.info('Image analysis started file=%s mime=%s model=%s', image_path.name, mime, _model)
        try:
            response = _client.chat.completions.create(
                model=_model,
                messages=[
                    {
                        'role': 'system',
                        'content': 'You analyze images for RAG and extract relevant facts as concise text.',
                    },
                    {
                        'role': 'user',
                        'content': [
                            {'type': 'text', 'text': f'Question: {question}\nDescribe key details and visible text.'},
                            {'type': 'image_url', 'image_url': {'url': image_url}},
                        ],
                    },
                ],
            )
            logger.info('Image analysis API call completed response_type=%s', type(response).__name__)
            if response and response.choices and len(response.choices) > 0:
                content = response.choices[0].message.content or ''
                logger.info('Image analysis successful content_length=%s', len(content))
                return content
            else:
                logger.warning('Empty response from image analysis API response=%s choices=%s', response, getattr(response, 'choices', None))
                return ''
        except Exception as exc:
            logger.error('Image analysis failed file=%s model=%s error=%s', image_path.name, _model, exc)
            return ''

    def ask(
        self,
        question: str,
        top_k: int | None = None,
        history: list[dict] | None = None,
        filters: dict | None = None,
        owner_ids: list[str] | None = None,
    ) -> tuple[str, list[dict]]:
        ask_start = perf_counter()
        retrieve_k = top_k or self.settings.retrieval_top_k
        logger.info('Ask pipeline started question_len=%s retrieve_k=%s', len(question), retrieve_k)
        expand_start = perf_counter()
        queries = [question]
        queries.extend([q for q in self._expand_query(question) if q.lower() != question.lower()])
        logger.info('Expansion elapsed_ms=%s', int((perf_counter() - expand_start) * 1000))
        logger.info('Retrieval queries prepared count=%s', len(queries))

        retrieval_start = perf_counter()
        candidates = self._retrieve_candidates(queries, retrieve_k, filters=filters, owner_ids=owner_ids)
        logger.info('Retrieval elapsed_ms=%s', int((perf_counter() - retrieval_start) * 1000))
        if not candidates:
            logger.info('Ask pipeline: no candidates, answering directly elapsed_ms=%s', int((perf_counter() - ask_start) * 1000))
            answer_text, _ = self._answer_from_documents(question, [], history=history)
            return answer_text, []

        if self.reranker is not None:
            logger.info('Rerank started candidate_docs=%s', len(candidates))
            rerank_start = perf_counter()
            selected_docs, fallback_used = self._rerank_documents(candidates, question)
            logger.info(
                'Rerank completed selected_docs=%s fallback_used=%s elapsed_ms=%s',
                len(selected_docs),
                fallback_used,
                int((perf_counter() - rerank_start) * 1000),
            )
        else:
            selected_docs = candidates[: self.settings.rerank_top_k]
            logger.info('Rerank skipped using top docs selected_docs=%s', len(selected_docs))

        answer_text, sources = self._answer_from_documents(question, selected_docs, history=history)
        logger.info('Ask pipeline completed sources=%s elapsed_ms=%s', len(sources), int((perf_counter() - ask_start) * 1000))
        return answer_text, sources

    def ask_with_file(
        self,
        question: str,
        file_path: Path,
        image_model: str | None = None,
        top_k: int | None = None,
        history: list[dict] | None = None,
        filters: dict | None = None,
        owner_ids: list[str] | None = None,
    ) -> tuple[str, list[dict]]:
        ask_start = perf_counter()
        logger.info('Ask-with-file started question_len=%s file=%s', len(question), file_path.name)
        source_name = file_path.name.removeprefix('chat_').removeprefix('adhoc_')

        retrieve_k = top_k or self.settings.retrieval_top_k
        expand_start = perf_counter()
        queries = [question]
        queries.extend([q for q in self._expand_query(question) if q.lower() != question.lower()])
        logger.info('Ask-with-file expansion elapsed_ms=%s', int((perf_counter() - expand_start) * 1000))

        file_docs: list[Document] = []
        ext = file_path.suffix.lower()
        retrieval_start = perf_counter()
        candidates = self._retrieve_candidates(queries, retrieve_k, filters=filters, owner_ids=owner_ids)
        logger.info('Ask-with-file retrieval elapsed_ms=%s', int((perf_counter() - retrieval_start) * 1000))

        if ext in IMAGE_EXTENSIONS:
            vision_text = self._analyze_image_with_llm(file_path, question, image_model=image_model)
            if vision_text.strip():
                file_docs.append(
                    Document(
                        page_content=vision_text,
                        metadata={'source': source_name, 'type': 'image_vision', 'file_type': file_path.suffix.lstrip('.')},
                    )
                )
                logger.info('Ask-with-file image analysis completed file=%s chars=%s', file_path.name, len(vision_text))
        else:
            file_text = load_document(file_path)
            chunks = chunk_text(file_text, chunk_size=self.settings.chunk_size, overlap=self.settings.chunk_overlap)
            for idx, chunk in enumerate(chunks[: self.settings.rerank_top_k]):
                file_docs.append(
                    Document(
                        page_content=chunk,
                        metadata={
                            'source': source_name,
                            'type': 'ad_hoc_file',
                            'file_type': file_path.suffix.lstrip('.'),
                            'chunk_id': f'adhoc:{idx}',
                        },
                    )
                )
            logger.info('Ask-with-file text extraction completed file=%s chunks=%s', file_path.name, len(file_docs))

        candidates = self._dedupe_docs(file_docs + candidates)
        if not candidates:
            logger.info('Ask-with-file completed: no candidates elapsed_ms=%s', int((perf_counter() - ask_start) * 1000))
            return 'No useful content was found in the uploaded file or shared library.', []

        if self.reranker is not None:
            rerank_start = perf_counter()
            selected_docs, fallback_used = self._rerank_documents(candidates, question)
            logger.info(
                'Ask-with-file rerank elapsed_ms=%s fallback_used=%s',
                int((perf_counter() - rerank_start) * 1000),
                fallback_used,
            )
        else:
            selected_docs = candidates[: self.settings.rerank_top_k]

        answer_text, sources = self._answer_from_documents(question, selected_docs, history=history)
        logger.info('Ask-with-file completed sources=%s elapsed_ms=%s', len(sources), int((perf_counter() - ask_start) * 1000))
        return answer_text, sources

    def chat(
        self,
        question: str,
        image_model: str | None = None,
        top_k: int | None = None,
        history: list[dict] | None = None,
        file_path: Path | None = None,
        filters: dict | None = None,
        owner_ids: list[str] | None = None,
    ) -> tuple[str, list[dict]]:
        # Chat upload is ad-hoc only and never indexed into persistent collections.
        if file_path is not None:
            return self.ask_with_file(
                question=question,
                file_path=file_path,
                image_model=image_model,
                top_k=top_k,
                history=history,
                filters=filters,
                owner_ids=owner_ids,
            )
        return self.ask(question=question, top_k=top_k, history=history, filters=filters, owner_ids=owner_ids)

    def delete_by_doc_id(self, doc_id: str) -> int:
        """Delete all vectors associated with a doc_id from both collections."""
        logger.info('Delete by doc_id started doc_id=%s', doc_id)
        deleted_count = 0
        
        # Delete from text collection
        try:
            self.qdrant_client.delete(
                collection_name=self.settings.qdrant_collection,
                points_selector=qmodels.FilterSelector(
                    filter=qmodels.Filter(
                        should=[
                            qmodels.FieldCondition(key='doc_id', match=qmodels.MatchValue(value=doc_id)),
                            qmodels.FieldCondition(key='metadata.doc_id', match=qmodels.MatchValue(value=doc_id)),
                        ]
                    )
                ),
                wait=True,
            )
            deleted_count += 1
            logger.info('Deleted from text collection doc_id=%s', doc_id)
        except Exception as exc:
            logger.warning('Failed to delete from text collection doc_id=%s error=%s', doc_id, exc)
        
        # Delete from image collection
        if self.qdrant_client.collection_exists(self.settings.qdrant_image_collection):
            try:
                self.qdrant_client.delete(
                    collection_name=self.settings.qdrant_image_collection,
                    points_selector=qmodels.FilterSelector(
                        filter=qmodels.Filter(
                            must=[qmodels.FieldCondition(key='doc_id', match=qmodels.MatchValue(value=doc_id))]
                        )
                    ),
                    wait=True,
                )
                deleted_count += 1
                logger.info('Deleted from legacy image collection doc_id=%s', doc_id)
            except Exception as exc:
                logger.warning('Failed to delete from legacy image collection doc_id=%s error=%s', doc_id, exc)
        
        logger.info('Delete by doc_id completed doc_id=%s collections=%s', doc_id, deleted_count)
        return deleted_count

    def _scroll_doc_ids(self, collection_name: str, owner_id: str, *, metadata_prefix: str = '') -> set[str]:
        doc_ids: set[str] = set()
        scroll_filter = self._build_qdrant_filter({'owner_id': owner_id}, metadata_prefix=metadata_prefix)
        offset = None

        while True:
            points, next_offset = self.qdrant_client.scroll(
                collection_name=collection_name,
                scroll_filter=scroll_filter,
                with_payload=True,
                with_vectors=False,
                limit=256,
                offset=offset,
            )
            for point in points:
                payload = point.payload or {}
                doc_id = self._payload_value(payload, 'doc_id')
                if doc_id:
                    doc_ids.add(str(doc_id))
            if next_offset is None:
                break
            offset = next_offset

        return doc_ids

    def cleanup_user_vectors(self, owner_id: str, valid_doc_ids: set[str]) -> dict:
        owner_id = str(owner_id)
        logger.info('User vector cleanup started owner_id=%s valid_doc_ids=%s', owner_id, len(valid_doc_ids))

        text_doc_ids = self._scroll_doc_ids(
            self.settings.qdrant_collection,
            owner_id,
            metadata_prefix='metadata.',
        )
        image_doc_ids: set[str] = set()
        if self.qdrant_client.collection_exists(self.settings.qdrant_image_collection):
            image_doc_ids = self._scroll_doc_ids(
                self.settings.qdrant_image_collection,
                owner_id,
            )

        text_to_remove = sorted(doc_id for doc_id in text_doc_ids if doc_id not in valid_doc_ids)
        image_to_remove = sorted(doc_id for doc_id in image_doc_ids if doc_id not in valid_doc_ids)

        for doc_id in sorted(set(text_to_remove + image_to_remove)):
            self.delete_by_doc_id(doc_id)

        logger.info(
            'User vector cleanup completed owner_id=%s text_removed=%s image_removed=%s',
            owner_id,
            len(text_to_remove),
            len(image_to_remove),
        )
        return {
            'success': True,
            'message': f'Removed {len(set(text_to_remove + image_to_remove))} stale library entries.',
            'text_doc_ids_removed': text_to_remove,
            'image_doc_ids_removed': image_to_remove,
        }
