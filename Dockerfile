FROM python:3.12-slim

# Don't buffer stdout/stderr so logs show up immediately in `docker logs`.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install dependencies first so the layer is cached until requirements change.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code.
COPY . .

# Run as an unprivileged user.
RUN useradd --create-home --uid 1000 screenmate \
    && chown -R screenmate:screenmate /app
USER screenmate

CMD ["python", "-u", "bot.py"]
