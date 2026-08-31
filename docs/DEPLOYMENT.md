# Deployment

There are two ways to run this, and they are not interchangeable.

## Why the hosted version cannot read your card

A web page in a browser cannot open `/dev/disk4`. No hosted deployment can, on
any platform, at any price: browsers have no access to raw block devices, and a
server in a data centre is not the machine your card is plugged into. This is a
property of the web, not a limitation of this project.

So the two modes are:

| | Local install | Hosted demo |
|---|---|---|
| Detects a plugged-in card | yes | no |
| Recovers from real hardware | yes | no |
| Analyses an uploaded disk image | yes | yes, up to the size cap |
| Runs the built-in sample card | yes | yes |
| Who it is for | someone with a card to recover | someone deciding whether this is worth installing |

The hosted demo exists to answer "what does this actually do" without asking a
visitor to install Python first. Real recovery work happens locally.

## Local install

```bash
git clone https://github.com/mithinsagar/flashforensics-ai
cd flashforensics-ai

pip install -e "backend[dev]"
flashforensics devices          # what is plugged in, and whether it is readable
flashforensics serve            # API on :8000

cd frontend && npm install && npm run dev
```

Then open http://localhost:3000. A card inserted while that page is open shows
up on its own within a few seconds.

Reading a raw device needs elevated permission on every operating system. If the
dashboard lists your card but marks it unreadable, either start the server with
`sudo -E flashforensics serve`, or take an image first and analyse that:

```bash
sudo dd if=/dev/rdisk4 of=~/card-backup.img bs=4m conv=noerror,sync status=progress
```

The `conv=noerror,sync` matters on a failing card: it keeps copying past bad
sectors and pads them, rather than stopping at the first read error.

## Hosted demo: Vercel for the frontend, a container host for the API

### Frontend on Vercel

Vercel is the right host for the Next.js half and takes about two minutes:

1. Import the repository at vercel.com/new.
2. Set **Root Directory** to `frontend`.
3. Add environment variable `NEXT_PUBLIC_API_BASE` = the API's public URL.
4. Deploy.

### API on Render, Railway or Fly

The API does not belong on Vercel, and it is worth being precise about why
rather than discovering it after deploying. Vercel's Python support is
serverless functions: a bundle-size ceiling that `chromadb` and its ONNX runtime
exceed on their own, a per-request time limit that an entropy scan over a large
image will pass, no persistent local filesystem for the memory-mapped image
between requests, and no long-lived process to hold a Server-Sent Events stream
open for the length of an analysis. Every one of those is load-bearing here.

`render.yaml` in the repository root deploys the container as-is:

1. At render.com, create a new Blueprint from the repository.
2. Render reads `render.yaml` and builds `backend/Dockerfile`.
3. After the frontend is deployed, set `FF_CORS_ORIGINS` to its origin, as a
   JSON array: `["https://your-app.vercel.app"]`.

Railway and Fly.io both deploy the same Dockerfile if you prefer them; the
container reads `$PORT`, which is what all three platforms set.

### Free-tier realities

A free instance sleeps when idle and takes roughly a minute to wake, so the
first visitor after a quiet period waits. Free instances are also memory-capped:
the sample card is 32 MB and comfortable, and `FF_MAX_UPLOAD_BYTES` in
`render.yaml` caps uploads at 128 MB so a stranger cannot push a 64 GB image
into a shared demo. Raise it if you move to a paid instance.

## Configuration

Every setting is an environment variable prefixed `FF_`. The ones that matter
for a deployment:

| Variable | Default | What it does |
|---|---|---|
| `FF_WORKSPACE` | `~/.flashforensics` | Uploads, exports and the format index |
| `FF_MAX_UPLOAD_BYTES` | 8 GB | Upload ceiling |
| `FF_CORS_ORIGINS` | `["http://localhost:3000"]` | JSON array of allowed origins |
| `FF_LLM_PROVIDER` | `auto` | `heuristic` pins the deterministic engine |
| `FF_EMBEDDING_PROVIDER` | `auto` | `hashing` forces the pure-Python fallback and skips onnxruntime entirely — set this on a memory-capped instance |
| `FF_ANTHROPIC_API_KEY` | unset | Enables model-written explanations |
| `FF_OPENAI_API_KEY` | unset | Same, via OpenAI |

No API key is needed. With none set, the pipeline runs on its deterministic rule
engine, which is what the published accuracy numbers were measured with, so a
deployment with no key reproduces them exactly.
