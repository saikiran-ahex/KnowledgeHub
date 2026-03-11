import sqlite3
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager
from uuid import uuid4

DB_PATH = Path('data/app.db')


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        doc_id TEXT UNIQUE NOT NULL,
        filename TEXT NOT NULL,
        file_path TEXT NOT NULL,
        file_type TEXT NOT NULL,
        content_hash TEXT,
        chunks_indexed INTEGER NOT NULL,
        uploaded_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )''')
    _ensure_column(c, 'files', 'content_hash', 'TEXT')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_files_user_content_hash ON files(user_id, content_hash) WHERE content_hash IS NOT NULL')

    c.execute('''CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS conversation_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        sources_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
    )''')
    
    conn.commit()
    conn.close()


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    try:
        yield conn
    finally:
        conn.close()


def create_user(username: str, password_hash: str) -> int:
    with get_db() as conn:
        c = conn.cursor()
        c.execute('INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)',
                  (username, password_hash, datetime.now(timezone.utc).isoformat()))
        conn.commit()
        return c.lastrowid


def get_user_by_username(username: str):
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM users WHERE username = ?', (username,))
        return c.fetchone()


def _ensure_column(cursor, table_name: str, column_name: str, column_def: str) -> None:
    cursor.execute(f"PRAGMA table_info({table_name})")
    existing = {row[1] for row in cursor.fetchall()}
    if column_name not in existing:
        cursor.execute(f'ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}')


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def create_file_record(user_id: int, doc_id: str, filename: str, file_path: str, file_type: str, chunks: int, content_hash: str | None = None):
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''INSERT INTO files (user_id, doc_id, filename, file_path, file_type, content_hash, chunks_indexed, uploaded_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                  (user_id, doc_id, filename, file_path, file_type, content_hash, chunks, datetime.now(timezone.utc).isoformat()))
        conn.commit()
        return c.lastrowid


def get_user_file_by_content_hash(user_id: int, content_hash: str):
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM files WHERE user_id = ? AND content_hash = ?', (user_id, content_hash))
        row = c.fetchone()
        return dict(row) if row else None


def get_user_files(user_id: int):
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM files WHERE user_id = ? ORDER BY uploaded_at DESC', (user_id,))
        return [dict(row) for row in c.fetchall()]


def delete_file_record(file_id: int, user_id: int):
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM files WHERE id = ? AND user_id = ?', (file_id, user_id))
        file = c.fetchone()
        if file:
            c.execute('DELETE FROM files WHERE id = ?', (file_id,))
            conn.commit()
            return dict(file)
    return None


def create_conversation(user_id: int, title: str = 'New Chat'):
    now = datetime.now(timezone.utc).isoformat()
    conversation_id = uuid4().hex
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            '''INSERT INTO conversations (id, user_id, title, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)''',
            (conversation_id, user_id, title, now, now),
        )
        conn.commit()
    return get_conversation(conversation_id, user_id)


def get_conversation(conversation_id: str, user_id: int):
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM conversations WHERE id = ? AND user_id = ?', (conversation_id, user_id))
        conversation = c.fetchone()
        if not conversation:
            return None

        c.execute(
            '''SELECT * FROM conversation_messages
               WHERE conversation_id = ?
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
        c = conn.cursor()
        c.execute(
            '''SELECT * FROM conversations
               WHERE user_id = ?
               ORDER BY updated_at DESC, created_at DESC''',
            (user_id,),
        )
        conversations = [dict(row) for row in c.fetchall()]
        for conversation in conversations:
            c.execute(
                '''SELECT * FROM conversation_messages
                   WHERE conversation_id = ?
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
        c = conn.cursor()
        c.execute('SELECT * FROM conversations WHERE id = ? AND user_id = ?', (conversation_id, user_id))
        conversation = c.fetchone()
        if not conversation:
            return None
        c.execute('DELETE FROM conversations WHERE id = ?', (conversation_id,))
        conn.commit()
        return dict(conversation)


def append_conversation_message(conversation_id: str, role: str, content: str, sources: list[dict] | None = None):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            '''INSERT INTO conversation_messages (conversation_id, role, content, sources_json, created_at)
               VALUES (?, ?, ?, ?, ?)''',
            (conversation_id, role, content, json.dumps(sources or []), now),
        )
        c.execute('UPDATE conversations SET updated_at = ? WHERE id = ?', (now, conversation_id))
        conn.commit()
        return c.lastrowid


def count_conversation_messages(conversation_id: str) -> int:
    with get_db() as conn:
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM conversation_messages WHERE conversation_id = ?', (conversation_id,))
        row = c.fetchone()
        return int(row[0]) if row else 0


def update_conversation_title(conversation_id: str, user_id: int, title: str):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            'UPDATE conversations SET title = ?, updated_at = ? WHERE id = ? AND user_id = ?',
            (title, now, conversation_id, user_id),
        )
        conn.commit()
