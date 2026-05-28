# PRISM Backend

> Recruitment and proctored assessment REST API

Built with Django 6, Django REST Framework, and SQLite.

## Features

- **Job postings** — create jobs with unique application slugs
- **Public applications** — candidates submit CV, cover letter, screening answers
- **AI screening pipeline** — blacklist check, CV strength (Groq/Llama), MCQ filtering
- **Blacklist management** — phone-based matching with fuzzy name fallback
- **Test builder** — MCQ and short-answer questions, PDF parser
- **Invite system** — UUID tokens, expiry, batch invites
- **Proctored sessions** — webcam recordings, violation tracking, timer sync
- **Results** — answer scoring, manual review, pass/fail

## Stack

- Django 6 + Django REST Framework
- SimpleJWT authentication
- SQLite (dev) / PostgreSQL (prod)
- Gunicorn + Nginx
- Groq API (Llama 3.3 70B) for CV screening

## Setup

```bash
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
python manage.py migrate
python seed.py
python manage.py runserver
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Django secret key |
| `DEBUG` | True/False |
| `ALLOWED_HOSTS` | Comma-separated hosts |
| `CORS_ALLOWED_ORIGINS` | Frontend URL |
| `GROK_API_KEY` | Groq API key for CV screening |
| `INVITE_EXPIRY_HOURS` | Hours before invite expires |

## Production

- Backend: `https://pas-backend.oticgs.com`
- Served via Gunicorn Unix socket + Nginx
- SSL via Let's Encrypt

## Deploy update

```bash
cd /var/www/pas-backend
git pull origin main
source venv/bin/activate
python manage.py migrate
systemctl restart pas-backend
```