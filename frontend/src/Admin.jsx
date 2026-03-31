import { useState, useEffect } from 'react';
import { useTheme } from './context/ThemeContext';

const API_BASE = '/api';
const SUPPORTED = '.txt,.md,.pdf,.doc,.docx,.csv,.png,.jpg,.jpeg,.webp';
const MAX_UPLOAD_SIZE_MB = 30;

async function readApiResponse(res) {
  const contentType = res.headers.get('content-type') || '';
  if (contentType.includes('application/json')) return res.json();
  return { detail: await res.text() };
}

function formatMetricPercent(value) {
  return value == null ? 'n/a' : `${(Number(value) * 100).toFixed(2)}%`;
}

export default function Admin() {
  const { theme, toggleTheme } = useTheme();
  const [token, setToken] = useState(localStorage.getItem('token'));
  const [username, setUsername] = useState(localStorage.getItem('username') || '');
  const [isAdmin, setIsAdmin] = useState(localStorage.getItem('is_admin') === 'true');
  const [files, setFiles] = useState([]);
  const [uploadOpen, setUploadOpen] = useState(false)
  const [uploading, setUploading] = useState(false);
  const [uploadResults, setUploadResults] = useState([]);
  const [loginUsername, setLoginUsername] = useState('');
  const [loginPassword, setLoginPassword] = useState('');
  const [authError, setAuthError] = useState('');
  const [notification, setNotification] = useState(null);
  const [showSettings, setShowSettings] = useState(false);
  const [settings, setSettings] = useState({
    chatModel: 'gpt-4o-mini',
    imageModel: 'gpt-4o-mini'
  });
  const [availableModels] = useState([
    { value: 'gpt-4o-mini', label: 'GPT-4o Mini' },
    { value: 'gpt-4.1-mini', label: 'GPT-4.1 Mini' },
    { value: 'gpt-5-mini', label: 'GPT-5 Mini' },
    { value: 'nemotron-nano', label: 'Nemotron Nano VL (Free)' },
    ]);
  const [evaluationBusy, setEvaluationBusy] = useState(false);
  const [evaluationDatasetPath, setEvaluationDatasetPath] = useState('eval/sample_ragas_eval.jsonl');
  const [evaluationResult, setEvaluationResult] = useState(null);
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [reviewQueue, setReviewQueue] = useState([]);
  const [reviewBusyId, setReviewBusyId] = useState(null);

  useEffect(() => {
    if (token && isAdmin) {
      loadFiles();
      loadLatestEvaluation();
      loadSettings();
      loadReviewQueue();
    }
  }, [token, isAdmin]);

  const FILES_PER_PAGE = 3;

  const [page, setPage] = useState(1);

  const startIndex = (page - 1) * FILES_PER_PAGE;
  const endIndex = startIndex + FILES_PER_PAGE;

  const currentFiles = files.slice(startIndex, endIndex);

  const totalPages = Math.ceil(files.length / FILES_PER_PAGE);

  function showNotification(message, type = 'success') {
    setNotification({ message, type });
    setTimeout(() => setNotification(null), 4000);
  }

  async function loadSettings() {
    try {
      const res = await fetch(`${API_BASE}/admin/settings`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await readApiResponse(res);
      if (res.ok && data) {
        setSettings(data);
      }
    } catch (err) {
      console.error('Failed to load settings:', err);
    }
  }

  async function saveSettings() {
    try {
      const res = await fetch(`${API_BASE}/admin/settings`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(settings)
      });
      const data = await readApiResponse(res);
      if (!res.ok) throw new Error(data.detail || 'Failed to save settings');
      
      showNotification('Settings saved successfully', 'success');
      setShowSettings(false);
    } catch (err) {
      showNotification(`Error: ${err.message || err}`, 'error');
    }
  }



  function logout() {
    localStorage.removeItem('token');
    localStorage.removeItem('username');
    localStorage.removeItem('is_admin');
    setToken(null);
    setUsername('');
    setIsAdmin(false);
  }

  async function handleLogin() {
    setAuthError('');
    try {
      const res = await fetch(`${API_BASE}/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: loginUsername, password: loginPassword }),
      });
      const data = await readApiResponse(res);
      if (!res.ok) throw new Error(data.detail || 'Login failed');

      if (!data.is_admin) {
        setAuthError('Admin access required');
        return;
      }

      localStorage.setItem('token', data.access_token);
      localStorage.setItem('username', data.username);
      localStorage.setItem('is_admin', 'true');
      setToken(data.access_token);
      setUsername(data.username);
      setLoginUsername('');
      setLoginPassword('');

      // Redirect to admin panel after successful login
      window.location.href = '/admin';
    } catch (err) {
      setAuthError(String(err.message || err));
    }
  }

  async function loadFiles() {
    try {
      const res = await fetch(`${API_BASE}/files`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await readApiResponse(res);
      if (!res.ok) throw new Error(data.detail || 'Failed to load files');
      setFiles(Array.isArray(data) ? data : []);
    } catch (err) {
      console.error('Failed to load files:', err);
      setFiles([]);
    }
  }

  async function handleUpload() {
    if (selectedFiles.length === 0) return;

    setUploading(true);
    setUploadResults([]);

    try {
      const results = [];

      for (let file of selectedFiles) {
        try {
          const fd = new FormData();
          fd.append('file', file);

          const res = await fetch(`${API_BASE}/upload`, {
            method: 'POST',
            headers: { Authorization: `Bearer ${token}` },
            body: fd,
          });

          const data = await readApiResponse(res);
          if (!res.ok) throw new Error(data.detail || 'Upload failed');

          results.push({ ...data, error: null });
        } catch (err) {
          results.push({
            filename: file.name,
            error: String(err.message || err),
          });
        }
      }

      setUploadResults(results);
      await loadFiles();

    } catch (err) {
      setUploadResults([
        { filename: 'error', error: String(err.message || err) },
      ]);
    } finally {
      setUploading(false);
    }
  }



  function formatFileSize(bytes) {
    if (!bytes) return '0 KB';

    const kb = bytes / 1024;
    const mb = kb / 1024;

    if (mb >= 1) {
      return `${mb.toFixed(2)} MB`;
    }
    return `${kb.toFixed(2)} KB`;
  }

  async function handleDelete(fileId) {
    try {
      const res = await fetch(`${API_BASE}/files/${fileId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await readApiResponse(res);
      if (!res.ok) throw new Error(data.detail || 'Delete failed');

      showNotification('File deleted successfully', 'success');
      await loadFiles();
    } catch (err) {
      showNotification(`Error: ${err.message || err}`, 'error');
    }
  }

  async function handleCleanup() {
    try {
      const res = await fetch(`${API_BASE}/files/cleanup-vectors`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await readApiResponse(res);
      if (!res.ok) throw new Error(data.detail || 'Cleanup failed');

      showNotification(data.message || 'Cleanup completed', 'success');
      await loadFiles();
    } catch (err) {
      showNotification(`Error: ${err.message || err}`, 'error');
    }
  }

  async function handleRunEvaluation() {
    setEvaluationBusy(true);
    try {
      const res = await fetch(`${API_BASE}/evaluation/run`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          dataset_path: evaluationDatasetPath.trim() || null,
        }),
      });
      const data = await readApiResponse(res);
      if (!res.ok) throw new Error(data.detail || 'Evaluation failed');
      setEvaluationResult(data);
      showNotification(`Evaluation completed for ${data.samples} samples`, 'success');
    } catch (err) {
      showNotification(`Error: ${err.message || err}`, 'error');
    } finally {
      setEvaluationBusy(false);
    }
  }

  async function loadLatestEvaluation() {
    try {
      const res = await fetch(`${API_BASE}/evaluation/latest`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await readApiResponse(res);
      if (!res.ok) return;
      setEvaluationResult(data);
    } catch (err) {
      console.error('Failed to load latest evaluation:', err);
    }
  }

  async function handleDownload(fileId) {
    try {
      const res = await fetch(`${API_BASE}/files/download/${fileId}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        const data = await readApiResponse(res);
        throw new Error(data.detail || 'Download failed');
      }
      const disposition = res.headers.get('content-disposition') || '';
      const match = disposition.match(/filename="?([^"]+)"?/);
      const filename = match ? match[1] : `file-${fileId}`;
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      showNotification(`Error: ${err.message || err}`, 'error');
    }
  }

  async function loadReviewQueue() {
    try {
      const res = await fetch(`${API_BASE}/review-queue`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await readApiResponse(res);
      if (!res.ok) throw new Error(data.detail || 'Failed to load review queue');
      setReviewQueue(Array.isArray(data) ? data : []);
    } catch (err) {
      console.error('Failed to load review queue:', err);
      setReviewQueue([]);
    }
  }

  async function markReviewed(queueId) {
    setReviewBusyId(queueId);
    try {
      const res = await fetch(`${API_BASE}/review-queue/${queueId}/reviewed`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await readApiResponse(res);
      if (!res.ok) throw new Error(data.detail || 'Failed to mark as reviewed');
      await loadReviewQueue();
      showNotification('Marked as reviewed', 'success');
    } catch (err) {
      showNotification(`Error: ${err.message || err}`, 'error');
    } finally {
      setReviewBusyId(null);
    }
  }

  function removeFile(indexToRemove) {
    setSelectedFiles((prev) =>
      prev.filter((_, index) => index !== indexToRemove)
    );
  }

  if (!token || !isAdmin) {
    return (
      <div className="loginPage">
        <div className="loginCard">
          <h1>
            <span className="emoji">🔐</span>
            <span className="gradientText"> Admin Panel</span>
          </h1>
          <p>Sign in as Admin</p>

          {authError && <div className="authError">{authError}</div>}

          <input
            type="text"
            placeholder="Admin Username"
            value={loginUsername}
            onChange={(e) => setLoginUsername(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleLogin()}
          />
          <input
            type="password"
            placeholder="Admin Password"
            value={loginPassword}
            onChange={(e) => setLoginPassword(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleLogin()}
          />

          <button onClick={handleLogin} className="authBtn">Sign in as Admin</button>

          <div style={{ marginTop: '20px', paddingTop: '20px', borderTop: '1px solid #2a2a2a' }}>
            <button
              onClick={() => {
                window.history.pushState({}, '', '/');
                window.dispatchEvent(new Event('navigate'));
              }}
              className="toggleAuthBtn"
            >
              ← Back to User Login
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="adminPage">
      {notification && (
        <div className={`notification ${notification.type}`}>
          <span>{notification.type === 'success' ? '✅' : '❌'}</span>
          <span>{notification.message}</span>
          <button onClick={() => setNotification(null)}>✕</button>
        </div>
      )}

      {showSettings && (
        <div className="settingsModal" onClick={() => setShowSettings(false)}>
          <div className="settingsCard" onClick={(e) => e.stopPropagation()}>
            <div className="settingsHeader">
              <h2>⚙️ Model Settings</h2>
              <button onClick={() => setShowSettings(false)} className="closeModalBtn">✕</button>
            </div>

            <div className="settingsContent">
              <div className="settingSection">
                <h3>Chat Model</h3>
                <p className="settingDescription">Select the default model for user chat</p>
                <select
                  value={settings.chatModel}
                  onChange={(e) => setSettings(prev => ({ ...prev, chatModel: e.target.value }))}
                  className="settingSelect"
                >
                  {availableModels.map(model => (
                    <option key={model.value} value={model.value}>{model.label}</option>
                  ))}
                </select>
              </div>

              <div className="settingSection">
                <h3>Image Model</h3>
                <p className="settingDescription">Select the default model for image analysis</p>
                <select
                  value={settings.imageModel}
                  onChange={(e) => setSettings(prev => ({ ...prev, imageModel: e.target.value }))}
                  className="settingSelect"
                >
                  {availableModels.map(model => (
                    <option key={model.value} value={model.value}>{model.label}</option>
                  ))}
                </select>
              </div>
            </div>

            <div className="settingsFooter">
              <button onClick={() => setShowSettings(false)} className="cancelBtn">Cancel</button>
              <button onClick={saveSettings} className="saveBtn">Save Settings</button>
            </div>
          </div>
        </div>
      )}

      <header className="adminHeader">

        <h1 className="adminTitle">
          <svg
            className="lockIcon"
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
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
            <path d="M7 11V7a5 5 0 0110 0v4"></path>
          </svg>

          Admin Panel
        </h1>

        <div className="adminHeaderActions">

          <button
            onClick={() => setShowSettings(true)}
            className="headerBtn"
            title="Model Settings"
          >
            ⚙️ Settings
          </button>

          <button
            onClick={toggleTheme}
            className="themeToggleMinimal"
            title="Toggle theme"
            aria-label="Toggle theme"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="20"
              height="20"
              fill="currentColor"
              viewBox="0 0 32 32"
            >
              <path d="M16 .5C7.4.5.5 7.4.5 16S7.4 31.5 16 31.5 31.5 24.6 31.5 16 24.6.5 16 .5zm0 28.1V3.4C23 3.4 28.6 9 28.6 16S23 28.6 16 28.6z" />
            </svg>
          </button>

          <span>👤 {username}</span>

          <button onClick={logout} className="headerBtn">
            Logout
          </button>

        </div>

      </header>

      <main className="adminMain">
        <section className="adminSection">
          <div className="sectionHeader">
            <h2>Evaluation</h2>
            <div className="fileActions">
              <button onClick={handleRunEvaluation} className="cleanupBtn" disabled={evaluationBusy}>
                {evaluationBusy ? 'Running...' : 'Run Evaluation'}
              </button>
            </div>
          </div>
          <p>Test your data file to check how well the system is working.</p>
          {evaluationResult ? (
            <div className="evalResults">
              {evaluationResult.truncated ? (
                <div className="evalMeta">Only the latest {evaluationResult.max_rows} rows were evaluated.</div>
              ) : null}
              <div className="evalGrid">
                {Object.entries(evaluationResult.summary || {}).map(([name, value]) => (
                  <div key={name} className="evalCard">
                    <div className="evalLabel">{name}</div>
                    <div className="evalValue">{formatMetricPercent(value)}</div>
                  </div>
                ))}
              </div>

            </div>
          ) : null}
        </section>
        <section className="adminSection">
          <div className="sectionHeader">
            <h2>Human Review Queue</h2>
          </div>
          <p>Flagged answers from pipeline checks and user feedback appear here for admin review.</p>
          {reviewQueue.length === 0 ? (
            <p className="emptyText">No flagged interactions right now</p>
          ) : (
            <div className="reviewQueueList">
              {reviewQueue.map((item) => (
                <div key={item.id} className="reviewQueueCard">
                  <div className="reviewQueueHeader">
                    <span className="reviewReason">{item.reason}</span>
                    <span className={`reviewStatus ${item.reviewed ? 'done' : 'pending'}`}>
                      {item.reviewed ? 'Reviewed' : 'Pending'}
                    </span>
                  </div>
                  {item.question ? <div className="reviewBlock"><strong>Question:</strong> {item.question}</div> : null}
                  <div className="reviewBlock"><strong>Answer:</strong> {item.answer}</div>
                  <div className="reviewMetrics">
                    <span className="metricChip">Self: {formatMetricPercent(item.ragas_score)}</span>
                    <span className="metricChip">Judge: {formatMetricPercent(item.judge_score)}</span>
                  </div>
                  {!item.reviewed ? (
                    <button
                      onClick={() => markReviewed(item.id)}
                      className="cleanupBtn"
                      disabled={reviewBusyId === item.id}
                    >
                      {reviewBusyId === item.id ? 'Saving...' : 'Mark as Reviewed'}
                    </button>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </section>
        <section className="adminSection">
          <div className="sectionHeader">
            <h2>📁 Shared Files ({files.length})</h2>
            <div className="fileActions">
              <div className="plusWrapper">
                <button
                  className="plusBtn"
                  onClick={() => {
                    setUploadResults([]);
                    setSelectedFiles([]);
                    setUploadOpen(true);
                  }}
                >
                  +
                </button>

                <span className="tooltip">Upload Files</span>
              </div>
            </div>
            {uploadOpen && (
              <div className="uploadModal">
                <div className="uploadModalCard">
                  <h2>Upload Files</h2>
                  <p>Upload files that all users can search and chat with</p>
                  <div className="uploadSection">
                    <label className={`uploadBtn ${uploading ? 'disabled' : ''}`}>
                      Choose Files
                      <input
                        type="file"
                        multiple
                        accept={SUPPORTED}
                        disabled={uploading}
                        onChange={(e) => {
                          const files = Array.from(e.target.files || []);
                          setSelectedFiles((prev) => [...prev, ...files]);
                        }}
                        style={{ display: "none" }}
                      />
                    </label>

                    {selectedFiles.length > 0 && (
                      <div className="uploadFileList">
                        {selectedFiles.map((file, index) => (
                          <div key={index} className="uploadFileItem">
                            <span>📄 {file.name}</span>

                            <button
                              className="removeFileBtn"
                              onClick={() => removeFile(index)}
                              disabled={uploading}
                            >
                              ✕
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                    {uploadResults.length > 0 && (
                      <div className="uploadResults">
                        <h4>Results:</h4>
                        {uploadResults.map((res, index) => (
                          <div key={index} className={res.error ? 'error' : 'success'}>
                            {res.filename} → {res.error ? `❌ ${res.error}` : '✅ Uploaded'}
                          </div>
                        ))}
                      </div>
                    )}

                    {selectedFiles.length > 0 && (
                      <button
                        className="uploadSubmitBtn"
                        disabled={uploading}
                        onClick={handleUpload}
                      >
                        {uploading ? "Uploading..." : "Upload Files"}
                      </button>
                    )}
                  </div>
                  <button
                    className="closeBtn"
                    onClick={() => {
                      if (!uploading) {
                        setUploadOpen(false);
                        setUploadResults([]);
                        setSelectedFiles([]);
                      }
                    }} disabled={uploading}
                  >
                    ✕
                  </button>
                </div>
              </div>
            )}
            <button onClick={handleCleanup} className="cleanupBtn">Sync Library</button>
          </div>

          {files.length === 0 ? (
            <p className="emptyText">No shared files uploaded yet</p>
          ) : (
            <div className="filesTableContainer">

              <table className="filesTable">
                <thead>
                  <tr>
                    <th>File Name</th>
                    <th>Type</th>
                    <th>Size</th>
                    <th>Download</th>
                    <th>Delete</th>
                  </tr>
                </thead>
                <tbody>
                  {currentFiles.map((file) => (
                    <tr key={file.id}>
                      <td>📄 {file.filename}</td>
                      <td>{file.file_type}</td>
                      <td>{formatFileSize(file.file_size)}</td>
                      <td>
                        <button
                          className="tableBtn"
                          onClick={() => handleDownload(file.id)}
                        >
                          Download
                        </button>
                      </td>
                      <td>
                        <button
                          className="deleteFileBtn"
                          onClick={() => handleDelete(file.id)}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="paginationContainer">

                <div className="pagination">
                  <button
                    disabled={page === 1}
                    onClick={() => setPage((p) => p - 1)}
                    className="pageBtn"
                  >
                    Prev
                  </button>

                  {Array.from({ length: totalPages }, (_, i) => (
                    <button
                      key={i}
                      className={`pageBtn ${page === i + 1 ? "activePage" : ""}`}
                      onClick={() => setPage(i + 1)}
                    >
                      {i + 1}
                    </button>
                  ))}

                  <button
                    disabled={page === totalPages}
                    onClick={() => setPage((p) => p + 1)}
                    className="pageBtn"
                  >
                    Next
                  </button>
                </div>

              </div>
            </div>
          )}
        </section>

      </main>
    </div>
  );
}
