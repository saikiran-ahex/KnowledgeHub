import logging
import re

from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)


def _normalize_chunk_text(text: str) -> str:
    text = re.sub(r'\r\n?', '\n', text or '')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def chunk_text(text: str, chunk_size: int = 900, overlap: int = 150) -> list[str]:
    clean_text = _normalize_chunk_text(text)
    if not clean_text:
        logger.info('Chunking skipped: empty text')
        return []

    logger.info('Chunking started chars=%s chunk_size=%s overlap=%s', len(clean_text), chunk_size, overlap)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=['\n# ', '\n## ', '\n### ', '\n\n', '\n- ', '\n* ', '\n', '. ', '; ', ', ', ' ', ''],
    )
    chunks = [_normalize_chunk_text(chunk) for chunk in splitter.split_text(clean_text)]
    chunks = [chunk for chunk in chunks if len(chunk) >= 80]
    logger.info('Chunking completed chunks=%s', len(chunks))
    return chunks
