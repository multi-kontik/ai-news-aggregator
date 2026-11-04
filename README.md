# AI News Aggregator

A personal daily AI news digest. Every day it collects new videos and posts from
YouTube channels and from the OpenAI and Anthropic blogs. An LLM summarizes each
item and ranks it against your reader profile. The top 10 arrive as one short
email, each with a link to the original.

## How it works

The daily run is a pipeline of five stages:

| Stage | What it does |
|---|---|
| 1. Ingest | Fetches everything published in the last 48 hours from every source, and stores what's new in Postgres. |
| 2. Enrich | Adds each item's full text: a video's transcript, or an article's page converted to markdown. |
| 3. Summarize | The **summarizer agent** writes a clear headline and a 2–3 sentence summary for each new item. |
| 4. Rank | The **editor agent** scores each unsent story from 0 to 10 against your profile, with a one-line reason. |
| 5. Email | The **email agent** writes a short introduction. The top 10 stories are sent through Resend and recorded as sent. |

All three agents use the OpenAI Responses API with structured outputs, through
`gpt-4.1-mini` by default.

**It's safe to re-run at any time:**
- **Nothing is stored or emailed twice.** Items are deduplicated, and sent
  stories are tracked.
- **One failure doesn't stop the run.** When a feed, transcript or summary fails,
  it's logged and retried on the next run.
- **Progress is saved as it goes.** Each item is committed when it's done, so an
  interrupted run keeps what it finished.
- **A whole-stage failure is flagged.** If a stage fails entirely, for example
  because the database is down, the command exits with an error so a scheduler
  can flag it.

### Sources

The stages don't know about any particular site. They work through a small
`Source` interface in `src/app/ingestion/base.py`. That interface says where
items come from, how to fill in their full text, and what text to summarize.

There's one implementation per *kind* of source:

- **`FeedSource`** reads one or more RSS or Atom feeds, and enriches each
  article by converting its page to markdown with docling. OpenAI and Anthropic
  are both configured instances of it.
- **`YouTubeSource`** lists each channel's uploads through the YouTube Data API,
  and enriches each video with its transcript.

Every source is listed in one place, `SOURCES` in `src/app/ingestion/__init__.py`.
Each feed URL and each channel is fetched on its own, so one that fails doesn't
affect the rest.

## Setup

You'll need Python 3.13, [uv](https://docs.astral.sh/uv/) and Docker.

```bash
uv sync
cp .env.example .env        # then fill in your keys
docker compose -f docker/docker-compose.yml up -d
```

Tables are created automatically on the first run.

### Configuration

All settings are read from `.env`. The comments in `.env.example` explain each one.

| Setting | Needed for |
|---|---|
| `OPENAI_API_KEY` | Everything. Every agent uses it. |
| `RESEND_API_KEY`, `EMAIL_TO` | Sending the email. A dry run works without them. |
| `EMAIL_FROM` | Optional. Without it, Resend's test sender is used, which only delivers to your Resend account's own address. |
| `YOUTUBE_API_KEY` | YouTube videos. Without it, YouTube is skipped. Create a key in the [Google Cloud console](https://console.cloud.google.com) by enabling **YouTube Data API v3** and adding an API key under **Credentials**. |
| `WEBSHARE_PROXY_USERNAME`, `WEBSHARE_PROXY_PASSWORD` | YouTube transcripts from a server, since YouTube blocks most datacenter IPs. Optional on your own machine. |
| `DATABASE_URL` | Optional. Defaults to the local Docker database. |

## Running it

```bash
uv run python main.py                                    # the full daily run
uv run python main.py --dry-run --preview digest.html   # build the email without sending it
```

Useful options:
- **`--dry-run`** builds the email without sending it, and doesn't mark stories as sent.
- **`--preview FILE`** saves the email's HTML so you can open it in a browser.
- **`--top N`** changes how many stories the email includes. The default is 10.
- **`-v`** shows debug logs.

Each stage can also be run on its own: `uv run ai-news ingest`, `enrich`,
`digest`, `rank` or `send`. Run `uv run ai-news --help` for all commands.

## Customizing

- **Your interests** are in `src/app/profile.py`. It holds your name for the
  greeting, your background, your interests in priority order, and topics you
  care less about. The editor agent ranks against it.
- **What gets scraped** is in `src/app/ingestion/__init__.py`. To follow
  another YouTube channel, add its handle. To read another feed from a site
  that's already listed, add its URL to that site's `feed_urls`.
- **The model** is set by `OPENAI_MODEL` in `.env`.
- **The agents' instructions** are at the top of each module in `src/app/agent/`.

### Adding a new site

1. **Give it a table.** In `src/app/database/models.py`, add a table for the
   site's articles, and a column on `Digest` that points at it. You can copy
   `OpenAIArticle` and `openai_article_id`. Also add its name to
   `Digest.source`.
2. **Register it.** Add one `FeedSource(name=..., model=..., digest_link=..., feed_urls=[...])`
   entry to `SOURCES`.

Nothing else changes. The stages, the ranking and the email all pick it up
automatically. A source that isn't an RSS feed, such as a scraped website or
another API, needs a new `Source` subclass with its own `fetch` and `enrich`.

## Project structure

```
main.py                     entry point for the daily run
src/app/
  ingestion/                where news comes from
    __init__.py             SOURCES: every channel and feed the pipeline reads
    base.py                 the Source interface
    feeds.py                FeedSource: RSS/Atom feeds, plus page-to-markdown conversion
    youtube.py              YouTubeSource and the YouTube Data API and transcript client
  agent/                    the summarizer, editor (ranking) and email agents
  database/                 SQLAlchemy models, repositories and session
  runner.py                 the five stages
  pipeline.py               runs every stage in order
  cli.py                    the ai-news command
  email_digest.py           email template and sending, through Resend
  profile.py                the reader profile
  config.py                 settings, read from .env
docker/                     local Postgres, and the image that runs the pipeline
render.yaml                 Render cron job and database, for deploying later
tests/                      pytest suite
```

## Tests

```bash
uv run pytest
```

Every outside service is mocked, including OpenAI, Resend, YouTube, the feeds
and the web pages. The database tests use an in-memory SQLite database, so
neither Docker nor any keys are needed.

## Known limitations

- **openai.com blocks some full-text fetches.** It often refuses automated page
  requests from servers. Those posts are summarized from the feed's short
  description instead.
- **Some transcripts need a retry.** Even through Webshare, a transcript request
  sometimes hits Google's "unusual traffic" page. It's retried on the next run.
- **New sites need a model change.** Each site has its own table, so adding one
  means editing the models. That's cheap, but it's the one step that isn't just
  configuration.

## Deployment

The app isn't deployed yet. `render.yaml` and `docker/Dockerfile` are ready for
running it as a daily cron job on Render, with a Render Postgres database.
