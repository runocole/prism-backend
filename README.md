# PRISM Backend

> Recruitment and proctored assessment REST API

Built with Django 6, Django REST Framework, and SQLite.

## Features

- **Job postings** — create jobs with unique application slugs
- **Public applications** — candidates submit CV, cover letter, screening answers
- **Screening pipeline** — blacklist check, CV strength (Claude reads the PDF/Word CV and scores it 0-100), preferred-answer filtering; runs in the background
- **Blacklist management** — phone-based matching with fuzzy name fallback
- **Test builder** — MCQ and short-answer questions, import questions from a PDF or Word test paper (Claude)
- **Invite system** — UUID tokens, expiry, batch invites
- **Proctored sessions** — webcam recordings, violation tracking, timer sync
- **Results** — answer scoring, manual review, saved pass/fail/hold decision

## Stack

- Django 6 + Django REST Framework
- SimpleJWT authentication
- SQLite (dev) / PostgreSQL (prod)
- Gunicorn + Nginx
- Anthropic API (Claude) for CV screening and question import

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
| `ANTHROPIC_API_KEY` | Anthropic API key — CV screening and question import |
| `CV_PASS_SCORE` | Minimum AI CV score (0-100) to screen in, default 50 |
| `FRONTEND_URL` | Used in invite links, default https://screening.oticgs.com |
| `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | SMTP login for invite emails |
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