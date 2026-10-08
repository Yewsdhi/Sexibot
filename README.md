# telegram_auction_bot

Telegram bot that provides auction features via telegram interface.

[![Deploy to Heroku](https://www.herokucdn.com/deploy/button.svg)](https://heroku.com/deploy?template=https://github.com/Yewsdhi/Sexibot)

## .env content

- `TOKEN` - telegram bot api key
- `API_ENDPOINT` - api address (optional if running in docker)
- `PORT` - api port (optional, 8001 by default)
- `ADMIN_CHAT_ID` - telegram chat id to create posts
- `MAIN_CHANNEL_ID` - telegram chat id to send posts
- `DEBUG` - optional, default `False`
- `DATABASE_URL` - optional Postgres URL; SQLite is used by default for local runs

## Run w/ Docker

1. `cd` to project folder
2. create `.env` file
3. download or restore database as `./data_base/sql_app.db`
4. `docker-compose up -d --build`

## Heroku Deploy

1. Click the deploy button above
2. Set required environment variables:
   - `TOKEN`
   - `ADMIN_CHAT_ID`
   - `MAIN_CHANNEL_ID`
   - `DEBUG` (optional, default `False`)
3. Deploy the app
4. Ensure both dynos are running: `web` and `worker`

The Heroku `Procfile` starts both the FastAPI API and the Telegram bot polling worker:

```procfile
web: gunicorn main_api:app -k uvicorn.workers.UvicornWorker
worker: python main_bot.py
```
