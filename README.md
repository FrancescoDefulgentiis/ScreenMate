# ScreenMate-
Can't decide what to watch? Tell ScreenMate what you're feeling, and it'll nail a recommendation. Then grab the film straight to your Jellyfin.

## Running with Docker

ScreenMate ships with a `Dockerfile` and `docker-compose.yml` so it runs as a
background service that starts automatically on boot and keeps access to your
Jellyfin `movies` and `tv` folders.

### 1. Configure

```bash
cp .env.example .env
```

Edit `.env` and fill in:

- `TELEGRAM_TOKEN`, `ALLOWED_USERS` – your bot token and allowed user ids.
- `OLLAMA_URL` / `OLLAMA_MODEL`, `JACKETT_URL` / `JACKETT_API_KEY` – the
  companion services (use `host.docker.internal` to reach services on the host).
  The default model is `qwen2.5:3b-instruct-q4_K_M` (a small 3B model that stays
  responsive on CPU-only machines and is strong at multilingual chat + JSON — run
  `ollama pull qwen2.5:3b-instruct-q4_K_M` first). Other CPU-friendly choices are
  `gemma2:2b`, `phi3.5` and `llama3.2`; on a GPU you can point `OLLAMA_MODEL` at
  something bigger like `qwen2.5:7b-instruct-q4_K_M`. The model is kept warm in RAM
  (`OLLAMA_KEEP_ALIVE`) and preloaded at startup so the first reply isn't slow;
  see `.env.example` for the timeout/keep-alive tunables.
- `JELLYFIN_URL` / `JELLYFIN_API_KEY` – used to trigger a library rescan after a
  download starts (create the key in Jellyfin's Dashboard > API Keys).
- `QBIT_URL`, `QBIT_USERNAME`, `QBIT_PASSWORD` – your existing qBittorrent
  instance. ScreenMate sends magnets there and tells it to save movies into
  `QBIT_MOVIES_PATH` and TV into `QBIT_TV_PATH`.
- `MOVIES_HOST_PATH` / `TV_HOST_PATH` – the real Jellyfin library folders on the
  host. They are bind-mounted into the container at `/media/movies` and
  `/media/tv`. When qBittorrent shares this host's filesystem, keep
  `QBIT_MOVIES_PATH=/media/movies` and `QBIT_TV_PATH=/media/tv` so downloads
  land straight in the Jellyfin libraries.

### 2. Build and start

```bash
docker compose up -d --build
```

`restart: unless-stopped` in `docker-compose.yml` means Docker relaunches the
bot whenever the machine boots (as long as the Docker service itself starts on
boot, e.g. `sudo systemctl enable docker`) and after any crash.

### 3. Manage

```bash
docker compose logs -f      # follow logs
docker compose restart      # restart
docker compose down         # stop and remove
```
