# BridgeSpace

AI-backed conflict mediation platform built with Flask, SQLAlchemy, SocketIO and Ollama Cloud.

## Features

- **Auth** – Registration, login, email verification, password reset, preferences (including WhatsApp/Telegram/Signal), delete account; optional reCAPTCHA on public forms
- **Mediation** – Request or create mediations; 5 phases (pre-mediation → perspectives → agenda → proposals → agreement); invite by email/SMS; join via token
- **Pre-mediation** – Mediator explains the process; participants can only mark as read after that; “Ask mediator for an explanation” notifies mediator; payments (Stripe)
- **Mediator selection** – First: random (fewer assignments); 48h to confirm; second by ranking if timeout; escalate to admins if both time out
- **Notifications** – Email + optional WhatsApp, Telegram, Signal (multi-channel per user; backoffice config for superadmin)
- **Mediator metrics** – Mediators see own; admins see all: mediations opened, agreements reached, explanation & confirmation response times
- **AI** – NVC reformulation, agenda suggestions, agreement drafting; translation; voice dictation (Web Speech API)
- **Search** – Anonymised precedent search by case description and filters
- **Backoffice** – Payment settings, messaging integrations (superadmin), users & roles, mediations list, mediator metrics

→ Full list: **[FEATURES.md](FEATURES.md)**

## Setup

1. Clone the repo
   git clone https://github.com/diogocsc/bridgespace.git
   cd bridgespace

2. Create virtual environment
   python -m venv venv
   source venv/bin/activate   # Windows: venv\Scripts\activate

3. Install dependencies
   pip install -r requirements.txt

4. Configure environment
   cp .env.example .env
   # Edit .env and add your API keys

5. Run
   python app.py

### Mediator 48h confirmation (cron)
To process mediator availability timeouts (reassign or escalate to admins after 48h), run periodically (e.g. hourly):
```bash
flask process-mediator-timeouts
```

## Tech stack
- Backend: Flask, Flask-SQLAlchemy, Flask-Login, Flask-SocketIO
- AI: Ollama Cloud (gpt-oss:120b)
- Database: SQLite (dev) / PostgreSQL (prod)
- Frontend: Jinja2 templates, vanilla JS, SocketIO