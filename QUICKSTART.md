# Quick Start Guide

## Getting Started with KnowledgeHub

### 1. Start the Application

```bash
docker compose up -d --build backend frontend
```

Wait for the backend to complete initialization (check logs):
```bash
docker compose logs -f backend
```

Look for: `Startup warmup completed`

### 2. Access the Application

Open your browser and navigate to:
```
http://localhost:8502
```

### 3. Create Your Account

On the login page:
1. Click "Don't have an account? Register"
2. Enter a username (minimum 3 characters)
3. Enter a password (minimum 6 characters)
4. Click "Register"

You'll be automatically logged in after registration.

### 4. Upload Your First Files

1. Click "📁 Upload Files" in the sidebar
2. Click the file input and select one or more files
3. Supported formats:
   - Documents: `.txt`, `.md`, `.pdf`, `.doc`, `.docx`, `.csv`
   - Images: `.png`, `.jpg`, `.jpeg`, `.webp`
4. Click "Upload and Index"
5. Wait for indexing to complete

### 5. Start Chatting

1. Click on a conversation in the sidebar (or the "New Chat" button creates one)
2. Type your question in the input box
3. Press Enter or click the send button
4. The AI will answer based on your uploaded documents

### 6. Manage Your Files

1. Click "📂 My Files" in the sidebar
2. View all your uploaded files with metadata
3. Delete files you no longer need (this removes the file and all its vectors)

## Tips

### Chat Features
- **Ad-hoc Files**: Click the 📎 icon to attach a file to a single message (not permanently indexed)
- **Conversation History**: Your chat history is maintained within each conversation
- **Multiple Chats**: Create multiple conversations for different topics

### File Management
- Each file is chunked and embedded into the vector database
- You can see how many chunks were created for each file
- Deleting a file removes it from storage and the vector database
- Only you can see and access your files

### Example Questions
- "What are the key points in the documents?"
- "Summarize the main findings"
- "What insights can you provide?"
- "Explain the technical details"

## Troubleshooting

### Can't Login?
- Make sure backend is fully started (check logs)
- Verify username and password are correct
- Try registering a new account

### Upload Fails?
- Check file size (max 30MB by default)
- Verify file type is supported
- Check backend logs for errors

### No Results in Chat?
- Make sure files have been uploaded and indexed first
- Check that files were successfully indexed (chunks > 0)
- Try rephrasing your question

### 502 Error?
- Backend is still warming up
- Wait 30-60 seconds and try again
- Check backend logs for completion

## Environment Setup

Make sure your `.env` file has:
```env
# Required
OPENAI_API_KEY=your-openai-key

# Optional but recommended
JWT_SECRET_KEY=your-secret-key-here
COHERE_API_KEY=your-cohere-key  # For better reranking
```

## Next Steps

- Upload more documents to build your knowledge base
- Experiment with different types of questions
- Try uploading images and asking about them
- Create multiple conversations for different topics
- Explore the API documentation at `http://localhost:8001/docs`

## Need Help?

- Check the main README.md for detailed documentation
- Review MIGRATION.md for technical details
- Check backend logs: `docker compose logs backend`
- Check frontend logs: `docker compose logs frontend`
