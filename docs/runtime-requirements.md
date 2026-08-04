# Runtime requirements

The Python service uses `requirements.txt` (which points to
`backend/requirements.txt`) inside the `roadman` Conda environment.

Optional but recommended travel-search integration:

```powershell
npm i -g @fly-ai/flyai-cli
flyai --help
```

The planning graph calls `flyai search-poi` for attractions and restaurants
and `flyai search-hotel` for accommodation. If the CLI is unavailable, the
graph records a degraded skill result and continues with AMap/OpenTripMap
sources; it never fabricates prices, images, or availability.

Set `OLLAMA_API_KEY` and keep `OLLAMA_MODEL=deepseek-v4-flash:0731-cloud` for
semantic requirement extraction, edit interpretation, and POI ranking.
