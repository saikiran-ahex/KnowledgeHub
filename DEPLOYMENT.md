# Deployment Checklist

## Pre-Deployment Checklist

### Security
- [ ] Change `JWT_SECRET_KEY` to a strong random value
- [ ] Set secure environment variables (not in code)
- [ ] Enable HTTPS/TLS for all connections
- [ ] Configure CORS to allow only specific origins
- [ ] Add rate limiting to prevent abuse
- [ ] Implement request size limits
- [ ] Add input validation and sanitization
- [ ] Enable security headers (HSTS, CSP, etc.)
- [ ] Review and update password requirements
- [ ] Consider adding 2FA for sensitive accounts

### Database
- [ ] Backup SQLite database regularly
- [ ] Consider migrating to PostgreSQL for production
- [ ] Set up database connection pooling
- [ ] Configure database backups
- [ ] Test database restore procedures
- [ ] Add database migration scripts
- [ ] Set up monitoring for database performance

### Infrastructure
- [ ] Set up proper logging (centralized)
- [ ] Configure log rotation
- [ ] Set up monitoring and alerting
- [ ] Configure health checks
- [ ] Set up auto-scaling if needed
- [ ] Configure load balancer
- [ ] Set up CDN for static assets
- [ ] Configure firewall rules

### Application
- [ ] Set appropriate file size limits
- [ ] Configure upload quotas per user
- [ ] Set up error tracking (e.g., Sentry)
- [ ] Configure proper CORS settings
- [ ] Test all endpoints thoroughly
- [ ] Load test the application
- [ ] Set up staging environment
- [ ] Document API endpoints

### Docker
- [ ] Use specific version tags (not :latest)
- [ ] Optimize Docker images for size
- [ ] Set resource limits (CPU, memory)
- [ ] Configure restart policies
- [ ] Set up Docker secrets for sensitive data
- [ ] Use multi-stage builds
- [ ] Scan images for vulnerabilities

### Qdrant
- [ ] Configure Qdrant authentication
- [ ] Set up Qdrant backups
- [ ] Monitor Qdrant performance
- [ ] Configure appropriate collection settings
- [ ] Test vector store recovery

### File Storage
- [ ] Set up file storage backups
- [ ] Consider using S3 or similar for files
- [ ] Implement file cleanup for deleted users
- [ ] Set up file virus scanning
- [ ] Configure file retention policies

## Environment Variables

### Required
```env
# OpenAI
OPENAI_API_KEY=sk-...

# JWT (MUST CHANGE)
JWT_SECRET_KEY=<generate-strong-random-key>

# Application
APP_ENV=production
APP_HOST=0.0.0.0
APP_PORT=8000
```

### Optional
```env
# Cohere (for reranking)
COHERE_API_KEY=...

# Qdrant
QDRANT_URL=http://qdrant:6333
QDRANT_API_KEY=...

# Limits
MAX_UPLOAD_SIZE_MB=30
CHUNK_SIZE=500
CHUNK_OVERLAP=100

# Models
OPENAI_MODEL=gpt-4
EMBEDDING_MODEL=text-embedding-3-large
```

## Generating Secure JWT Secret

```bash
# Python
python -c "import secrets; print(secrets.token_urlsafe(32))"

# OpenSSL
openssl rand -base64 32

# Node.js
node -e "console.log(require('crypto').randomBytes(32).toString('base64'))"
```

## Docker Compose Production Example

```yaml
version: '3.8'

services:
  backend:
    build: ./backend
    restart: always
    environment:
      - APP_ENV=production
      - JWT_SECRET_KEY=${JWT_SECRET_KEY}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
    volumes:
      - ./backend/data:/app/data
    depends_on:
      - qdrant
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 4G

  frontend:
    build: ./frontend
    restart: always
    ports:
      - "443:443"
      - "80:80"
    depends_on:
      - backend
    deploy:
      resources:
        limits:
          cpus: '1'
          memory: 512M

  qdrant:
    image: qdrant/qdrant:v1.7.4
    restart: always
    volumes:
      - ./backend/data/qdrant:/qdrant/storage
    environment:
      - QDRANT__SERVICE__API_KEY=${QDRANT_API_KEY}
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 4G
```

## Nginx Configuration for Production

```nginx
server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name yourdomain.com;

    ssl_certificate /etc/ssl/certs/cert.pem;
    ssl_certificate_key /etc/ssl/private/key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;

    # Rate limiting
    limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;
    limit_req zone=api burst=20 nodelay;

    # File upload size
    client_max_body_size 30M;

    location / {
        root /usr/share/nginx/html;
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://backend:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }
}
```

## Monitoring Setup

### Health Check Endpoints
```bash
# Backend health
curl https://yourdomain.com/api/health

# Expected: {"status":"ok"}
```

### Metrics to Monitor
- [ ] API response times
- [ ] Error rates
- [ ] Database query performance
- [ ] Vector store query times
- [ ] File upload success rate
- [ ] Authentication success/failure rate
- [ ] Active users
- [ ] Storage usage
- [ ] Memory usage
- [ ] CPU usage

### Logging
```python
# Configure structured logging
import logging
import json

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            'timestamp': self.formatTime(record),
            'level': record.levelname,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
        }
        return json.dumps(log_data)
```

## Backup Strategy

### Database Backup
```bash
# Daily backup
0 2 * * * sqlite3 /app/data/app.db ".backup '/backups/app-$(date +\%Y\%m\%d).db'"

# Keep last 30 days
find /backups -name "app-*.db" -mtime +30 -delete
```

### File Storage Backup
```bash
# Daily backup
0 3 * * * rsync -av /app/data/uploads/ /backups/uploads/

# Or use S3
aws s3 sync /app/data/uploads/ s3://your-bucket/uploads/
```

### Qdrant Backup
```bash
# Create snapshot
curl -X POST 'http://qdrant:6333/collections/rag_chunks/snapshots'

# Download snapshot
curl 'http://qdrant:6333/collections/rag_chunks/snapshots/{snapshot_name}' \
  --output snapshot.tar
```

## Testing Before Deployment

### Unit Tests
```bash
cd backend
pytest tests/
```

### Integration Tests
```bash
# Test authentication
curl -X POST http://localhost:8001/register \
  -H "Content-Type: application/json" \
  -d '{"username":"test","password":"test123"}'

# Test file upload
curl -X POST http://localhost:8001/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@test.pdf"

# Test chat
curl -X POST http://localhost:8001/chat \
  -H "Authorization: Bearer $TOKEN" \
  -F "question=What is this about?"
```

### Load Testing
```bash
# Using Apache Bench
ab -n 1000 -c 10 -H "Authorization: Bearer $TOKEN" \
  http://localhost:8001/health

# Using wrk
wrk -t12 -c400 -d30s http://localhost:8001/health
```

## Post-Deployment

### Immediate
- [ ] Verify all services are running
- [ ] Test authentication flow
- [ ] Test file upload
- [ ] Test chat functionality
- [ ] Check logs for errors
- [ ] Verify backups are working
- [ ] Test monitoring alerts

### First Week
- [ ] Monitor error rates
- [ ] Check performance metrics
- [ ] Review user feedback
- [ ] Optimize slow queries
- [ ] Adjust resource limits if needed

### Ongoing
- [ ] Regular security updates
- [ ] Database maintenance
- [ ] Log analysis
- [ ] Performance optimization
- [ ] User feedback incorporation
- [ ] Feature updates

## Rollback Plan

If deployment fails:

1. **Stop new services**
   ```bash
   docker compose down
   ```

2. **Restore database backup**
   ```bash
   cp /backups/app-YYYYMMDD.db /app/data/app.db
   ```

3. **Restore file storage**
   ```bash
   rsync -av /backups/uploads/ /app/data/uploads/
   ```

4. **Deploy previous version**
   ```bash
   git checkout <previous-tag>
   docker compose up -d
   ```

5. **Verify rollback**
   - Test authentication
   - Test file access
   - Check logs

## Support Contacts

- Infrastructure: [contact]
- Database: [contact]
- Application: [contact]
- Security: [contact]

## Documentation Links

- API Documentation: http://yourdomain.com/api/docs
- User Guide: [link]
- Admin Guide: [link]
- Troubleshooting: [link]
