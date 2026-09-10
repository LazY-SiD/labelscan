# LabelScan

LabelScan turns packaged-food ingredient labels into plain-language nutrition and additive-risk information. It can search Blinkit products or analyze a label photo and suggest alternative products.

## Run locally

1. Create and activate a Python virtual environment.
2. Install dependencies with `pip install -r requirements.txt`.
3. Set the `OPENROUTER_API_KEY` environment variable.
4. Run `python app.py` and open `http://127.0.0.1:5000`.

## Deploy

The included `apprunner.yaml` configures deployment to AWS App Runner. Store `OPENROUTER_API_KEY` in AWS Secrets Manager and expose it to the service as an environment variable.

