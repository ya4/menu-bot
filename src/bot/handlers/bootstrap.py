"""
Bootstrap handlers for initial setup of the Menu Bot.
Walks the family through setting up members and initial meal preferences.
"""

import re
import logging
from typing import Optional
from datetime import datetime
from slack_bolt import App
from slack_sdk import WebClient

from src.integrations.firestore_client import FirestoreClient, FamilyMember, Preferences
from src.integrations.claude_client import ClaudeClient
from src.integrations.recipe_scraper import RecipeScraper
from src.integrations.sheets_client import SheetsClient, SheetRecipe
from src.bot.slack_utils import format_bootstrap_welcome

logger = logging.getLogger(__name__)


class BootstrapHandlers:
    """Handlers for the bootstrap/setup flow."""

    def __init__(self, app: App, db: FirestoreClient, claude: ClaudeClient = None,
                 sheets: Optional[SheetsClient] = None):
        """Initialize bootstrap handlers."""
        self.app = app
        self.db = db
        self.claude = claude or ClaudeClient()
        self.scraper = RecipeScraper()
        self.sheets = sheets  # Optional - for hybrid architecture
        self._register_handlers()

    def _register_handlers(self):
        """Register all bootstrap-related handlers."""

        @self.app.command("/menu-setup")
        def handle_setup_command(ack, body, client, respond):
            """Handle the /menu-setup command to start or continue setup."""
            ack()

            user_id = body["user_id"]
            channel_id = body["channel_id"]

            # Check if bootstrap is already complete
            prefs = self.db.get_preferences()
            if prefs.bootstrap_complete:
                respond(
                    "Setup is already complete! Use `/menu-help` to see available commands, "
                    "or `/menu-settings` to modify your settings."
                )
                return

            # Check if this is the first user - they become a parent
            members = self.db.get_all_family_members()
            if not members:
                # First user is automatically a parent
                self._open_setup_modal(client, body["trigger_id"], user_id, is_first=True)
            else:
                # Check if user is already registered
                existing = self.db.get_family_member(user_id)
                if existing and existing.is_parent:
                    self._open_setup_modal(client, body["trigger_id"], user_id, is_first=False)
                else:
                    respond(
                        "Only parents can run setup. Ask a parent to complete the setup, "
                        "or have them add you as a parent."
                    )

        @self.app.view("bootstrap_family_setup")
        def handle_family_setup_submission(ack, body, client, view):
            """Handle the family setup modal submission."""
            ack()

            user_id = body["user"]["id"]
            values = view["state"]["values"]

            # Extract family members from the form
            members_text = values["members_block"]["members_input"]["value"]
            channel_id = values["channel_block"]["channel_select"]["selected_channel"]

            # Parse members
            members = self._parse_members(members_text, user_id)

            # Save members
            for member in members:
                self.db.save_family_member(member)

            # Save the planning channel
            prefs = self.db.get_preferences()
            prefs.planning_channel_id = channel_id
            self.db.save_preferences(prefs)

            # Notify in channel
            client.chat_postMessage(
                channel=channel_id,
                text=f"Family setup complete! I've registered {len(members)} family members.",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"Family setup complete! I've registered *{len(members)}* family members.\n\n"
                                "*Next step:* Let's add some recipes you already love! "
                                "Share links, photos, or descriptions of your favorite meals.\n\n"
                                "Or use `/menu-add-favorites` to quickly add a list of go-to meals."
                            ),
                        },
                    },
                ],
            )

        @self.app.command("/menu-add-favorites")
        def handle_add_favorites_command(ack, body, client, respond):
            """Handle command to add initial favorite meals."""
            ack()

            user_id = body["user_id"]

            # Only parents can do this
            if not self.db.is_parent(user_id):
                respond("Only parents can add favorite meals during setup.")
                return

            self._open_favorites_modal(client, body["trigger_id"])

        @self.app.view("bootstrap_favorites")
        def handle_favorites_submission(ack, body, client, view):
            """Handle the favorites modal submission."""
            ack()

            values = view["state"]["values"]
            favorites_text = values["favorites_block"]["favorites_input"]["value"]

            # Parse favorites (one per line)
            favorites = [
                line.strip()
                for line in favorites_text.strip().split("\n")
                if line.strip()
            ]

            # Save favorites to preferences
            prefs = self.db.get_preferences()
            prefs.favorite_meals = favorites
            self.db.save_preferences(prefs)

            channel_id = prefs.planning_channel_id

            if channel_id:
                client.chat_postMessage(
                    channel=channel_id,
                    text=f"Got it! I've noted {len(favorites)} favorite meals.",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": (
                                    f"Got it! I've saved *{len(favorites)}* favorite meals:\n"
                                    + "\n".join([f"- {f}" for f in favorites[:10]])
                                    + (f"\n_...and {len(favorites) - 10} more_" if len(favorites) > 10 else "")
                                    + "\n\n*Next:* Run `/menu-find-recipes` to search for recipes matching these favorites, "
                                    "or share specific recipe links directly in this channel!"
                                ),
                            },
                        },
                    ],
                )

            # Mark bootstrap as complete
            self.db.set_bootstrap_complete()

        @self.app.command("/menu-add-parent")
        def handle_add_parent(ack, body, client, respond):
            """Add another parent to the system."""
            ack()

            user_id = body["user_id"]
            text = body.get("text", "").strip()

            if not self.db.is_parent(user_id):
                respond("Only existing parents can add new parents.")
                return

            if not text:
                respond("Please specify a user: `/menu-add-parent @username`")
                return

            new_parent_id = None
            name = None

            # Try to extract user ID from Slack mention format <@U12345|username>
            match = re.search(r"<@([A-Z0-9]+)\|?[^>]*>", text)
            if match:
                new_parent_id = match.group(1)
            else:
                # Try to find user by username/display name
                # Remove @ prefix if present
                search_name = text.lstrip("@")
                try:
                    # Search for the user in the workspace
                    users_response = client.users_list()
                    for user in users_response.get("members", []):
                        if user.get("deleted") or user.get("is_bot"):
                            continue
                        # Check various name fields
                        if (user.get("name", "").lower() == search_name.lower() or
                            user.get("profile", {}).get("display_name", "").lower() == search_name.lower() or
                            user.get("profile", {}).get("real_name", "").lower() == search_name.lower()):
                            new_parent_id = user["id"]
                            name = user.get("real_name") or user.get("name")
                            break
                except Exception as e:
                    respond(f"Error searching for user: {str(e)}")
                    return

            if not new_parent_id:
                respond(
                    f"Couldn't find user '{text}'. Try mentioning them directly "
                    f"(type @ and select from the dropdown) or check the username."
                )
                return

            # Get user info if we don't have it yet
            if not name:
                try:
                    user_info = client.users_info(user=new_parent_id)
                    name = user_info["user"]["real_name"] or user_info["user"]["name"]
                except Exception:
                    name = "Unknown"

            # Check if already a member
            existing = self.db.get_family_member(new_parent_id)
            if existing:
                existing.is_parent = True
                self.db.save_family_member(existing)
                respond(f"Updated <@{new_parent_id}> to parent status!")
            else:
                member = FamilyMember(
                    slack_user_id=new_parent_id,
                    name=name,
                    user_type="adult",
                    is_parent=True,
                    preference_weight=1.0,
                )
                self.db.save_family_member(member)
                respond(f"Added <@{new_parent_id}> as a parent!")

        @self.app.command("/menu-add-kid")
        def handle_add_kid(ack, body, client, respond):
            """Add a kid to the system."""
            ack()

            user_id = body["user_id"]
            text = body.get("text", "").strip()

            if not self.db.is_parent(user_id):
                respond("Only parents can add family members.")
                return

            # Kids might not have Slack accounts, so accept just a name
            if text.startswith("<@"):
                # Slack user mentioned
                match = re.search(r"<@([A-Z0-9]+)\|?[^>]*>", text)
                if match:
                    kid_id = match.group(1)
                    try:
                        user_info = client.users_info(user=kid_id)
                        name = user_info["user"]["real_name"] or user_info["user"]["name"]
                    except Exception:
                        name = text.replace(f"<@{kid_id}>", "").strip() or "Unknown"

                    member = FamilyMember(
                        slack_user_id=kid_id,
                        name=name,
                        user_type="kid",
                        is_parent=False,
                        preference_weight=1.5,  # Kids get higher weight
                    )
                    self.db.save_family_member(member)
                    respond(f"Added <@{kid_id}> as a kid! Their meal preferences will be prioritized.")
            else:
                # Just a name, no Slack account
                # Generate a placeholder ID
                kid_id = f"kid_{text.lower().replace(' ', '_')}"
                member = FamilyMember(
                    slack_user_id=kid_id,
                    name=text,
                    user_type="kid",
                    is_parent=False,
                    preference_weight=1.5,
                )
                self.db.save_family_member(member)
                respond(f"Added {text} as a kid! Their meal preferences will be prioritized.")

        @self.app.command("/menu-find-recipes")
        def handle_find_recipes(ack, body, client, respond):
            """Find real recipes from cooking sites for saved favorite meals."""
            ack()

            user_id = body["user_id"]

            # Only parents can find recipes
            if not self.db.is_parent(user_id):
                respond("Only parents can find recipes.")
                return

            # Get saved favorites
            prefs = self.db.get_preferences()
            favorites = prefs.favorite_meals

            if not favorites:
                respond(
                    "No favorite meals found! Use `/menu-add-favorites` first to add some meals, "
                    "then run this command to find recipes for them."
                )
                return

            # Check how many recipes we already have
            existing_recipes = self.db.get_all_recipes()
            existing_names = {r.name.lower() for r in existing_recipes}

            # Filter out favorites that already have recipes
            to_find = [f for f in favorites if f.lower() not in existing_names]

            if not to_find:
                respond(
                    f"You already have recipes for all {len(favorites)} favorites! "
                    f"Total recipes: {len(existing_recipes)}. Run `/menu-plan new` to create a meal plan."
                )
                return

            # Notify user we're starting
            channel_id = prefs.planning_channel_id or body["channel_id"]
            client.chat_postMessage(
                channel=channel_id,
                text=f"Searching for recipes for {len(to_find)} favorite meals... This may take a minute."
            )

            # Find recipes from real cooking sites (no AI needed)
            found = []
            failed = []

            for meal_name in to_find:
                try:
                    logger.info(f"Searching for recipe: {meal_name}")
                    recipes = self.scraper.search_and_extract(meal_name)

                    if recipes:
                        # Take the first recipe found - save as pending approval
                        recipe = recipes[0]

                        # Save to Sheets if available, otherwise Firestore
                        if self.sheets:
                            sheet_recipe = SheetRecipe(
                                name=recipe.name,
                                source_url=recipe.source_url or "",
                                approved=False,
                                prep_time_min=recipe.prep_time_min or 0,
                                cook_time_min=recipe.cook_time_min or 0,
                                servings=recipe.servings or 4,
                                tags=", ".join(recipe.tags) if recipe.tags else "",
                                ingredients="\n".join(
                                    f"{i.quantity} {i.unit} {i.name}".strip()
                                    for i in recipe.ingredients
                                ),
                                instructions="\n".join(
                                    f"{n+1}. {step}" for n, step in enumerate(recipe.instructions)
                                ),
                                created_date=datetime.now().strftime("%Y-%m-%d"),
                            )
                            self.sheets.add_recipe(sheet_recipe)
                        else:
                            # Fall back to Firestore
                            recipe.approved = False
                            self.db.save_recipe(recipe)

                        source_info = f" (from {recipe.source_url})" if recipe.source_url else ""
                        found.append(f"{recipe.name}{source_info}")
                        logger.info(f"Successfully found recipe: {recipe.name}")
                    else:
                        failed.append(meal_name)
                        logger.warning(f"Could not find recipe for: {meal_name}")
                except Exception as e:
                    failed.append(meal_name)
                    logger.error(f"Error finding recipe for {meal_name}: {e}")

            # Report results - get counts from appropriate source
            if self.sheets:
                all_recipes = self.sheets.get_all_recipes()
                total_recipes = len(all_recipes)
                approved_count = len([r for r in all_recipes if r.approved])
                sheet_url = self.sheets.get_spreadsheet_url()
            else:
                total_recipes = len(self.db.get_all_recipes())
                approved_count = len([r for r in self.db.get_all_recipes() if r.approved])
                sheet_url = None

            pending_count = total_recipes - approved_count

            result_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Recipe search complete!*\n\n"
                               f"✅ Found: {len(found)} recipes\n"
                               + (f"❌ Not found: {len(failed)} ({', '.join(failed)})\n" if failed else "")
                               + f"\n*Recipes pending approval: {pending_count}*\n"
                               f"*Approved recipes: {approved_count}*"
                    }
                }
            ]

            if len(found) > 0:
                if sheet_url:
                    result_blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"📋 *Next step:* <{sheet_url}|Open the Recipe Sheet> to review and approve recipes."
                        }
                    })
                else:
                    result_blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "📋 *Next step:* Run `/menu-recipes` to review and approve the recipes."
                        }
                    })

            if failed:
                result_blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "💡 *Tip:* For meals I couldn't find, try sharing a specific recipe URL in the channel."
                    }
                })

            if approved_count >= 7:
                result_blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "🎉 You have enough approved recipes! Run `/menu-plan new` to create your first meal plan."
                    }
                })
            else:
                result_blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"You need at least 7 approved recipes for meal planning. "
                               f"Approve {7 - approved_count} more or add new ones with `/menu-add-favorites`."
                    }
                })

            client.chat_postMessage(
                channel=channel_id,
                text=f"Found {len(found)} recipes!",
                blocks=result_blocks
            )

        @self.app.command("/menu-init-sheet")
        def handle_init_sheet(ack, body, client, respond):
            """Initialize the Google Sheet structure for recipe/meal plan management."""
            ack()

            user_id = body["user_id"]

            # Only parents can initialize the sheet
            if not self.db.is_parent(user_id):
                respond("Only parents can initialize the recipe sheet.")
                return

            if not self.sheets:
                respond(
                    "Google Sheets integration is not configured. "
                    "Set the `GOOGLE_SHEET_ID` environment variable to enable this feature."
                )
                return

            respond("Initializing the recipe sheet structure... This may take a moment.")
            logger.info(f"Starting sheet initialization for user {user_id}")

            try:
                success = self.sheets.initialize_spreadsheet()
                logger.info(f"Sheet initialization result: {success}")
                if success:
                    sheet_url = self.sheets.get_spreadsheet_url()
                    client.chat_postMessage(
                        channel=body["channel_id"],
                        text="Recipe sheet initialized!",
                        blocks=[
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": (
                                        "✅ *Recipe sheet initialized successfully!*\n\n"
                                        "I've created the following tabs:\n"
                                        "• *Recipes* - Add and manage recipes here\n"
                                        "• *Meal Plans* - View weekly meal plans\n"
                                        "• *Family* - Family member info\n"
                                        "• *Config* - Bot settings\n\n"
                                        f"<{sheet_url}|📋 Open the Recipe Sheet>"
                                    )
                                }
                            },
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": (
                                        "*Next steps:*\n"
                                        "1. Share the sheet with family members\n"
                                        "2. Run `/menu-add-favorites` to add meal names\n"
                                        "3. Run `/menu-find-recipes` to populate recipes\n"
                                        "4. Mark recipes as approved in the sheet (set Approved = TRUE)"
                                    )
                                }
                            }
                        ]
                    )
                else:
                    respond(
                        "❌ Failed to initialize the sheet. Please check that the bot has "
                        "edit access to the spreadsheet and try again."
                    )
            except Exception as e:
                logger.error(f"Error initializing sheet: {e}")
                respond(f"❌ Error initializing sheet: {str(e)}")

    def _open_setup_modal(self, client: WebClient, trigger_id: str, user_id: str, is_first: bool):
        """Open the family setup modal."""
        # Get user's name for pre-filling
        try:
            user_info = client.users_info(user=user_id)
            user_name = user_info["user"]["real_name"] or user_info["user"]["name"]
        except Exception:
            user_name = "Parent 1"

        initial_text = f"@{user_name} - parent\n"

        client.views_open(
            trigger_id=trigger_id,
            view={
                "type": "modal",
                "callback_id": "bootstrap_family_setup",
                "title": {"type": "plain_text", "text": "Family Setup"},
                "submit": {"type": "plain_text", "text": "Save"},
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                "Let's set up your family! List each family member, "
                                "one per line, in this format:\n"
                                "`@username - parent` or `@username - kid` or `Name - kid`"
                            ),
                        },
                    },
                    {
                        "type": "input",
                        "block_id": "members_block",
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "members_input",
                            "multiline": True,
                            "initial_value": initial_text,
                            "placeholder": {
                                "type": "plain_text",
                                "text": "@mom - parent\n@dad - parent\nEmma - kid\nJack - kid",
                            },
                        },
                        "label": {"type": "plain_text", "text": "Family Members"},
                    },
                    {
                        "type": "input",
                        "block_id": "channel_block",
                        "element": {
                            "type": "channels_select",
                            "action_id": "channel_select",
                            "placeholder": {
                                "type": "plain_text",
                                "text": "Select a channel",
                            },
                        },
                        "label": {"type": "plain_text", "text": "Meal Planning Channel"},
                        "hint": {
                            "type": "plain_text",
                            "text": "Where should I post meal plans and grocery lists?",
                        },
                    },
                ],
            },
        )

    def _open_favorites_modal(self, client: WebClient, trigger_id: str):
        """Open the favorites input modal."""
        client.views_open(
            trigger_id=trigger_id,
            view={
                "type": "modal",
                "callback_id": "bootstrap_favorites",
                "title": {"type": "plain_text", "text": "Favorite Meals"},
                "submit": {"type": "plain_text", "text": "Save"},
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                "List some meals your family already loves! "
                                "One meal per line. These will help me learn your preferences.\n\n"
                                "Don't worry about being specific - I'll help find recipes later."
                            ),
                        },
                    },
                    {
                        "type": "input",
                        "block_id": "favorites_block",
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "favorites_input",
                            "multiline": True,
                            "placeholder": {
                                "type": "plain_text",
                                "text": "Tacos\nSpaghetti and meatballs\nChicken stir-fry\nPizza\nGrilled cheese",
                            },
                        },
                        "label": {"type": "plain_text", "text": "Favorite Meals"},
                    },
                ],
            },
        )

    def _parse_members(self, text: str, submitter_id: str) -> list[FamilyMember]:
        """Parse family members from the setup text."""
        members = []
        lines = text.strip().split("\n")

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Parse line format: @username - type or Name - type
            parts = line.rsplit("-", 1)
            if len(parts) != 2:
                continue

            name_part = parts[0].strip()
            type_part = parts[1].strip().lower()

            is_parent = type_part == "parent"
            user_type = "adult" if is_parent else "kid"
            weight = 1.0 if user_type == "adult" else 1.5

            # Check for Slack mention
            match = re.search(r"<@([A-Z0-9]+)\|?([^>]*)>", name_part)
            if match:
                slack_id = match.group(1)
                name = match.group(2) or name_part.replace(f"<@{slack_id}>", "").strip()
                if not name:
                    name = f"User {slack_id[:4]}"
            elif name_part.startswith("@"):
                # @username format without full mention
                name = name_part[1:]
                slack_id = f"pending_{name.lower().replace(' ', '_')}"
            else:
                # Just a name
                name = name_part
                slack_id = f"member_{name.lower().replace(' ', '_')}"

            members.append(FamilyMember(
                slack_user_id=slack_id,
                name=name,
                user_type=user_type,
                is_parent=is_parent,
                preference_weight=weight,
            ))

        # Ensure submitter is a parent if this is first setup
        submitter_found = any(m.slack_user_id == submitter_id for m in members)
        if not submitter_found:
            members.append(FamilyMember(
                slack_user_id=submitter_id,
                name="Parent",
                user_type="adult",
                is_parent=True,
                preference_weight=1.0,
            ))

        return members
