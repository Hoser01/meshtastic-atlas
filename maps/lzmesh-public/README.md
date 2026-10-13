# LZMesh Public Map

The public LZMesh node map is an ATLAS presentation surface. It reads the
curated ATLAS map feed and does not collect Meshtastic traffic itself.

## Configuration

Runtime configuration stays outside Git. Supported environment variables are:

- `ATLAS_FEED_URL`
- `ATLAS_TIMEOUT_SECONDS`
- `ATLAS_CACHE_SECONDS`
- `NEXT_PUBLIC_CARTO_API_KEY`

## Local check

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/flask --app app run --port 5010
curl -fsS http://127.0.0.1:5010/health
```

Generated packages and credentials must not be committed.
