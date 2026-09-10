# Eir

Eir is a Flask/MongoDB web app built for [Greve Gymnastik og Trampolin](https://greve-gymnastik.dk/) (GGT), a Danish gymnastics and trampoline club, to handle scheduling, coach/parent notifications, and integration with the club's existing systems. It is currently being used for the Trampoline department only.

This repo is a **sanitized snapshot** of an actively developed private codebase, shared as a portfolio sample. Real member data, IDs, and secrets have been removed or replaced with synthetic test data; some tests that depended on third-party platform data have been excluded rather than reworked around fake data, for reasons noted below. History has been squashed to a single clean commit.

## What it does

- Pulls team and schedule data from **Conventus** (the club's membership/scheduling system, no public API — done via scraping) and **GymDanmark**, converting schedule data into iCalendar and FullCalendar-compatible formats
- Automates coach/parent notifications via Facebook Messenger (HMAC webhook verification, verification-code linking flow, Meta App Review, Business Verification, and Utility Message templates to work within Meta's EU/EEA messaging restrictions), with Telegram as an additional channel
- Handles session-based role logic for coaches vs. other users, with Danish/English localization throughout (python-i18n)
- Runs on a daily automated pipeline (GitHub Actions) that regenerates schedule files, deployed on Render with MongoDB Atlas

## Stack

Flask · MongoEngine · MongoDB Atlas · pytest · Playwright · GitHub Actions (CI + scheduled jobs) · Docker · uv · Ruff · mypy · Render

## A few things worth knowing as a reviewer

**The scraping and integration work was the hard part.** Conventus has no API, so getting reliable, session-authenticated data out of it — and keeping that resilient as the site's markup shifts — was more involved than the CRUD side of the app.

**The Messenger integration was a real lesson in platform constraints.** It passed Meta's App Review and Business Verification, then ran into Meta's EU/EEA restrictions on standard messaging — solved by moving to Utility Message templates, which are exempt from the usual 24-hour messaging window. Telegram was added as an additional channel alongside it.

**Some tests involving Conventus page data are adjusted for this snapshot — including the Playwright end-to-end tests.** Conventus's terms of service restrict local storage of platform data. Where reasonable, tests were rebuilt with synthetic data or fixtures instead of real Conventus pages; tests with no clean synthetic substitute were removed from this public copy. The full test suite — using live data where synthetic substitutes aren't practical — exists in the private repo.

## Running it locally

The app is designed to run via Docker — see the included `Dockerfile` and `compose.yaml`:

```bash
docker compose up --build
```

Requires a `.env` file — see `.env.example` for required variables. Conventus/GymDanmark credentials are needed for the live scraping paths.

## Running the tests

```bash
uv sync
uv run pytest
```

The test suite runs against fixtures and doesn't require real credentials or a running Docker setup.

## Deployment

Deployed on Render's native Python runtime. Since the app runs from the `app/` subdirectory, Render's `uv`/`pyproject.toml` auto-detection (which only looks at the repo root) doesn't apply — `app/requirements.txt` is generated from `pyproject.toml` via:

```bash
uv pip compile pyproject.toml --python-platform linux -o app/requirements.txt
```

and kept in sync manually when dependencies change.

## License

No license is granted — this repo is shared for review purposes. Please don't reuse or redistribute without asking first.
