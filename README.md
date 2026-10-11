# AskMyFile – Secure File Sharing

A full-stack Django web application for securely sharing files via unique links.
Files are **encrypted in the browser** (AES-256-GCM) before upload.
The server only ever stores ciphertext — it never sees file contents or the passphrase.

## Features

- 🔐 Client-side AES-256-GCM encryption via the Web Crypto API
- 🔑 PBKDF2-HMAC-SHA-256 key derivation (600 000 iterations by default)
- 👤 Guest sharing (1 link/day) with a forced quota overlay (no close button)
- ✅ Full registration flow: email verification + SMS OTP
- 🏠 Authenticated Home page mirroring the landing page design
- 📊 Server-enforced daily quotas with atomic counters
- 💳 Subscription plans (Free / Starter / Plus / Pro) with webhook verification
- 🧹 Cleanup management command for expired/revoked shares
- 🌙 Dark, high-contrast responsive UI (320 px → 1440 px+)

## Quick Start (Development)

```bash
# 1. Clone / open the project
cd AskMyFile

# 2. Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy environment template
copy .env.example .env
# Edit .env if needed (DEBUG=True and console email work out of the box)

# 5. Apply migrations
python manage.py migrate

# 6. Seed subscription plans
python manage.py seed_plans

# 7. Create a superuser (optional)
python manage.py createsuperuser

# 8. Run the development server
python manage.py runserver
```

Then open http://127.0.0.1:8000/ in your browser.

## Project Structure

```
AskMyFile/
├── AskMyFile/          # Django project config (settings, urls, wsgi)
├── accounts/           # Custom User model, email/SMS verification, auth
├── shares/             # Share creation, quota, ciphertext storage, cleanup
├── billing/            # Plans, subscriptions, payment webhooks
├── core/               # Landing page, authenticated home, static pages
├── templates/          # All HTML templates
├── static/
│   ├── css/main.css    # Dark theme design tokens + full stylesheet
│   └── js/
│       ├── crypto.js   # AES-GCM + PBKDF2 encryption/decryption
│       ├── uploader.js # Upload flow + guest overlay
│       └── decryptor.js# Recipient download + decrypt flow
├── private_uploads/    # Local dev storage (gitignored)
├── .env.example        # Environment variable reference
├── THIRD_PARTY_NOTICES.md
└── requirements.txt
```

## Management Commands

```bash
# Delete expired/revoked ciphertext from storage
python manage.py cleanup_expired_shares

# Dry run (preview without deleting)
python manage.py cleanup_expired_shares --dry-run

# Re-seed / update subscription plans
python manage.py seed_plans
```

## Key URLs

| URL | Description |
|---|---|
| `/` | Public landing page (guest share flow) |
| `/app/` | Authenticated home (requires verified account) |
| `/accounts/register/` | Registration |
| `/accounts/login/` | Sign in |
| `/accounts/verify-phone/` | SMS OTP verification |
| `/s/<token>/` | Share recipient page |
| `/s/<token>/download/` | Ciphertext download |
| `/billing/plans/` | Subscription plan listing |
| `/admin/` | Django admin |
| `/health/` | Health check |

## Security Notes

- Passphrases **never leave the browser** and are not logged, stored, or transmitted.
- A lost passphrase cannot be recovered — the server does not have it.
- Guest quotas are best-effort (cookie + rate limiting) and cannot guarantee per-human enforcement.
- This project has not been independently audited as production-grade cryptographic software.
- Run `python manage.py check --deploy` before any production deployment.

## Production Checklist

- [ ] `DEBUG=False` in environment
- [ ] Strong random `SECRET_KEY`
- [ ] `ALLOWED_HOSTS` set correctly
- [ ] HTTPS configured; secure cookie flags enabled automatically when `DEBUG=False`
- [ ] Switch to PostgreSQL (`DATABASE_URL`)
- [ ] Configure email provider (`EMAIL_BACKEND`, `EMAIL_HOST`, etc.)
- [ ] Configure SMS provider (`SMS_PROVIDER_KEY`)
- [ ] Configure payment provider (`PAYMENT_SECRET_KEY`, `PAYMENT_WEBHOOK_SECRET`)
- [ ] Use private persistent object storage for `private_uploads/`
- [ ] Schedule `python manage.py cleanup_expired_shares` (cron / task queue)
- [ ] Run `python manage.py collectstatic`
