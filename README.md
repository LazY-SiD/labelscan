# LabelScan

LabelScan turns packaged-food ingredient labels into plain-language nutrition and additive-risk information. It can search Blinkit products or analyze a label photo and suggest alternative products.

Blinkit search uses an anonymous consumer-web session and an `impit` Chrome-compatible TLS bridge. Blinkit's interface is unofficial and may require maintenance if Blinkit changes its private web API.

## Run locally

1. Install Python 3.11+ and Node.js 20+.
2. Create and activate a Python virtual environment.
3. Install dependencies with `pip install -r requirements.txt` and `npm install --omit=dev`.
4. Set the `OPENROUTER_API_KEY` environment variable.
5. Run `python app.py` and open `http://127.0.0.1:5000`.

When the host's IP is blocked by Blinkit, either set `BLINKIT_PROXY_URL` to one fixed
Indian residential proxy URL or configure rotating sticky sessions with:

```env
BLINKIT_PROXY_HOST=92.204.164.15
BLINKIT_PROXY_PORT=10000
BLINKIT_PROXY_USER_BASE=your-user-base-without-session-suffix
BLINKIT_PROXY_PASSWORD=your-secret
BLINKIT_PROXY_SESSION_SECONDS=780
```

The managed configuration appends `-session-<random-id>` to the username, reuses that
session for about 13 minutes, and rotates it on request failures. Blinkit requests are
retried at most three times with 2- and 5-second delays. Proxy credentials remain in
`.env` and are never logged. Do not commit `.env`.

## Deploy

The included `apprunner.yaml` configures deployment to AWS App Runner. Store `OPENROUTER_API_KEY` in AWS Secrets Manager and expose it to the service as an environment variable.
