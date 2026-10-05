# Econ Desk: setup guide

Your dashboard has two parts:

- **The dashboard website** (this project). GitHub runs a script three times each weekday that pulls about 90 data series from FRED and 25 news feeds, then publishes a website only you need to know the address of. It's free and needs no computer of yours to be on.
- **The morning brief.** A Claude scheduled task reads the dashboard's snapshot, checks the morning's news, and writes you a summary.

Setup takes about 30 to 45 minutes, once. You won't write any code. Where a step says "click", the button names match what GitHub shows.

---

## Part 1: Get two free accounts

### Step 1. Create a GitHub account
Go to **github.com/signup** and follow the prompts. Your username becomes part of your dashboard's address (`https://your-username.github.io/econ-desk/`), so pick something you're happy to see there.

### Step 2. Get a FRED API key
FRED is the St. Louis Fed's economic database. An "API key" is a password that lets the script download data.

1. Go to **fredaccount.stlouisfed.org** and create an account.
2. Once signed in, open **API Keys** (under My Account) and click **Request API Key**.
3. For the description, write something like "Personal dashboard for economics reporting."
4. Copy the key it gives you (32 letters and numbers) into a note for a few minutes. Treat it like a password.

---

## Part 2: Put the project on GitHub

### Step 3. Create a repository
A repository ("repo") is a project folder on GitHub.

1. Go to **github.com/new**.
2. Repository name: `econ-desk`
3. Choose **Public**. (Free accounts need this for the website. It only contains public economic data; your FRED key stays hidden.)
4. Tick **Add a README file**.
5. Click **Create repository**.

### Step 4. Upload the files
1. Unzip `econ-desk.zip` on your computer and open the `econ-desk` folder.
2. On your repo's page, click **Add file**, then **Upload files**.
3. Drag these into the upload box: the `docs` folder, the `scripts` folder, the `setup` folder, `config.json` and `README.md`.
4. Scroll down and click **Commit changes**. ("Commit" just means save.)

### Step 5. Add the automation file
This file tells GitHub when to run the update. It lives in a folder that starts with a dot, which Mac and Windows hide, so you'll create it directly on GitHub instead of uploading it.

1. On your computer, open `setup/update.yml.txt` in any text editor and copy everything.
2. On your repo's page, click **Add file**, then **Create new file**.
3. In the name box, type exactly: `.github/workflows/update.yml`
   (Typing each `/` creates a folder. That's expected.)
4. Paste the contents into the big box.
5. Click **Commit changes**, then **Commit changes** again in the pop-up.

### Step 6. Add your FRED key as a secret
1. In your repo, click **Settings** (top right of the repo, not your account settings).
2. In the left menu: **Secrets and variables**, then **Actions**.
3. Click **New repository secret**.
4. Name: `FRED_API_KEY` (exactly like that)
5. Secret: paste your FRED key.
6. Click **Add secret**.

### Step 7. Turn on the website
1. Still in **Settings**, click **Pages** in the left menu.
2. Under **Build and deployment**, set **Source** to **GitHub Actions**.

### Step 8. Run the first update
1. Click the **Actions** tab at the top of your repo.
2. If GitHub asks whether to enable workflows, click the green button to enable them.
3. Click **Update dashboard** on the left, then **Run workflow**, then the green **Run workflow** button.
4. Wait about two minutes. A green check means it worked.

### Step 9. Open your dashboard
Go to `https://your-username.github.io/econ-desk/` (with your username). Bookmark it. On your phone, open it in the browser and use **Add to Home Screen** to get an app-like icon.

From now on it updates itself on weekdays at about 6:30 am, 9:45 am and 6:15 pm Eastern (an hour earlier in winter). GitHub sometimes starts scheduled runs 5 to 30 minutes late at busy times; that's normal.

---

## Part 3: Set up the morning brief

This uses a scheduled task in Claude Cowork, which needs a paid Claude plan (Pro or above). Scheduled tasks run on Anthropic's servers, so your computer doesn't need to be on.

1. Open `setup/morning-brief-prompt.md` and replace `YOUR-USERNAME` with your GitHub username in both places.
2. In the Claude app, open Cowork and click **Scheduled** in the left sidebar, then **New task**.
3. Paste the prompt as the task's instructions.
4. Set it to run weekdays at 10:00 am Eastern, after the morning data update.

You'll find each day's brief under **Scheduled**.

---

## Everyday use

**Tabs.** Today (policy rate, next Fed meeting, headline numbers, calendar, top stories), The Fed (rate corridor, balance sheet, plumbing, meeting dates), Data, Markets, Money, Global, Reading (every headline, filterable), and Notebook (story ideas saved in your browser).

**Click any row** to open its full chart with 1-year to full-history ranges and a link to the series on FRED.

**The balance sheet** can switch between assets, Treasuries by type (bills, notes and bonds, TIPS) and liabilities (currency, reserves, reverse repos, the Treasury's account). Hover to see every component for a week. The milestone lines mark QE and QT dates.

**"Highest since" notes** in gold appear when the latest reading beats at least several prior periods. They're your headline language, but always confirm them in the original release.

**The status note** at the bottom of the page lists any series or news feed that failed on the last update.

## Changing what's on the dashboard

Everything lives in `config.json`: which series appear, their names, the headline tiles, news feeds, FOMC dates and your own calendar events.

To edit it on GitHub: open `config.json`, click the pencil icon, make the change, and click **Commit changes**. The next update uses it, or run one yourself from the Actions tab.

The easiest way to make changes is to paste `config.json` into a chat with Claude and describe what you want ("add the Chicago Fed National Activity Index", "add a Google News feed about tariffs"). Then paste the result back.

To find a FRED series id, search at fred.stlouisfed.org; the id is the code at the end of the page's address (for example, `UNRATE`).

**Keep the FOMC dates current.** The Fed usually publishes the next year's schedule in the summer. Add it to `fomc_meetings` when it does.

## Troubleshooting

**The Actions run shows a red X.** Click the run, then the red step, and read the message.
- "FRED_API_KEY is missing" or "No series downloaded": redo Step 6, checking the name is exactly `FRED_API_KEY` and the key has no extra spaces.
- An error mentioning `config.json`: an edit broke the file's format. Paste it into jsonlint.com to find the problem (usually a missing or extra comma), or ask Claude to fix it.
- "Branch is not allowed to deploy to github-pages": go to **Settings**, then **Environments**, then **github-pages**, and make sure your main branch is allowed.

**The website shows "Page not found".** Check Step 7 (Source must be GitHub Actions), then run the update again.

**The website shows "No data yet".** The first update hasn't finished successfully. Go to Step 8.

**Scheduled runs stopped.** GitHub pauses scheduled jobs on repositories with no activity for 60 days and emails you. Open the Actions tab and re-enable the workflow.

## What this costs

Nothing. GitHub Actions and Pages are free for public repositories, and FRED is free. The morning brief uses your existing Claude plan.

## Where the data comes from

Economic and market data: FRED, Federal Reserve Bank of St. Louis. Some series have licensing limits on FRED (for example, credit spreads show about three years of history, and the S&P 500 about ten). The ISM surveys aren't on FRED, so the morning brief covers them from the news. Headlines link to their publishers; many are paywalled.
