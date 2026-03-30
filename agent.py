"""
IT Asset Lifecycle Manager
==========================
Pattern : Function Calling — write-back to a system of record

The agent manages IT hardware assets through natural language. When the user
makes a request, the agent calls one or more functions that read from and
write to cmdb.json — the mock Configuration Management Database.

The critical difference from a read-only function calling agent is that
these functions have persistent side effects. A "check out laptop LT-1001
to Alice" call actually mutates cmdb.json. If the session is restarted,
those changes are still there.

Function call flow is identical to Project 02:
  1. User message → agent emits function_call items
  2. Dispatch each call locally (tools.py)
  3. Post FunctionCallOutput list with previous_response_id
  4. Agent confirms the action and explains what changed
"""

import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition, FunctionTool
from openai.types.responses.response_input_param import FunctionCallOutput

from tools import FUNCTION_MAP


# ---------------------------------------------------------------------------
# Load prompt from file
# ---------------------------------------------------------------------------
def load_prompt(filename: str) -> str:
    prompt_path = Path(__file__).parent / "prompts" / filename
    return prompt_path.read_text(encoding="utf-8").strip()


# ---------------------------------------------------------------------------
# FunctionTool schema definitions
# ---------------------------------------------------------------------------
TOOL_DEFINITIONS = [
    FunctionTool(
        name="search_assets",
        description=(
            "Search the CMDB for assets by keyword (asset ID, type, make, model, "
            "or assigned employee name). Optionally filter by status."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keyword to search for. Use empty string to return all assets.",
                },
                "status": {
                    "type": "string",
                    "description": "Filter by status. One of: available, checked_out, in_repair, retired, all.",
                    "enum": ["available", "checked_out", "in_repair", "retired", "all"],
                },
            },
            "required": ["query", "status"],
            "additionalProperties": False,
        },
        strict=True,
    ),
    FunctionTool(
        name="get_asset_details",
        description="Get the full record for a single asset, including complete history.",
        parameters={
            "type": "object",
            "properties": {
                "asset_id": {
                    "type": "string",
                    "description": "The asset ID, e.g. 'LT-1001', 'MN-2001'.",
                },
            },
            "required": ["asset_id"],
            "additionalProperties": False,
        },
        strict=True,
    ),
    FunctionTool(
        name="checkout_asset",
        description=(
            "Assign an available asset to an employee. "
            "The asset status changes to 'checked_out' and the change is persisted to the CMDB."
        ),
        parameters={
            "type": "object",
            "properties": {
                "asset_id": {"type": "string", "description": "ID of the asset to check out."},
                "employee_id": {"type": "string", "description": "Employee ID, e.g. 'EMP-0099'."},
                "employee_name": {"type": "string", "description": "Full name of the employee."},
            },
            "required": ["asset_id", "employee_id", "employee_name"],
            "additionalProperties": False,
        },
        strict=True,
    ),
    FunctionTool(
        name="checkin_asset",
        description=(
            "Return a checked-out or in-repair asset back to available inventory. "
            "The asset status changes to 'available' and the change is persisted."
        ),
        parameters={
            "type": "object",
            "properties": {
                "asset_id": {"type": "string", "description": "ID of the asset being returned."},
                "return_condition": {
                    "type": "string",
                    "description": "Condition of the asset on return: 'good', 'damaged', or 'needs_inspection'.",
                    "enum": ["good", "damaged", "needs_inspection"],
                },
            },
            "required": ["asset_id", "return_condition"],
            "additionalProperties": False,
        },
        strict=True,
    ),
    FunctionTool(
        name="flag_for_repair",
        description=(
            "Flag an asset as needing repair and log the issue description. "
            "The asset status changes to 'in_repair' and the issue is persisted."
        ),
        parameters={
            "type": "object",
            "properties": {
                "asset_id": {"type": "string", "description": "ID of the asset to flag."},
                "issue_description": {
                    "type": "string",
                    "description": "Clear description of the fault or issue reported.",
                },
            },
            "required": ["asset_id", "issue_description"],
            "additionalProperties": False,
        },
        strict=True,
    ),
    FunctionTool(
        name="retire_asset",
        description=(
            "Permanently retire an asset from service. "
            "Status changes to 'retired'. The asset must be checked in first if currently checked out."
        ),
        parameters={
            "type": "object",
            "properties": {
                "asset_id": {"type": "string", "description": "ID of the asset to retire."},
                "reason": {
                    "type": "string",
                    "description": "Reason for retirement, e.g. 'End of life', 'Beyond economic repair'.",
                },
            },
            "required": ["asset_id", "reason"],
            "additionalProperties": False,
        },
        strict=True,
    ),
    FunctionTool(
        name="create_procurement_request",
        description="Raise a procurement request for new hardware assets.",
        parameters={
            "type": "object",
            "properties": {
                "asset_type": {
                    "type": "string",
                    "description": "Type of asset needed, e.g. 'Laptop', 'Monitor', 'Docking Station'.",
                },
                "quantity": {
                    "type": "integer",
                    "description": "Number of units needed.",
                },
                "justification": {
                    "type": "string",
                    "description": "Business justification for the purchase.",
                },
                "priority": {
                    "type": "string",
                    "description": "Priority level: 'low', 'normal', or 'urgent'.",
                    "enum": ["low", "normal", "urgent"],
                },
            },
            "required": ["asset_type", "quantity", "justification", "priority"],
            "additionalProperties": False,
        },
        strict=True,
    ),
]


# ---------------------------------------------------------------------------
# Function call dispatcher
# ---------------------------------------------------------------------------
def dispatch_function_calls(response) -> list:
    outputs = []
    for item in response.output:
        if item.type != "function_call":
            continue

        fn = FUNCTION_MAP.get(item.name)
        if fn is None:
            result = json.dumps({"error": f"Unknown function: {item.name}"})
        else:
            try:
                args = json.loads(item.arguments)
                result = fn(**args)
                # Show which write operations fired so the user can see side effects
                parsed = json.loads(result)
                status_icon = "✓" if parsed.get("success", True) else "✗"
                print(f"  [cmdb] {status_icon} {item.name}({args})")
            except Exception as exc:
                result = json.dumps({"error": str(exc)})
                print(f"  [cmdb] ✗ {item.name} → ERROR: {exc}")

        outputs.append(
            FunctionCallOutput(
                type="function_call_output",
                call_id=item.call_id,
                output=result,
            )
        )
    return outputs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def print_banner():
    print("=" * 60)
    print("   IT ASSET LIFECYCLE MANAGER")
    print("   Powered by Azure AI Foundry — Function Calling")
    print("=" * 60)
    print()


def has_function_calls(response) -> bool:
    return any(item.type == "function_call" for item in response.output)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print_banner()

    load_dotenv()
    project_endpoint = os.getenv("PROJECT_ENDPOINT")
    model_deployment = os.getenv("MODEL_DEPLOYMENT_NAME")

    if not project_endpoint or not model_deployment:
        print("ERROR: PROJECT_ENDPOINT and MODEL_DEPLOYMENT_NAME must be set in .env")
        sys.exit(1)

    with (
        DefaultAzureCredential(
            exclude_environment_credential=True,
            exclude_managed_identity_credential=True,
        ) as credential,
        AIProjectClient(endpoint=project_endpoint, credential=credential) as project_client,
        project_client.get_openai_client() as openai_client,
    ):

        system_prompt = load_prompt("system_prompt.txt")
        agent = project_client.agents.create_version(
            agent_name="it-asset-manager",
            definition=PromptAgentDefinition(
                model=model_deployment,
                instructions=system_prompt,
                tools=TOOL_DEFINITIONS,
            ),
        )
        print(f"Agent ready: {agent.name} (version={agent.version})\n")

        conversation = openai_client.conversations.create()
        print("Session started. Type 'quit' to exit.\n")
        print("Suggested requests to try:")
        print("  1. Show me all available laptops.")
        print("  2. Check out LT-1001 to Alice Chen, employee ID EMP-0201.")
        print("  3. Sarah Mitchell is leaving — check in all her assets.")
        print("  4. Flag LT-1002 for repair: the screen has dead pixels.")
        print("  5. We need 3 new laptops for the new engineering hires.")
        print("  6. Retire DK-4002 — it's beyond repair.")
        print()

        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting...")
                break

            if user_input.lower() in ("quit", "exit", "q"):
                break
            if not user_input:
                print("Please enter a request.\n")
                continue

            openai_client.conversations.items.create(
                conversation_id=conversation.id,
                items=[{"type": "message", "role": "user", "content": user_input}],
            )

            print("\nProcessing...\n")
            response = openai_client.responses.create(
                conversation=conversation.id,
                extra_body={"agent": {"name": agent.name, "type": "agent_reference"}},
                input="",
            )

            if response.status == "failed":
                print(f"[Error] {response.error}\n")
                continue

            while has_function_calls(response):
                function_outputs = dispatch_function_calls(response)
                print()
                response = openai_client.responses.create(
                    input=function_outputs,
                    previous_response_id=response.id,
                    extra_body={"agent": {"name": agent.name, "type": "agent_reference"}},
                )
                if response.status == "failed":
                    print(f"[Error] {response.error}\n")
                    break

            print(f"Manager: {response.output_text}\n")
            print("-" * 60)
            print()

        # -- Cleanup ----------------------------------------------------------
        print("\nCleaning up...")
        try:
            openai_client.conversations.delete(conversation_id=conversation.id)
            print("  Conversation deleted.")
        except Exception as e:
            print(f"  Warning: {e}")

        try:
            project_client.agents.delete_version(
                agent_name=agent.name, agent_version=agent.version
            )
            print("  Agent deleted.")
        except Exception as e:
            print(f"  Warning: {e}")

        print("\nSession complete. Changes to cmdb.json are persisted.")


if __name__ == "__main__":
    main()
