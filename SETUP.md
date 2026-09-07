# Setting Clearway up on a new machine

Two ways in. **Path A** runs the whole stack in Docker and needs almost nothing
installed. **Path B** runs Python and Node natively, which is what you want if
you are going to change the code.

Both work on Windows, Linux and macOS. Where a command differs, the Windows
version is given separately.

> **Clearway runs with no API keys at all.** Without one it works in
> **CAMS-only mode**: nowcasts and forecasts come from the Copernicus physics
> model and are labelled as uncorrected. Adding a free key turns on the machine
> learning: ground truth to train on, and a scorecard to grade it against.
> Do the quick start first, add keys later.

---

## 1. What you need

| Tool | Version | Needed for | Check with |
|---|---|---|---|
| Git | any recent | Getting the code | `git --version` |
| Docker | 24+ with Compose v2 | Path A entirely; Path B just for the database | `docker --version` |
| Python | **3.12 or newer** | Path B backend | `python --version` |
| Node.js | **20 or newer** | Path B frontend | `node --version` |

Windows notes:

- Install **Docker Desktop** and let it use the WSL 2 backend. Start it and
  wait for the whale icon to stop animating before running any `docker` command.
- Install Python from [python.org](https://www.python.org/downloads/) and tick
  **"Add python.exe to PATH"** on the first screen.
- On Windows the command is `python`. On Linux and macOS it is usually
  `python3`. Substitute accordingly throughout.
- There is no `make` on Windows, so every Makefile target is also written out
  as a raw command in [section 7](#7-everyday-commands).

---

## 2. Get the code

```bash
git clone <your-repo-url> clearway
cd clearway
```

Every path below is relative to that folder.

---

## Path A: everything in Docker

```bash
docker compose up --build -d
```

The first build takes a few minutes. Migrations run automatically on start.

- Site: <http://localhost:3000>
- API docs: <http://localhost:8000/docs>
- Health check: <http://localhost:8000/healthz>

That is a working install in CAMS-only mode. To turn the learning loop on, put a
key in a `.env` file next to `docker-compose.yml`:

```
CLEARWAY_OPENAQ_API_KEY=your-key-here
CLEARWAY_WAQI_API_TOKEN=your-token-here
```

then rebuild and seed:

```bash
docker compose up -d --build
docker compose exec api python -m app.cli bootstrap --days 60
```

`bootstrap` discovers stations, backfills 60 days of history, trains the first
model and issues a forecast run. It takes a while, mostly waiting on rate
limits.

Useful afterwards:

```bash
docker compose logs -f api          # follow the API
docker compose logs -f scheduler    # follow the background jobs
docker compose ps                   # what is running
docker compose down                 # stop, keep the data
docker compose down -v              # stop and wipe the database
```

Skip to [section 6](#6-check-it-worked).

---

## Path B: local development

### 3.1 Start the database

PostgreSQL 17 listens on **5436**, not the usual 5432, so it will not collide
with a Postgres you already have.

Windows:

```bash
docker run -d --name clearway-postgres ^
  -e POSTGRES_USER=clearway -e POSTGRES_PASSWORD=clearway -e POSTGRES_DB=clearway ^
  -p 127.0.0.1:5436:5432 postgres:17-alpine
```

Linux and macOS:

```bash
docker run -d --name clearway-postgres \
  -e POSTGRES_USER=clearway -e POSTGRES_PASSWORD=clearway -e POSTGRES_DB=clearway \
  -p 127.0.0.1:5436:5432 postgres:17-alpine
```

To start it again after a reboot: `docker start clearway-postgres`.

> **Postgres 15 or newer is required.** One constraint uses
> `UNIQUE NULLS NOT DISTINCT`, which older versions do not support. If you
> install Postgres yourself instead of using Docker, create a database
> `clearway` owned by a user `clearway` with password `clearway`, and edit the
> port in `backend/.env` to match.

### 3.2 Backend

```bash
cd backend
```

**Create and activate a virtual environment**

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

If PowerShell refuses with "running scripts is disabled on this system", allow
it for this window only and try again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Windows Command Prompt:

```cmd
python -m venv .venv
.venv\Scripts\activate.bat
```

Linux and macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Install and configure**

```bash
pip install --upgrade pip
pip install -r requirements-dev.txt
```

Copy the environment file. Windows:

```cmd
copy .env.example .env
```

Linux and macOS:

```bash
cp .env.example .env
```

The defaults already point at the database from step 3.1, so it works
unedited. Then apply the schema and start the API:

```bash
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Leave that running. The API is on <http://127.0.0.1:8000>, with interactive
docs at <http://127.0.0.1:8000/docs>.

### 3.3 Frontend

In a **second terminal**, from the project root:

```bash
cd web
npm install
```

Copy the environment file. Windows:

```cmd
copy .env.local.example .env.local
```

Linux and macOS:

```bash
cp .env.local.example .env.local
```

Then:

```bash
npm run dev
```

The site is on <http://localhost:3000>.

> Use `npm install` the first time. If you get odd build errors after pulling
> changes, use `npm ci` instead: it installs exactly the versions in
> `package-lock.json`, which is what CI does.

---

## 4. Turning the machine learning on

Everything above works without a key. To train and score a model you need
ground truth, which means at least one of these free accounts.

| Provider | Sign up | What it adds |
|---|---|---|
| **OpenAQ** | <https://explore.openaq.org/register> | Station observations, **and history**, so it is the one that lets you backfill and train |
| **WAQI** | <https://aqicn.org/data-platform/token/> | 12,000+ stations, much better coverage in Beijing and Chattogram. Latest reading only, so it improves coverage going forward but cannot backfill |

Put them in `backend/.env`:

```
CLEARWAY_OPENAQ_API_KEY=your-key-here
CLEARWAY_WAQI_API_TOKEN=your-token-here
```

Restart the API, then bootstrap:

```bash
python -m app.cli bootstrap --days 60
```

That runs four steps in order: discover stations near the configured cities,
backfill 60 days of observations and weather, train the first model, and issue
a forecast run. Expect it to take a while.

To keep it current, run the scheduler in a **third terminal**:

```bash
python -m app.jobs.run
```

It ingests and forecasts hourly, scores yesterday's forecasts daily, and
retrains weekly. A retrained model is promoted only if it beats every baseline.

---

## 5. Running the tests

The test suite needs its own database, created once:

```bash
docker exec clearway-postgres psql -U clearway -d postgres -c "CREATE DATABASE clearway_test;"
```

Then, from `backend` with the virtual environment active:

```bash
pytest
```

123 tests, a few seconds. They need no API key and make no network calls: the
providers are tested against recorded response shapes, and the machine learning
is tested on synthetic data with a known answer.

---

## 6. Check it worked

1. Open <http://localhost:3000>. You should see an AQI reading for Dhaka and a
   24-hour forecast chart. If the model has not been trained the chart is
   labelled "Raw CAMS output", which is correct, not an error.
2. Try the city buttons and the search box. Search works for anywhere in the
   world, not just the seven configured cities.
3. Open <http://localhost:3000/model>. Before any training this shows "Nothing
   scored yet", which is the honest state.
4. Check the API directly:

```bash
curl "http://127.0.0.1:8000/healthz"
curl "http://127.0.0.1:8000/api/v1/air/now?latitude=23.8103&longitude=90.4125"
```

`healthz` tells you which ground-truth providers are configured and which model
version is serving.

---

## 7. Everyday commands

On Linux and macOS a `Makefile` wraps these; run `make help` to list them.
Windows has no `make`, so the raw commands are given here. All backend commands
assume you are in `backend` with the virtual environment active.

| What | Command |
|---|---|
| Start the API | `uvicorn app.main:app --reload --port 8000` |
| Start the site | `npm run dev` (in `web`) |
| Start the background jobs | `python -m app.jobs.run` |
| Apply migrations | `alembic upgrade head` |
| Create a migration after a model change | `alembic revision --autogenerate -m "what changed"` |
| Discover stations | `python -m app.cli discover` |
| Backfill history | `python -m app.cli backfill --days 60` |
| Train a model | `python -m app.cli train --days 90` |
| Train without promoting | `python -m app.cli train --no-promote` |
| Run one forecast round | `python -m app.cli forecast` |
| Score yesterday | `python -m app.cli score` |
| Data coverage per station | `python -m app.cli status` |
| Everything at once | `python -m app.cli bootstrap --days 60` |
| Backend tests | `pytest` |
| Backend tests with coverage | `pytest --cov=app --cov-report=term-missing` |
| Backend lint | `ruff check .` |
| Backend type check | `mypy app` |
| Frontend type check | `npm run typecheck` (in `web`) |
| Frontend lint | `npm run lint` |
| Frontend production build | `npm run build` |

---

## 8. When something goes wrong

**`port is already allocated` when starting Postgres**
A container by that name already exists. Check with `docker ps -a`. If
`clearway-postgres` is listed, start it rather than creating another:
`docker start clearway-postgres`. To use a different port, change both the
`-p` flag and `CLEARWAY_DATABASE_URL` in `backend/.env`.

**`connection refused` on port 5436**
The container is not running or is still starting. `docker ps` should list it.
Give it five seconds after `docker start` and retry.

**`relation "stations" does not exist`**
Migrations have not been applied. Run `alembic upgrade head`.

**`syntax error at or near "NULLS"` during migration**
Your PostgreSQL is older than 15. Use the Docker image in step 3.1, which is
Postgres 17.

**Tests fail with `database "clearway_test" does not exist`**
Create it, as in [section 5](#5-running-the-tests).

**The site loads but every reading says "modelled" rather than "measured"**
That is CAMS-only mode, and it is correct if you have not added a provider key
or run `bootstrap`. It is also correct for any point with no monitoring station
within 30 km.

**The scorecard says "Nothing scored yet"**
Scores appear only after a full day of forecasts has been matched against
observations that arrived later. Predictions are never re-derived with
hindsight, so this genuinely takes a day of the scheduler running.

**The city buttons are missing on the home page**
The home page is statically prerendered, so the API has to be reachable when
`npm run build` runs. Start the API first, then rebuild. In development
(`npm run dev`) this does not apply.

**`npm run build` fails on a Tailwind error about `ScannerOptions`**
`tailwindcss` and `@tailwindcss/postcss` have drifted apart. Reinstall from the
lock file:

```bash
cd web
rm -rf node_modules        # Windows PowerShell: Remove-Item -Recurse -Force node_modules
npm ci
```

**PowerShell: "running scripts is disabled on this system"**
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, then activate the
virtual environment again. It only affects the current window.

**`python` is not recognised on Windows**
Python is not on PATH. Re-run the installer, choose "Modify", and tick "Add
python.exe to PATH". Open a new terminal afterwards.

**Upstream requests fail with 429**
You have hit a rate limit. OpenAQ limits are per key; Open-Meteo allows 10,000
calls a day without one. Backfilling is chunked and resumable, so wait and run
`python -m app.cli backfill` again. It picks up where it stopped.

**CORS errors in the browser console**
The site is on a port the API does not trust. Add it to
`CLEARWAY_CORS_ORIGINS` in `backend/.env` and restart the API.

**Docker commands hang on Windows**
Docker Desktop is not running, or WSL 2 has not finished starting. Open Docker
Desktop and wait for "Engine running".

---

## 9. Things worth knowing

- **Never commit `.env` or `.env.local`.** They are already in `.gitignore`.
  The `.env.example` files are the ones that belong in the repository.
- **Everything is stored in UTC** and keyed on whole hours. The interface
  formats to the viewer's locale; the database never holds a local time.
- **The seven configured cities** are Dhaka, Chattogram, Delhi, Beijing,
  Moscow, Berlin and London, set in `backend/app/core/config.py`. That list
  bounds ingestion so it stays inside free rate limits. Place search still works
  anywhere, falling back to CAMS.
- **Model artefacts** are written to `backend/var/models` and versioned in the
  `model_versions` table. In Docker they live in a named volume, so they
  survive a rebuild.
- **A retrained model is not automatically trusted.** It is promoted only if it
  beats persistence, climatology and raw CAMS on held-out data. If it loses, the
  incumbent keeps serving and the candidate is recorded with the reason.
- **`docker compose down -v` deletes the database and the trained models.**
  Without `-v` both survive a restart.

For how the pieces fit together rather than how to start them, read
[`docs/DECISIONS.md`](docs/DECISIONS.md) and [`PLAN.md`](PLAN.md).
