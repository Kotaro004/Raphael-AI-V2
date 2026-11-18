<div align="center">

# Raphael

### *The AI that thinks with you, not just for you.*

![Python](https://img.shields.io/badge/Python-3.12+-blue?style=flat-square&logo=python)
![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)

</div>

Raphael is a self-hostable AI chat application built with Flask and the Anthropic API. It includes conversation history, owner authentication, persistent memory management, web search, and temporary file attachments.

## Repository layout

- `raphael/`: application source, templates, static assets, tests, and dependency files.
- `.github/workflows/`: automated security and test workflow.

## Requirements

- Python 3.12
- An Anthropic API key
- Optional Redis-compatible storage for production rate limiting

## Quick start

```bash
git clone https://github.com/Kotaro004/Raphael-AI-V2.git
cd Raphael-AI-V2/raphael
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
cp .env.example .env
```

Edit `.env` and provide your own values for:

- `FLASK_SECRET_KEY`: generate a long random value.
- `RAPHAEL_USERNAME`: owner login name.
- `RAPHAEL_PASSWORD_HASH`: Werkzeug password hash.
- `ANTHROPIC_API_KEY`: your private Anthropic API key.

Never commit `.env`, real API keys, passwords, database files, or conversation data. The example file contains placeholders only.

Generate a password hash:

```bash
python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('YOUR_PASSWORD'))"
```

Run locally:

```bash
python app.py
```

Open http://127.0.0.1:5000.

## Tests

From the `raphael/` directory:

```bash
pytest -q
```

## Production

Read [raphael/SECURITY.md](raphael/SECURITY.md) before deployment. Production requires HTTPS, a production WSGI server, a strong Flask secret, trusted host configuration, and shared rate-limit storage such as Redis. Do not expose the development server directly to the Internet.

## Security

Report vulnerabilities privately to the repository owner rather than publishing exploit details in an issue. Do not submit secrets or personal conversation data in bug reports.

## License

MIT. See [LICENSE](LICENSE).
