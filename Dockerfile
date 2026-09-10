FROM python:3.14-slim

# pull the uv binary
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# copy the config files from the root to the container's /app folder
COPY pyproject.toml uv.lock /app/

# install dependencies globally inside the container
# Tell uv to treat pyproject.toml like a standard requirements file
RUN uv pip install --system -r pyproject.toml

# copy app folder content INTO the container's working directory
COPY app/ /app/

EXPOSE 5000

# Gunicorn runs from inside /app, so it looks directly for the app.py file right next to it.
CMD ["uv", "run", "flask", "run", "--host=0.0.0.0", "--port=5000", "--debug"]