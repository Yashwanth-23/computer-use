import os
import re
import time
import logging
import hashlib
from datetime import datetime, timezone
from typing import Optional
from playwright.sync_api import sync_playwright

from src.agent.observer import SurfaceObserver
from src.agent.llm_client import get_llm_client
from src.agent.artifact_compiler import compile_capability_from_trace
from src.schemas.artifact import CapabilityArtifact

logger = logging.getLogger(__name__)


class DiscoveryAgent:
    """Orchestrates the goal-driven LLM discovery loop against a live application surface."""

    SYSTEM_PROMPT = """
You are an expert AI automation discovery agent navigating an enterprise banking portal.
Your objective is to accomplish the user's natural language goal by observing the page and deciding the next action.

You can take one of the following actions:
- CLICK: click an element by index. Format: {"thought": "...", "action": "CLICK", "element_index": N}
- TYPE: fill an input by index. Format: {"thought": "...", "action": "TYPE", "element_index": N, "value": "...", "parameter_name": "member_id"}
- NAVIGATE: go to URL. Format: {"thought": "...", "action": "NAVIGATE", "url": "..."}
- FINISH: when the goal is achieved. Format: {"thought": "...", "action": "FINISH", "outputs": {"savings_balance": {"element_index": 5, "type": "number", "transform": "strip_currency_symbol"}, "checking_balance": {"element_index": 6, "type": "number", "transform": "strip_currency_symbol"}}}

Respond strictly with a valid JSON object.
"""

    def __init__(self, headless: bool = True, log_file: str = "evidence/discovery_run.log"):
        self.headless = headless
        self.log_file = log_file
        self.observer = SurfaceObserver()
        os.makedirs(os.path.dirname(log_file), exist_ok=True)

    def discover(
        self,
        goal: str,
        target_url: str,
        capability_name: str = "lookup_member_balance",
        max_steps: int = 8,
        provider: Optional[str] = None,
        max_duration_seconds: float = 60.0,
    ) -> CapabilityArtifact:
        """Runs the observe -> decide -> act loop until goal is achieved, then compiles artifact."""
        llm = get_llm_client(provider)
        steps_record = []
        action_history = []
        inputs_meta = []
        outputs_meta = []
        state_history = []
        finished_cleanly = False
        loop_start_time = time.perf_counter()

        model_name = getattr(llm, "model", type(llm).__name__)
        log_entries = [
            f"=== DISCOVERY RUN INITIATED: {datetime.now(timezone.utc).isoformat()} ===",
            f"GOAL: {goal}",
            f"TARGET ENTRY: {target_url}",
            f"PROVIDER: {type(llm).__name__} (Model: {model_name})",
            "----------------------------------------------------------------------"
        ]

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            page = browser.new_page()

            try:
                # Step 0: Initial Navigation
                page.goto(target_url)
                steps_record.append({
                    "action": "NAVIGATE",
                    "target_url": target_url,
                    "element": None,
                    "checkpoint_desc": "Initial navigation landed",
                })
                action_history.append(f"NAVIGATE -> {target_url}")
                log_entries.append(f"[Step 0] NAVIGATE to {target_url}")

                for step_num in range(1, max_steps + 1):
                    # Check wall-clock timeout
                    if (time.perf_counter() - loop_start_time) > max_duration_seconds:
                        raise TimeoutError(f"Discovery exceeded wall-clock deadline of {max_duration_seconds}s")

                    # 1. OBSERVE
                    elements = self.observer.observe(page)
                    prompt_view = self.observer.format_for_prompt(page, elements)

                    # State fingerprint & cycle / dead-end detection
                    state_fp = hashlib.sha256(f"{page.url}:{prompt_view}".encode("utf-8")).hexdigest()[:16]
                    state_history.append(state_fp)
                    if state_history.count(state_fp) >= 3:
                        raise RuntimeError(f"Discovery stuck in dead-end repeated state at {page.url} (detected 3 duplicate cycles)")

                    user_prompt = (
                        f"GOAL: {goal}\n\n"
                        f"PREVIOUS ACTIONS:\n" + "\n".join(action_history) + "\n\n"
                        f"{prompt_view}\n\n"
                        "Decide the next action to achieve the goal."
                    )

                    # 2. DECIDE
                    t_start = time.perf_counter()
                    decision = llm.decide_next_action(self.SYSTEM_PROMPT, user_prompt)
                    latency = round((time.perf_counter() - t_start) * 1000, 1)

                    action = decision.get("action", "").upper()
                    thought = decision.get("thought", "")
                    usage = decision.get("_usage")
                    usage_str = f" | Tokens: in={usage['input_tokens']}, out={usage['output_tokens']}" if usage else ""

                    clean_args = {k: v for k, v in decision.items() if not k.startswith("_") and k != "thought" and k != "action"}
                    log_entries.append(f"[Step {step_num}] Model: {model_name} (Latency: {latency}ms{usage_str})")
                    log_entries.append(f"  Thought: {thought}")
                    log_entries.append(f"  Action:  {action} {clean_args}")

                    # 3. ACT
                    if action == "FINISH":
                        if "outputs" in decision:
                            for out_name, out_cfg in decision["outputs"].items():
                                raw_target = str(out_cfg.get("element_index") or out_cfg.get("element_id") or "")
                                target_el = None
                                if raw_target.isdigit():
                                    target_el = next((e for e in elements if e.index == int(raw_target)), None)
                                elif raw_target:
                                    target_el = next((e for e in elements if e.element_id == raw_target), None)

                                real_id = target_el.element_id if (target_el and target_el.element_id) else raw_target

                                outputs_meta.append({
                                    "name": out_name,
                                    "element_id": real_id,
                                    "type": out_cfg.get("type", "number"),
                                    "transform": out_cfg.get("transform", "strip_currency_symbol")
                                })
                                steps_record.append({
                                    "action": "EXTRACT",
                                    "element": target_el.__dict__ if target_el else {"element_id": real_id},
                                    "checkpoint_desc": f"Extract output {out_name}"
                                })
                        log_entries.append("Goal reported complete. Finishing discovery.")
                        finished_cleanly = True
                        break

                    elif action == "CLICK":
                        el_idx = decision.get("element_index")
                        target_el = next((e for e in elements if e.index == el_idx), None)
                        if not target_el:
                            raise RuntimeError(f"LLM targeted invalid element index: {el_idx}")

                        page.locator(target_el.selector).first.click()
                        action_history.append(f"CLICK element #{el_idx} ({target_el.selector})")
                        steps_record.append({
                            "action": "CLICK",
                            "element": target_el.__dict__,
                            "checkpoint_desc": f"Clicked {target_el.accessible_name or target_el.selector}"
                        })

                    elif action == "TYPE":
                        el_idx = decision.get("element_index")
                        val = decision.get("value", "")
                        raw_param = decision.get("parameter_name") or "member_id"
                        param_name = re.sub(r"[^a-zA-Z0-9_]", "_", raw_param.strip()).lower().strip("_")

                        target_el = next((e for e in elements if e.index == el_idx), None)
                        if not target_el:
                            raise RuntimeError(f"LLM targeted invalid element index: {el_idx}")

                        page.locator(target_el.selector).first.fill(val)
                        action_history.append(f"TYPE '{val}' into #{el_idx} ({target_el.selector})")

                        input_placeholder = f"{{{param_name}}}"
                        if not any(p["name"] == param_name for p in inputs_meta):
                            inputs_meta.append({
                                "name": param_name,
                                "type": "string",
                                "required": True,
                                "pattern": r"^\d{1,10}$",
                                "description": f"Input parameter for {param_name}"
                            })

                        steps_record.append({
                            "action": "TYPE",
                            "element": target_el.__dict__,
                            "input_value": input_placeholder,
                            "checkpoint_desc": f"Entered {param_name}"
                        })

                    page.wait_for_timeout(400)

                if not finished_cleanly:
                    raise RuntimeError(f"Discovery stopped: reached max_steps ({max_steps}) without achieving goal.")

            finally:
                browser.close()

        # Parse allowed domains from target URL
        from urllib.parse import urlparse
        parsed = urlparse(target_url)
        allowed_domains = [parsed.netloc, "127.0.0.1:8000", "localhost:8000"]

        # Compile artifact
        artifact = compile_capability_from_trace(
            capability_name=capability_name,
            description=goal,
            target_url=target_url,
            allowed_domains=allowed_domains,
            steps_record=steps_record,
            inputs_meta=inputs_meta,
            outputs_meta=outputs_meta,
        )

        log_entries.append("----------------------------------------------------------------------")
        log_entries.append(f"Capability artifact compiled successfully: ID={artifact.metadata.id} (Version {artifact.metadata.version})")
        log_entries.append(f"Steps: {len(artifact.steps)} | Inputs: {len(artifact.input_parameters)} | Outputs: {len(artifact.output_parameters)}")
        log_entries.append("=== DISCOVERY COMPLETED ===")

        with open(self.log_file, "w", encoding="utf-8") as f:
            f.write("\n".join(log_entries) + "\n")

        return artifact
