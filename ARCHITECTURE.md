# HostKit Platform Architecture

## Quick Reference

HostKit is a VPS-native deployment platform where each project = Linux user + home directory + systemd service + assigned port.

```
Project "myapp":
├── Linux user: myapp
├── Home: /home/myapp/
├── Service: hostkit-myapp (systemd)
├── Port: 8001 (assigned, in .env as PORT)
├── Domain: myapp.hostkit.dev (auto, SSL included)
└── URL: https://myapp.hostkit.dev
```

---

## Project Provisioning Lifecycle

### Phase 1: Linux System Setup (steps 1-6)

1. **Name validation**: 3-32 chars, lowercase alphanumeric + hyphens, starts with letter, ends with letter/number
   - Valid: `my-app`, `api-service`, `web-app-v2`
   - Invalid: `My_App`, `MYAPP`, `my_app`, `my--app`

2. **Create Linux user**: `useradd {project}` with home at `/home/{project}`

3. **Create directory structure**:
   ```
   /home/{project}/
   ├── app/                    # Deployed code (symlink to releases/current/)
   ├── releases/               # Release history (for rollbacks)
   ├── shared/                 # Persistent data
   ├── .env                    # Environment variables
   └── venv/ or node_modules/  # Dependencies

   /var/log/projects/{project}/
   ├── app.log                 # stdout
   └── error.log               # stderr
   ```

4. **Assign port**: Allocated from range (typically 8001+)
5. **Assign Redis DB**: Dedicated database 0-15 in shared Redis
6. **Create .env file** with initial variables:
   ```bash
   PROJECT_NAME={project}
   PORT={assigned_port}
   HOST=127.0.0.1
   REDIS_URL=redis://localhost:6379/{db_number}
   CELERY_BROKER_URL=redis://localhost:6379/{db_number}
   CELERY_RESULT_BACKEND=redis://localhost:6379/{db_number}
   # DATABASE_URL added later when DB enabled
   ```

### Phase 2: Systemd Service Setup (steps 7-8)

7. **Generate systemd service** at `/etc/systemd/system/hostkit-{project}.service`:
   ```ini
   [Unit]
   Description=HostKit Project: {project}
   After=network.target

   [Service]
   Type=simple
   User={project}
   Group={project}
   WorkingDirectory=/home/{project}/app  # For Node/Next.js
   EnvironmentFile=/home/{project}/.env
   ExecStart=/usr/bin/npm start          # Next.js example
   Restart=always
   RestartSec=5
   StandardOutput=append:/var/log/projects/{project}/app.log
   StandardError=append:/var/log/projects/{project}/error.log

   [Install]
   WantedBy=multi-user.target
   ```

8. **Create log directory** with proper ownership

### Phase 3: Access Control Setup (steps 9-10)

9. **Generate sudoers rules** at `/etc/sudoers.d/hostkit-{project}` for project-scoped CLI access
10. **Add operator SSH keys** for CI/CD access

### Phase 4: Database Registration (steps 11-12)

11. **Register in database** with metadata (name, runtime, port, created_by, etc.)
12. **Auto-register hostkit.dev subdomain**: `{project}.hostkit.dev` with SSL pre-provisioned

### Phase 5: Networking Setup (step 13)

13. **Regenerate Nginx port mappings** for wildcard routing

### Service Steps (default ON with `provision`, opt-in with `project create`)

The `provision` command enables these by default. Use `--no-db`, `--no-auth`, `--no-storage` to opt out.
The low-level `project create` command still requires `--with-db`, `--with-auth`, `--with-storage` flags.

| Service | `provision` default | `project create` flag | Action |
|---------|--------------------|-----------------------|--------|
| PostgreSQL | ON | `--with-db` | Create database, set `DATABASE_URL` |
| Auth | ON | `--with-auth` | Enable auth service on port `{base_port}+1000` |
| Storage | ON | `--with-storage` | Create MinIO bucket, set `S3_*` vars |
| Secrets | OFF | `--with-secrets` | Inject secrets from vault into `.env` |
| Domain | OFF | `--domain {domain}` | Add custom domain via Nginx |
| SSL | OFF | `--ssl` | Provision Let's Encrypt certificate |
| Source | OFF | `--source {path}` | Deploy initial code on creation |

---

## Supported Runtimes

| Runtime | Working Dir | Start Command | Entry Point | Package Manager |
|---------|------------|---------------|------------|-----------------|
| `python` | `/home/{project}` | `venv/bin/python -m app` | `app/` module | pip |
| `node` | `/home/{project}/app` | `/usr/bin/node app/index.js` | `app/index.js` | npm/yarn |
| `nextjs` | `/home/{project}/app` | `/usr/bin/npm start` | `npm start` script | npm |
| `static` | `/home/{project}/app` | `/bin/true` | Nginx serves directly | N/A |

---

## Directory Layout After Deployment

```
/home/{project}/
├── releases/                    # Release history
│   ├── 20250215-120000/        # Timestamped release
│   ├── 20250214-150000/
│   └── ...
├── app → releases/20250215-120000/  # Symlink to current (atomic swap)
├── shared/                      # Persistent data (user-created)
├── .env                         # Environment variables (loaded by systemd)
├── .auth/                       # Auth service (if enabled)
├── .payments/                   # Payments service (if enabled)
└── venv/ or node_modules/       # Dependencies

/var/log/projects/{project}/
├── app.log                      # Process stdout (append mode)
└── error.log                    # Process stderr (append mode)

/etc/systemd/system/
├── hostkit-{project}.service    # Main service
├── hostkit-{project}-auth.service      # Optional
├── hostkit-{project}-payments.service  # Optional
└── ...

/etc/nginx/sites-available/
└── hostkit-{project}            # Reverse proxy config
```

---

## Service Port Mapping

Each service gets a port offset from the base project port:

| Service | Offset | Example (base 8001) | Purpose |
|---------|--------|---------------------|---------|
| Main app | +0 | 8001 | Your Next.js app |
| Auth | +1000 | 9001 | Authentication service |
| Payments | +2000 | 10001 | Stripe integration |
| SMS | +3000 | 11001 | Twilio SMS |
| Booking | +4000 | 12001 | Calendar/booking |
| Chatbot | +5000 | 13001 | Claude/OpenAI chatbot |
| Voice | 8900 (central) | 8900 | Twilio voice |

---

## Database Integration

### PostgreSQL (with --with-db)

When `--with-db` flag is used:
1. Database created with name `{project}_db`
2. PostgreSQL role created for project user
3. `DATABASE_URL` set in `.env`:
   ```
   postgresql://{user}:{password}@localhost:5432/{project}_db
   ```
4. Migrations auto-discovered and run on deploy (if using supported tools)
5. Backup/restore via `hostkit backup` commands

### Redis (automatic)

Every project gets a dedicated Redis database:
- **REDIS_URL**: `redis://localhost:6379/{db_number}` (auto in .env)
- **Use cases**: Caching, session store, job queue, real-time data
- **Data persistence**: All data persists across deploys and restarts
- **Database isolation**: Projects can't access other projects' Redis databases

---

## Nginx Reverse Proxy

### How It Works

1. **HTTPS termination**: Listens on 443 (and 80 → 301 redirect)
2. **Reverse proxy**: Routes to `127.0.0.1:{project_port}`
3. **Service-aware routing**: Specific paths routed to service ports (if enabled)
4. **Security headers**: Added automatically

### Request Flow

```
HTTPS Request → nginx (443)
  ├─ /auth/* → Auth service (port +1000) if enabled
  ├─ /payments/* → Payments service (port +2000) if enabled
  ├─ /api/sms/* → SMS service (port +3000) if enabled
  └─ /* → Main app (base port)
```

### Domain Registration

- **Default**: `{project}.hostkit.dev` auto-created at project creation
- **Custom**: Add via `hostkit nginx add {project} {custom.domain}`
- **SSL**: Automatically provisioned with Let's Encrypt
- **Renewal**: Certbot auto-renewal configured

### Configuration Example

```nginx
server {
    listen 443 ssl http2;
    server_name myapp.hostkit.dev;

    ssl_certificate /etc/letsencrypt/live/myapp.hostkit.dev/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/myapp.hostkit.dev/privkey.pem;

    # Auth service (if enabled)
    location ~ ^/auth/(signup|signin|...)/? {
        proxy_pass http://127.0.0.1:9001;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Main app
    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

---

## Node.js Runtime Environment

### Available Tools

- **Node.js**: `/usr/bin/node` (version: check with `node -v` on VPS)
- **npm**: `/usr/bin/npm` (installed with Node)
- **yarn**: Available if needed (not standard)

### Environment Variables Available

At process start, these vars from `/home/{project}/.env` are available:

```bash
PORT={assigned_port}
HOST=127.0.0.1
PROJECT_NAME={project_name}
REDIS_URL=redis://localhost:6379/{db_number}
CELERY_BROKER_URL=redis://localhost:6379/{db_number}
CELERY_RESULT_BACKEND=redis://localhost:6379/{db_number}
# Plus any custom vars in .env
```

**Note**: System environment is NOT inherited. Only vars explicitly in `.env` are available to the process.

---

## What Persists Across Deploys

| Resource | Persists | Details |
|----------|----------|---------|
| `/home/{project}/shared/` | ✅ | User-created persistent storage |
| Database | ✅ | PostgreSQL data (if --with-db) |
| Redis | ✅ | Cache/session data |
| `.env` file | ✅ | Can be overwritten by new deploy |
| Logs | ✅ | Append mode, can grow large |
| Old releases | ❌ | Last 5 kept, older ones deleted |
| `node_modules/` | ❌ | Replaced if `--install` used |
| `.next/` build cache | ❌ | Rebuilt on every build |

---

## Links to Detailed Documentation

- **[Deployment Process →](DEPLOYMENT.md)** - Full build & deploy pipeline
- **[Environment Configuration →](ENVIRONMENT.md)** - How env vars are managed
- **[Next.js Specifics →](NEXTJS.md)** - Build output, config, requirements
- **[Troubleshooting →](TROUBLESHOOTING.md)** - Common issues & solutions
