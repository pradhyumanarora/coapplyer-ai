# CoApplyer AI

CoApplyer AI automates LinkedIn job discovery and Easy Apply flows using a Playwright MCP-first browser runtime, with Selenium available as an explicit fallback.

You do not need Claude Code to run this project. You can run it from PowerShell or from VS Code using the included tasks and launch profiles.

## What this tool does

- Loads your profile, resume, and job preferences from `data_folder/`.
- Opens LinkedIn in a persistent Chrome profile.
- Searches and filters jobs.
- Fills Easy Apply forms with verified inputs.
- Pauses before final submit for human confirmation.

## Important setup facts

- Run the code from the repository root:
  `C:\Users\pradarora\MyFiles\Auto_Jobs_Applier_AI_Agent`
- The app resolves the Chrome profile from the current working directory, so running from another folder can break the browser profile path.
- Playwright MCP is started automatically by the app through `npx @playwright/mcp@latest`.
- If Playwright MCP cannot start, the app falls back to Selenium unless you explicitly force Selenium with `--selenium`.

## Prerequisites

Install these before first run:

- Python 3.12 or newer
- Google Chrome or Chromium
- Node.js LTS with `npx` available on PATH
- A LinkedIn account already logged in inside the local Chrome profile

## One-time onboarding

### 1) Clone or open the repo

Open a terminal in the repository root.

### 2) Create and activate the virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3) Install Python dependencies

```powershell
pip install -r requirements.txt
```

### 4) Prepare the local data folder

Copy the example folder and fill in your details:

- `data_folder_example/` -> `data_folder/`

You need these files in `data_folder/`:

- `secrets.yaml` - your LLM API key
- `work_preferences.yaml` - job search preferences
- `plain_text_resume.yaml` - your resume content

### 5) Configure the app

If needed, copy `config.example.py` to `config.py` and set your local values.

Key settings:

- `BROWSER_ENGINE = "playwright"`
- `REQUIRE_HUMAN_CONFIRMATION_FOR_SUBMIT = True`

### 6) Make sure Chrome is logged in

The app uses the local Chrome profile under `chrome_profile/linkedin_profile`.

If LinkedIn is not already signed in there, open Chrome with that profile once and log in manually before running the app.

## How to make sure Playwright MCP starts

The app starts Playwright MCP automatically when you run the tool.

To verify your environment:

```powershell
npx @playwright/mcp@latest --help
```

Run that command from the repository root.

The first time you run it, `npx` may prompt to install `@playwright/mcp`. That is expected. Answer `Y` to continue.

If that command fails, install or repair Node.js / `npx` first.

When the app starts, you should see it using the Playwright MCP path in the logs. If the MCP process cannot start, the app logs a warning and falls back to Selenium.

## Where to run the code

Run everything from the repository root in PowerShell or VS Code Terminal.

Do not run it from inside `src/` or `docs/`.

## Using VS Code

If you prefer VS Code, open the repository and use the built-in tasks:

- `Playwright MCP: verify install`
- `CoApplyer AI: demo run`
- `CoApplyer AI: collect-only run`
- `CoApplyer AI: selenium run`
- `CoApplyer AI: test suite`

You can also launch the demo directly from the Run and Debug panel with `CoApplyer AI: demo`.

## Common commands

### Demo run

Fastest onboarding/demo path:

```powershell
.\.venv\Scripts\python.exe .\main.py --resume "C:\Users\pradarora\MyFiles\Auto_Jobs_Applier_AI_Agent\PradhyumanResume.pdf" --demo
```

### Collect-only run

```powershell
.\.venv\Scripts\python.exe .\main.py --resume "C:\Users\pradarora\MyFiles\Auto_Jobs_Applier_AI_Agent\PradhyumanResume.pdf" --collect
```

### Force Selenium fallback

```powershell
.\.venv\Scripts\python.exe .\main.py --resume "C:\Users\pradarora\MyFiles\Auto_Jobs_Applier_AI_Agent\PradhyumanResume.pdf" --selenium
```

## Recommended first run

Use demo mode first so you can verify the full browser chain without attempting multiple applications.

```powershell
.\.venv\Scripts\python.exe .\main.py --resume "C:\Users\pradarora\MyFiles\Auto_Jobs_Applier_AI_Agent\PradhyumanResume.pdf" --demo
```

## What the demo mode does

- Uses the default Playwright MCP path when available.
- Limits the run to one application attempt.
- Keeps the final submit step behind a human-confirmation gate.

## Troubleshooting

### Playwright MCP does not start

- Confirm Node.js and `npx` are installed.
- Run the `npx @playwright/mcp@latest --help` check above.
- If needed, rerun with `--selenium` to use the legacy browser path.

### Chrome profile issues

- Make sure you are launching from the repo root.
- Check that `chrome_profile/linkedin_profile` exists.
- Reopen Chrome with that profile and log in to LinkedIn again if session data is stale.

### Missing data folder files

- Copy `data_folder_example/` to `data_folder/`.
- Make sure `secrets.yaml`, `work_preferences.yaml`, and `plain_text_resume.yaml` are present.

## Verification

The repository includes automated tests for runtime selection, login, and apply behavior.

To run them:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -x --tb=short -q
```

## Related docs

- [Playwright MCP roadmap](docs/playwright-mcp-roadmap.md)
- [Playwright MCP learnings](docs/playwright-mcp-learnings.md)
- [Demo quickstart](docs/demo-quickstart.md)
