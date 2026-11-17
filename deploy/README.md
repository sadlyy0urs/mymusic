# Deploying to Your Local Ubuntu Server (Docker + Cloudflare Tunnel)

## 1. First-Time Setup on Ubuntu Server
SSH into your Ubuntu server and run:
```bash
git clone https://github.com/sadlyy0urs/mymusic.git ~/mymusic
cd ~/mymusic
chmod +x deploy/update.sh
docker compose up -d --build
```

### Optional: Rename the App or Change Port
Create a `.env` file in `~/mymusic/`:
```env
APP_NAME=YourCustomName
PORT=5000
```
To use your own logo, replace `static/logo.svg` (or add `static/logo.png` — `logo.png` is used automatically if present).

## 2. Auto-Update from GitHub via Cron
To check GitHub every 5 minutes and rebuild only when you push new commits:
```bash
crontab -e
```
Add this line at the bottom:
```cron
*/5 * * * * /home/<your-user>/mymusic/deploy/update.sh >> /home/<your-user>/mymusic/data/update.log 2>&1
```

## 3. Connect Cloudflare Tunnel
In your Cloudflare Zero Trust dashboard (or `cloudflared` `config.yml`), point a public hostname (e.g. `music.yourdomain.com`) to:
- `http://localhost:5000` (if `cloudflared` runs on the host), or
- `http://mymusic:5000` (if `cloudflared` runs in Docker on the same network).
