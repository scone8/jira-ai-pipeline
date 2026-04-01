# Jira AI Issue Processor

An event-driven pipeline that automatically enriches Jira issues using a local AI model.
No API keys. No cloud AI. Everything runs on your machine.

<img width="1536" height="1024" alt="Copilot_20260401_183042" src="https://github.com/user-attachments/assets/248099ba-9ba3-4441-8295-eb8aac89c2f6" />

---

## How It Works

You create a Jira issue and type **everything into the title** — all the detail, urgency, who should fix it, and any context. The pipeline does the rest automatically:

```
Jira Issue Created
       ↓
Django receives webhook (POST /jira-webhook/)
       ↓
Kafka topic: jira.issues.created
       ↓
Consumer 1 — classifier.py (Ollama local LLM)
  • Writes a clean short title
  • Writes a proper description
  • Generates a resolution timeline
  • Sets priority: Highest / High / Medium / Low / Lowest
  • Extracts assignee name if mentioned
  • Saves original title at the bottom
       ↓
Kafka topic: jira.issues.classified
       ↓
Consumer 2 — jira_updater.py
  • Updates the Jira issue via REST API
  • Sets title, description, priority, assignee
```

### Example

**You type this as the issue title:**
```
Need to fix the payment issue so everyone gets paid before tomorrow.
Ethan should do it by EOD. Reset the server and clear the logs.
```

**Jira is automatically updated to:**

| Field | Result |
|---|---|
| Title | `Payment processing failure blocking payroll` |
| Priority | Highest |
| Assignee | Ethan |
| Description | AI-written professional description |

**Description body in Jira:**
```
── Description ──────────────────────────────────────
The payment processing system is failing, preventing payroll
from being completed before tomorrow's deadline. The issue is
believed to be caused by a log overflow requiring a server
reset and log clear to resolve.

── Resolution Timeline ──────────────────────────────
1. Immediate (0-1h): Alert Ethan, verify server and log status
2. Investigation (1-2h): Confirm log issue is root cause
3. Fix & Deploy (2-4h): Reset server, clear logs, verify payments
4. Follow-up (next day): Monitor payment system and add log rotation

── Original Issue Title ─────────────────────────────
Need to fix the payment issue so everyone gets paid before
tomorrow. Ethan should do it by EOD. Reset the server and
clear the logs.
```

---

## File Structure

```
jira-ticket-priority/
├── docker-compose.yml          # Kafka + Zookeeper + Ollama
├── requirements.txt
├── .env                        # Your secrets — never commit this
├── .env.example                # Safe template — copy to .env
├── .gitignore
├── django_app/
│   ├── manage.py
│   ├── config/
│   │   ├── settings.py
│   │   ├── urls.py
│   │   └── wsgi.py
│   └── webhook/
│       ├── views.py            # POST /jira-webhook/ — receives Jira events
│       ├── kafka_producer.py   # Publishes to jira.issues.created
│       └── urls.py
├── consumers/
│   ├── classifier.py           # AI enrichment via local Ollama model
│   └── jira_updater.py         # Writes enriched data back to Jira REST API
└── README.md
```

---

## Prerequisites

- Docker + Docker Compose
- Python 3.9+
- A Jira Cloud account with an API token

---

## Setup (one-time)

### 1 — Create a Python virtual environment

```bash
cd /Users/alecowens/Desktop/code/jira-ticket-priority
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Activate the venv every time you open a new terminal for this project:
```bash
source .venv/bin/activate
```

---

### 2 — Configure `.env`

```bash
cp .env.example .env
```

Open `.env` and fill in your Jira credentials:

```
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=llama3.2

JIRA_URL=https://your-domain.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=...
```

Get your Jira API token at:
[https://id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)

> `.env` is in `.gitignore` and will never be committed.

---

### 3 — Start Docker services

```bash
docker compose up -d
```

Starts `zookeeper`, `kafka`, and `ollama`. Wait ~15 seconds then check all three are up:

```bash
docker ps
```

---

### 4 — Pull the AI model (one-time, ~2 GB download)

```bash
docker exec ollama ollama pull llama3.2
```

Wait for the download to finish, then verify:
```bash
docker exec ollama ollama list
```

If you have less than ~3 GB of free RAM, use a smaller model instead:
```bash
docker exec ollama ollama pull qwen2.5:0.5b
# then set OLLAMA_MODEL=qwen2.5:0.5b in your .env
```

---

### 5 — Create Kafka topics (one-time)

```bash
docker exec kafka kafka-topics \
  --create --topic jira.issues.created \
  --bootstrap-server localhost:9092 \
  --partitions 1 --replication-factor 1

docker exec kafka kafka-topics \
  --create --topic jira.issues.classified \
  --bootstrap-server localhost:9092 \
  --partitions 1 --replication-factor 1
```

---

## Running the Pipeline

You need 4 terminals open at the same time.

**Terminal 1 — Django**
```bash
source .venv/bin/activate
cd django_app
python manage.py runserver
```

**Terminal 2 — Classifier**
```bash
source .venv/bin/activate
python consumers/classifier.py
```

**Terminal 3 — Jira Updater**
```bash
source .venv/bin/activate
python consumers/jira_updater.py
```

**Terminal 4 — Test webhook (optional)**
```bash
curl -X POST http://localhost:8000/jira-webhook/ \
  -H "Content-Type: application/json" \
  -d '{
    "issue": {
      "key": "KAN-1",
      "fields": {
        "summary": "URGENT payment gateway is down, assign to Sarah, affects all EU customers, losing money every minute",
        "description": ""
      }
    }
  }'
```

Expected response: `{"status": "accepted"}`

---

## Connecting to Real Jira Webhooks (ngrok)

To have Jira trigger the pipeline automatically when a real issue is created:

**1. Start ngrok:**
```bash
ngrok http 8000 --request-header-add "ngrok-skip-browser-warning: true"
```

Copy the `https://....ngrok-free.app` URL.

**2. Add the webhook in Jira:**
- Go to `https://your-domain.atlassian.net` → Settings → System → Webhooks
- Click **Create a WebHook**
- URL: `https://your-ngrok-url.ngrok-free.app/jira-webhook/`
- Events: tick **Issue → created** only
- Click **Create**

Now every new Jira issue you create will flow through the full pipeline automatically.

> The ngrok URL changes each time you restart it on the free plan. Update the Jira webhook URL to match.

---

## Priority Classification Rules

| Priority | Triggers |
|---|---|
| **Highest** | Payments / money / revenue affected; hard deadline today or EOD; words like urgent/ASAP/critical |
| **High** | Many users affected; needs fixing today or tomorrow; important feature broken |
| **Medium** | Some users affected; no hard deadline; inconvenient but not blocking |
| **Low** | Minor issue; no deadline; low impact |
| **Lowest** | Cosmetic or trivial; no user impact |

---

## Expected Log Output

**Classifier terminal:**
```
INFO Processing issue KAN-1
INFO Short title: Payment gateway failing for EU customers
INFO Priority: HIGHEST
INFO Description generated (201 chars)
INFO Timeline generated (198 chars)
INFO Assignee extracted: Sarah
INFO Delivered to jira.issues.classified [partition 0]
```

**Jira updater terminal:**
```
INFO Jira auth OK — connected as Alec Owens (you@example.com)
INFO Received parsed issue KAN-1
INFO Found assignee: Sarah Jones → accountId=...
INFO Updating KAN-1 — title='Payment gateway failing...' priority=Highest assignee=Sarah
INFO Successfully updated KAN-1
```

---

## Stopping Everything

```bash
# Ctrl+C in each terminal (Django, classifier, updater)

# Stop Docker:
docker compose down

# Stop Docker and delete downloaded models (frees disk space):
docker compose down -v
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `model not found` | Run `docker exec ollama ollama pull llama3.2` |
| `requires more memory than available` | Pull a smaller model: `ollama pull qwen2.5:0.5b` and update `OLLAMA_MODEL` in `.env` |
| `Jira auth FAILED (401)` | Check `JIRA_EMAIL` exactly matches your Atlassian account email |
| `Issue not found (404)` | The issue key doesn't exist in your Jira — use a real key |
| ngrok `502 Bad Gateway` | Django isn't running, or restart ngrok with `--request-header-add "ngrok-skip-browser-warning: true"` |
| Priority too low | Restart classifier after any `.env` or code changes |
| Stale Kafka messages replaying | Reset offsets: `docker exec kafka kafka-consumer-groups --bootstrap-server localhost:9092 --group classifier-group --topic jira.issues.created --reset-offsets --to-latest --execute` |
