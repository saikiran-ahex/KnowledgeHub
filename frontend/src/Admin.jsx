import { useState, useEffect } from 'react';

const API_BASE = '/api';
const SUPPORTED = '.txt,.md,.pdf,.doc,.docx,.csv,.png,.jpg,.jpeg,.webp';
const MAX_UPLOAD_SIZE_MB = 30;

async function readApiResponse(res) {
  const contentType = res.headers.get('content-type') || '';
  if (contentType.includes('application/json')) return res.json();
  return { detail: await res.text() };
}

export default function Admin() {
  const [token, setToken] = useState(localStorage.getItem('token'));
  const [username, setUsername] = useState(localStorage.getItem('username') || '');
  const [isAdmin, setIsAdmin] = useState(localStorage.getItem('is_admin') === 'true');
  const [files, setFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [loginUsername, setLoginUsername] = useState('');
  const [loginPassword, setLoginPassword] = useState('');
  const [authError, setAuthError] = useState('');

  useEffect(() => {
    if (token && isAdmin) loadFiles();
  }, [token, isAdmin]);

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

  async function handleUpload(e) {
    const file = e.target.files?.[0];
    if (!file) return;

    setUploading(true);
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
      
      alert(`Uploaded: ${data.filename} (${data.chunks_indexed} chunks)`);
      await loadFiles();
    } catch (err) {
      alert(`Error: ${err.message || err}`);
    } finally {
      setUploading(false);
      e.target.value = '';
    }
  }

  async function handleDelete(fileId) {
    if (!confirm('Delete this file?')) return;
    try {
      const res = await fetch(`${API_BASE}/files/${fileId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await readApiResponse(res);
      if (!res.ok) throw new Error(data.detail || 'Delete failed');
      
      alert('File deleted successfully');
      await loadFiles();
    } catch (err) {
      alert(`Error: ${err.message || err}`);
    }
  }

  async function handleCleanup() {
    if (!confirm('Clean orphaned vectors?')) return;
    try {
      const res = await fetch(`${API_BASE}/files/cleanup-vectors`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await readApiResponse(res);
      if (!res.ok) throw new Error(data.detail || 'Cleanup failed');
      
      alert(data.message || 'Cleanup completed');
      await loadFiles();
    } catch (err) {
      alert(`Error: ${err.message || err}`);
    }
  }

  if (!token || !isAdmin) {
    return (
      <div className="loginPage">
        <div className="loginCard">
          <h1>🔐 Admin Panel</h1>
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
      <header className="adminHeader">
        <h1>🔐 Admin Panel</h1>
        <div className="adminHeaderActions">
          <span>👤 {username}</span>
          <button onClick={logout} className="headerBtn">Logout</button>
        </div>
      </header>

      <main className="adminMain">
        <section className="adminSection">
          <h2>Upload Files</h2>
          <p>Upload files that all users can query</p>
          <label className="uploadBtn">
            {uploading ? 'Uploading...' : 'Choose File'}
            <input
              type="file"
              accept={SUPPORTED}
              onChange={handleUpload}
              disabled={uploading}
              style={{ display: 'none' }}
            />
          </label>
        </section>

        <section className="adminSection">
          <div className="sectionHeader">
            <h2>📁 My Files ({files.length})</h2>
            <button onClick={handleCleanup} className="cleanupBtn">Clean Orphaned Vectors</button>
          </div>
          
          {files.length === 0 ? (
            <p className="emptyText">No files uploaded yet</p>
          ) : (
            <div className="fileList">
              {files.map((file) => (
                <div key={file.id} className="fileItem">
                  <div className="fileInfo">
                    <span className="fileName">📄 {file.filename}</span>
                    <span className="fileDetails">
                      {file.file_type} • {new Date(file.uploaded_at).toLocaleDateString()}
                    </span>
                  </div>
                  <button onClick={() => handleDelete(file.id)} className="deleteFileBtn">Delete</button>
                </div>
              ))}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
