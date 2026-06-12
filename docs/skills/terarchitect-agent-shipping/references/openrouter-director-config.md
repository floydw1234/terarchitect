# OpenRouter Director Config

- Use `DIRECTOR_PROVIDER=custom`, `DIRECTOR_LLM_URL=https://openrouter.ai/api`, and a valid OpenRouter model id such as `google/gemini-2.5-flash-lite`.
- Ensure `DIRECTOR_API_KEY` resolves in the runtime that performs the request.
- Smoke test the configured `/v1/chat/completions` path before running tickets.
