# BridgeSpace – Implemented features

AI-backed conflict mediation platform. This document lists all currently implemented features.

---

## Authentication & user management

- **Registration** – Sign up with email, username, display name; optional “register as mediator”.
- **Login / logout** – Session-based auth with “remember me”.
- **Email verification** – Verification link sent on registration (48h validity).
- **Forgot / reset password** – Email link to set a new password (1h validity).
- **Preferences** – Display name, anonymous alias, phone, **WhatsApp**, **Telegram**, **Signal**, preferred language, allow case sharing, become mediator, change password.
- **Delete account** – User can delete their account (PII removed, mediation content anonymised).
- **Roles** – `user`, `mediator`, `admin`, `superadmin`. Admins manage users and settings; superadmins also access integration settings.

---

## Mediation workflow

### Mediation types

- **Structured** – The default phased process: pre-mediation → perspectives → agenda → proposals → agreement. Mediator advances when conditions are met; creator can close.
- **Unstructured** – Free-flow conversation using the **posts** view (single thread). No fixed phases. The mediator can **mark a specific post as the agreement** and **close the mediation** with an **outcome** (agreement reached / agreement not reached) and **justification**. All participants are notified on close.

When requesting or creating a mediation, the user chooses **Structured** or **Unstructured**. Unstructured sessions open directly in the posts view; structured sessions follow the phase stepper.

### Requesting & creating

- **Request mediation** – Any user can request a mediation with title, description, **mediation type** (structured / unstructured), mode (async/live), optional start date, optional invitees. Mediator can be chosen automatically (load-balanced) or manually.
- **Create mediation** – Mediators and admins can create a mediation directly (no request step), with the same type choice.
- **Join via token** – Invited parties join via email/SMS link (invite token or mediation invite token).
- **Invite more parties** – At any phase (or in the unstructured view), participants can invite more people by email or phone (invitations sent by email or SMS).

### Phases (structured mediation only)

1. **Pre-mediation**
   - Mediator writes an explanation of the process and can set price per party.
   - Participants can only **mark as read** after the mediator has added an explanation.
   - **“Ask mediator for an explanation”** – If there is no explanation yet, participants see a button; the mediator is notified (email + optional WhatsApp/Telegram/Signal) with a link to add the explanation.
   - Payments (Stripe) – Standard fee, donation, or pro bono; optional extra donation.
2. **Perspectives** – Parties submit their point of view; AI can reformulate in non-violent communication (NVC) style.
3. **Agenda** – Agenda points (manual or AI-suggested from perspectives).
4. **Proposals** – Proposals per agenda point (NVC reformulation); mediator can set status (e.g. accepted).
5. **Agreement** – Draft agreement (manual or AI from accepted proposals); parties can sign.

- **Advance phase** – Mediator advances when conditions are met (e.g. all acknowledged, payments if required).
- **Close mediation** – In structured: creator can close the session (status + end date; participants notified). In unstructured: mediator closes from the posts view with outcome and justification.

### Mediator selection & 48h confirmation

- **First assignment** – Random among active mediators, favouring those selected fewer times.
- **Notification on selection** – Mediator is notified on all configured channels (email, WhatsApp, Telegram, Signal) and must **confirm availability within 48 hours** via link.
- **If not confirmed in 48h** – Mediator’s ranking is reduced; a second mediator is chosen by **ranking** (highest first); they are notified and have another 48h to confirm.
- **If second also times out** – All admins and superadmins are notified on all their configured channels (email, WhatsApp, Telegram, Signal).
- **Cron** – Run `flask process-mediator-timeouts` periodically (e.g. hourly) to process timeouts.

---

## Notifications & contact channels

- **Email** – All main notifications (verification, password reset, invitations, mediator explanation request, mediator availability request, admin escalation) sent via Flask-Mail.
- **Multi-channel** – Users can set **email**, **WhatsApp**, **Telegram**, **Signal** in Preferences. Notifications are sent to every configured channel (email always when present; others when backoffice has the integration enabled).
- **SMS** – Stub for Twilio (invitations by phone).
- **WhatsApp / Telegram / Signal** – Backoffice config (superadmin); send logic is stubbed and ready to be wired to real APIs.

---

## Backoffice (admin)

- **Dashboard** – Overview (user count, mediation count) and links to sections.
- **Payment settings** (superadmin only) – Stripe (publishable + secret key, webhook secret), **platform commission %**. Stored in `SiteSetting`. **On any change**, all admins and superadmins are notified by email (and optional WhatsApp/Telegram/Signal).
- **Messaging integrations** (superadmin only) – Enable/configure WhatsApp, Telegram, Signal (API key, bot token, API URL).
- **Users & roles** – List users; change role (`user`, `mediator`, `admin`, `superadmin`). Only superadmin can assign superadmin.
- **Mediations** – List recent mediations (e.g. last 200).
- **Mediator metrics** – Table of all mediators with: mediations opened, agreements reached, explanation response (avg), confirmation response (avg). Click **View** for one mediator’s detail.

---

## Mediator metrics

- **Mediators** – **My metrics** (from dashboard): mediations opened, agreements reached, explanation response average time, mediation confirmation response average time.
- **Admins / superadmins** – **Mediator metrics** in admin: same indicators for all mediators; drill-down per mediator.
- **Explanation response time** – From “explanation requested” to “explanation added” (when participant asks and mediator saves text).
- **Confirmation response time** – From “mediator invited” to “mediator confirmed”.

---

## AI & content

- **NVC reformulation** – Perspectives and proposals can be reformulated in non-violent communication style (Ollama Cloud).
- **AI reformulation tag** – When a user submits text that was AI-reformulated (posts, perspectives, or proposals with NVC reformulation), a visible **“AI reformulation”** badge is shown on the message so everyone can see it had AI reformulation.
- **Agenda suggestions** – AI suggests agenda points from perspectives.
- **Agreement drafting** – AI drafts agreement text from accepted proposals.
- **Translation** – API to translate text; posts can be shown in each party’s preferred language.
- **Voice dictation** – Web Speech API in mediation view for voice input (browser-supported); posts can be marked as voice.

---

## Search (precedents)

- **Search page** – Describe a dispute; optionally filter by dispute type and tags.
- **Similar cases** – Query returns anonymised past agreements ranked by similarity (privacy-minded).
- **Index mediation** – After a mediation is closed with an agreement, it can be indexed for search (creator triggers; requires agreement text). **Per-party consent:** when a mediation is closed ending in agreement, each party (not the mediator) may give consent for this mediation to be shared in the general search with private data masked. Indexing proceeds only when all parties have consented.

---

## Payments

- **Stripe** – Checkout session; success redirect records payment. **Mocked in tests** so the app loads without the real Stripe module.
- **Payment types** – **Fixed price** (standard fee), **donation** (each party can add an optional extra amount), **pro-bono** (fee waived). All create a registered transaction.
- **Per-mediation** – Price per party set by mediator in pre-mediation; payment required (or not) per mediation.
- **Transaction registration** – Every payment (fixed, donation, pro-bono) is stored in `MediationPayment` with amount, **platform commission**, and **mediator payout**.
- **Platform commission** – Configurable percentage (backoffice, superadmin). Applied to each paid mediation; the remainder is the mediator’s payout.
- **Mediator payout** – Mediators configure **IBAN** and/or **mobile number** in **Payout settings** (dashboard → Payout settings) to receive payments. All transactions for mediations they mediate are listed there with commission and payout breakdown.

---

## Technical

- **Live updates** – SocketIO for join/leave and typing in mediation rooms.
- **Schema migrations** – Additive migrations at startup (`services/schema_migrations.py`); no Alembic.
- **i18n** – EN/PT translations for UI and flash messages (`services/translations.py`).
- **CLI** – `flask process-mediator-timeouts` (48h job), `flask reset-superadmin-password`.

---

## Tech stack

- **Backend:** Flask, Flask-SQLAlchemy, Flask-Login, Flask-SocketIO, Flask-Mail
- **AI:** Ollama Cloud (e.g. gpt-oss:120b)
- **Database:** SQLite (dev) / PostgreSQL (prod)
- **Frontend:** Jinja2 templates, vanilla JS, SocketIO
