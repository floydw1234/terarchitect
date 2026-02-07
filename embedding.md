# Embedding Service

Short summary for the other app: **embedding service** that embeds texts, running on the same machine.

**Base URL:** `http://localhost:9009`

---

## Endpoints

| Method | Path     | Description        |
|--------|----------|--------------------|
| POST   | `/embed` | Embed one or more texts |
| GET    | `/health`| Health check       |
| GET    | `/models`| List available models |

---

## POST /embed

**Request body (JSON):**

```json
{
  "texts": ["first sentence", "second sentence"],
  "model_id": "mpnet-multilingual",
  "normalize": true
}
```

| Field      | Type     | Required | Description |
|------------|----------|----------|-------------|
| `texts`    | string[] | yes      | Strings to embed (max 10 000 per request, each &lt; 10 000 chars by default). |
| `model_id`| string   | yes      | One of: `mpnet-multilingual`, `minilm-fast`, `bge-large`. |
| `normalize`| bool    | no       | L2-normalize vectors (default `true`; use for cosine similarity). |

**Response (200):**

```json
{
  "embeddings": [[0.12, -0.34, ...], [0.56, 0.78, ...]],
  "model_id": "mpnet-multilingual",
  "model_name": "paraphrase-multilingual-mpnet-base-v2",
  "dimension": 768
}
```

---

## Auth (optional)

If the service is started with `API_KEY` set, send the key in the header:

```
X-API-Key: your-secret-api-key-here
```

---

## Examples

**curl:**

```bash
curl -X POST http://localhost:9009/embed \
  -H "Content-Type: application/json" \
  -d '{"texts": ["Hello world", "Another text"], "model_id": "mpnet-multilingual"}'
```

**Python:**

```python
import requests

r = requests.post(
    "http://localhost:9009/embed",
    json={"texts": ["Hello world", "Another text"], "model_id": "mpnet-multilingual"},
    headers={"X-API-Key": "your-key"}  # omit if API_KEY not set on server
)
r.raise_for_status()
data = r.json()
# data["embeddings"] = list of lists of floats, one per text
# data["dimension"] = 768 for mpnet-multilingual
```

---

## Other endpoints

- **Health check:** `GET http://localhost:9009/health`
- **List models:** `GET http://localhost:9009/models`
