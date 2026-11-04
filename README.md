The build is an AI news aggregator where I can take multiple sources. So, for example, YouTube channels, and I want, for example, blog posts from OpenAI and Anthropic, and I want to scrape those, put them into a database where we have some kind of structure, where we have sources, we have, let's call them, articles. And then what I wanna do is I want to run a daily digest where we're going to take all of the articles from within the time frame, and we're going to do an LLM summary around that. And then based on the user insights that we specify in some kind of, like, agent system prompt, we can generate a daily digest, which is going to be short snippets with a link to the original source. Now from the YouTube channels, I want to I want to be able to create a list of channels, and then we want to get the latest videos from those channels. I think we can use the YouTube RSS feed for that and from... for the blog post, we can just have URLs that we can scrape for that. Okay. So I want everything built in a Python back end. I want to use a PostgreSQL database. I want to use SQL Alchemy in order to define the database models and then to also create the tables. I want the project structure to be an app folder where all of the app logic is in, and then I also want a Docker folder where we first create a very minimal setup for the PostgreSQL database. And then that will probably be starting point. We want to make sure that later down the line, we can easily deploy the whole app to render and then also schedule it every twenty four hours to run the reports, get everything. And then when we've created the Daily Digest, I want to send an email to my personal inbox with this. that's that's what I have in mind. I think that would be pretty cool to build.

https://openai.com/news/

https://www.anthropic.com/engineering

https://www.anthropic.com/research

## Running it

Everything runs through one command, `ai-news`. Copy `.env.example` to `.env`
and fill in `OPENAI_API_KEY`, `RESEND_API_KEY` and `EMAIL_TO`, then start the
database:

```bash
docker compose -f docker/docker-compose.yml up -d
```

The daily pipeline is a single command. It fetches new videos and articles,
stores them, fills in transcripts and article content, summarizes everything
new, ranks the unsent stories against your profile in `src/app/profile.py`, and
emails you the top 10:

```bash
uv run python main.py           # the full daily run (same as: uv run ai-news run)
uv run python main.py --dry-run --preview digest.html   # build the email without sending it
```

Each stage can also be run on its own: `ingest`, `enrich`, `digest`, `rank`,
`send` and `init-db`. Run `uv run ai-news --help` for the options.

Every stage is safe to re-run. Stored items are deduplicated, and a story is
recorded once it has been emailed, so it never goes out twice. The command exits
non-zero if a whole stage fails, so a scheduler can flag the run. Single items
that fail, such as one feed or one summary, are logged and retried next run.

## Deploying to a server

The server runs two containers with Docker Compose: Postgres, and the app. The
app container runs `main.py` every day at 7:00 Toronto time, following daylight
saving, and stays idle in between. The schedule is in `docker/crontab`, and the
image is built from `docker/Dockerfile`.

**1. Create a server.** Any Ubuntu 24.04 VPS with 4 GB of RAM works, such as a
Hetzner CX22 or a DigitalOcean droplet. Log in with an SSH key and allow only
SSH inbound. The database is never exposed outside the server.

**2. Install Docker and get the code.**

```bash
curl -fsSL https://get.docker.com | sh
git clone https://github.com/multi-kontik/ai-news-aggregator.git
cd ai-news-aggregator
```

For a private repo, add a read-only deploy key to the GitHub repo and clone over SSH.

**3. Create `.env`** from `.env.example`. Fill in the OpenAI and Resend keys,
`EMAIL_TO`, and a strong `POSTGRES_PASSWORD`, for example from
`openssl rand -hex 24`. For YouTube videos, also set `YOUTUBE_API_KEY` and the
Webshare proxy username and password. The API key lists new uploads, and the
proxy is needed for transcripts because YouTube blocks most server IPs.

**4. Start it.**

```bash
docker compose -f docker/docker-compose.server.yml --env-file .env up -d --build
```

**5. Check it with a dry run,** then one real run if you want an email straight away:

```bash
docker compose -f docker/docker-compose.server.yml --env-file .env run --rm app python main.py --dry-run
docker compose -f docker/docker-compose.server.yml --env-file .env run --rm app python main.py
```

**Logs** for the scheduled runs:

```bash
docker compose -f docker/docker-compose.server.yml --env-file .env logs -f app
```

**Updating** after you push changes:

```bash
git pull
docker compose -f docker/docker-compose.server.yml --env-file .env up -d --build
```

Tables are created and upgraded automatically on every run. The database lives
in a Docker volume, so it survives restarts, rebuilds and server reboots. If the
server is down at 7:00, that run is skipped, and the next run picks up anything
published in the previous 48 hours.
