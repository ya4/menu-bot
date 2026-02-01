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
  secretmanager.googleapis.com

# Create Firestore database
gcloud firestore databases create --location=us-central1
```

### 2. Slack App Setup

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and click "Create New App"
2. Choose "From scratch" and name it "Menu Bot"
3. Select your family workspace

#### OAuth & Permissions

Add these **Bot Token Scopes**:
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

#### Event Subscriptions

1. Enable Events
2. Set Request URL to: `https://YOUR_CLOUD_RUN_URL/slack/events`
3. Subscribe to bot events:
   - `app_home_opened`
   - `app_mention`
   - `message.channels`

#### Slash Commands

Create these commands (all pointing to `https://YOUR_CLOUD_RUN_URL/slack/events`):

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

#### Interactivity

1. Enable Interactivity
2. Set Request URL to: `https://YOUR_CLOUD_RUN_URL/slack/interactions`

#### Install App

1. Click "Install to Workspace"
2. Copy the **Bot User OAuth Token** (starts with `xoxb-`)
3. Copy the **Signing Secret** from Basic Information

### 3. Google OAuth Setup (for Google Tasks)

1. Go to [Google Cloud Console > APIs & Services > Credentials](https://console.cloud.google.com/apis/credentials)
2. Click "Create Credentials" > "OAuth client ID"
3. Application type: "Web application"
4. Name: "Menu Bot"
5. Authorized redirect URIs: `https://YOUR_CLOUD_RUN_URL/oauth/callback`
6. Copy the Client ID and Client Secret

### 4. Anthropic API Key

1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Create an API key
3. Copy it for later

### 5. Configure Secrets

```bash
# Store secrets in Google Secret Manager
echo -n "xoxb-your-token" | gcloud secrets create slack-bot-token --data-file=-
echo -n "your-signing-secret" | gcloud secrets create slack-signing-secret --data-file=-
echo -n "sk-ant-your-key" | gcloud secrets create anthropic-api-key --data-file=-
echo -n "your-oauth-client-id" | gcloud secrets create google-oauth-client-id --data-file=-
echo -n "your-oauth-client-secret" | gcloud secrets create google-oauth-client-secret --data-file=-
```

### 6. Deploy to Cloud Run

```bash
# Build and deploy
gcloud builds submit --config cloudbuild.yaml

# Or deploy manually
gcloud run deploy menu-bot \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-secrets="SLACK_BOT_TOKEN=slack-bot-token:latest,SLACK_SIGNING_SECRET=slack-signing-secret:latest,ANTHROPIC_API_KEY=anthropic-api-key:latest,GOOGLE_OAUTH_CLIENT_ID=google-oauth-client-id:latest,GOOGLE_OAUTH_CLIENT_SECRET=google-oauth-client-secret:latest" \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_OAUTH_REDIRECT_URI=https://menu-bot-HASH-uc.a.run.app/oauth/callback"
```

After deployment, update your Slack app's URLs with the Cloud Run URL.

### 7. Deploy Cloud Functions

```bash
# Weekly meal plan generator (runs Saturday 9 AM)
gcloud functions deploy weekly-planner \
  --gen2 \
  --runtime python311 \
  --region us-central1 \
  --source ./src/functions \
  --entry-point generate_weekly_plan \
  --trigger-http \
  --set-secrets="SLACK_BOT_TOKEN=slack-bot-token:latest,ANTHROPIC_API_KEY=anthropic-api-key:latest"

# Daily feedback prompt (runs daily 7 PM)
gcloud functions deploy feedback-prompt \
  --gen2 \
  --runtime python311 \
  --region us-central1 \
  --source ./src/functions \
  --entry-point prompt_meal_feedback \
  --trigger-http \
  --set-secrets="SLACK_BOT_TOKEN=slack-bot-token:latest"

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

### 8. Initial Setup in Slack

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
