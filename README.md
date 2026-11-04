# AI News Aggregator

A personal daily AI news digest. It collects new videos and posts from YouTube
channels, the OpenAI news blog and Anthropic's news, research and engineering
blogs. It summarizes each one with an LLM, ranks them against a reader profile,
and emails the top 10 as a short digest with links to the originals.

## How it works

One run of the pipeline goes through five stages:

1. **Ingest.** Fetch everything published in the last 48 hours and store what's new.
   - YouTube channels come from the YouTube Data API.
   - OpenAI's posts come from its RSS feed.
   - Anthropic's posts come from community-maintained RSS feeds.
2. **Enrich.** Add a transcript to each video, and the full text, as markdown, to each article.
3. **Summarize.** The summarizer agent writes a clear headline and a 2–3
   sentence summary for every item that doesn't have one yet.
4. **Rank.** The editor agent scores each unsent story from 0 to 10 for
   relevance to the reader profile, with a one-line reason.
5. **Email.** The email agent writes a short introduction. The top 10 stories
   are sent as an HTML email through Resend and recorded as sent.

Every stage is safe to re-run:
- **Nothing is stored twice.** Items that are already stored are skipped.
- **Nothing is emailed twice.** Once a story has been emailed, it's never sent again.
- **One failure doesn't stop the run.** If one feed, transcript or summary
  fails, it's logged, the run carries on, and it's retried next time.
- **A whole-stage failure is flagged.** If a stage fails entirely, for example
  because the database is down, the command exits with an error so a scheduler
  can flag it.

All three agents use the OpenAI Responses API with structured outputs, through
`gpt-4.1-mini` by default.

## Requirements

- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- Docker, for the local Postgres database
- API keys for:
  - **OpenAI**, required. Every agent uses it.
  - **Resend**, required to send the email.
  - **YouTube Data API v3**, needed for YouTube videos.
  - **Webshare**, optional locally. It's a rotating residential proxy for YouTube transcripts.

## Setup

```bash
uv sync
cp .env.example .env        # then fill in your keys, see Configuration below
docker compose -f docker/docker-compose.yml up -d
```

Tables are created automatically on the first run. To create them up front,
run `uv run ai-news init-db`.

## Configuration

All settings are read from `.env`. The comments in `.env.example` explain each one.

| Setting | Needed for | Notes |
|---|---|---|
| `DATABASE_URL` | Everything | Defaults to the local Docker database. Plain `postgresql://` URLs work too. |
| `OPENAI_API_KEY` | Everything | |
| `RESEND_API_KEY`, `EMAIL_TO` | Sending the email | Not needed for a dry run. |
| `EMAIL_FROM` | Optional | Defaults to Resend's test sender, which only delivers to your Resend account's address. Set it to an address on a verified domain to send anywhere. |
| `YOUTUBE_API_KEY` | YouTube videos | YouTube's public RSS feeds return 404s, so videos are listed through the Data API. |
| `WEBSHARE_PROXY_USERNAME`, `WEBSHARE_PROXY_PASSWORD` | YouTube transcripts | Optional locally. YouTube blocks most server IPs, so a server needs them. Use a "Residential" plan. |
| `WEBSHARE_PROXY_COUNTRIES` | Optional | Limits the proxy's IPs to some countries, e.g. `us,ca`. |

### Creating a YouTube API key

1. In the [Google Cloud console](https://console.cloud.google.com), create a project.
2. Go to **APIs & Services**, then **Library**. Find **YouTube Data API v3** and enable it.
3. Go to **APIs & Services**, then **Credentials**. Click **Create credentials** and choose **API key**.
4. Edit the key, and under **API restrictions** limit it to the YouTube Data API v3.

The free quota is 10,000 units a day. A daily run uses a handful of units.

## Running it

```bash
uv run python main.py                                    # the full daily run
uv run python main.py --dry-run --preview digest.html   # build the email without sending it
```

`main.py` is the same as `uv run ai-news run`. Useful options:

- **`--dry-run`** builds the email without sending it, and doesn't mark stories as sent.
- **`--preview FILE`** saves the email's HTML so you can open it in a browser.
- **`--top N`** changes how many stories go in the email. The default is 10.
- **`--ingest-hours` and `--digest-hours`** change the lookback windows. Both default to 48 hours.
- **`-v`** shows debug logs.

Each stage can also be run on its own:

```bash
uv run ai-news ingest     # fetch and store new videos and articles
uv run ai-news enrich     # add transcripts and full article text
uv run ai-news digest     # summarize anything without a summary
uv run ai-news rank       # show the ranking, without sending anything
uv run ai-news send       # rank and email the top stories
uv run ai-news --help     # every command and option
```

## Customizing

- **What you're interested in.** Edit `src/app/profile.py`. It holds your
  name, used in the greeting, plus your background, your interests in priority
  order, and the topics you care less about. The editor agent ranks against it.
- **YouTube channels.** Edit `YOUTUBE_CHANNELS` in `src/app/config.py`. Use
  channel handles like `@OpenAI`.
- **Blog feeds.** They live in `src/app/ingestion/openai_news.py` and
  `src/app/ingestion/anthropic_news.py`.
- **The model.** It's `openai_model` in `src/app/config.py`. You can also set
  `OPENAI_MODEL` in `.env`.
- **The agents' instructions.** Each agent's system prompt is at the top of its
  module in `src/app/agent/`.

## Project structure

```
main.py                    entry point for the daily run
src/app/
  cli.py                   the ai-news command and its subcommands
  pipeline.py              runs every stage in order
  runner.py                the five stages
  profile.py               the reader profile used for ranking
  config.py                settings, read from .env
  email_digest.py          email template and sending, through Resend
  agent/                   summarizer, editor (ranking) and email agents
  ingestion/               YouTube, RSS feeds and article-to-markdown conversion
  database/                SQLAlchemy models, repositories and session
docker/
  docker-compose.yml       local Postgres for development
  Dockerfile               image that runs the pipeline once
render.yaml                Render cron job and database, for deploying later
tests/                     pytest suite
```

## Tests

```bash
uv run pytest
```

The tests mock every outside service, including OpenAI, Resend, YouTube and
the feeds. The database tests use an in-memory SQLite database, so neither
Docker nor any API keys are needed.

## Known limitations

- **openai.com blocks some full-text fetches.** Many OpenAI article pages answer
  automated requests with 403 Forbidden. Those posts are summarized from the
  feed's short description instead.
- **Some transcripts need a retry.** Even through Webshare, a request
  occasionally hits Google's "unusual traffic" page. Those transcripts are
  retried on the next run.

## Deployment

The app isn't deployed yet. `render.yaml` and `docker/Dockerfile` are ready for
running it as a daily cron job on Render, with a Render Postgres database.
