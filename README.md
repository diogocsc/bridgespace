# BridgeSpace

AI-backed conflict mediation platform built with Flask, SQLAlchemy, SocketIO and Ollama Cloud.

## Features
- Secure user registration and authentication
- Create live or asynchronous mediations
- AI-powered post reformulation (non-violent communication)
- Automatic translation of posts to each party's language
- Voice dictation input
- Email and SMS invitations
- Search anonymised past agreements for precedents

## Setup

1. Clone the repo
   git clone https://github.com/YOUR_USERNAME/bridgespace.git
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

## Tech stack
- Backend: Flask, Flask-SQLAlchemy, Flask-Login, Flask-SocketIO
- AI: Ollama Cloud (gpt-oss:120b)
- Database: SQLite (dev) / PostgreSQL (prod)
- Frontend: Jinja2 templates, vanilla JS, SocketIO