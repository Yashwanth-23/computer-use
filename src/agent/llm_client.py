import os
import re
import json
import subprocess
from typing import Optional, Dict, Any, List


def get_anthropic_key() -> Optional[str]:
    """Retrieve Anthropic API key from process env or Windows User env."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        try:
            cmd = "powershell -Command \"[System.Environment]::GetEnvironmentVariable('ANTHROPIC_API_KEY', 'User')\""
            key = subprocess.check_output(cmd, shell=True).decode().strip()
            if key:
                os.environ["ANTHROPIC_API_KEY"] = key
        except Exception:
            pass
    return key


class LLMClient:
    """Abstract interface for LLM decision making during discovery."""

    def decide_next_action(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        raise NotImplementedError


class AnthropicClient(LLMClient):
    """Production frontier LLM driver using Anthropic Claude Sonnet 5."""

    def __init__(self, api_key: Optional[str] = None):
        import anthropic
        self.api_key = api_key or get_anthropic_key()
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY not found in environment.")
        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.model = "claude-sonnet-5"

    def decide_next_action(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        content_text = ""
        thinking_text = ""
        for block in response.content:
            if getattr(block, "type", None) == "thinking" or hasattr(block, "thinking"):
                thinking_text += getattr(block, "thinking", "")
            elif getattr(block, "type", None) == "text" or hasattr(block, "text"):
                content_text += getattr(block, "text", "")

        usage = {
            "input_tokens": getattr(response.usage, "input_tokens", 0),
            "output_tokens": getattr(response.usage, "output_tokens", 0),
        }

        # Parse JSON from response
        m = re.search(r"\{.*\}", content_text, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
            data["_model"] = self.model
            data["_usage"] = usage
            if thinking_text and not data.get("thought"):
                data["thought"] = thinking_text.strip()[:200]
            return data
        raise ValueError(f"Failed to parse JSON action from Claude Sonnet 5 response: {content_text}")


class GeminiClient(LLMClient):
    """Frontier LLM driver using Google Gemini."""

    def __init__(self, api_key: Optional[str] = None):
        from google import genai
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY not found in environment.")
        self.client = genai.Client(api_key=self.api_key)

    def decide_next_action(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        full_prompt = f"{system_prompt}\n\nUSER REQUEST & CURRENT STATE:\n{user_prompt}"
        response = self.client.models.generate_content(
            model="gemini-2.5-flash",
            contents=full_prompt,
        )
        content = response.text
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
            data["_model"] = "gemini-2.5-flash"
            return data
        raise ValueError(f"Failed to parse JSON action from Gemini response: {content}")


class SimulatedDiscoveryClient(LLMClient):
    """Fallback explorer that performs goal-driven heuristics against the DOM.

    Allows running the full discovery loop on live browsers without an external API key.
    """

    def decide_next_action(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        prompt_lower = user_prompt.lower()

        # Check for maintenance banner first
        if "maintenance notice" in prompt_lower and "acknowledge" in prompt_lower:
            m = re.search(r"\[#(\d+)\].*?(?:acknowledge|btnackmaintenance)", user_prompt, re.I)
            if m:
                return {
                    "thought": "Acknowledge the scheduled maintenance interstitial banner.",
                    "action": "CLICK",
                    "element_index": int(m.group(1))
                }

        # Step: Search for Member ID
        if "member lookup" in prompt_lower or "txtmemberid" in prompt_lower:
            if 'text="1001"' not in prompt_lower and 'text="9999"' not in prompt_lower:
                m = re.search(r"\[#(\d+)\].*?txtmemberid", user_prompt, re.I)
                if m:
                    return {
                        "thought": "Enter member ID 1001 into the search input.",
                        "action": "TYPE",
                        "element_index": int(m.group(1)),
                        "value": "1001",
                        "parameter_name": "member_id"
                    }

            # Click Search button
            m_btn = re.search(r"\[#(\d+)\].*?btnsearch", user_prompt, re.I)
            if m_btn:
                return {
                    "thought": "Submit the member lookup search form.",
                    "action": "CLICK",
                    "element_index": int(m_btn.group(1))
                }

        # Step: Extract Balances on Member Detail page
        if "lblsavingsbalance" in prompt_lower or "account balances" in prompt_lower:
            return {
                "thought": "Successfully reached member balances detail page. Extract balances and finish.",
                "action": "FINISH",
                "outputs": {
                    "savings_balance": {"element_id": "ctl00_MainContent_gvBalances_ctl02_lblSavingsBalance", "type": "number", "transform": "strip_currency_symbol"},
                    "checking_balance": {"element_id": "ctl00_MainContent_gvBalances_ctl03_lblCheckingBalance", "type": "number", "transform": "strip_currency_symbol"}
                }
            }

        return {
            "thought": "Unknown state, attempting to finish.",
            "action": "FINISH"
        }


def get_llm_client(provider: Optional[str] = None) -> LLMClient:
    """Factory selecting the appropriate LLM client based on available environment keys."""
    if provider == "anthropic" or (not provider and get_anthropic_key()):
        return AnthropicClient()
    elif provider == "gemini" or (not provider and os.environ.get("GEMINI_API_KEY")):
        return GeminiClient()
    return SimulatedDiscoveryClient()
