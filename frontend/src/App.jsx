import { useMemo, useState, useRef, useEffect } from 'react';
import { IoMicSharp } from "react-icons/io5";
import { TiAttachment } from "react-icons/ti";
import Admin from './Admin';
import { useTheme } from './context/ThemeContext';

const API_BASE = '/api';
const SUPPORTED = '.txt,.md,.pdf,.doc,.docx,.csv,.png,.jpg,.jpeg,.webp';
const DEFAULT_IMAGE_MODELS = [
  { value: 'gpt-5-mini', label: 'GPT-5 Mini' },
  { value: 'gpt-4.1-mini', label: 'GPT-4.1 Mini' },
  { value: 'nemotron-nano', label: 'Nemotron Nano VL (Free)' },
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
    <div className="sourcesContainer">
      <div className="sourcesHeader">
        <svg 
          className="sourcesIcon" 
          xmlns="http://www.w3.org/2000/svg" 
          width="14" 
          height="14" 
          viewBox="0 0 24 24" 
          fill="none" 
          stroke="currentColor" 
          strokeWidth="2" 
          strokeLinecap="round" 
          strokeLinejoin="round"
        >
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
          <polyline points="14 2 14 8 20 8"></polyline>
        </svg>
        <span className="sourcesLabel">Sources ({sources.length})</span>
      </div>
      <div className="sourcesList">
        {sources.map((s, i) => (
          <div key={`${s.source}-${i}`} className="sourceItem">
            <div className="sourceNumber">{i + 1}</div>
            <div className="sourceContent">
              <div className="sourceName">{s.source}</div>
              <div className="sourceScore">
                Relevance: {(Number(s.score || 0) * 100).toFixed(0)}%
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function FeedbackBar({ messageId, onSubmit }) {
  if (!messageId) return null;
  return (
    <div className="feedbackBar">
      <button className="feedbackBtn" onClick={() => onSubmit(messageId, true)} title="Helpful">
        👍
      </button>
      <button className="feedbackBtn" onClick={() => onSubmit(messageId, false)} title="Not helpful">
        👎
      </button>
    </div>
  );
}

function EvaluationSummary({ ragasScore, judgeScore, evaluationScores }) {
  const items = [
    ['Self', evaluationScores?.overall ?? ragasScore],
    ['Judge', evaluationScores?.judge_score ?? judgeScore],
  ].filter(([, value]) => value != null);
  if (!items.length) return null;
  return (
    <div className="messageMetrics">
      {items.map(([label, value]) => (
        <span key={label} className="metricChip">
          {label}: {(Number(value) * 100).toFixed(0)}%
        </span>
      ))}
    </div>
  );
}

function parseMarkdownText(text) {
  if (!text) return null;
  
  const elements = [];
  let key = 0;
  
  const lines = text.split('\n');
  let i = 0;
  
  while (i < lines.length) {
    const line = lines[i].trim();
    
    if (!line) {
      i++;
      continue;
    }
    
    if (line.match(/^[-•*]\s/)) {
      const listItems = [];
      while (i < lines.length) {
        const currentLine = lines[i].trim();
        if (currentLine.match(/^[-•*]\s/)) {
          const itemText = currentLine.replace(/^[-•*]\s+/, '');
          listItems.push(itemText);
          i++;
        }
        else if (!currentLine) {
          i++;
        }
        else {
          break;
        }
      }
      elements.push(
        <ul key={key++} className="responseList">
          {listItems.map((item, idx) => (
            <li key={idx}>{formatInlineMarkdown(item)}</li>
          ))}
        </ul>
      );
      continue;
    }
    
    if (line.match(/^\d+\.\s/)) {
      const listItems = [];
      while (i < lines.length) {
        const currentLine = lines[i].trim();
        if (currentLine.match(/^\d+\.\s/)) {
          const itemText = currentLine.replace(/^\d+\.\s+/, '');
          listItems.push(itemText);
          i++;
        }
        else if (!currentLine) {
          i++;
        }
        else {
          break;
        }
      }
      elements.push(
        <ol key={key++} className="responseList">
          {listItems.map((item, idx) => (
            <li key={idx}>{formatInlineMarkdown(item)}</li>
          ))}
        </ol>
      );
      continue;
    }
    
    let paragraph = line;
    i++;
    while (i < lines.length && lines[i].trim() && !lines[i].trim().match(/^[-•*]\s/) && !lines[i].trim().match(/^\d+\.\s/)) {
      paragraph += ' ' + lines[i].trim();
      i++;
    }
    
    elements.push(
      <p key={key++} className="responseParagraph">
        {formatInlineMarkdown(paragraph)}
      </p>
    );
  }
  
  return elements;
}

function formatInlineMarkdown(text) {
  if (!text) return text;
  
  const parts = [];
  let currentIndex = 0;
  let key = 0;
  
  const regex = /(\*\*(.+?)\*\*|`([^`]+?)`)/g;
  let match;
  
  while ((match = regex.exec(text)) !== null) {
    if (match.index > currentIndex) {
      parts.push(text.substring(currentIndex, match.index));
    }
    
    if (match[2] !== undefined) {
      parts.push(
        <strong key={`bold-${key++}`} className="boldText">
          {match[2]}
        </strong>
      );
    }
    else if (match[3] !== undefined) {
      parts.push(
        <code key={`code-${key++}`} className="inlineCode">
          {match[3]}
        </code>
      );
    }
    
    currentIndex = match.index + match[0].length;
  }
  
  if (currentIndex < text.length) {
    parts.push(text.substring(currentIndex));
  }
  
  return parts.length > 0 ? parts : text;
}

function ChatMessage({ role, content, sources, filePreviewUrl, fileName, image_base64, messageId, ragas_score, judge_score, evaluation_scores, onFeedback }) {
  const imgSrc = image_base64
    ? `data:image/jpeg;base64,${image_base64}`
    : filePreviewUrl;
  return (
    <div className={`msg ${role}`}>
      {role === 'assistant' && (
        <div className="botHeader">
          <div className="botName">Zill Assistant</div>
        </div>
      )}
      <div className={`messageStack ${role}`}>
        {imgSrc && (
          <div className="imagePreviewContainer">
            <img
              className="chatImagePreview"
              src={imgSrc}
              alt={fileName || 'uploaded image'}
              style={{ display: 'block' }}
            />
            {fileName && <div className="imageCaption">{fileName}</div>}
          </div>
        )}
        {content && (
          <div className="bubble">
            <div className="responseContent">
              {role === 'assistant' ? parseMarkdownText(content) : <p className="responseParagraph">{content}</p>}
            </div>
          </div>
        )}
        {role === 'assistant' ? (
          <>
            <EvaluationSummary
              ragasScore={ragas_score}
              judgeScore={judge_score}
              evaluationScores={evaluation_scores}
            />
            <Sources sources={sources} />
            <FeedbackBar messageId={messageId} onSubmit={onFeedback} />
          </>
        ) : null}
      </div>
    </div>
  );
}

export default function App() {
  const [isAdminRoute, setIsAdminRoute] = useState(window.location.pathname === '/admin');

  useEffect(() => {
    const handleLocationChange = () => {
      setIsAdminRoute(window.location.pathname === '/admin');
    };
    window.addEventListener('popstate', handleLocationChange);
    window.addEventListener('navigate', handleLocationChange);
    return () => {
      window.removeEventListener('popstate', handleLocationChange);
      window.removeEventListener('navigate', handleLocationChange);
    };
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
  const [activeChat, setActiveChat] = useState(localStorage.getItem('activeChat') || DEFAULT_CHAT.id);
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
  const liveTranscriptRef = useRef('');
  const pendingSendAfterListeningRef = useRef(false);
  const pendingSendTextRef = useRef('');
  const { toggleTheme } = useTheme();

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
          liveTranscriptRef.current = baseTranscriptRef.current;
          setQuestion(baseTranscriptRef.current);
        } else {
          liveTranscriptRef.current = baseTranscriptRef.current + interimTranscript;
          setQuestion(liveTranscriptRef.current);
        }
      };

      recognitionRef.current.onerror = () => {
        pendingSendAfterListeningRef.current = false;
        pendingSendTextRef.current = '';
        setIsListening(false);
      };

      recognitionRef.current.onend = () => {
        setIsListening(false);
        if (pendingSendAfterListeningRef.current) {
          pendingSendAfterListeningRef.current = false;
          const textToSend = pendingSendTextRef.current || liveTranscriptRef.current || baseTranscriptRef.current;
          pendingSendTextRef.current = '';
          setQuestion(textToSend);
          setTimeout(() => sendMessage(textToSend), 0);
        }
      };
    }
  }, []);

  function toggleListening() {
    if (!recognitionRef.current) {
      alert('Speech recognition is not supported in your browser');
      return;
    }
    if (question.trim().length !== 0) {
      sendMessage();
    }
    if (isListening) {
      pendingSendAfterListeningRef.current = false;
      pendingSendTextRef.current = '';
      recognitionRef.current.stop();
      setIsListening(false);
    } else {
      baseTranscriptRef.current = question;
      liveTranscriptRef.current = question;
      recognitionRef.current.start();
      setIsListening(true);
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (isListening && recognitionRef.current) {
        pendingSendAfterListeningRef.current = true;
        pendingSendTextRef.current = liveTranscriptRef.current || question;
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

  // Save activeChat to localStorage whenever it changes
  useEffect(() => {
    if (activeChat) {
      localStorage.setItem('activeChat', activeChat);
    }
  }, [activeChat]);

  function logout() {
    localStorage.removeItem('token');
    localStorage.removeItem('username');
    localStorage.removeItem('is_admin');
    localStorage.removeItem('activeChat');
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

  async function sendMessage(overrideQuestion = null) {
    const q = String(overrideQuestion ?? question).trim();
    if (!q || !currentChat) return;

    baseTranscriptRef.current = '';
    liveTranscriptRef.current = '';
    pendingSendTextRef.current = '';
    setBusy(true);
    setQuestion('');

    const isImage = file && /\.(png|jpe?g|webp)$/i.test(file.name);
    const filePreviewUrl = isImage ? URL.createObjectURL(file) : null;
    const userMsg = { role: 'user', content: q, filePreviewUrl, fileName: file?.name };
    const capturedFile = file;
    setFile(null);


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

      if (capturedFile) {
        fd.append('file', capturedFile);
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
              messages: [...c.messages, {
                id: data.assistant_message_id,
                role: 'assistant',
                content: data.answer,
                sources: data.sources || [],
                ragas_score: data.evaluation_scores?.overall ?? null,
                judge_score: data.evaluation_scores?.judge_score ?? null,
                evaluation_scores: data.evaluation_scores || {},
                created_at: new Date().toISOString(),
              }],
            }
            : c
        )
      );
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

  async function submitFeedback(messageId, feedbackResult) {
    try {
      const res = await fetch(`${API_BASE}/feedback`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          message_id: messageId,
          feedback_result: feedbackResult,
          knowledge_gap_flag: !feedbackResult,
        }),
      });
      const data = await readApiResponse(res);
      if (!res.ok) throw new Error(data.detail || 'Failed to submit feedback');
    } catch (err) {
      console.error('Failed to submit feedback:', err);
    }
  }

  function truncateTitle(title) {
    const words = title.split(' ');
    if (words.length > 4) {
      return words.slice(0, 4).join(' ') + '...';
    }
    return title;
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
          <h2>
            <svg
              className="conversationIcon"
              xmlns="http://www.w3.org/2000/svg"
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
              <path d="M8 10h.01"></path>
              <path d="M12 10h.01"></path>
              <path d="M16 10h.01"></path>
            </svg>
            Conversations
          </h2>
          <button onClick={newChat} className="newChatBtn">+</button>
        </div>

        <div className="chatList">
          {chats.map((chat) => (
            <div
              key={chat.id}
              className={`chatItem ${activeChat === chat.id ? 'active' : ''}`}
              onClick={() => setActiveChat(chat.id)}
            >
              <span className="chatTitle">{truncateTitle(chat.title)}</span>
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
            <button
              onClick={toggleTheme}
              className="themeToggleMinimal"
              title="Toggle theme"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="18"
                height="18"
                fill="currentColor"
                viewBox="0 0 32 32"
              >
                <path d="M16 .5C7.4.5.5 7.4.5 16S7.4 31.5 16 31.5 31.5 24.6 31.5 16 24.6.5 16 .5zm0 28.1V3.4C23 3.4 28.6 9 28.6 16S23 28.6 16 28.6z" />
              </svg>
            </button>
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

          {history.map((m, i) => (
            <ChatMessage
              key={m.id || i}
              role={m.role}
              content={m.content}
              sources={m.sources}
              filePreviewUrl={m.filePreviewUrl}
              fileName={m.fileName}
              image_base64={m.image_base64}
              messageId={m.id}
              ragas_score={m.ragas_score}
              judge_score={m.judge_score}
              evaluation_scores={m.evaluation_scores}
              onFeedback={submitFeedback}
            />
          ))}
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
          {file ? <div className="fileChip">
            <svg
              className="fileIcon"
              xmlns="http://www.w3.org/2000/svg"
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
              <circle cx="9" cy="9" r="2"></circle>
              <path d="M21 15l-3.086-3.086a2 2 0 0 0-2.828 0L6 21"></path>
            </svg>
            {file.name} <button onClick={() => setFile(null)}>✕</button></div> : null}
          <div className="chatInput">
            <label className="fileBtn">
              <TiAttachment />
              <input type="file" accept={SUPPORTED} onChange={(e) => { setFile(e.target.files?.[0] || null); e.target.value = ''; }} style={{ display: 'none' }} />
            </label>
            <button
              onClick={toggleListening}
              className={`micBtn ${isListening ? 'listening' : ''}`}
              title="Voice input"
            >
              <IoMicSharp />
            </button>
            {isAdhocImage && (
              <select
                value={selectedImageModel}
                onChange={(e) => setSelectedImageModel(e.target.value)}
                className="imageModelSelect"
              >
                {imageModels.map((model) => (
                  <option key={model.value} value={model.value}>
                    {model.label}
                  </option>
                ))}
              </select>
            )}
            <textarea
              rows={1}
              placeholder={isListening ? 'Listening...' : 'Type your message...'}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={handleKeyDown}
            />
            <button onClick={() => sendMessage()} disabled={!canSend} className="sendBtn">{busy ? '⏳' : '➤'}</button>
          </div>
        </div>
      </main>
    </div>
  );
}
