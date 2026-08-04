# Runtime requirements

The Python service uses `requirements.txt` (which points to
`backend/requirements.txt`) inside the `roadman` Conda environment.

Travel-search integration (the Docker images install the CLI automatically; a local
Conda run may install it separately):

```powershell
npm i -g @fly-ai/flyai-cli
flyai --help
```

The planning graph calls `flyai search-poi` for attractions/restaurants,
`flyai search-hotel` for accommodation, and the destination-research Agent calls
`flyai keyword-search --query` plus `flyai ai-search --query` for source-backed
must-see and must-eat evidence. If the CLI is unavailable, the
graph records a degraded skill result and continues with AMap/OpenTripMap
sources; it never fabricates prices, images, or availability.

Set `OLLAMA_API_KEY` and keep `OLLAMA_MODEL=deepseek-v4-flash:0731-cloud` for
semantic requirement extraction, edit interpretation, and POI ranking.
Set `FLYAI_API_KEY` when the CLI is not already authenticated with `flyai config`.
