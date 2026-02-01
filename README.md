# Menu Bot

A family meal planning Slack bot that helps you:
- Collect and organize recipes from various sources (URLs, text, cookbook photos)
- Generate weekly meal plans optimized for your family's preferences
- Create grocery lists organized by store
- Sync shopping lists to Google Tasks

Built for Google Cloud with cost-efficient serverless architecture.

## Features

- **Recipe Ingestion**: Share recipe URLs (NYT Cooking, AllRecipes, etc.), text descriptions, or photos of cookbook pages - the bot extracts and stores structured recipe data using Claude AI
- **Smart Meal Planning**: Generates weekly plans considering kid preferences (weighted higher), seasonal produce, health balance, and avoiding recent repeats
- **Store-Optimized Grocery Lists**: Automatically assigns items to your preferred stores based on categories (produce to Trader Joe's, bulk to Costco, etc.)
- **Family Access Controls**: Parents approve meal plans and grocery lists before they're finalized
- **Kid-Friendly Ratings**: Simple emoji-based ratings for kids, star ratings for adults
- **Google Tasks Sync**: Export grocery lists to Google Tasks for easy mobile access while shopping

## Architecture

```
Slack Workspace <-> Cloud Run (Bot) <-> Firestore (Data)
                         |
                         +-> Claude API (AI)
                         +-> Google Tasks API

Cloud Scheduler -> Cloud Functions (Weekly tasks)
```

**Estimated Monthly Cost**: $5-20 (mainly Claude API usage)

## Prerequisites

- Google Cloud account with billing enabled
- Slack workspace where you can install apps
- Anthropic API key (for Claude)
- Python 3.11+

## Setup Guide

> **Note**: These steps are ordered to handle dependencies correctly. Some steps require
> going back to update earlier configurations once URLs are available.

### 1. Google Cloud Project Setup

```bash
# Create a new project (or use existing)
gcloud projects create menu-bot-family --name="Menu Bot"
gcloud config set project menu-bot-family

# Enable required APIs
gcloud services enable \
  cloudbuild.googleapis.com \
  run.googleapis.com \
  firestore.googleapis.com \
  cloudscheduler.googleapis.com \
  cloudfunctions.googleapis.com \
  secretmanager.googleapis.com \
  tasks.googleapis.com

# Create Firestore database
gcloud firestore databases create --location=us-central1
```

### 2. Get API Keys (No Dependencies)

#### Anthropic API Key

1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Create an API key
3. Save it somewhere secure - you'll need it in Step 5

### 3. Slack App Setup - Part 1 (Create App & Get Credentials)

We'll create the Slack app and get credentials first, then configure URLs after Cloud Run is deployed.

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and click "Create New App"
2. Choose "From scratch" and name it "Menu Bot"
3. Select your family workspace

#### OAuth & Permissions

Navigate to **OAuth & Permissions** and add these **Bot Token Scopes**:
- `app_mentions:read` - Respond to @mentions
- `channels:history` - Read messages in channels
- `channels:read` - View channel info
- `chat:write` - Send messages
- `commands` - Add slash commands
- `files:read` - Access shared files (for cookbook photos)
- `reactions:read` - See reactions
- `reactions:write` - Add reactions
- `users:read` - Get user info
- `im:write` - Send DMs

#### Install App to Workspace

1. Still in **OAuth & Permissions**, click "Install to Workspace"
2. Authorize the app
3. Copy the **Bot User OAuth Token** (starts with `xoxb-`) - save this for Step 5

#### Get Signing Secret

1. Navigate to **Basic Information**
2. Under "App Credentials", copy the **Signing Secret** - save this for Step 5

> **Stop here for Slack setup** - we'll configure Event Subscriptions, Slash Commands,
> and Interactivity after deploying Cloud Run (Step 6).

### 4. Google OAuth Setup - Part 1 (Create Credentials)

We need to create OAuth credentials now, but we'll update the redirect URI after deployment.

1. Go to [Google Cloud Console > APIs & Services > OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent)
2. Configure the consent screen:
   - User Type: **External** (or Internal if using Google Workspace)
   - App name: "Menu Bot"
   - User support email: your email
   - Developer contact: your email
   - Click Save and Continue through the remaining steps

3. Go to [Credentials](https://console.cloud.google.com/apis/credentials)
4. Click "Create Credentials" > "OAuth client ID"
5. Application type: **Web application**
6. Name: "Menu Bot"
7. For now, leave "Authorized redirect URIs" empty (we'll add it in Step 7)
8. Click Create
9. Copy the **Client ID** and **Client Secret** - save these for Step 5

### 5. Configure Secrets in Google Cloud

Now store all the credentials you've collected:

```bash
# Store Slack credentials
echo -n "xoxb-your-actual-token" | gcloud secrets create slack-bot-token --data-file=-
echo -n "your-actual-signing-secret" | gcloud secrets create slack-signing-secret --data-file=-

# Store Anthropic API key
echo -n "sk-ant-your-actual-key" | gcloud secrets create anthropic-api-key --data-file=-

# Store Google OAuth credentials
echo -n "your-client-id.apps.googleusercontent.com" | gcloud secrets create google-oauth-client-id --data-file=-
echo -n "your-client-secret" | gcloud secrets create google-oauth-client-secret --data-file=-
```

### 6. Deploy to Cloud Run

Deploy the bot to get your Cloud Run URL:

```bash
# Set your project ID
export PROJECT_ID=$(gcloud config get-value project)

# Deploy (first time - redirect URI will be updated after we get the URL)
gcloud run deploy menu-bot \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-secrets="SLACK_BOT_TOKEN=slack-bot-token:latest,SLACK_SIGNING_SECRET=slack-signing-secret:latest,ANTHROPIC_API_KEY=anthropic-api-key:latest,GOOGLE_OAUTH_CLIENT_ID=google-oauth-client-id:latest,GOOGLE_OAUTH_CLIENT_SECRET=google-oauth-client-secret:latest" \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=$PROJECT_ID"
```

After deployment completes, note your **Service URL** - it will look like:
```
https://menu-bot-XXXXXXXXXX-uc.a.run.app
```

Save this URL - you'll need it for the next steps!

### 7. Complete Slack App Setup (Requires Cloud Run URL)

Now go back to your Slack app configuration at [api.slack.com/apps](https://api.slack.com/apps) and select your Menu Bot app.

#### Event Subscriptions

1. Navigate to **Event Subscriptions**
2. Toggle "Enable Events" to **On**
3. Set Request URL to: `https://YOUR_CLOUD_RUN_URL/slack/events`
   - Replace `YOUR_CLOUD_RUN_URL` with your actual URL from Step 6
   - Slack will verify the URL - wait for the green "Verified" checkmark
4. Under "Subscribe to bot events", add:
   - `app_home_opened`
   - `app_mention`
   - `message.channels`
5. Click **Save Changes**

#### Slash Commands

Navigate to **Slash Commands** and create each command below.
For each command, set the Request URL to: `https://YOUR_CLOUD_RUN_URL/slack/events`

| Command | Description |
|---------|-------------|
| `/menu-setup` | Initial family setup |
| `/menu-help` | Show available commands |
| `/menu-add-recipe` | Add a recipe |
| `/menu-recipes` | List all recipes |
| `/menu-plan` | Show/generate meal plan |
| `/menu-approve-plan` | Approve pending plan |
| `/menu-grocery` | Show/generate grocery list |
| `/menu-approve-grocery` | Approve pending list |
| `/menu-rate` | Rate a meal |
| `/menu-feedback` | Share detailed feedback |
| `/menu-link-tasks` | Connect Google Tasks |
| `/menu-add-parent` | Add a parent |
| `/menu-add-kid` | Add a kid |
| `/menu-add-favorites` | Add favorite meals |

#### Interactivity & Shortcuts

1. Navigate to **Interactivity & Shortcuts**
2. Toggle "Interactivity" to **On**
3. Set Request URL to: `https://YOUR_CLOUD_RUN_URL/slack/events`
4. Click **Save Changes**

### 8. Complete Google OAuth Setup (Requires Cloud Run URL)

Go back to [Google Cloud Console > APIs & Services > Credentials](https://console.cloud.google.com/apis/credentials):

1. Click on your "Menu Bot" OAuth 2.0 Client ID
2. Under "Authorized redirect URIs", click **Add URI**
3. Add: `https://YOUR_CLOUD_RUN_URL/oauth/callback`
4. Click **Save**

Now redeploy Cloud Run with the correct OAuth redirect URI:

```bash
export CLOUD_RUN_URL="https://menu-bot-XXXXXXXXXX-uc.a.run.app"  # Your actual URL

gcloud run services update menu-bot \
  --region us-central1 \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_OAUTH_REDIRECT_URI=$CLOUD_RUN_URL/oauth/callback"
```

### 9. Deploy Cloud Functions (Scheduled Tasks)

```bash
# Weekly meal plan generator (runs Saturday 9 AM)
gcloud functions deploy weekly-planner \
  --gen2 \
  --runtime python311 \
  --region us-central1 \
  --source . \
  --entry-point generate_weekly_plan \
  --trigger-http \
  --allow-unauthenticated \
  --set-secrets="SLACK_BOT_TOKEN=slack-bot-token:latest,ANTHROPIC_API_KEY=anthropic-api-key:latest" \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=$PROJECT_ID"

# Daily feedback prompt (runs daily 7 PM)
gcloud functions deploy feedback-prompt \
  --gen2 \
  --runtime python311 \
  --region us-central1 \
  --source . \
  --entry-point prompt_meal_feedback \
  --trigger-http \
  --allow-unauthenticated \
  --set-secrets="SLACK_BOT_TOKEN=slack-bot-token:latest" \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=$PROJECT_ID"

# Create Cloud Scheduler jobs
gcloud scheduler jobs create http weekly-meal-plan \
  --location us-central1 \
  --schedule "0 9 * * 6" \
  --uri "https://us-central1-$PROJECT_ID.cloudfunctions.net/weekly-planner" \
  --time-zone "America/Detroit"

gcloud scheduler jobs create http daily-feedback \
  --location us-central1 \
  --schedule "0 19 * * *" \
  --uri "https://us-central1-$PROJECT_ID.cloudfunctions.net/feedback-prompt" \
  --time-zone "America/Detroit"
```

### 10. Initial Setup in Slack

1. Invite the bot to your meal planning channel: `/invite @Menu Bot`
2. Run `/menu-setup` to configure your family
3. Add some favorite meals with `/menu-add-favorites`
4. Start sharing recipe links!

## Usage

### Adding Recipes

**Share a URL:**
```
Just paste a recipe URL in the channel:
https://cooking.nytimes.com/recipes/1234-chicken-parmesan
```

**Share text:**
```
/menu-add-recipe Grandma's Meatloaf: 2 lbs ground beef, 1 egg, breadcrumbs...
```

**Share a photo:**
Upload a photo of a cookbook page and mention the bot or add "recipe" in the caption.

### Meal Planning

```
/menu-plan new     # Generate a new weekly plan
/menu-plan         # Show current plan
```

Parents can approve plans via buttons or:
```
/menu-approve-plan
```

### Grocery Lists

```
/menu-grocery new   # Generate from current meal plan
/menu-grocery       # Show current list
/menu-grocery text  # Get as plain text for copy/paste
```

### Rating Meals

After dinner, the bot will prompt for ratings. Kids tap emoji buttons, adults select stars.

```
/menu-rate          # Manually rate today's meal
/menu-feedback The pasta needed more salt  # Detailed feedback
```

### Google Tasks

```
/menu-link-tasks    # Connect your Google account
```

Then click "Send to Google Tasks" on any approved grocery list.

## Store Configuration

Edit `config/stores.yaml` to customize store preferences:

```yaml
stores:
  trader_joes:
    priority_categories:
      - produce
      - cheese
    # Items in these categories go to Trader Joe's
```

## Seasonal Produce

The bot uses `config/seasonal.yaml` for Michigan seasonal awareness. Edit for your region.

## Access Controls

- **Parents**: Can approve meal plans, grocery lists, and add recipes that are immediately available
- **Kids**: Can rate meals and add recipes (require parent approval)
- **Anyone**: Can view plans and lists, share recipes

## Local Development

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
# Edit .env with your credentials

# Run locally
python -m src.bot.app
```

Use [ngrok](https://ngrok.com) to expose your local server for Slack:
```bash
ngrok http 8080
```

## Troubleshooting

**Bot doesn't respond to messages:**
- Check Event Subscriptions URL is correct and verified
- Ensure bot is invited to the channel
- Check Cloud Run logs: `gcloud run logs read menu-bot`

**Recipe extraction fails:**
- Some sites block scraping - try a different recipe URL
- Check Anthropic API key is valid
- Check Cloud Run logs for errors

**Google Tasks sync fails:**
- Re-run `/menu-link-tasks` to refresh OAuth
- Check OAuth redirect URI matches deployment URL

## Cost Optimization

- Cloud Run scales to zero when idle
- Firestore free tier covers most family usage
- Claude API is the main cost driver:
  - Recipe extraction: ~$0.01-0.05 per recipe
  - Meal planning: ~$0.02-0.05 per plan
  - Limit to ~10 new recipes/week to stay under $10/month

## License

MIT
