# Ottomate Instagram Autoposter

Posts your Ottomate posts, stories and reels to Instagram automatically, at the times in each batch's `schedule.csv`. It runs for free on GitHub, every 15 minutes, even when your computer is off.

Setup takes about 45 minutes, once. Do the parts in order.

---

## Part 1: Make the Instagram a Business account (2 mins)

1. In the Instagram app, open the Ottomate profile.
2. Tap ☰ > **Settings and activity** > **Account type and tools** > **Switch to professional account**.
3. Choose **Business** (not Creator). Stories can only be posted automatically from a Business account.

---

## Part 2: Put the system on GitHub (15 mins)

### 2a. Create the repository
1. Sign up or log in at **github.com**.
2. Click **+** (top right) > **New repository**.
3. Name it `ottomate-instagram`, choose **Public**, tick **Add a README file**, click **Create repository**.

### 2b. Upload the files
1. Unzip `ottomate-instagram.zip` on your computer.
2. On your repository page, click **Add file > Upload files**.
3. Drag in everything from the unzipped folder: `autopost.py`, `config.json`, `requirements.txt`, `README.md`, and the folders `content`, `state` and `workflows-to-paste`.
4. Click **Commit changes**. It may take a minute (the videos are a few MB).

### 2c. Add the two timer files
GitHub needs these in a hidden folder called `.github`, which drag-and-drop often skips, so create them by hand:

1. Click **Add file > Create new file**.
2. In the name box, type exactly: `.github/workflows/post.yml`
3. Open `workflows-to-paste/post.yml` in your repo, click the copy icon, and paste it into the new file.
4. Click **Commit changes**.
5. Repeat for `.github/workflows/refresh-token.yml`, pasting from `workflows-to-paste/refresh-token.yml`.

### 2d. Turn on GitHub Pages (this hosts your images for Instagram)
1. In your repo, go to **Settings > Pages**.
2. Under **Build and deployment**, set Source to **Deploy from a branch**, Branch to **main** and **/ (root)**, then click **Save**.
3. Wait 2–3 minutes, then open this link in your browser (put your GitHub username in):
   `https://YOUR-USERNAME.github.io/ottomate-instagram/content/batch-01-launch-week/intro-1.jpg`
   If you see the "Hi, we're Ottomate" image, it works.

### 2e. Let the system save its posting log
1. Go to **Settings > Actions > General**.
2. Scroll to **Workflow permissions**, choose **Read and write permissions**, click **Save**.

---

## Part 3: Get your Instagram access token (20 mins)

This is the "key" that lets the system post to your account. Meta changes its screens now and then, so names may be slightly different from these.

1. Go to **developers.facebook.com**, log in with Facebook, and register as a developer if asked (free).
2. Click **My Apps > Create App**.
3. App name: `Ottomate Poster`. Add your email.
4. Use case: choose **Manage messaging & content on Instagram**. Click through the rest (skip connecting a business portfolio if asked) and click **Create app**.
5. In the app dashboard, open the Instagram use case and go to **API setup with Instagram login**.
6. Under **Generate access tokens**, click **Add account** and log in as your Ottomate Instagram. Allow the permissions it asks for. It needs to be able to publish content (`instagram_business_content_publish`).
   - **If it says the account must be a tester:** go to **App roles > Roles > Add People > Instagram Tester**, type your Ottomate username, and send. Then in the Instagram app go to **Settings > Website permissions > Apps and websites > Tester invites** and accept. Come back and try step 6 again.
7. Click **Generate token** next to your account and copy the long token.
8. You can leave the app in **Development** mode. That's fine for posting to your own account.

---

## Part 4: Add your secrets to GitHub (5 mins)

### 4a. The Instagram token
1. In your repo: **Settings > Secrets and variables > Actions > New repository secret**.
2. Name: `IG_ACCESS_TOKEN`. Secret: paste the token from Part 3. Click **Add secret**.

### 4b. Auto-renewal key (so the token never expires)
The Instagram token lasts 60 days. Every Monday the system renews it, and it needs this key to save the new one.

1. Go to **github.com/settings/personal-access-tokens** > **Generate new token** (fine-grained).
2. Name: `ottomate-token-refresh`. Expiration: the longest it offers. Put a reminder in your calendar for the expiry date.
3. Repository access: **Only select repositories** > `ottomate-instagram`.
4. Permissions > Repository permissions > **Secrets: Read and write**.
5. Click **Generate token** and copy it.
6. Back in your repo, add another secret: Name `GH_PAT`, Secret: the token you just copied.

---

## Part 5: Test it (5 mins)

1. Open the **Actions** tab. If GitHub asks, click **I understand my workflows, go ahead and enable them**.
2. Click **Post to Instagram** > **Run workflow** > mode **whoami** > **Run workflow**.
   After about 30 seconds, click the run. It should say `Connected to @your_username (account type: BUSINESS)`.
3. Run it again with mode **upcoming**. You'll see the next 15 scheduled items.
4. Click **Refresh Instagram token** > **Run workflow**. It should go green.

### Set your launch date
Open `content/batch-01-launch-week/batch.json`, click the ✏️ pencil, and set `start_date` to the day you want the intro posts to go out (format `YYYY-MM-DD`). Click **Commit changes**. Everything else in the batch moves with it.

**You're live.** The system checks every 15 minutes and posts whatever is due.

---

## Your weekly routine

1. Ask Claude for the next batch. You'll get a zip with one folder inside, like `batch-02-...`.
2. Unzip it. In your repo, open the `content` folder > **Add file > Upload files**, and drag in the batch folder.
3. Click **Commit changes**.

That's it. When you upload, the system automatically checks the batch for mistakes. If something's wrong, the run goes red in the Actions tab and GitHub emails you.

---

## Handy to know

- **Change a time or caption:** open the batch's `schedule.csv`, click ✏️, edit, commit.
- **Move a whole batch:** change `start_date` in its `batch.json`.
- **Pause everything:** **Actions > Post to Instagram > ··· > Disable workflow**. Enable it again to resume.
- **Timing:** GitHub's free timer can run 5–30 minutes late at busy times.
- **Late posts:** if the system misses something by more than 6 hours (for example, while paused), it skips it instead of posting a pile at once. To post it, move the time or `start_date`.
- **Failures:** if Instagram rejects a post, it retries up to 3 times, and GitHub emails you about the failed run. The reason shows in the run log.
- **What's posted:** `state/posted.json` lists everything the system has posted. Don't edit it, or things may post twice.
- **API limits:** you can't add Instagram music, poll or link stickers through the API. Add those by hand if you want them on a particular story.

### The schedule.csv columns

| column | what it means |
|---|---|
| id | a unique name for the row |
| day | 0 = the batch's start_date, 1 = the day after, and so on |
| time | 24-hour UK time, like `08:30` |
| type | `post`, `story`, `reel` or `carousel` |
| media | file name in the batch folder. For a carousel, list several separated by `|` |
| caption | the caption. Stories have no caption |
