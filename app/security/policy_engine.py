import logging
import re
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from app.security.models import BasePolicy, PolicyAction, PolicyDecision

logger = logging.getLogger(__name__)


class PolicyEngine:
    """Evaluates tool calls against the defined security policies."""

    def __init__(self, policy_path: Path):
        self.policy_path = policy_path
        self._policy: BasePolicy | None = None
        self._load_policy()

    def _load_policy(self):
        try:
            if not self.policy_path.exists():
                logger.warning(
                    f"Security policy file not found at {self.policy_path}. "
                    "Defaulting to BLOCK (fail-secure). "
                    "Create data/security_policies.yaml to configure rules."
                )
                self._policy = BasePolicy(
                    version="1.0", default_action=PolicyAction.BLOCK, rules=[]
                )
                return

            with open(self.policy_path) as f:
                data = yaml.safe_load(f)

            self._policy = BasePolicy.model_validate(data)
            logger.info(f"Loaded {len(self._policy.rules)} security rules from {self.policy_path}")
        except Exception as e:
            logger.error(
                f"Failed to load security policies: {e}. Defaulting to Fail-Secure (BLOCK).",
                exc_info=True,
            )
            self._policy = BasePolicy(version="1.0", default_action=PolicyAction.BLOCK, rules=[])

    def evaluate(self, tool_name: str, arguments: dict) -> PolicyDecision:
        """
        Evaluates a tool call against the loaded rules.
        Rules are evaluated in order. The first match wins.
        If no rules match, the default action is returned.
        """
        if not self._policy:
            return PolicyDecision(
                action=PolicyAction.BLOCK, reason="Policy engine initialization failed."
            )

        for rule in self._policy.rules:
            if rule.target_tool != tool_name:
                continue

            matches_all_args = True
            for arg_key, arg_regex in rule.argument_match.items():
                arg_value = str(arguments.get(arg_key, ""))

                try:
                    if not re.fullmatch(arg_regex, arg_value):
                        matches_all_args = False
                        break
                except re.error as e:
                    logger.error(f"Invalid regex in rule {rule.id}: {arg_regex} -> {e}")
                    matches_all_args = False
                    break

            if matches_all_args:
                logger.debug(f"Tool {tool_name} matched rule {rule.id}, action={rule.action.value}")
                return PolicyDecision(action=rule.action, reason=rule.reason, rule_id=rule.id)

        # Fallback to default
        return PolicyDecision(
            action=self._policy.default_action, reason="Matched default policy action."
        )
