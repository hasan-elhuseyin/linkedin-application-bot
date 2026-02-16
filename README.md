# LinkedIn Jobs Easy Apply Helper

This connects to an existing Chrome session via the remote debugging port and automates the Easy Apply flow while keeping you in control of final submission.

## 1) Start Chrome with Remote Debugging

On macOS, start a dedicated Chrome instance with a separate profile:

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-linkedin-debug
```

Then open this URL in that Chrome window:

```
https://www.linkedin.com/jobs/
```

If you want to use your usual profile, replace `--user-data-dir` with a real folder (not your default Chrome profile) to avoid locking conflicts.

## 2) Install Dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

## 3) Configure

Edit `config.yaml` to set filters and behavior.

## 4) Run

```bash
python app.py
```

Stop anytime with Ctrl+C.

## Notes

- The bot will try to apply filters automatically. If LinkedIn changes the UI, it will prompt you to set filters manually.
- It will not click the final Submit button. When the submit step is reached, you click it in the browser.
- Applications are tracked in `state/applied.json` so the bot skips jobs it already handled.
