# RatingRelay

# Development

- Dependencies
    - [pnpm](https://pnpm.io/)
    - [uv](https://docs.astral.sh/uv/)

- Backend
```shell
uv sync
uv run alembic upgrade head  # apply database migrations (required before first run and after pulling new migrations)
uv run app.py
```
- Frontend
```shell
cd frontend
pnpm install
pnpm run dev
```
