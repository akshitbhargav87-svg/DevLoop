# Hackathon Deployment

DevLoop can be deployed as one public web service: the service hosts the React
frontend and FastAPI backend on the same origin, uses a hosted OpenAI-compatible
LLM, and stores its SQLite database and LangGraph checkpoints on a persistent
disk. Public mode requires a passcode and rejects local filesystem repositories.

## Render

1. Push this repository to a GitHub repository that Render can access.
2. In Render, choose **New** then **Blueprint**, and select the repository.
3. Render reads `render.yaml` and creates the `devloop-hackathon` web service.
4. Enter an OpenRouter API key for `OPENAI_API_KEY` when prompted. This is a
   secret; do not add it to source control.
5. Deploy. Wait for `/health` to report `{"status":"ok"}`.
6. Open the generated `https://...onrender.com` URL. Render generates
   `ACCESS_TOKEN`; find it in the service's Environment settings and share it
   with hackathon reviewers using a private channel.

The starter service and persistent disk may have a cost. Confirm current Render
pricing before enabling the service. A persistent disk is required because the
workflow database and approval checkpoints must survive restarts.

## Model Provider

The Blueprint defaults to OpenRouter using the OpenAI-compatible API, model
`openai/gpt-4o-mini`. Configure `OPENAI_API_KEY` in Render's secret environment
settings. To use a different compatible provider, update `OPENAI_BASE_URL`,
`OPENAI_MODEL`, and `OPENAI_API_KEY` there.

Local development defaults to Ollama and remains unauthenticated. To use a
hosted provider locally, set `LLM_PROVIDER=openai_compatible` and configure the
three `OPENAI_*` variables in `backend/.env`.

## Public Demo Limits

- The access code protects API operations with an HTTP-only cookie.
- Git URLs are supported; server-local repository paths and apply-to-source are
  disabled in the public deployment.
- Do not publish provider keys or the access code in the repository, screenshots,
  or the submitted public URL.
- The hosted service runs tests and repository tooling on submitted Git clones.
  Accept repositories only from people you trust with this demo instance.