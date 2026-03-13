import { useMemo, useState, useRef, useEffect } from 'react';
import { IoMicSharp } from "react-icons/io5";
import { TiAttachment } from "react-icons/ti";
import Admin from './Admin';

const API_BASE = '/api';
const SUPPORTED = '.txt,.md,.pdf,.doc,.docx,.csv,.png,.jpg,.jpeg,.webp';
const MAX_UPLOAD_SIZE_MB = 30;
const DEFAULT_IMAGE_MODELS = [
  { value: 'gpt-5-mini', label: 'GPT-5 Mini' },
  { value: 'gpt-4.1-mini', label: 'GPT-4.1 Mini' },
  {value:'free model', label: 'Nemotron (Free )'}
];

const EXAMPLE_QUESTIONS = [
  "What are the key points in the documents?",
  "Summarize the main findings",
  "What insights can you provide?",
  "Explain the technical details"
];

const DEFAULT_CHAT = { id: 'draft', title: 'New Chat', messages: [], isDraft: true };
const DEFAULT_CHATS = [DEFAULT_CHAT];

async function readApiResponse(res) {
  const contentType = res.headers.get('content-type') || '';
  if (contentType.includes('application/json')) return res.json();
  return { detail: await res.text() };
}

function Sources({ sources }) {
  if (!sources?.length) return null;
  return (
    <div className="sources">
      {sources.map((s, i) => (
        <span key={`${s.source}-${i}`} className="chip">
          {s.source} ({Number(s.score || 0).toFixed(2)})
        </span>
      ))}
    </div>
  );
}

function ChatMessage({ role, content, sources, filePreviewUrl, fileName }) {
  return (
    <div className={`msg ${role}`}>
      {role === 'assistant' && <div className="botName">Zill</div>}
      <div className="bubble" style={{ background: 'transparent', boxShadow: 'none', padding: 0, display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '6px' }}>
        {filePreviewUrl && (
          <img
            src={filePreviewUrl}
            alt={fileName || 'uploaded image'}
            style={{ maxWidth: '220px', maxHeight: '180px', borderRadius: '12px', display: 'block' }}
          />
        )}
        {content && (
          <div
            className="bubble"
          style={{
          margin: 0,
          maxWidth: "260px",
          width: "fit-content",
          alignSelf: "flex-end"
          }}
        >
            <p style={{ margin: 0, wordBreak: "break-word" }}>{content}</p>
          </div>
        )}
        {role === 'assistant' ? <Sources sources={sources} /> : null}
      </div>
    </div>
  );
}

export default function App() {
  const [isAdminRoute, setIsAdminRoute] = useState(window.location.pathname === '/admin');
  const [isAdmin, setIsAdmin] = useState(localStorage.getItem('is_admin') === 'true');

  useEffect(() => {
    const handleLocationChange = () => {
      setIsAdminRoute(window.location.pathname === '/admin');
      setIsAdmin(localStorage.getItem('is_admin') === 'true');
    };
    
    window.addEventListener('popstate', handleLocationChange);
    
    // Listen for custom navigation events
    window.addEventListener('navigate', handleLocationChange);
    
    return () => {
      window.removeEventListener('popstate', handleLocationChange);
      window.removeEventListener('navigate', handleLocationChange);
    };
  }, []);

  // Check route on mount and when localStorage changes
  useEffect(() => {
    setIsAdminRoute(window.location.pathname === '/admin');
    setIsAdmin(localStorage.getItem('is_admin') === 'true');
  }, []);

  if (isAdminRoute) {
    return <Admin />;
  }

  return <ChatApp />;
}

function ChatApp() {
  const [token, setToken] = useState(localStorage.getItem('token'));
  const [username, setUsername] = useState(localStorage.getItem('username') || '');
  const [chats, setChats] = useState(DEFAULT_CHATS);
  const [activeChat, setActiveChat] = useState(DEFAULT_CHAT.id);
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const chatEndRef = useRef(null);

  const [imageModels, setImageModels] = useState(DEFAULT_IMAGE_MODELS);
  const [selectedImageModel, setSelectedImageModel] = useState(DEFAULT_IMAGE_MODELS[0].value);

  const [loginUsername, setLoginUsername] = useState('');
  const [loginPassword, setLoginPassword] = useState('');
  const [isRegister, setIsRegister] = useState(false);
  const [authError, setAuthError] = useState('');

  const [isListening, setIsListening] = useState(false);
  const recognitionRef = useRef(null);
  const baseTranscriptRef = useRef('');
  const pendingSendAfterListeningRef = useRef(false);

  const currentChat = chats.find((c) => c.id === activeChat) || chats[0];
  const history = currentChat.messages;
  const canSend = useMemo(() => question.trim().length > 1 && !busy, [question, busy]);
  const isAdhocImage = useMemo(() => {
    if (!file?.name) return false;
    const lower = file.name.toLowerCase();
    return ['.png', '.jpg', '.jpeg', '.webp'].some((ext) => lower.endsWith(ext));
  }, [file]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [history]);

  useEffect(() => {
    loadImageModels();
  }, []);

  useEffect(() => {
    if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
      const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
      recognitionRef.current = new SpeechRecognition();
      recognitionRef.current.continuous = true;
      recognitionRef.current.interimResults = true;
      recognitionRef.current.lang = 'en-US';

      recognitionRef.current.onresult = (event) => {
        let interimTranscript = '';
        let finalTranscript = '';

        for (let i = 0; i < event.results.length; i++) {
          const transcript = event.results[i][0].transcript;
          if (event.results[i].isFinal) {
            finalTranscript += transcript + ' ';
          } else {
            interimTranscript += transcript;
          }
        }

        if (finalTranscript) {
          baseTranscriptRef.current += finalTranscript;
          setQuestion(baseTranscriptRef.current);
        } else {
          setQuestion(baseTranscriptRef.current + interimTranscript);
        }
      };

      recognitionRef.current.onerror = () => {
        pendingSendAfterListeningRef.current = false;
        setIsListening(false);
      };

      recognitionRef.current.onend = () => {
        setIsListening(false);
        if (pendingSendAfterListeningRef.current) {
          pendingSendAfterListeningRef.current = false;
          setTimeout(() => sendMessage(), 0);
        }
      };
    }
  }, []);

  function toggleListening() {
    if (!recognitionRef.current) {
      alert('Speech recognition is not supported in your browser');
      return;
    }

    if (isListening) {
      pendingSendAfterListeningRef.current = false;
      recognitionRef.current.stop();
      setIsListening(false);
    } else {
      baseTranscriptRef.current = question;
      recognitionRef.current.start();
      setIsListening(true);
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (isListening && recognitionRef.current) {
        pendingSendAfterListeningRef.current = true;
        recognitionRef.current.stop();
        setIsListening(false);
        return;
      }
      setTimeout(() => sendMessage(), 0);
    }
  }

  useEffect(() => {
    if (!token) {
      setChats(DEFAULT_CHATS);
      setActiveChat(DEFAULT_CHAT.id);
      return;
    }
    loadConversations();
  }, [token]);

  function logout() {
    localStorage.removeItem('token');
    localStorage.removeItem('username');
    localStorage.removeItem('is_admin');
    setToken(null);
    setUsername('');
    setChats(DEFAULT_CHATS);
    setActiveChat(DEFAULT_CHAT.id);
  }

  async function handleAuth() {
    setAuthError('');
    const endpoint = isRegister ? '/register' : '/login';
    try {
      const res = await fetch(`${API_BASE}${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: loginUsername, password: loginPassword }),
      });
      const data = await readApiResponse(res);
      if (!res.ok) throw new Error(data.detail || 'Authentication failed');
      
      localStorage.setItem('token', data.access_token);
      localStorage.setItem('username', data.username);
      localStorage.setItem('is_admin', data.is_admin ? 'true' : 'false');
      setToken(data.access_token);
      setUsername(data.username);
      setLoginUsername('');
      setLoginPassword('');
    } catch (err) {
      setAuthError(String(err.message || err));
    }
  }

  async function loadImageModels() {
    try {
      const res = await fetch(`${API_BASE}/image-models`);
      const data = await readApiResponse(res);
      if (!res.ok) throw new Error(data.detail || 'Failed to load image models');
      if (Array.isArray(data) && data.length > 0) {
        setImageModels(data);
        setSelectedImageModel((prev) => (
          data.some((model) => model.value === prev) ? prev : data[0].value
        ));
      }
    } catch (err) {
      console.error('Failed to load image models:', err);
      setImageModels(DEFAULT_IMAGE_MODELS);
      setSelectedImageModel((prev) => (
        DEFAULT_IMAGE_MODELS.some((model) => model.value === prev) ? prev : DEFAULT_IMAGE_MODELS[0].value
      ));
    }
  }

  async function loadConversations() {
    try {
      const res = await fetch(`${API_BASE}/conversations`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await readApiResponse(res);
      if (!res.ok) throw new Error(data.detail || 'Failed to load conversations');
      const items = Array.isArray(data) && data.length ? data : DEFAULT_CHATS;
      setChats(items);
      setActiveChat((prev) => (items.some((chat) => chat.id === prev) ? prev : items[0].id));
    } catch (err) {
      console.error('Failed to load conversations:', err);
      setChats(DEFAULT_CHATS);
      setActiveChat(DEFAULT_CHAT.id);
    }
  }

  async function createConversation() {
    const res = await fetch(`${API_BASE}/conversations`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    });
    const data = await readApiResponse(res);
    if (!res.ok) throw new Error(data.detail || 'Failed to create conversation');
    return data.conversation;
  }

  async function newChat() {
    try {
      const conversation = await createConversation();
      setChats((prev) => [conversation, ...prev.filter((chat) => !chat.isDraft)]);
      setActiveChat(conversation.id);
    } catch (err) {
      console.error('Failed to create conversation:', err);
      setChats((prev) => (prev.some((chat) => chat.isDraft) ? prev : [DEFAULT_CHAT, ...prev]));
      setActiveChat(DEFAULT_CHAT.id);
    }
  }

  async function deleteChat(id) {
    const chatToDelete = chats.find((chat) => chat.id === id);
    if (chats.length === 1) return;
    if (chatToDelete && !chatToDelete.isDraft) {
      try {
        const res = await fetch(`${API_BASE}/conversations/${id}`, {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${token}` },
        });
        const data = await readApiResponse(res);
        if (!res.ok) throw new Error(data.detail || 'Failed to delete conversation');
      } catch (err) {
        alert(`Error: ${err.message || err}`);
        return;
      }
    }

    const filtered = chats.filter((c) => c.id !== id);
    const nextChats = filtered.length ? filtered : DEFAULT_CHATS;
    setChats(nextChats);
    if (activeChat === id) setActiveChat(nextChats[0].id);
  }

  async function sendMessage() {
    const q = question.trim();
    if (!q || !currentChat) return;

    baseTranscriptRef.current = '';
    setBusy(true);
    setQuestion('');

    const isImage = file && /\.(png|jpe?g|webp)$/i.test(file.name);
    const filePreviewUrl = isImage ? URL.createObjectURL(file) : null;
    const userMsg = { role: 'user', content: q, filePreviewUrl, fileName: file?.name };
    
    const historyForApi = history.map((m) => ({ role: m.role, content: m.content }));
    let targetConversationId = currentChat.id;

    setChats((prev) =>
      prev.map((c) =>
        c.id === activeChat
          ? {
              ...c,
              messages: [...c.messages, userMsg],
              title: c.messages.length === 0 ? q.slice(0, 30) : c.title,
            }
          : c
      )
    );

    try {
      if (currentChat.isDraft) {
        const conversation = await createConversation();
        targetConversationId = conversation.id;
        setChats((prev) =>
          prev.map((c) =>
            c.id === activeChat
              ? { ...conversation, messages: c.messages }
              : c
          )
        );
        setActiveChat(conversation.id);
      }

      const fd = new FormData();
      fd.append('question', q);
      fd.append('conversation_id', targetConversationId);
      fd.append('history_json', JSON.stringify(historyForApi));

      if (file) {
        fd.append('file', file);
        if (isAdhocImage && selectedImageModel) {
          fd.append('image_model', selectedImageModel);
        }
      }

      const res = await fetch(`${API_BASE}/chat`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: fd,
      });
      const data = await readApiResponse(res);
      if (!res.ok) throw new Error(data.detail || 'Failed');

      setChats((prev) =>
        prev.map((c) =>
          c.id === targetConversationId
            ? {
                ...c,
                id: data.conversation_id || c.id,
                isDraft: false,
                title: c.title === 'New Chat' ? q.slice(0, 30) : c.title,
                messages: [...c.messages, { role: 'assistant', content: data.answer, sources: data.sources || [] }],
              }
            : c
        )
      );
      setFile(null);
    } catch (err) {
      setChats((prev) =>
        prev.map((c) =>
          c.id === targetConversationId || (currentChat.isDraft && c.id === activeChat)
            ? { ...c, messages: [...c.messages, { role: 'assistant', content: `Error: ${String(err.message || err)}`, sources: [] }] }
            : c
        )
      );
    } finally {
      setBusy(false);
    }
  }

  if (!token) {
    return (
      <div className="loginPage">
        <div className="loginCard">
          <h1>✨ KnowledgeHub</h1>
          <p>{isRegister ? 'Create your account' : 'Sign in to continue'}</p>
          
          {authError && <div className="authError">{authError}</div>}
          
          <input
            type="text"
            placeholder="Username"
            value={loginUsername}
            onChange={(e) => setLoginUsername(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleAuth()}
          />
          <input
            type="password"
            placeholder="Password"
            value={loginPassword}
            onChange={(e) => setLoginPassword(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleAuth()}
          />
          
          <button onClick={handleAuth} className="authBtn">
            {isRegister ? 'Register' : 'Login'}
          </button>
          
          <button onClick={() => setIsRegister(!isRegister)} className="toggleAuthBtn">
            {isRegister ? 'Already have an account? Login' : "Don't have an account? Register"}
          </button>
          
          <div style={{ marginTop: '20px', paddingTop: '20px', borderTop: '1px solid #2a2a2a' }}>
            <button 
              onClick={() => {
                window.history.pushState({}, '', '/admin');
                window.dispatchEvent(new Event('navigate'));
              }} 
              className="toggleAuthBtn"
              style={{ marginTop: '0' }}
            >
              🔐 Login as Admin
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
        <div className="sidebarHeader">
          <h2>💬 Conversations</h2>
          <button onClick={newChat} className="newChatBtn">+</button>
        </div>

        <div className="chatList">
          {chats.map((chat) => (
            <div
              key={chat.id}
              className={`chatItem ${activeChat === chat.id ? 'active' : ''}`}
              onClick={() => setActiveChat(chat.id)}
            >
              <span className="chatTitle">{chat.title}</span>
              <button
                onClick={async (e) => {
                  e.stopPropagation();
                  await deleteChat(chat.id);
                }}
                className="deleteBtn"
              >
                🗑️
              </button>
            </div>
          ))}
        </div>

        <div className="sidebarFooter">
          <div className="userInfo">
            <span>👤 {username}</span>
            <button onClick={logout} className="logoutBtn">Logout</button>
          </div>
        </div>
      </aside>

      <main className="main">
        <header className="header">
          <button className="menuBtn" onClick={() => setSidebarOpen(!sidebarOpen)}>menu</button>
          <h1>✨ KnowledgeHub</h1>
        </header>

        <div className="chatWindow">
          {history.length === 0 ? (
            <div className="emptyState">
              <div className="emptyIcon">✨</div>
              <h3>Welcome to KnowledgeHub</h3>
              <p>Your intelligent document assistant</p>
              <div className="exampleQuestions">
                <p className="exampleLabel">Try asking:</p>
                {EXAMPLE_QUESTIONS.map((q, i) => (
                  <button key={i} className="exampleBtn" onClick={() => setQuestion(q)}>
                    {q}
                  </button>
                ))}
              </div>
            </div>
          ) : null}

          {history.map((m, i) => <ChatMessage key={i} role={m.role} content={m.content} sources={m.sources} filePreviewUrl={m.filePreviewUrl} fileName={m.fileName} />)}
          {busy && (
            <div className="msg assistant">
              <div className="botName">Zill</div>
              <div className="bubble">
                <div className="loader">
                  <span></span>
                  <span></span>
                  <span></span>
                </div>
              </div>
            </div>
          )}
          <div ref={chatEndRef} />
        </div>

        <div className="inputArea">
          {file ? <div className="fileChip">📎 {file.name} <button onClick={() => setFile(null)}>✕</button></div> : null}
          <div className="chatInput">
            <label className="fileBtn">
              <TiAttachment />
              <input type="file" accept={SUPPORTED} onChange={(e) => setFile(e.target.files?.[0] || null)} style={{ display: 'none' }} />
            </label>
            <button 
              onClick={toggleListening} 
              className={`micBtn ${isListening ? 'listening' : ''}`}
              title="Voice input"
            >
              <IoMicSharp />
            </button>
            {isAdhocImage ? (
              <select
                value={selectedImageModel}
                onChange={(e) => setSelectedImageModel(e.target.value)}
                className="imageModelSelect"
                title="Image model for this ad-hoc image"
              >
                {imageModels.map((model) => (
                  <option key={model.value} value={model.value}>{model.label}</option>
                ))}
              </select>
            ) : null}
            <textarea
              rows={1}
              placeholder={isListening ? 'Listening...' : 'Type your message...'}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={handleKeyDown}
            />
            <button onClick={sendMessage} disabled={!canSend} className="sendBtn">{busy ? '⏳' : '➤'}</button>
          </div>
        </div>
      </main>
    </div>
  );
}
