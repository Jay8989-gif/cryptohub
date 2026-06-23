# Going live — step-by-step (no command line needed)

This puts your site online for free and keeps the Smart-Money Tracker updating
itself every 30 minutes in the cloud — independent of your computer.

The pieces:
- **GitHub** — stores the files and runs the auto-refresh job.
- **GitHub Desktop** — a simple app to upload changes (no terminal).
- **Netlify** — hosts the website and redeploys automatically on every change.

---

## 0. One-time cleanup
There is a leftover empty `.git` folder in this project folder that got created by accident.
In File Explorer: View → Show → **Hidden items**, then delete the `.git` folder
(leave `.github` — that one is needed). GitHub Desktop will create a fresh one.

---

## 1. Create a free GitHub account
1. Go to https://github.com and sign up (use your email).
2. Verify your email.

## 2. Install GitHub Desktop and add the project
1. Download from https://desktop.github.com and install. Sign in with your GitHub account.
2. **File → Add local repository →** browse to this folder
   (`...\Documents\Claude\Projects\Hyperliquid`).
3. It will say it's not a git repository yet → click **"create a repository"**.
   - Name: `cryptohub` (or anything). Leave the rest default. Click **Create repository**.
4. You'll see all the files listed as changes. Click **Commit to main** (bottom-left).
5. Click **Publish repository** (top). 
   - **Recommended: leave "Keep this code private" UNCHECKED (public).** See the note on
     Actions minutes below. Click **Publish**.

Your code is now on GitHub.

## 3. Turn on the auto-refresh job
1. On https://github.com open your new `cryptohub` repository.
2. **Settings → Actions → General →** scroll to **Workflow permissions** →
   select **"Read and write permissions"** → **Save**.
   (This lets the job push the updated `data.json` back.)
3. Go to the **Actions** tab. If prompted, click **"I understand my workflows, enable them"**.
4. Click **"Refresh smart-money data"** → **Run workflow** → **Run workflow** to test it now.
   After ~1 minute it should show a green check and a new commit "refresh data.json".

From now on it runs automatically every 30 minutes.

## 4. Put the site online with Netlify
1. Go to https://www.netlify.com and **Sign up with GitHub** (one click).
2. **Add new site → Import an existing project → GitHub →** authorize → pick `cryptohub`.
3. Build settings: leave **Build command empty**, **Publish directory = `.`** (already set by
   `netlify.toml`). Click **Deploy**.
4. In ~30 seconds you get a live URL like `random-name.netlify.app`.
   Rename it under **Site configuration → Change site name**, or add a custom domain later.

**Done.** Visit the URL — homepage prices, Cards, Exchanges, and the live tracker all work.

---

## How we keep working together
1. You ask me for a change; I edit the files in this folder.
2. Open **GitHub Desktop** → it shows the changes → **Commit to main** → **Push origin**.
3. Netlify redeploys automatically in ~30 seconds. The live site updates.

That's the whole loop. The tracker's data refreshes on its own via the cron.

---

## Two things to know

**Pause the local refresh task.** Now that the cloud cron updates `data.json`, the old
in-app scheduled task ("hyperliquid-refresh-data") would edit your *local* copy and create
conflicts in GitHub Desktop. Pause it in the app's **Scheduled** section. (We can keep it only
if you ever want to run things locally.)

**Public repo + the paywall.** A free static site means `data.json` is publicly fetchable, so
the future paywall would gate the *interface*, not the raw data. That's normal for an MVP. When
you're ready to truly gate the data, the next step is moving the engine behind a small
authenticated API — we'll tackle that with the payment integration.

> If you prefer a **private** repo: GitHub's free tier gives 2,000 Actions minutes/month.
> A 30-min cron uses roughly that much, so either keep the repo public (unlimited minutes) or
> change the cron in `.github/workflows/refresh-data.yml` from `*/30` to hourly (`0 * * * *`).
