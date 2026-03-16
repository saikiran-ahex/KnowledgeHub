import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from app.config import get_settings


def _connect():
    return psycopg.connect(get_settings().database_url, row_factory=dict_row)


def init_db():
    with _connect() as conn:
        with conn.cursor() as c:
            c.execute(
                '''CREATE TABLE IF NOT EXISTS users (
                    id BIGSERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_admin BOOLEAN DEFAULT FALSE,
                    created_at TEXT NOT NULL
                )'''
            )

            c.execute(
                '''CREATE TABLE IF NOT EXISTS files (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    doc_id TEXT UNIQUE NOT NULL,
                    filename TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    content_hash TEXT,
                    chunks_indexed INTEGER NOT NULL,
                    uploaded_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )'''
            )
            c.execute(
                'CREATE UNIQUE INDEX IF NOT EXISTS idx_files_user_content_hash '
                'ON files(user_id, content_hash) WHERE content_hash IS NOT NULL'
            )

            c.execute(
                '''CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )'''
            )

            c.execute(
                '''CREATE TABLE IF NOT EXISTS conversation_messages (
                    id BIGSERIAL PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sources_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                )'''
            )
        conn.commit()


@contextmanager
def get_db():
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def create_user(username: str, password_hash: str, is_admin: bool = False) -> int:
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                'INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (%s, %s, %s, %s) RETURNING id',
                (username, password_hash, is_admin, datetime.now(timezone.utc).isoformat()),
            )
            user_id = c.fetchone()['id']
        conn.commit()
        return int(user_id)


def get_user_by_username(username: str):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute('SELECT * FROM users WHERE username = %s', (username,))
            return c.fetchone()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def create_file_record(
    user_id: int,
    doc_id: str,
    filename: str,
    file_path: str,
    file_type: str,
    chunks: int,
    content_hash: str | None = None,
):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                '''INSERT INTO files (user_id, doc_id, filename, file_path, file_type, content_hash, chunks_indexed, uploaded_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id''',
                (user_id, doc_id, filename, file_path, file_type, content_hash, chunks, datetime.now(timezone.utc).isoformat()),
            )
            file_id = c.fetchone()['id']
        conn.commit()
        return int(file_id)


def get_user_file_by_content_hash(user_id: int, content_hash: str):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute('SELECT * FROM files WHERE user_id = %s AND content_hash = %s', (user_id, content_hash))
            row = c.fetchone()
            return dict(row) if row else None


def get_user_files(user_id: int):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute('SELECT * FROM files WHERE user_id = %s ORDER BY uploaded_at DESC', (user_id,))
            return [dict(row) for row in c.fetchall()]


def get_all_admin_files():
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                '''SELECT f.* FROM files f
                   JOIN users u ON f.user_id = u.id
                   WHERE u.is_admin = TRUE
                   ORDER BY f.uploaded_at DESC'''
            )
            return [dict(row) for row in c.fetchall()]


def delete_file_record(file_id: int, user_id: int):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute('SELECT * FROM files WHERE id = %s AND user_id = %s', (file_id, user_id))
            file = c.fetchone()
            if file:
                c.execute('DELETE FROM files WHERE id = %s', (file_id,))
                conn.commit()
                return dict(file)
    return None


def create_conversation(user_id: int, title: str = 'New Chat'):
    now = datetime.now(timezone.utc).isoformat()
    conversation_id = uuid4().hex
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                '''INSERT INTO conversations (id, user_id, title, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s)''',
                (conversation_id, user_id, title, now, now),
            )
        conn.commit()
    return get_conversation(conversation_id, user_id)


def get_conversation(conversation_id: str, user_id: int):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute('SELECT * FROM conversations WHERE id = %s AND user_id = %s', (conversation_id, user_id))
            conversation = c.fetchone()
            if not conversation:
                return None

            c.execute(
                '''SELECT * FROM conversation_messages
                   WHERE conversation_id = %s
                   ORDER BY created_at ASC, id ASC''',
                (conversation_id,),
            )
            messages = []
            for row in c.fetchall():
                item = dict(row)
                item['sources'] = json.loads(item.pop('sources_json') or '[]')
                messages.append(item)

            data = dict(conversation)
            data['messages'] = messages
            return data


def get_user_conversations(user_id: int):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                '''SELECT * FROM conversations
                   WHERE user_id = %s
                   ORDER BY updated_at DESC, created_at DESC''',
                (user_id,),
            )
            conversations = [dict(row) for row in c.fetchall()]
            for conversation in conversations:
                c.execute(
                    '''SELECT * FROM conversation_messages
                       WHERE conversation_id = %s
                       ORDER BY created_at ASC, id ASC''',
                    (conversation['id'],),
                )
                messages = []
                for row in c.fetchall():
                    item = dict(row)
                    item['sources'] = json.loads(item.pop('sources_json') or '[]')
                    messages.append(item)
                conversation['messages'] = messages
            return conversations


def delete_conversation(conversation_id: str, user_id: int):
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute('SELECT * FROM conversations WHERE id = %s AND user_id = %s', (conversation_id, user_id))
            conversation = c.fetchone()
            if not conversation:
                return None
            c.execute('DELETE FROM conversations WHERE id = %s', (conversation_id,))
        conn.commit()
        return dict(conversation)


def append_conversation_message(conversation_id: str, role: str, content: str, sources: list[dict] | None = None):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                '''INSERT INTO conversation_messages (conversation_id, role, content, sources_json, created_at)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id''',
                (conversation_id, role, content, json.dumps(sources or []), now),
            )
            message_id = c.fetchone()['id']
            c.execute('UPDATE conversations SET updated_at = %s WHERE id = %s', (now, conversation_id))
        conn.commit()
        return int(message_id)


def count_conversation_messages(conversation_id: str) -> int:
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute('SELECT COUNT(*) AS count FROM conversation_messages WHERE conversation_id = %s', (conversation_id,))
            row = c.fetchone()
            return int(row['count']) if row else 0


def update_conversation_title(conversation_id: str, user_id: int, title: str):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        with conn.cursor() as c:
            c.execute(
                'UPDATE conversations SET title = %s, updated_at = %s WHERE id = %s AND user_id = %s',
                (title, now, conversation_id, user_id),
            )
        conn.commit()
